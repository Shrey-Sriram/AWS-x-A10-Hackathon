"""Fetch a bounded USGS export and build the same terrain shown by the app."""
import io
import json
import math
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
from PIL import Image
from shapely.geometry import shape

from .terrain import local_boundary, terrain_scene

SERVICE = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage"


def measure(geometry):
    local = local_boundary(geometry)
    west, north, east, south = local.bounds
    width, depth = east - west, south - north
    spacing = max(1, width / 64, depth / 64)
    cols, rows = max(2, math.ceil(width / spacing)), max(2, math.ceil(depth / spacing))
    query = urlencode(dict(bbox=",".join(map(str, shape(geometry).bounds)), bboxSR=4326,
                           imageSR=4326, size=f"{cols},{rows}", adjustAspectRatio="false",
                           pixelType="F32", format="tiff", f="image",
                           renderingRule=json.dumps({"rasterFunction": "None"})))
    req = Request(SERVICE + "?" + query, headers={"User-Agent": "Parcelsite/1.0"})
    with urlopen(req, timeout=45) as response:
        raw = response.read(2_000_000)
    image = Image.open(io.BytesIO(raw))
    heights = np.asarray(image)
    if (heights.shape != (rows, cols) or heights.dtype.kind != "f" or
            not np.isfinite(heights).all() or heights.min() < -500 or heights.max() > 9000):
        raise ValueError("USGS did not return complete elevation coverage for this parcel.")
    scene, _ = terrain_scene(local, heights, None)
    ground = scene.geometry["USGS 3DEP terrain"]
    info = dict(source="USGS 3DEP", grid_spacing_m=round(spacing, 1),
                relief_m=round(float(ground.extents[1]), 2))
    return heights.tolist(), scene.export(file_type="glb"), info
