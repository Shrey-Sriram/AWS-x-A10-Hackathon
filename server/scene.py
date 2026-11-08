"""Build parcel GLBs and place optional SageMaker-generated features."""
import base64
import io
import json
import math
import os
from contextlib import closing
import numpy as np
import trimesh
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException
from shapely import constrained_delaunay_triangles
from shapely.geometry import LineString
from . import sagemaker
from .terrain import terrain_scene, texture_surface, local_boundary

ENDPOINT=os.getenv("PARCELSITE_3D_ENDPOINT","text-to-3d-shape")


def concept_mesh(prompt):
    try:
        with closing(sagemaker.client()) as client:
            response=client.invoke_endpoint(EndpointName=ENDPOINT,ContentType="application/json",Accept="application/json",
                                            Body=json.dumps(dict(prompt=prompt,format="glb",num_steps=32)))
            with closing(response["Body"]) as body:
                raw=base64.b64decode(json.load(body)["mesh_base64"],validate=True)
        mesh=trimesh.load(io.BytesIO(raw),file_type="glb",force="mesh")
        if len(mesh.faces)>1800:
            mesh=mesh.simplify_quadric_decimation(face_count=1800)
        mesh.apply_transform(trimesh.transformations.rotation_matrix(-math.pi/2,[1,0,0]))
        return mesh
    except (BotoCoreError,ClientError) as exc:
        raise HTTPException(503,"The SageMaker 3D endpoint is unavailable. You can still explore the parcel.") from exc
    except (KeyError, ValueError,TypeError,IndexError) as exc:
        raise HTTPException(502,"The 3D endpoint returned an unreadable mesh.") from exc


def surface(region,height=0):
    points=[(x,height,z) for t in constrained_delaunay_triangles(region).geoms for x,z in list(t.exterior.coords)[:3]]
    mesh=trimesh.Trimesh(points,np.arange(len(points)).reshape(-1,3))
    reverse=mesh.face_normals[:,1]<0
    mesh.faces[reverse]=mesh.faces[reverse,::-1]
    return mesh


def footprint(local,aerial):
    scene=trimesh.Scene()
    mesh=surface(local)
    span=max(mesh.extents)
    slab=trimesh.creation.extrude_triangulation(mesh.vertices[:,[0,2]],mesh.faces[:,::-1],span*.065)
    slab.apply_transform(trimesh.transformations.rotation_matrix(-math.pi/2,[1,0,0]))
    slab.apply_transform(trimesh.transformations.reflection_matrix([0,0,0],[0,0,1]))
    slab.apply_translation([0,-span*.065,0])
    sides=slab.face_normals[:,1]<.8
    slab.visual.face_colors=[125,149,104,255]
    slab.visual.face_colors[sides]=[114,85,60,255]
    rim=surface(local.difference(local.buffer(-span*.007)),span*.0005)
    rim.visual.face_colors=[229,216,152,255]
    scene.add_geometry(rim,geom_name="Lease boundary")
    if aerial:
        texture_surface(mesh,local.bounds,aerial)
        slab.update_faces(sides)
        scene.add_geometry(mesh,geom_name="Site imagery")
    else:
        west,north,east,south=local.bounds
        spacing=10**math.floor(math.log10(span/4))
        if span/spacing>14:
            spacing*=5
        lines=[LineString([(x,north),(x,south)]) for x in np.arange(math.ceil(west/spacing)*spacing,east,spacing)]
        lines.extend(LineString([(west,z),(east,z)]) for z in np.arange(math.ceil(north/spacing)*spacing,south,spacing))
        for i,line in enumerate(lines):
            strip=local.intersection(line.buffer(span*.0008,cap_style="flat"))
            if strip.area:
                grid=surface(strip,span*.00025)
                grid.visual.face_colors=[156,175,137,255]
                scene.add_geometry(grid,geom_name=f"Ground grid {i}")
    scene.add_geometry(slab,geom_name="Lease footprint")
    return scene


def build(parcel,addition=None,aerial=None,elevations=None):
    local = local_boundary(parcel["geometry"])
    if elevations is not None:
        scene,ground_level=terrain_scene(local,elevations,aerial)
    else:
        scene,ground_level=footprint(local,aerial),0
    if addition is not None:
        anchor=local.representative_point()
        addition.apply_translation([-addition.bounds.mean(axis=0)[0],-addition.bounds[0,1],-addition.bounds.mean(axis=0)[2]])
        addition.apply_scale(.85*anchor.distance(local.boundary)/max(addition.extents))
        addition.apply_translation([anchor.x,ground_level,anchor.y])
        scene.add_geometry(addition,geom_name="Proposed feature")
    return scene.export(file_type="glb")
