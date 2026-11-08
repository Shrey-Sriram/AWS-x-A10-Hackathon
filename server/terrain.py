"""Construct a clipped surface from north-up elevation samples in meters."""
import io
import math
from shapely.affinity import affine_transform
import numpy as np
import trimesh
from PIL import Image
from scipy.ndimage import map_coordinates
from shapely import constrained_delaunay_triangles
from shapely.geometry import box, shape


def texture_surface(mesh, bounds, aerial):
    west, north, east, south = bounds
    source, raw = aerial
    uv = np.column_stack([(mesh.vertices[:, 0] - west) / (east - west),
                          (south - mesh.vertices[:, 2]) / (south - north)])
    material = trimesh.visual.material.PBRMaterial(name=source, baseColorTexture=Image.open(io.BytesIO(raw)),
                                                    metallicFactor=0, roughnessFactor=1, doubleSided=True)
    mesh.visual = trimesh.visual.TextureVisuals(uv=uv, material=material)


def terrain_scene(region, samples, aerial):
    heights = np.asarray(samples, dtype=float)
    rows, cols = heights.shape
    west, north, east, south = region.bounds
    width, depth = east-west, south-north
    def elevation(x,z):
        coordinates = np.array([(z-north)/depth*rows-.5, (x-west)/width*cols-.5])
        return map_coordinates(heights, coordinates, order=1, mode="nearest")-heights.min()
    xs, zs = np.linspace(west,east,cols+1), np.linspace(north,south,rows+1)
    points = []
    for left,right in zip(xs[:-1],xs[1:]):
        for top,bottom in zip(zs[:-1],zs[1:]):
            clipped=region.intersection(box(left,top,right,bottom))
            if clipped.area:
                for t in constrained_delaunay_triangles(clipped).geoms:
                    points.extend(list(t.exterior.coords)[:3])
    x,z=np.asarray(points).T
    surface=trimesh.Trimesh(np.column_stack([x,elevation(x,z),z]),np.arange(len(x)).reshape(-1,3))
    reverse=surface.face_normals[:,1]<0
    surface.faces[reverse]=surface.faces[reverse,::-1]
    if aerial:
        texture_surface(surface,region.bounds,(aerial[0]+" / USGS 3DEP",aerial[1]))
    else:
        surface.visual.face_colors=[125,149,104,255]
    scene=trimesh.Scene()
    scene.add_geometry(surface,geom_name="USGS 3DEP terrain")
    edges=surface.edges[np.bincount(surface.edges_unique_inverse)[surface.edges_unique_inverse]==1]
    walls=[]
    floor=surface.bounds[0,1]-max(width,depth)*.025
    for a,b in surface.vertices[edges]:
        low_a,low_b=a.copy(),b.copy()
        low_a[1]=low_b[1]=floor
        walls.extend([b,a,low_a,b,low_a,low_b])
    sides=trimesh.Trimesh(walls,np.arange(len(walls)).reshape(-1,3))
    sides.visual.face_colors=[108,86,65,255]
    scene.add_geometry(sides,geom_name="Illustrative cutaway")
    anchor=region.representative_point()
    return scene,float(elevation(np.array([anchor.x]),np.array([anchor.y]))[0])


def local_boundary(geometry):
    region=shape(geometry)
    origin=region.centroid
    # Local east/south metres on WGS84 at the parcel centroid. An affine
    # transform keeps straight GeoJSON edges and the north-up raster aligned.
    phi=math.radians(origin.y)
    e2=6.6943799901413165e-3
    w=math.sqrt(1-e2*math.sin(phi)**2)
    east=math.pi/180*6378137*math.cos(phi)/w
    north=math.pi/180*6378137*(1-e2)/w**3
    return affine_transform(region,[east,0,0,-north,-origin.x*east,origin.y*north])
