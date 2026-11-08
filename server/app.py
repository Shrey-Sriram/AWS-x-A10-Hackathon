import base64
import csv
import io
import json
import os
import secrets
import sqlite3
import threading
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import pbkdf2_hmac, sha256
from hmac import compare_digest
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import requests
from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, FiniteFloat
from shapely.geometry import shape

from . import county, bedrock, sagemaker, scene, terrain_api
from .parcel_demo import router as demo_router
from .lease_boundary import read_boundary
from .domain import (
    SIGNALS,
    buffer_zone,
    extract,
    document_pages,
    extract_clauses,
    geometry,
    heat_risk,
    image_change,
    import_map,
)


@asynccontextmanager
async def lifespan(app):
    from .monitor import loop, boundary_loop

    stop = threading.Event()
    worker = threading.Thread(target=loop, args=(stop,), daemon=True)
    if os.environ.get("LEASESENSE_MONITOR", "1") == "1":
        worker.start()
        threading.Thread(target=boundary_loop, args=(stop,), daemon=True).start()
    try:
        yield
    finally:
        stop.set()


app = FastAPI(title="parcelsite", lifespan=lifespan)
app.include_router(demo_router)
DB = Path(os.environ.get("LEASESENSE_DB", "data/leasesense.sqlite"))
DB.parent.mkdir(exist_ok=True, parents=True)
with sqlite3.connect(DB) as conn:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users(email TEXT PRIMARY KEY,salt BLOB,hash BLOB);
    CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,email TEXT,expires TEXT);
    CREATE TABLE IF NOT EXISTS parcels(id TEXT PRIMARY KEY,email TEXT,body TEXT);
    CREATE TABLE IF NOT EXISTS contacts(id TEXT PRIMARY KEY,email TEXT,message TEXT,time TEXT);
    CREATE TABLE IF NOT EXISTS terrain_jobs(parcel TEXT PRIMARY KEY, job TEXT, info TEXT, model BLOB, updated TEXT);
    CREATE TABLE IF NOT EXISTS parcel_elevations(parcel TEXT PRIMARY KEY, samples TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS parcel_images(parcel TEXT PRIMARY KEY, source TEXT NOT NULL, jpeg BLOB NOT NULL);
    CREATE TABLE IF NOT EXISTS ai_outputs(
        parcel TEXT NOT NULL, request TEXT NOT NULL, info TEXT NOT NULL, png BLOB,
        PRIMARY KEY(parcel,request)
    );
    """)


def now():
    return datetime.now(timezone.utc)


@contextmanager
def database(write=False):
    db = sqlite3.connect(DB, timeout=15)
    try:
        with db:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
    finally:
        db.close()


class Account(BaseModel):
    email: str = Field(
        min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$"
    )
    password: str = Field(min_length=10, max_length=256)


def session(response, email, db):
    token = secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO sessions VALUES(?,?,?)",
        (
            sha256(token.encode()).hexdigest(),
            email,
            (now() + timedelta(days=7)).isoformat(),
        ),
    )
    response.set_cookie(
        "session",
        token,
        httponly=True,
        samesite="strict",
        max_age=604800,
        secure=os.getenv("HTTPS_ONLY") == "1",
    )
    return {"email": email}


@app.post("/api/signup")
def signup(a: Account, response: Response):
    email = a.email.strip().lower()
    salt = os.urandom(16)
    hashed = pbkdf2_hmac("sha256", a.password.encode(), salt, 600000)
    with database(True) as db:
        try:
            db.execute("INSERT INTO users VALUES(?,?,?)", (email, salt, hashed))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Account already exists")
        return session(response, email, db)


@app.post("/api/login")
def login(a: Account, response: Response):
    email = a.email.strip().lower()
    with database() as db:
        row = db.execute(
            "SELECT salt,hash FROM users WHERE email=?", (email,)
        ).fetchone()
        if not row or not compare_digest(
            row[1], pbkdf2_hmac("sha256", a.password.encode(), row[0], 600000)
        ):
            raise HTTPException(401, "Check your email and password")
        return session(response, email, db)


def user(request: Request):
    digest = sha256(request.cookies.get("session", "").encode()).hexdigest()
    with database() as db:
        row = db.execute(
            "SELECT email FROM sessions WHERE id=? AND expires>?",
            (digest, now().isoformat()),
        ).fetchone()
    if not row:
        raise HTTPException(401, "Sign in to continue")
    return row[0]


@app.post("/api/logout")
def logout(request: Request, response: Response):
    with database(True) as db:
        db.execute(
            "DELETE FROM sessions WHERE id=?",
            (sha256(request.cookies.get("session", "").encode()).hexdigest(),),
        )
    response.delete_cookie("session")
    return {"ok": True}


class Rule(BaseModel):
    metric: Literal["temperature", "soil_moisture", "wind", "pm25", "change"]
    op: Literal["above", "below"]
    limit: FiniteFloat
    clause: str = Field(min_length=1, max_length=2000)
    lease_ref: str = Field(default="", max_length=400)


class LeaseDetails(BaseModel):
    company: str = Field(default="", max_length=200)
    renewal: str = Field(default="", pattern=r"^(?:\d{4}-\d{2}-\d{2})?$")
    stage: Literal["Draft", "In review", "Signed", "Expired"] = "Draft"
    favorite: bool = False


class Parcel(LeaseDetails):
    name: str = Field(min_length=1, max_length=120)
    geometry: dict
    layers: list[dict] = Field(default_factory=list, max_length=32)
    buffer_m: float = Field(default=0, ge=0, le=10000)


def get(db, pid, email):
    row = db.execute(
        "SELECT body FROM parcels WHERE id=? AND email=?", (pid, email)
    ).fetchone()
    if not row:
        raise HTTPException(404, "Parcel not found")
    return json.loads(row[0])


@contextmanager
def edit(pid, email):
    with database(True) as db:
        p = get(db, pid, email)
        yield p
        db.execute(
            "UPDATE parcels SET body=? WHERE id=?",
            (json.dumps(p, allow_nan=False), pid),
        )


@app.get("/api/parcels")
def parcels(email=Depends(user)):
    with database() as db:
        return [
            dict(json.loads(row[0]), site_image_source=row[1])
            for row in db.execute(
                "SELECT p.body,i.source FROM parcels p LEFT JOIN parcel_images i ON i.parcel=p.id WHERE p.email=? ORDER BY p.rowid DESC", (email,)
            )
        ]


@app.delete("/api/parcels/{pid}")
def delete_parcel(pid: str, email=Depends(user)):
    with database(True) as db:
        get(db, pid, email)
        for table in ("parcel_images", "parcel_elevations", "terrain_jobs", "ai_outputs"):
            db.execute(f"DELETE FROM {table} WHERE parcel=?", (pid,))
        db.execute("DELETE FROM parcels WHERE id=? AND email=?", (pid, email))
    return {"ok": True}


@app.delete("/api/parcels/{pid}/ai/{rid}")
def delete_output(pid: str, rid: UUID, email=Depends(user)):
    with database(True) as db:
        get(db, pid, email)
        deleted = db.execute("DELETE FROM ai_outputs WHERE parcel=? AND request=?",
                             (pid, str(rid))).rowcount
        if not deleted:
            raise HTTPException(404, "Saved output not found")
    return {"ok": True}


@app.delete("/api/parcels/{pid}/comparison")
def delete_comparison(pid: str, email=Depends(user)):
    with edit(pid, email) as parcel:
        parcel.pop("latest_image", None)
        parcel.pop("satellite", None)
    return {"ok": True}


@app.delete("/api/parcels/{pid}/site-image")
def delete_site_image(pid: str, email=Depends(user)):
    with database(True) as db:
        get(db, pid, email)
        db.execute("DELETE FROM parcel_images WHERE parcel=?", (pid,))
    return {"ok": True}


@app.delete("/api/parcels/{pid}/records/{kind}/{index}")
def delete_record(pid: str, kind: Literal["rules", "leases", "history"], index: int,
                  expected: dict, email=Depends(user)):
    with edit(pid, email) as parcel:
        records = parcel[kind]
        if not 0 <= index < len(records):
            raise HTTPException(409, "Records changed. Refresh the parcel and try again.")
        record = records[index]
        identity = record if kind == "rules" else {"time": record["time"]}
        if identity != expected:
            raise HTTPException(409, "Records changed. Refresh the parcel and try again.")
        if kind == "leases" and any(r.get("lease_ref") == record["time"] for r in parcel["rules"]):
            raise HTTPException(409, "Delete this lease’s active monitoring rules first.")
        records.pop(index)
        if kind == "rules":
            active = sum(not event["resolved"] for event in parcel["events"])
            parcel["status"] = "Critical" if active > 1 else "Flagged" if active else "Awaiting readings"
            parcel["outlook"] = []
            if record["metric"] == "temperature":
                parcel["risk"] = None
    return {"ok": True}


@app.get("/api/parcels/{pid}/boundary.geojson")
def current_boundary(pid: str, email=Depends(user)):
    with database() as db:
        p = get(db, pid, email)
    feature = {"type": "Feature", "id": pid, "geometry": p["geometry"],
               "properties": {"name": p["name"], "acres": p["acres"]}}
    return Response(json.dumps(feature), media_type="application/geo+json",
                    headers={"Cache-Control": "private, no-store"})


@app.get("/api/parcels/{pid}/observations")
def observation_export(pid: str, email=Depends(user)):
    with database() as db:
        parcel = get(db, pid, email)
    return {"parcel": parcel["name"], "observations": parcel["observations"],
            "satellite": parcel.get("satellite")}


class MapPoint(BaseModel):
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)


@app.post("/api/map/county-parcel")
def county_preview(body: MapPoint, email=Depends(user)):
    return county.lookup(point=(body.lon, body.lat))


class CountyParcel(LeaseDetails):
    name: str = Field(min_length=1, max_length=120)
    number: str = Field(min_length=1, max_length=100)
    buffer_m: float = Field(default=0, ge=0, le=10000)


@app.post("/api/parcels/from-county")
def from_county(body: CountyParcel, email=Depends(user)):
    found = county.lookup(number=body.number)
    record = Parcel(**body.model_dump(exclude={"number"}), geometry=found["geometry"])
    return save_parcel(record, email, boundary_source={
        "provider": "Ada County", "number": found["number"], "checked": now().isoformat()
    })


def acres_for(g):
    from pyproj import Geod
    from shapely import orient_polygons

    area, _ = Geod(ellps="WGS84").geometry_area_perimeter(orient_polygons(shape(g)))
    return round(abs(area) / 4046.8564224, 2)


def sync_county(pid, email, number):
    try:
        found = county.lookup(number=number)
        message = ""
    except HTTPException as exc:
        found = None
        message = exc.detail
    with database(True) as db:
        parcel = get(db, pid, email)
        source = parcel.get("boundary_source") or {}
        if source.get("provider") != "Ada County" or source.get("number") != number:
            return
        source.update(checked=now().isoformat(), message=message)
        if found:
            apply_boundary(db, parcel, found)
        db.execute("UPDATE parcels SET body=? WHERE id=?", (json.dumps(parcel), pid))


def apply_boundary(db, parcel, found):
    pid = parcel["id"]
    source = parcel["boundary_source"]
    if not shape(found["geometry"]).equals(shape(parcel["geometry"])):
        g = found["geometry"]
        parcel.update(geometry=g, acres=acres_for(g),
                      buffer=buffer_zone(g, parcel["buffer_m"]) if parcel["buffer_m"] else None)
        for table in ("terrain_jobs", "parcel_elevations"):
            db.execute(f"DELETE FROM {table} WHERE parcel=?", (pid,))
        source["changed"] = source["checked"]
        db.execute("UPDATE parcel_images SET source=? WHERE parcel=?",
                   ("Site image from earlier boundary", pid))


@app.get("/api/parcels/{pid}/county-preview")
def existing_county_preview(pid: str, email=Depends(user)):
    with database() as db:
        inside = shape(get(db, pid, email)["geometry"]).representative_point()
    return county.lookup(point=(inside.x, inside.y))


class CountyLink(BaseModel):
    number: str = Field(min_length=1, max_length=100)


@app.post("/api/parcels/{pid}/county-link")
def link_county(pid: str, body: CountyLink, email=Depends(user)):
    found = existing_county_preview(pid, email)
    if found["number"] != body.number:
        raise HTTPException(409, "The county match changed. Preview it again.")
    with database(True) as db:
        p = get(db, pid, email)
        old = p.get("boundary_source") or {}
        if old.get("provider") == "Ada County":
            raise HTTPException(409, "Already linked. Refresh this parcel.")
        p["boundary_source"] = {"provider": "Ada County", "number": body.number,
                                "checked": now().isoformat(), "previous_source": old,
                                "previous_geometry": p["geometry"]}
        apply_boundary(db, p, found)
        db.execute("UPDATE parcels SET body=? WHERE id=?", (json.dumps(p), pid))
    return {"ok": True}


@app.post("/api/parcels")
def create(body: Parcel, email=Depends(user)):
    return save_parcel(body, email)


def save_parcel(body, email, lease=None, boundary_source=None):
    try:
        g = geometry(body.geometry)
        layers = [
            dict(
                label=str(layer.get("label", "Land use")),
                geometry=geometry(layer["geometry"]),
            )
            for layer in body.layers
        ]
        p = dict(
            **body.model_dump(exclude={"geometry", "layers"}),
            id=uuid4().hex,
            geometry=g,
            layers=layers,
            acres=acres_for(g),
            buffer=buffer_zone(g, body.buffer_m) if body.buffer_m else None,
            rules=[],
            leases=[lease] if lease else [],
            boundary_source=boundary_source,
            history=[],
            observations=[],
            events=[],
            risk=None,
            status="Unassessed",
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, str(exc))
    with database(True) as db:
        db.execute("INSERT INTO parcels VALUES(?,?,?)", (p["id"], email, json.dumps(p)))
    return p


def read_file(file):
    raw = file.file.read(10_000_001)
    if len(raw) > 10_000_000:
        raise HTTPException(413, "File exceeds 10 MB")
    return raw


@app.post("/api/map/lease-preview")
def lease_preview(file: UploadFile, allow_missing: bool = False, email=Depends(user)):
    raw = read_file(file)
    try:
        return read_boundary(document_pages(file.filename.lower(), raw), allow_missing) or {"geometry": None}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(422, "Could not read this PDF or DOCX exhibit.") from exc


@app.post("/api/parcels/from-lease")
def create_from_lease(file: UploadFile, parcel: str = Form(...),
                      boundary_method: Literal["exhibit", "draw", "county", "import"] = Form("exhibit"),
                      number: str = Form(""), email=Depends(user)):
    raw = read_file(file)
    try:
        body = Parcel.model_validate_json(parcel)
        pages = document_pages(file.filename.lower(), raw)
        preview = read_boundary(pages, allow_missing=boundary_method != "exhibit")
        if preview:
            if boundary_method != "exhibit" or not shape(preview["geometry"]).equals(shape(body.geometry)):
                raise ValueError("The uploaded exhibit differs from the preview. Select the file again.")
            source = {k: v for k, v in preview.items() if k != "geometry"}
        elif boundary_method == "county":
            if not number.strip():
                raise ValueError("Select a county parcel first.")
            found = county.lookup(number=number)
            if not shape(found["geometry"]).equals(shape(body.geometry)):
                raise ValueError("The county boundary changed. Select the parcel again.")
            source = {"provider": "Ada County", "number": found["number"], "checked": now().isoformat()}
        else:
            source = {"method": "User-drawn outline" if boundary_method == "draw" else "Imported boundary file"}
        source["filename"] = file.filename
        lease = dict(
            name=file.filename, time=now().isoformat(), clauses=extract_clauses(pages)
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(422, "Could not read this PDF or DOCX exhibit.") from exc
    return save_parcel(body, email, lease, source)


@app.post("/api/map/import")
def map_file(file: UploadFile, email=Depends(user)):
    raw = read_file(file)
    try:
        return import_map(file.filename.lower(), raw)
    except Exception:
        raise HTTPException(
            422, "Unable to read polygons from this GeoJSON or KML file"
        )


@app.post("/api/parcels/{pid}/details")
def lease_details(pid: str, body: LeaseDetails, email=Depends(user)):
    with edit(pid, email) as p:
        p.update(body.model_dump())
    return p


@app.post("/api/parcels/{pid}/leases")
def lease(pid: str, file: UploadFile, email=Depends(user)):
    raw = read_file(file)
    try:
        clauses = extract(file.filename.lower(), raw)
    except Exception as exc:
        raise HTTPException(422, f"Could not extract lease text: {exc}")
    with edit(pid, email) as p:
        p["leases"].append(
            dict(name=file.filename, time=now().isoformat(), clauses=clauses)
        )
    return p


@app.post("/api/parcels/{pid}/rules")
def rule(pid: str, body: Rule, email=Depends(user)):
    with edit(pid, email) as p:
        if body.lease_ref and not any(
            body.lease_ref == lease["time"] for lease in p["leases"]
        ):
            raise HTTPException(422, "Unknown lease revision")
        p["rules"].append(body.model_dump())
        p["status"] = "Awaiting readings"
    return p


class Note(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


@app.post("/api/parcels/{pid}/notes")
def note(pid: str, body: Note, email=Depends(user)):
    with edit(pid, email) as p:
        p["history"].append({"time": now().isoformat(), "text": body.text})
    return p


@app.post("/api/contact")
def contact(body: Note, email=Depends(user)):
    with database(True) as db:
        db.execute(
            "INSERT INTO contacts VALUES(?,?,?,?)",
            (uuid4().hex, email, body.text, now().isoformat()),
        )
    return {"saved": True}


def observe(p, when, values, source, image=None, prior_samples=None):
    obs = dict(time=when, values=values, source=source)
    if prior_samples is not None:
        obs["prior_samples"] = prior_samples
    if obs in p["observations"]:
        return
    p["observations"].append(obs)
    for r in p["rules"]:
        value = values.get(r["metric"])
        if value is None:
            continue
        breached = value > r["limit"] if r["op"] == "above" else value < r["limit"]
        if breached:
            existing = next(
                (e for e in p["events"] if e["rule"] == r and not e["resolved"]), None
            )
            if existing:
                if when >= existing.get("last_seen", existing["time"]):
                    existing.update(last_seen=when, latest_value=value)
                continue
            p["events"].append(
                dict(
                    id=uuid4().hex,
                    time=when,
                    source=source,
                    rule=r,
                    value=value,
                    image=image,
                    action=SIGNALS[r["metric"]][1],
                    resolved=False,
                )
            )
    if "temperature" in values:
        p["risk"] = heat_risk(p, obs)
    active = sum(not e["resolved"] for e in p["events"])
    latest = max(datetime.fromisoformat(o["time"]) for o in p["observations"])
    covered = {
        k
        for o in p["observations"]
        if datetime.fromisoformat(o["time"]) >= latest - timedelta(days=1)
        for k in o["values"]
    }
    p["status"] = (
        "Critical"
        if active > 1
        else "Flagged"
        if active
        else "Compliant"
        if p["rules"] and all(r["metric"] in covered for r in p["rules"])
        else "Unassessed"
    )


@app.post("/api/parcels/{pid}/events/{eid}/resolve")
def resolve(pid: str, eid: str, body: Note, email=Depends(user)):
    with edit(pid, email) as p:
        event = next((e for e in p["events"] if e["id"] == eid), None)
        if not event:
            raise HTTPException(404, "Event not found")
        event.update(resolved=True, resolution=body.text, resolved_at=now().isoformat())
        active = sum(not e["resolved"] for e in p["events"])
        p["status"] = (
            "Critical" if active > 1 else "Flagged" if active else "Awaiting readings"
        )
    return p


def feed(host, params):
    try:
        res = requests.get(host, params=params, timeout=15)
        res.raise_for_status()
        return res.json()
    except (requests.RequestException, ValueError) as exc:
        raise HTTPException(502, "Sensor provider unavailable, retry later") from exc


@app.post("/api/parcels/{pid}/refresh")
def refresh(pid: str, email=Depends(user)):
    with database() as db:
        point = shape(get(db, pid, email)["geometry"]).centroid
    loc = dict(latitude=point.y, longitude=point.x, timezone="GMT")
    weather = feed(
        "https://api.open-meteo.com/v1/forecast",
        dict(
            **loc,
            current="temperature_2m,wind_speed_10m",
            hourly="temperature_2m,soil_moisture_0_to_1cm,soil_moisture_1_to_3cm,soil_moisture_3_to_9cm,precipitation",
            past_days=1,
            forecast_days=1,
        ),
    )
    air_error = None
    try:
        air = feed(
            "https://air-quality-api.open-meteo.com/v1/air-quality",
            dict(**loc, current="pm2_5"),
        )["current"]
    except HTTPException as exc:
        air_error = exc.detail
        air = {}
    current = weather["current"]
    hour = current["time"][:13] + ":00"
    hourly = weather["hourly"]
    with edit(pid, email) as p:
        values = {
            "temperature": current["temperature_2m"],
            "wind": current["wind_speed_10m"],
        }
        index = hourly["time"].index(hour) if hour in hourly["time"] else -1
        prior = (
            [hourly["temperature_2m"][index - offset] for offset in (24, 18, 12, 6)]
            if index >= 24
            else None
        )
        prior = prior if prior and None not in prior else None
        observe(
            p,
            current["time"] + "+00:00",
            {k: v for k, v in values.items() if v is not None},
            "Open-Meteo weather",
            prior_samples=prior,
        )
        if hour in hourly["time"]:
            index = hourly["time"].index(hour)
            depths = [
                hourly[k][index]
                for k in (
                    "soil_moisture_0_to_1cm",
                    "soil_moisture_1_to_3cm",
                    "soil_moisture_3_to_9cm",
                )
            ]
            soil = (
                None
                if None in depths
                else (depths[0] + 2 * depths[1] + 4 * depths[2]) / 7
            )
            if soil is not None:
                observe(
                    p,
                    hour + "+00:00",
                    {"soil_moisture": round(soil, 4)},
                    "Open-Meteo soil (0–7 cm estimate)",
                )
                rain = hourly["precipitation"][index - 23 : index + 1]
                if (
                    values["temperature"] is not None
                    and len(rain) == 24
                    and None not in rain
                ):
                    from .forecast import soil_outlook

                    p["outlook"] = soil_outlook(
                        p,
                        values["temperature"],
                        soil,
                        sum(rain),
                        point,
                        datetime.fromisoformat(hour),
                    )
        if air.get("pm2_5") is not None:
            observe(p, air["time"] + "+00:00", {"pm25": air["pm2_5"]}, "Open-Meteo air")
        p["feed_notice"] = air_error
    return p


@app.post("/api/parcels/{pid}/images")
def images(pid: str, before: UploadFile, after: UploadFile, email=Depends(user)):
    try:
        pct, img = image_change(read_file(before), read_file(after))
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc))
    with edit(pid, email) as p:
        evidence = "data:image/jpeg;base64," + base64.b64encode(img).decode()
        p["latest_image"] = evidence
        p.pop("satellite", None)
        observe(
            p, now().isoformat(), {"change": pct}, "Uploaded aligned imagery", evidence
        )
    return p


@app.post("/api/parcels/{pid}/satellite")
def satellite(pid: str, email=Depends(user)):
    from .satellite import compare

    with database() as db:
        geom = get(db, pid, email)["geometry"]
    day = now().date() - timedelta(days=2)
    try:
        percent, img, metadata = compare(geom, day)
    except (ValueError, OSError, requests.RequestException) as exc:
        raise HTTPException(502, "Satellite comparison unavailable") from exc
    with edit(pid, email) as p:
        evidence = "data:image/jpeg;base64," + base64.b64encode(img).decode()
        p.update(latest_image=evidence, satellite=metadata)
        observe(
            p,
            day.isoformat() + "T00:00:00+00:00",
            {"change": percent},
            metadata["provider"],
            evidence,
        )
    return p


@app.get("/api/parcels/{pid}/reports/{kind}")
def report(
    pid: str,
    kind: Literal["weekly", "violations", "scorecard", "geojson", "heatmap"],
    email=Depends(user),
):
    with database() as db:
        p = get(db, pid, email)
    if kind == "heatmap":
        from .exports import heatmap

        return Response(
            heatmap(p),
            media_type="image/png",
            headers={
                "Content-Disposition": f'attachment; filename="heatmap-{pid}.png"'
            },
        )
    if kind == "violations":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["UTC", "rule", "metric", "value", "action", "resolved"])
        for e in p["events"]:
            row = [
                e["time"],
                e["rule"]["clause"],
                e["rule"]["metric"],
                e["value"],
                e["action"],
                e["resolved"],
            ]
            writer.writerow(
                [
                    "'" + v
                    if isinstance(v, str) and v.startswith(("=", "+", "-", "@"))
                    else v
                    for v in row
                ]
            )
        data, mime, ext = buf.getvalue(), "text/csv", "csv"
    elif kind == "geojson":
        areas = [{"label": p["name"], "geometry": p["geometry"]}, *p["layers"]]
        if p["buffer"]:
            areas.append(dict(label="Buffer", geometry=p["buffer"]))
        data = json.dumps(
            dict(
                type="FeatureCollection",
                features=[
                    dict(
                        type="Feature",
                        geometry=a["geometry"],
                        properties={
                            "name": a["label"],
                            "risk_72h": p["risk"],
                            "status": p["status"],
                        },
                    )
                    for a in areas
                ],
            )
        )
        mime, ext = "application/geo+json", "geojson"
    else:
        cutoff = (now() - timedelta(days=7)).isoformat()
        summary = dict(
            parcel=p["name"],
            generated=now().isoformat(),
            status=p["status"],
            heat_risk_72h=p["risk"],
            rules=p["rules"],
            open_events=sum(not e["resolved"] for e in p["events"]),
        )
        if kind == "scorecard":
            latest = {}
            for obs in sorted(p["observations"], key=lambda o: o["time"]):
                for key, value in obs["values"].items():
                    latest[key] = (value, obs["time"], obs["source"])
            rows = []
            for r in p["rules"]:
                reading = latest.get(r["metric"])
                state = "Unassessed"
                if reading and datetime.fromisoformat(reading[1]) >= now() - timedelta(
                    days=1
                ):
                    breached = (
                        reading[0] > r["limit"]
                        if r["op"] == "above"
                        else reading[0] < r["limit"]
                    )
                    state = "Exceeded" if breached else "Within limit"
                rows.append(dict(rule=r, state=state, latest_reading=reading))
            assessed = [row for row in rows if row["state"] != "Unassessed"]
            summary["checks"] = rows
            summary["percent_within_limit"] = (
                round(
                    100
                    * sum(row["state"] == "Within limit" for row in assessed)
                    / len(assessed),
                    1,
                )
                if assessed
                else None
            )
        if kind == "weekly":
            summary.update(
                since=cutoff,
                events=[e for e in p["events"] if e["time"] >= cutoff],
                observations=[o for o in p["observations"] if o["time"] >= cutoff],
            )
        data, mime, ext = json.dumps(summary, indent=2), "application/json", "json"
    return Response(
        data,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{kind}-{pid}.{ext}"'},
    )


class Generation(BaseModel):
    request_id: UUID
    kind: Literal["concept", "brief", "concept3d"]
    prompt: str = Field(min_length=1, max_length=3000, pattern=r"\S")
    aspect_ratio: Literal["16:9", "1:1", "9:16"] = "16:9"


@app.get("/api/parcels/{pid}/ai")
def generations(pid: str, email=Depends(user)):
    with database() as db:
        get(db, pid, email)
        rows = db.execute(
            "SELECT info FROM ai_outputs WHERE parcel=? AND json_extract(info,'$.kind') IN ('brief','concept','concept3d') ORDER BY rowid DESC", (pid,)
        )
        return [
            info for (raw,) in rows if (info := json.loads(raw))["state"] == "ready"
        ]


@app.get("/api/parcels/{pid}/ai/{rid}/image")
def generated_image(pid: str, rid: UUID, email=Depends(user)):
    with database() as db:
        get(db, pid, email)
        row = db.execute(
            "SELECT png FROM ai_outputs WHERE parcel=? AND request=? AND json_extract(info,'$.kind')='concept'", (pid, str(rid))
        ).fetchone()
    if not row or not row[0]:
        raise HTTPException(404, "Image not found")
    return Response(
        row[0], media_type="image/png", headers={"Cache-Control": "private, no-store"}
    )


def site_image(db, pid):
    return db.execute("SELECT source,jpeg FROM parcel_images WHERE parcel=?", (pid,)).fetchone()


@app.post("/api/parcels/{pid}/site-image")
def upload_site_image(pid: str, file: UploadFile, email=Depends(user)):
    from PIL import Image, ImageOps
    with database() as db:
        get(db, pid, email)
    raw = file.file.read(10_000_001)
    if len(raw) > 10_000_000:
        raise HTTPException(413, "Choose an image under 10 MB.")
    try:
        image = Image.open(io.BytesIO(raw))
        if image.format not in {"JPEG", "PNG"} or image.width * image.height > 24_000_000:
            raise ValueError()
        image.thumbnail((1600, 1600))
        out = io.BytesIO()
        ImageOps.exif_transpose(image).convert("RGB").save(out, "JPEG", quality=90)
    except (ValueError, OSError, Image.DecompressionBombError):
        raise HTTPException(422, "Choose a readable JPEG or PNG under 24 megapixels.")
    with database(True) as db:
        get(db, pid, email)
        db.execute("INSERT OR REPLACE INTO parcel_images VALUES(?,?,?)", (pid, "Uploaded site image", out.getvalue()))
    return {"source": "Uploaded site image"}


@app.get("/api/parcels/{pid}/site-image")
def parcel_photo(pid: str, email=Depends(user)):
    with database() as db:
        get(db, pid, email)
        image = site_image(db, pid)
    if image is None:
        raise HTTPException(404, "No site image attached.")
    return Response(image[1], media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})


def elevation_grid(db, pid):
    row = db.execute("SELECT samples FROM parcel_elevations WHERE parcel=?", (pid,)).fetchone()
    return json.loads(row[0]) if row else None


@app.get("/api/parcels/{pid}/terrain")
def terrain_status(pid: str, email=Depends(user)):
    with database() as db:
        return terrain_api.status(db, get(db, pid, email))


@app.post("/api/parcels/{pid}/terrain")
def load_terrain(pid: str, email=Depends(user)):
    with database(True) as db:
        return terrain_api.start(db, get(db, pid, email))


@app.get("/api/parcels/{pid}/scene.glb")
def parcel_scene(pid: str, email=Depends(user)):
    with database() as db:
        parcel = get(db, pid, email)
        aerial = site_image(db, pid)
        if aerial and aerial[0] == "Site image from earlier boundary":
            aerial = None
        elevations = elevation_grid(db, pid)
        cached = db.execute("SELECT model FROM terrain_jobs WHERE parcel=?", (pid,)).fetchone() if not aerial else None
    model = cached[0] if cached and cached[0] else scene.build(parcel, aerial=aerial, elevations=elevations)
    return Response(model, media_type="model/gltf-binary", headers={"Cache-Control": "private, no-store"})


@app.get("/api/parcels/{pid}/ai/{rid}/model.glb")
def saved_scene(pid: str, rid: UUID, email=Depends(user)):
    with database() as db:
        get(db, pid, email)
        row = db.execute("SELECT png FROM ai_outputs WHERE parcel=? AND request=? AND json_extract(info,'$.kind')='concept3d'", (pid, str(rid))).fetchone()
    if not row or not row[0]:
        raise HTTPException(404, "Model not found")
    return Response(row[0], media_type="model/gltf-binary", headers={"Cache-Control":"private, no-store"})


@app.post("/api/parcels/{pid}/ai")
def generate(pid: str, body: Generation, email=Depends(user)):
    rid = str(body.request_id)
    model = {"concept": sagemaker.MODEL, "concept3d": "OpenAI Shap-E", "brief": bedrock.TEXT}[body.kind]
    info = dict(
        id=rid,
        kind=body.kind,
        model=model,
        prompt=body.prompt,
        region=sagemaker.REGION if body.kind != "brief" else bedrock.REGION,
        provider="SageMaker" if body.kind != "brief" else "Bedrock",
        aspect_ratio=body.aspect_ratio,
        time=now().isoformat(),
        state="pending",
    )
    with database(True) as db:
        parcel = get(db, pid, email)
        row = db.execute(
            "SELECT info FROM ai_outputs WHERE parcel=? AND request=?", (pid, rid)
        ).fetchone()
        if row:
            previous = json.loads(row[0])
            if any(
                previous[k] != info[k]
                for k in ("kind", "prompt", "model", "aspect_ratio")
            ):
                raise HTTPException(409, "Request ID already used")
            if previous["state"] == "ready":
                return previous
            raise HTTPException(
                409,
                "This request was already submitted. Refresh the gallery before starting another.",
            )
        db.execute(
            "INSERT INTO ai_outputs(parcel,request,info) VALUES(?,?,?)",
            (pid, rid, json.dumps(info)),
        )
    try:
        if body.kind == "concept3d":
            with database() as db:
                aerial = site_image(db, pid)
                if aerial and aerial[0] == "Site image from earlier boundary":
                    aerial = None
                elevations = elevation_grid(db, pid)
            png = scene.build(parcel, scene.concept_mesh(body.prompt), aerial=aerial, elevations=elevations)
            extra = {"endpoint": scene.ENDPOINT}
        elif body.kind == "concept":
            extra, png = sagemaker.image(body.prompt, body.aspect_ratio)
        else:
            extra, png = bedrock.aws_call(
                bedrock.brief,
                model,
                prompt=body.prompt,
                records=bedrock.sources(parcel),
            )
        info.update(extra, state="ready")
        with database(True) as db:
            db.execute(
                "UPDATE ai_outputs SET info=?,png=? WHERE parcel=? AND request=?",
                (json.dumps(info), png, pid, rid),
            )
    except Exception:
        with database(True) as db:
            info["state"] = "failed"
            db.execute(
                "UPDATE ai_outputs SET info=? WHERE parcel=? AND request=?",
                (json.dumps(info), pid, rid),
            )
        raise
    return info


app.mount(
    "/",
    StaticFiles(directory=Path(__file__).resolve().parents[1] / "web", html=True),
    name="ui",
)
