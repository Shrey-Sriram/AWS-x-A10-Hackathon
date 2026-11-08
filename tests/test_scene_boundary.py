import io
import unittest

import trimesh
from pyproj import Geod
from shapely import union_all
from shapely.geometry import MultiPolygon, Polygon, box, mapping, shape

from server.scene import build


def footprint_error(geometry, samples=None):
    """Compare the exported ground triangles with independent geodesic offsets."""
    region = shape(geometry)
    center = region.centroid
    geod = Geod(ellps="WGS84")
    def ring(coords):
        out = []
        for lon, lat in coords:
            x = geod.inv(center.x, center.y, lon, center.y)[2]
            z = geod.inv(center.x, center.y, center.x, lat)[2]
            out.append((x if lon >= center.x else -x, z if lat <= center.y else -z))
        return out
    parts = region.geoms if region.geom_type == "MultiPolygon" else [region]
    expected = union_all([Polygon(ring(p.exterior.coords), [ring(i.coords) for i in p.interiors]) for p in parts])
    raw = build({"geometry": geometry}, elevations=samples)
    scene = trimesh.load_scene(io.BytesIO(raw), file_type="glb")
    ground = scene.geometry["USGS 3DEP terrain" if samples is not None else "Lease footprint"]
    triangles = ground.triangles[ground.face_normals[:, 1] > 0.01]
    actual = union_all([Polygon(t[:, [0, 2]]) for t in triangles])
    return expected.boundary.hausdorff_distance(actual.boundary), expected.symmetric_difference(actual).area / expected.area


class ParcelFootprintTest(unittest.TestCase):
    def test_holes_and_separate_parcels(self):
        outer = box(-116.66, 43.62, -116.658, 43.623)
        hole = box(-116.6596, 43.621, -116.659, 43.622)
        geometry = mapping(MultiPolygon([outer.difference(hole), box(-116.655, 43.62, -116.654, 43.621)]))
        for samples in (None, [[10, 11], [14, 15]]):
            with self.subTest(measured=samples is not None):
                distance, fraction = footprint_error(geometry, samples)
                self.assertLess(distance, 0.01)
                self.assertLess(fraction, 0.0001)
