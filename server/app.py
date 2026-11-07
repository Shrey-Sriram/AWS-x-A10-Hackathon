from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import pbkdf2_hmac, sha256
from hmac import compare_digest
from pathlib import Path
from typing import Literal
from uuid import uuid4
import base64
import csv
import io
import json
import os
import secrets
import sqlite3

import requests
from fastapi import Depends, FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, FiniteFloat
from shapely.geometry import shape
from .domain import SIGNALS, buffer_zone, extract, geometry, heat_risk, image_change, import_map

app=FastAPI(title="LeaseSense")
DB=Path(os.environ.get("LEASESENSE_DB","data/leasesense.sqlite"))
DB.parent.mkdir(exist_ok=True,parents=True)
with sqlite3.connect(DB) as conn:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users(email TEXT PRIMARY KEY,salt BLOB,hash BLOB);
    CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,email TEXT,expires TEXT);
    CREATE TABLE IF NOT EXISTS parcels(id TEXT PRIMARY KEY,email TEXT,body TEXT);
    CREATE TABLE IF NOT EXISTS contacts(id TEXT PRIMARY KEY,email TEXT,message TEXT,time TEXT);
    """)


def now():
    return datetime.now(timezone.utc)


@contextmanager
def database(write=False):
    db=sqlite3.connect(DB,timeout=15)
    try:
        with db:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
    finally:
        db.close()


class Account(BaseModel):
    email: str = Field(min_length=3,max_length=254,pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    password: str = Field(min_length=10,max_length=256)


def session(response,email,db):
    token=secrets.token_urlsafe(32)
    db.execute("INSERT INTO sessions VALUES(?,?,?)",(sha256(token.encode()).hexdigest(),email,(now()+timedelta(days=7)).isoformat()))
    response.set_cookie("session",token,httponly=True,samesite="strict",max_age=604800,secure=os.getenv("HTTPS_ONLY")=="1")
    return {"email":email}


@app.post("/api/signup")
def signup(a:Account,response:Response):
    email=a.email.strip().lower()
    salt=os.urandom(16)
    hashed=pbkdf2_hmac("sha256",a.password.encode(),salt,600000)
    with database(True) as db:
        try:
            db.execute("INSERT INTO users VALUES(?,?,?)",(email,salt,hashed))
        except sqlite3.IntegrityError:
            raise HTTPException(409,"Account already exists")
        return session(response,email,db)


@app.post("/api/login")
def login(a:Account,response:Response):
    email=a.email.strip().lower()
    with database() as db:
        row=db.execute("SELECT salt,hash FROM users WHERE email=?",(email,)).fetchone()
        if not row or not compare_digest(row[1],pbkdf2_hmac("sha256",a.password.encode(),row[0],600000)):
            raise HTTPException(401,"Check your email and password")
        return session(response,email,db)


def user(request:Request):
    digest=sha256(request.cookies.get("session","").encode()).hexdigest()
    with database() as db:
        row=db.execute("SELECT email FROM sessions WHERE id=? AND expires>?",(digest,now().isoformat())).fetchone()
    if not row:
        raise HTTPException(401,"Sign in to continue")
    return row[0]


@app.post("/api/logout")
def logout(request:Request,response:Response):
    with database(True) as db:
        db.execute("DELETE FROM sessions WHERE id=?",(sha256(request.cookies.get("session","").encode()).hexdigest(),))
    response.delete_cookie("session")
    return {"ok":True}


class Rule(BaseModel):
    metric: Literal["temperature","soil_moisture","wind","pm25","change"]
    op: Literal["above","below"]
    limit: FiniteFloat
    clause: str = Field(min_length=1,max_length=2000)


class Parcel(BaseModel):
    name: str = Field(min_length=1,max_length=120)
    geometry: dict
    layers: list[dict] = Field(default_factory=list,max_length=32)
    buffer_m: float = Field(default=0,ge=0,le=10000)
    renewal: str = ""
    company: str = ""


def get(db,pid,email):
    row=db.execute("SELECT body FROM parcels WHERE id=? AND email=?",(pid,email)).fetchone()
    if not row:
        raise HTTPException(404,"Parcel not found")
    return json.loads(row[0])


@contextmanager
def edit(pid,email):
    with database(True) as db:
        p=get(db,pid,email)
        yield p
        db.execute("UPDATE parcels SET body=? WHERE id=?",(json.dumps(p,allow_nan=False),pid))


@app.get("/api/parcels")
def parcels(email=Depends(user)):
    with database() as db:
        return [json.loads(row[0]) for row in db.execute("SELECT body FROM parcels WHERE email=? ORDER BY rowid DESC",(email,))]


@app.post("/api/parcels")
def create(body:Parcel,email=Depends(user)):
    try:
        g=geometry(body.geometry)
        layers=[dict(label=str(layer.get("label","Land use")),geometry=geometry(layer["geometry"])) for layer in body.layers]
        p=dict(**body.model_dump(exclude={"geometry","layers"}),id=uuid4().hex,geometry=g,layers=layers,
               buffer=buffer_zone(g,body.buffer_m) if body.buffer_m else None,
               rules=[],leases=[],history=[],observations=[],events=[],risk=None,status="Unassessed")
    except (ValueError,KeyError,TypeError) as exc:
        raise HTTPException(422,str(exc))
    with database(True) as db:
        db.execute("INSERT INTO parcels VALUES(?,?,?)",(p["id"],email,json.dumps(p)))
    return p


def read_file(file):
    raw=file.file.read(10_000_001)
    if len(raw)>10_000_000:
        raise HTTPException(413,"File exceeds 10 MB")
    return raw


@app.post("/api/map/import")
def map_file(file:UploadFile,email=Depends(user)):
    raw=read_file(file)
    try:
        return import_map(file.filename.lower(),raw)
    except Exception:
        raise HTTPException(422,"Unable to read polygons from this GeoJSON or KML file")


@app.post("/api/parcels/{pid}/leases")
def lease(pid:str,file:UploadFile,email=Depends(user)):
    raw=read_file(file)
    try:
        clauses=extract(file.filename.lower(),raw)
    except Exception as exc:
        raise HTTPException(422,f"Could not extract lease text: {exc}")
    with edit(pid,email) as p:
        p["leases"].append(dict(name=file.filename,time=now().isoformat(),clauses=clauses))
    return p


@app.post("/api/parcels/{pid}/rules")
def rule(pid:str,body:Rule,email=Depends(user)):
    with edit(pid,email) as p:
        p["rules"].append(body.model_dump())
        p["status"]="Awaiting readings"
    return p


class Note(BaseModel):
    text: str = Field(min_length=1,max_length=5000)


@app.post("/api/parcels/{pid}/notes")
def note(pid:str,body:Note,email=Depends(user)):
    with edit(pid,email) as p:
        p["history"].append({"time":now().isoformat(),"text":body.text})
    return p


@app.post("/api/contact")
def contact(body:Note,email=Depends(user)):
    with database(True) as db:
        db.execute("INSERT INTO contacts VALUES(?,?,?,?)",(uuid4().hex,email,body.text,now().isoformat()))
    return {"saved":True}


def observe(p,when,values,source,image=None):
    obs=dict(time=when,values=values,source=source)
    if obs in p["observations"]:
        return
    p["observations"].append(obs)
    for r in p["rules"]:
        value=values.get(r["metric"])
        if value is None:
            continue
        breached=value>r["limit"] if r["op"]=="above" else value<r["limit"]
        if breached:
            p["events"].append(dict(id=uuid4().hex,time=when,source=source,rule=r,value=value,
                                    image=image,action=SIGNALS[r["metric"]][1],resolved=False))
    if "temperature" in values:
        p["risk"]=heat_risk(p,obs)
    active=sum(not e["resolved"] for e in p["events"])
    covered={k for o in p["observations"] if o["time"]>=when[:10] for k in o["values"]}
    p["status"]="Critical" if active>1 else "Flagged" if active else "Compliant" if p["rules"] and all(r["metric"] in covered for r in p["rules"]) else "Unassessed"


@app.post("/api/parcels/{pid}/events/{eid}/resolve")
def resolve(pid:str,eid:str,body:Note,email=Depends(user)):
    with edit(pid,email) as p:
        event=next((e for e in p["events"] if e["id"]==eid),None)
        if not event:
            raise HTTPException(404,"Event not found")
        event.update(resolved=True,resolution=body.text,resolved_at=now().isoformat())
        active=sum(not e["resolved"] for e in p["events"])
        p["status"]="Critical" if active>1 else "Flagged" if active else "Awaiting readings"
    return p


def feed(host,params):
    try:
        res=requests.get(host,params=params,timeout=15)
        res.raise_for_status()
        return res.json()
    except (requests.RequestException,ValueError) as exc:
        raise HTTPException(502,"Sensor provider unavailable, retry later") from exc


@app.post("/api/parcels/{pid}/refresh")
def refresh(pid:str,email=Depends(user)):
    with database() as db:
        point=shape(get(db,pid,email)["geometry"]).centroid
    loc=dict(latitude=point.y,longitude=point.x,timezone="GMT")
    weather=feed("https://api.open-meteo.com/v1/forecast",dict(**loc,current="temperature_2m,wind_speed_10m",hourly="soil_moisture_0_to_1cm",forecast_days=1))
    air_error=None
    try:
        air=feed("https://air-quality-api.open-meteo.com/v1/air-quality",dict(**loc,current="pm2_5"))["current"]
    except HTTPException as exc:
        air_error=exc.detail
        air={}
    current=weather["current"]
    hour=current["time"][:13]+":00"
    hourly=weather["hourly"]
    with edit(pid,email) as p:
        values={"temperature":current["temperature_2m"],"wind":current["wind_speed_10m"]}
        observe(p,current["time"]+"+00:00",{k:v for k,v in values.items() if v is not None},"Open-Meteo weather")
        if hour in hourly["time"]:
            soil=hourly["soil_moisture_0_to_1cm"][hourly["time"].index(hour)]
            if soil is not None:
                observe(p,hour+"+00:00",{"soil_moisture":soil},"Open-Meteo soil")
        if air.get("pm2_5") is not None:
            observe(p,air["time"]+"+00:00",{"pm25":air["pm2_5"]},"Open-Meteo air")
        p["feed_notice"]=air_error
    return p


@app.post("/api/parcels/{pid}/images")
def images(pid:str,before:UploadFile,after:UploadFile,email=Depends(user)):
    try:
        pct,img=image_change(read_file(before),read_file(after))
    except (ValueError,OSError) as exc:
        raise HTTPException(422,str(exc))
    with edit(pid,email) as p:
        evidence="data:image/jpeg;base64,"+base64.b64encode(img).decode()
        p["latest_image"]=evidence
        observe(p,now().isoformat(),{"change":pct},"Uploaded aligned imagery",evidence)
    return p


@app.get("/api/parcels/{pid}/reports/{kind}")
def report(pid:str,kind:Literal["weekly","violations","scorecard","geojson"],email=Depends(user)):
    with database() as db:
        p=get(db,pid,email)
    if kind=="violations":
        buf=io.StringIO()
        writer=csv.writer(buf)
        writer.writerow(["UTC","rule","metric","value","action","resolved"])
        for e in p["events"]:
            row=[e["time"],e["rule"]["clause"],e["rule"]["metric"],e["value"],e["action"],e["resolved"]]
            writer.writerow(["'"+v if isinstance(v,str) and v.startswith(("=","+","-","@")) else v for v in row])
        data,mime,ext=buf.getvalue(),"text/csv","csv"
    elif kind=="geojson":
        areas=[{"label":p["name"],"geometry":p["geometry"]},*p["layers"]]
        if p["buffer"]:
            areas.append(dict(label="Buffer",geometry=p["buffer"]))
        data=json.dumps(dict(type="FeatureCollection",features=[dict(type="Feature",geometry=a["geometry"],properties={"name":a["label"],"risk_72h":p["risk"],"status":p["status"]}) for a in areas]))
        mime,ext="application/geo+json","geojson"
    else:
        cutoff=(now()-timedelta(days=7)).isoformat()
        summary=dict(parcel=p["name"],generated=now().isoformat(),status=p["status"],heat_risk_72h=p["risk"],rules=p["rules"],open_events=sum(not e["resolved"] for e in p["events"]))
        if kind=="weekly":
            summary.update(since=cutoff,events=[e for e in p["events"] if e["time"]>=cutoff],observations=[o for o in p["observations"] if o["time"]>=cutoff])
        data,mime,ext=json.dumps(summary,indent=2),"application/json","json"
    return Response(data,media_type=mime,headers={"Content-Disposition":f'attachment; filename="{kind}-{pid}.{ext}"'})


app.mount("/",StaticFiles(directory=Path(__file__).resolve().parents[1]/"web",html=True),name="ui")
