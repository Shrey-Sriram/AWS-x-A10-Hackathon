"""Read Ada County assessor boundaries from the highway district's public map."""
import requests
from fastapi import HTTPException
from .domain import geometry

LAYER = "https://gis.achdidaho.org/server/rest/services/OpenGov/OG_parcelbase2/MapServer/2"


def lookup(*, point=None, number=None):
    params = dict(f="geojson", outSR=4326, outFields="PARCEL", returnGeometry="true",
                  resultRecordCount=2)
    if point is not None:
        params.update(geometry=",".join(map(str, point)), geometryType="esriGeometryPoint",
                      inSR=4326, spatialRel="esriSpatialRelIntersects")
    else:
        escaped = number.replace("'", "''")
        params["where"] = f"PARCEL='{escaped}'"
    try:
        response = requests.get(LAYER + "/query", params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get("error") or not isinstance(data.get("features"), list):
            raise ValueError()
        features = data["features"]
        if not features:
            raise HTTPException(404, "No Ada County parcel found. Try another location or draw the boundary.")
        if len(features) != 1 or data.get("exceededTransferLimit"):
            raise HTTPException(409, "Several parcels match. Select a point inside a single parcel.")
        feature = features[0]
        key = feature["properties"]["PARCEL"]
        if not isinstance(key, str) or not key.strip() or (number is not None and key != number):
            raise ValueError()
        return {"number": key, "geometry": geometry(feature["geometry"])}
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(503, "County boundary service unavailable. Try again later.") from exc
