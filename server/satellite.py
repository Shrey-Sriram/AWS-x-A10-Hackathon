from datetime import timedelta

import joblib
import requests

from ml.imagery import ROOT, changed, frame_for, image_features

seasonal_model = joblib.load(ROOT / "imagery-risk.joblib")
from shapely.geometry import shape

from .domain import image_change

GIBS = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"


def compare(geom, latest):
    west, south, east, north = shape(geom).bounds
    # Match both acquisitions to the same map extent. Include at least a 2 km context.
    dx = max(0, (0.02 - (east - west)) / 2)
    dy = max(0, (0.02 - (north - south)) / 2)
    bbox = [
        max(-180, west - dx),
        max(-90, south - dy),
        min(180, east + dx),
        min(90, north + dy),
    ]
    dates = [latest - timedelta(days=7), latest]
    frames = []
    for date in dates:
        r = requests.get(
            GIBS,
            params=dict(
                SERVICE="WMS",
                REQUEST="GetMap",
                VERSION="1.1.1",
                LAYERS="MODIS_Terra_CorrectedReflectance_TrueColor",
                STYLES="",
                FORMAT="image/jpeg",
                SRS="EPSG:4326",
                BBOX=",".join(map(str, bbox)),
                WIDTH=384,
                HEIGHT=384,
                TIME=date.isoformat(),
            ),
            timeout=25,
        )
        r.raise_for_status()
        if not r.headers.get("Content-Type", "").startswith("image/"):
            raise ValueError("Satellite provider returned no image")
        frames.append(r.content)
    change, image = image_change(*frames)
    metadata = dict(
        provider="NASA GIBS Terra MODIS",
        bounds=bbox,
        dates=[d.isoformat() for d in dates],
    )
    point = shape(geom).centroid
    try:
        previous, current = (frame_for(point.x, point.y, d) for d in dates)
        if min(previous.max(), current.max()) > 10:
            vector = image_features(
                current, changed(previous, current), point.x, point.y, latest
            )
            metadata["regional_change_risk_7d"] = round(
                float(seasonal_model.predict_proba([vector])[0, 1]), 4
            )
    except (requests.RequestException, OSError):
        metadata["forecast_notice"] = "Regional satellite frame unavailable"
    return change, image, metadata
