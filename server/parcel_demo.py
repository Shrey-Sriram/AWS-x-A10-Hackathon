"""Repeatable Willow Bend demos using a saved site-specific USGS elevation grid."""
import json
from pathlib import Path
from typing import Literal
import trimesh
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from .domain import document_pages
from .lease_boundary import read_boundary
from .scene import build

router = APIRouter(prefix="/api/demos/willow-bend")
ROOT = Path(__file__).resolve().parents[1]
EXHIBITS = ROOT / "web/examples/willow-bend"


@router.get("/{mode}/preview")
def demo_preview(mode: Literal["embedded", "map"]):
    pdf = (EXHIBITS / f"{mode}.pdf").read_bytes()
    return read_boundary(document_pages("exhibit.pdf", pdf), allow_missing=True) or {"geometry": None}


@router.get("/{mode}/scene.glb")
def build_demo_scene(mode: Literal["embedded", "map"], selected: bool = False, shed: bool = False):
    boundary = demo_preview(mode)
    if boundary["geometry"]:
        geometry = boundary["geometry"]
    elif selected:
        geometry = json.loads((EXHIBITS / "boundary.geojson").read_text())["geometry"]
    else:
        raise HTTPException(422, "Select Willow Bend Field on the demonstration map.")
    elevations = json.loads((ROOT / "examples/terrain/willow-bend-elevations.json").read_text())
    addition = None
    if shed:
        addition = trimesh.load(ROOT / "examples/text3d/shed-continued.glb", force="mesh")
    model = build({"geometry": geometry}, addition=addition, elevations=elevations)
    return Response(model, media_type="model/gltf-binary", headers={"Cache-Control": "no-store"})
