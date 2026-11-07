import json
import re
from datetime import datetime, timezone
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import joblib
import numpy as np
from PIL import Image, ImageOps
from pypdf import PdfReader
from shapely import transform
from shapely.geometry import Polygon, mapping, shape

from ml.train import ROOT, features

clause_model = joblib.load(ROOT / "clauses.joblib")
risk_model = joblib.load(ROOT / "heat-risk.joblib")
SIGNALS = {
    "temperature": ("°C", "Suspend heat-sensitive work and check site temperature."),
    "soil_moisture": (
        "m³/m³",
        "Inspect drainage and verify moisture with a field reading.",
    ),
    "wind": ("km/h", "Secure exposed equipment and review operating limits."),
    "pm25": ("µg/m³", "Review dust controls and check local monitoring."),
    "change": (
        "%",
        "Inspect the imagery and confirm ground conditions before classifying the change.",
    ),
}


def suggested_rule(text):
    """Propose only explicitly directional, unit-bearing thresholds for review."""
    hit = re.search(
        r"(above|below|exceeds?|greater than|less than|over|under)\s+(-?\d+(?:\.\d+)?)\s*(?:degrees?\s*)?(°?\s*[CF]\b|Celsius\b|Fahrenheit\b|km/h|m/s|µg/m³|m³/m³|%)",
        text,
        re.I,
    )
    if not hit:
        return None
    direction, value, unit = hit.groups()
    value = float(value)
    unit = unit.replace("°", "").strip().lower()
    if unit in ("f", "fahrenheit", "c", "celsius"):
        metric = "temperature"
        if unit in ("f", "fahrenheit"):
            value = (value - 32) * 5 / 9
    elif unit in ("km/h", "m/s"):
        metric = "wind"
        if unit == "m/s":
            value *= 3.6
    elif unit == "µg/m³":
        metric = "pm25"
    elif unit == "m³/m³" or unit == "%" and "moisture" in text.lower():
        metric = "soil_moisture"
        if unit == "%":
            value /= 100
    elif unit == "%" and "change" in text.lower():
        metric = "change"
    else:
        return None
    return dict(
        metric=metric,
        op="below" if direction.lower() in ("below", "under", "less than") else "above",
        limit=round(value, 4),
    )


def extract(name, raw):
    if name.endswith(".pdf"):
        pages = [
            (i + 1, p.extract_text() or "")
            for i, p in enumerate(PdfReader(BytesIO(raw)).pages)
        ]
    elif name.endswith(".docx"):
        with ZipFile(BytesIO(raw)) as doc:
            if doc.getinfo("word/document.xml").file_size > 10_000_000:
                raise ValueError("Word document too large")
            root = ET.fromstring(doc.read("word/document.xml"))
        pages = [
            (None, "\n".join("".join(p.itertext()) for p in root.findall(".//{*}p")))
        ]
    else:
        raise ValueError("Choose a PDF or DOCX file")
    clauses = []
    for page, text in pages:
        for line in re.split(r"(?<=[.!?])\s+|\n\s*\n", text):
            line = " ".join(line.split())
            if line:
                clauses.append(
                    dict(
                        page=page,
                        text=line,
                        category=str(clause_model.predict([line])[0]),
                        suggestion=suggested_rule(line),
                    )
                )
    if not clauses:
        raise ValueError("No readable text found. Upload a text-based document.")
    return clauses


def geometry(obj):
    if obj.get("type") == "Feature":
        obj = obj["geometry"]
    g = shape(obj)
    if g.geom_type not in ("Polygon", "MultiPolygon") or not g.is_valid or g.area <= 0:
        raise ValueError("Provide a valid polygon with a positive area")
    w, s, e, n = g.bounds
    if w < -180 or e > 180 or s < -90 or n > 90 or e - w > 180:
        raise ValueError("Use WGS84 longitude and latitude coordinates")
    return mapping(g)


def import_map(name, raw):
    if name.endswith(".kml"):
        root = ET.fromstring(raw)

        def ring(node):
            return [
                tuple(map(float, p.split(",")[:2]))
                for p in node.find(".//{*}coordinates").text.split()
            ]

        layers = []
        for place in root.findall(".//{*}Placemark"):
            for poly in place.findall(".//{*}Polygon"):
                g = Polygon(
                    ring(poly.find("{*}outerBoundaryIs")),
                    [ring(n) for n in poly.findall("{*}innerBoundaryIs")],
                )
                layers.append(
                    dict(
                        geometry=geometry(mapping(g)),
                        label=place.findtext("{*}name", "Parcel"),
                    )
                )
    else:
        obj = json.loads(raw)
        items = obj["features"] if obj.get("type") == "FeatureCollection" else [obj]
        layers = [
            dict(
                geometry=geometry(o),
                label=(o.get("properties") or {}).get("name", "Parcel"),
            )
            for o in items
        ]
    if not layers:
        raise ValueError("No polygons found")
    return layers


def buffer_zone(obj, meters):
    from pyproj import CRS, Transformer

    g = shape(obj)
    c = g.centroid
    local = CRS.from_proj4(
        f"+proj=aeqd +lat_0={c.y} +lon_0={c.x} +datum=WGS84 +units=m"
    )
    to_m = Transformer.from_crs(4326, local, always_xy=True).transform
    to_deg = Transformer.from_crs(local, 4326, always_xy=True).transform
    return mapping(
        transform(
            transform(g, to_m, interleaved=False).buffer(meters),
            to_deg,
            interleaved=False,
        )
    )


def image_change(before, after):
    def load(raw):
        im = Image.open(BytesIO(raw))
        if im.width * im.height > 8_000_000:
            raise ValueError("Use images under 8 megapixels")
        return ImageOps.exif_transpose(im).convert("RGB")

    a, b = load(before), load(after)
    if a.size != b.size:
        raise ValueError("Use two aligned images of the same extent and dimensions")
    delta = np.abs(np.asarray(a, dtype=float) - np.asarray(b, dtype=float)).mean(axis=2)
    mask = delta > 35
    out = np.asarray(b).copy()
    out[mask] = (0.45 * out[mask] + 0.55 * np.array([255, 91, 55])).astype("uint8")
    result = BytesIO()
    im = Image.fromarray(out)
    im.thumbnail((640, 640))
    im.save(result, format="JPEG")
    return round(float(mask.mean() * 100), 2), result.getvalue()


def heat_risk(parcel, obs):
    rule = min(
        (
            r
            for r in parcel["rules"]
            if r["metric"] == "temperature" and r["op"] == "above"
        ),
        key=lambda rule: rule["limit"],
        default=None,
    )
    if not rule or "temperature" not in obs["values"] or "prior_samples" not in obs:
        return None
    pt = shape(parcel["geometry"]).centroid
    when = datetime.fromisoformat(obs["time"]).astimezone(timezone.utc)
    x = features(
        obs["values"]["temperature"],
        rule["limit"],
        pt.y,
        pt.x,
        when,
        sum(v > rule["limit"] for v in obs["prior_samples"]),
    )
    return round(float(risk_model.predict_proba([x])[0, 1]), 4)
