"""Read explicit parcel polygons and labeled decimal-degree boundary tables."""

import json
import re

from shapely.geometry import Polygon, mapping
from .domain import geometry

NO_BOUNDARY = (
    "No explicit boundary found. Use a polygon exhibit, import a survey file, "
    "or draw the boundary. An address, parcel ID, acreage or survey bearing alone is insufficient."
)
HEADER = re.compile(
    r"^(?:(?:point|vertex|corner)\s*[,|\s]+)?(longitude|latitude)\s*[,|\s]+(longitude|latitude)$",
    re.I,
)
NUMBER = r"[+-]?\d+(?:\.\d+)?"
ROW = re.compile(
    rf"^(?:(?:[A-Za-z][\w-]*|\d+)\s*[,|\s]+)?({NUMBER})\s*[,|\s]+({NUMBER})$"
)


def table_candidates(text):
    lines = text.splitlines()
    for index, line in enumerate(lines):
        header = HEADER.fullmatch(line.strip())
        if not header or header[1].lower() == header[2].lower():
            continue
        context = " ".join(lines[max(0, index - 3) : index]).lower()
        if not re.search(r"\bboundar(?:y|ies)\b", context):
            continue
        points = []
        for row in lines[index + 1 :]:
            if not row.strip():
                if points:
                    break
                continue
            match = ROW.fullmatch(row.strip())
            if not match:
                if re.search(r"\d", row) and re.match(r"^[\s\w-]*[,|\s]+[+-]?\d", row):
                    raise ValueError("Incomplete coordinate row in the boundary table.")
                break
            a, b = map(float, match.groups())
            points.append((a, b) if header[1].lower() == "longitude" else (b, a))
        if len(set(points)) < 3:
            raise ValueError(
                "The boundary table needs at least three distinct corners."
            )
        yield mapping(Polygon(points)), "Boundary coordinate table", line.strip()


def candidates(text):
    # JSON objects may be embedded in explanatory prose. Consume whole objects
    # so a Feature and its child geometry are never counted twice.
    decoder = json.JSONDecoder()
    pos = 0
    while (start := text.find("{", pos)) != -1:
        try:
            obj, end = decoder.raw_decode(text, start)
        except ValueError:
            pos = start + 1
            continue
        pos = end
        if not isinstance(obj, dict):
            continue
        if "crs" in obj:
            raise ValueError(
                "Provide GeoJSON in WGS84 longitude and latitude without a custom CRS."
            )
        items = (
            obj.get("features", []) if obj.get("type") == "FeatureCollection" else [obj]
        )
        for item in items:
            g = item.get("geometry") if item.get("type") == "Feature" else item
            if isinstance(g, dict) and g.get("type") in {"Polygon", "MultiPolygon"}:
                yield g, "Embedded GeoJSON", text[start:end][:160]
    yield from table_candidates(text)


def read_boundary(pages, allow_missing=False):
    found = []
    for page, text in pages:
        for obj, method, excerpt in candidates(text):
            found.append(
                {
                    "geometry": geometry(obj),
                    "page": page,
                    "method": method,
                    "excerpt": excerpt,
                }
            )
    if not found:
        if allow_missing and any(text.strip() for _, text in pages):
            return None
        if not any(text.strip() for _, text in pages):
            raise ValueError("No readable text. Run OCR on the exhibit and upload it again.")
        raise ValueError(NO_BOUNDARY)
    if len(found) != 1:
        raise ValueError(
            "Multiple boundary candidates found. Supply the exhibit for a single parcel or draw its boundary."
        )
    return found[0]
