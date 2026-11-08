import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from PIL import Image

scratch = tempfile.TemporaryDirectory()
os.environ["LEASESENSE_DB"] = str(Path(scratch.name) / "test.sqlite")
os.environ["LEASESENSE_MONITOR"] = "0"
from fastapi.testclient import TestClient

from server.app import app


class ParcelWorkflow(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.email = f"{self._testMethodName}@example.org"
        self.client.post(
            "/api/signup", json={"email": self.email, "password": "local-test-password"}
        ).raise_for_status()
        geom = {
            "type": "Polygon",
            "coordinates": [[[-99, 35], [-99.1, 35], [-99.1, 35.1], [-99, 35]]],
        }
        response = self.client.post(
            "/api/parcels",
            json={"name": "West tract", "geometry": geom, "buffer_m": 50},
        )
        response.raise_for_status()
        self.path = "/api/parcels/" + response.json()["id"]

    def test_monitoring_and_reports(self):
        doc = BytesIO()
        with ZipFile(doc, "w") as f:
            f.writestr(
                "word/document.xml",
                '<w:document xmlns:w="urn:word"><w:body><w:p><w:r><w:t>No work above 30 C.</w:t></w:r></w:p></w:body></w:document>',
            )
        r = self.client.post(
            self.path + "/leases", files={"file": ("lease.docx", doc.getvalue())}
        )
        r.raise_for_status()
        self.assertEqual(
            r.json()["leases"][0]["clauses"][0]["text"], "No work above 30 C."
        )
        rule = {
            "metric": "temperature",
            "op": "above",
            "limit": 30,
            "clause": "No work above 30 C.",
        }
        self.client.post(self.path + "/rules", json=rule).raise_for_status()
        from server.app import now

        hour = now().strftime("%Y-%m-%dT%H:00")
        from datetime import datetime, timedelta

        stamps = [
            (datetime.fromisoformat(hour) - timedelta(hours=24 - j)).isoformat(
                timespec="minutes"
            )
            for j in range(25)
        ]
        hourly = {
            "time": stamps,
            "temperature_2m": [27] * 25,
            "precipitation": [0] * 25,
            **{
                k: [0.3] * 25
                for k in (
                    "soil_moisture_0_to_1cm",
                    "soil_moisture_1_to_3cm",
                    "soil_moisture_3_to_9cm",
                )
            },
        }
        weather = {
            "hourly": hourly,
            "current": {"time": hour, "temperature_2m": 33, "wind_speed_10m": 7},
        }
        air = {"current": {"time": hour, "pm2_5": 12}}
        with patch("server.app.feed", side_effect=[weather, air, weather, air]):
            for _ in range(2):
                res = self.client.post(self.path + "/refresh")
                res.raise_for_status()
        p = res.json()
        self.assertIsNotNone(p["risk"])
        self.assertEqual(len(p["events"]), 1)
        self.assertEqual(p["status"], "Flagged")
        csv = self.client.get(self.path + "/reports/violations").text
        self.assertIn("No work above 30 C.", csv)
        self.assertEqual(
            len(self.client.get(self.path + "/reports/weekly").json()["events"]), 1
        )
        self.client.post(
            self.path + "/events/" + p["events"][0]["id"] + "/resolve",
            json={"text": "Crew stood down."},
        ).raise_for_status()
        other = TestClient(app)
        other.post(
            "/api/signup",
            json={"email": "other@example.org", "password": "local-test-password"},
        ).raise_for_status()
        self.assertEqual(other.get(self.path + "/reports/weekly").status_code, 404)
        self.assertEqual(other.get("/api/parcels").json(), [])

    def test_images_and_map(self):
        kml = b"<kml><Placemark><name>Wetland</name><Polygon><outerBoundaryIs><LinearRing><coordinates>-99,35 -99.1,35 -99.1,35.1 -99,35</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></kml>"
        self.assertEqual(
            self.client.post(
                "/api/map/import", files={"file": ("map.kml", kml)}
            ).json()[0]["label"],
            "Wetland",
        )
        self.client.post(
            self.path + "/rules",
            json={
                "metric": "change",
                "op": "above",
                "limit": 10,
                "clause": "Review changed cover",
            },
        ).raise_for_status()
        images = []
        for color in ("black", "white"):
            buf = BytesIO()
            Image.new("RGB", (20, 20), color).save(buf, "PNG")
            images.append(("scene.png", buf.getvalue()))
        r = self.client.post(
            self.path + "/images", files=dict(zip(("before", "after"), images))
        )
        self.assertEqual(r.json()["events"][0]["value"], 100)
        self.assertTrue(r.json()["events"][0]["image"].startswith("data:image/jpeg"))
        self.assertEqual(
            len(self.client.get(self.path + "/reports/geojson").json()["features"]), 2
        )

    def test_review_and_report_formats(self):
        from server.domain import suggested_rule

        self.assertEqual(
            suggested_rule("No work above 95 °F."),
            {"metric": "temperature", "op": "above", "limit": 35.0},
        )
        self.assertEqual(
            suggested_rule("Stop if soil moisture is below 12%.")["limit"], 0.12
        )
        self.assertIsNone(suggested_rule("The lease renews in thirty days."))
        res = self.client.get(self.path + "/reports/heatmap")
        res.raise_for_status()
        self.assertEqual(Image.open(BytesIO(res.content)).size, (900, 640))
        self.assertIsNone(
            self.client.get(self.path + "/reports/scorecard").json()[
                "percent_within_limit"
            ]
        )

    def test_sagemaker_image_and_access(self):
        import base64
        import json
        from uuid import uuid4

        raw = BytesIO()
        Image.new("RGB", (32, 32), "green").save(raw, "PNG")
        encoded = base64.b64encode(raw.getvalue()).decode()
        payload = dict(
            kind="concept",
            aspect_ratio="1:1",
            prompt="An orchard",
            request_id=str(uuid4()),
        )
        with patch("server.sagemaker.client") as runtime:
            inv = runtime.return_value.invoke_endpoint
            inv.return_value = {
                "Body": BytesIO(json.dumps({"data": [{"b64_json": encoded}]}).encode())
            }
            result = self.client.post(self.path + "/ai", json=payload)
            result.raise_for_status()
            self.client.post(self.path + "/ai", json=payload).raise_for_status()
            self.assertEqual(inv.call_count, 1)
            self.assertEqual(
                inv.call_args.kwargs["CustomAttributes"], "route=/v1/images/generations"
            )
            self.assertEqual(
                json.loads(inv.call_args.kwargs["Body"])["size"], "1024x1024"
            )
        url = self.path + "/ai/" + payload["request_id"] + "/image"
        self.assertEqual(self.client.get(url).content, raw.getvalue())
        self.assertEqual(len(self.client.get(self.path + "/ai").json()), 1)
        other = TestClient(app)
        other.post(
            "/api/signup",
            json={"email": "ai-other@example.org", "password": "local-test-password"},
        ).raise_for_status()
        self.assertEqual(other.get(url).status_code, 404)
        self.assertEqual(other.get(self.path + "/ai").status_code, 404)
        self.assertEqual(other.post(self.path + "/ai", json=payload).status_code, 404)

    def test_sagemaker_denial_and_invalid_response(self):
        from botocore.exceptions import ClientError
        from uuid import uuid4

        with patch("server.sagemaker.client") as runtime:
            invoke = runtime.return_value.invoke_endpoint
            invoke.side_effect = ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "Denied"}},
                "InvokeEndpoint",
            )

            def attempt():
                return self.client.post(
                    self.path + "/ai",
                    json=dict(
                        kind="concept", prompt="A meadow", request_id=str(uuid4())
                    ),
                )

            self.assertEqual(attempt().status_code, 403)
            invoke.side_effect = None
            invoke.return_value = {"Body": BytesIO(b'{"data":[]}')}
            self.assertEqual(attempt().status_code, 502)
            self.assertEqual(self.client.get(self.path + "/ai").json(), [])

    def test_lease_boundary_preview_and_atomic_save(self):
        import json
        from server.lease_boundary import read_boundary

        raw = BytesIO()
        with ZipFile(raw, "w") as f:
            rows = [
                ["Latitude", "Longitude"],
                ["43.642", "-116.645"],
                ["43.642", "-116.640"],
                ["43.645", "-116.640"],
            ]
            table = "".join(
                "<w:tr>"
                + "".join(
                    "<w:tc><w:p><w:r><w:t>" + v + "</w:t></w:r></w:p></w:tc>"
                    for v in row
                )
                + "</w:tr>"
                for row in rows
            )
            f.writestr(
                "word/document.xml",
                '<w:document xmlns:w="urn:word"><w:body><w:p><w:r><w:t>Boundary coordinates</w:t></w:r></w:p><w:tbl>'
                + table
                + "</w:tbl></w:body></w:document>",
            )
        files = {"file": ("exhibit.docx", raw.getvalue())}
        preview = self.client.post("/api/map/lease-preview", files=files)
        preview.raise_for_status()
        g = preview.json()["geometry"]
        self.assertEqual(g["coordinates"][0][0], [-116.645, 43.642])
        body = {"name": "Idaho test", "geometry": g}
        res = self.client.post(
            "/api/parcels/from-lease", data={"parcel": json.dumps(body)}, files=files
        )
        res.raise_for_status()
        self.assertEqual(res.json()["leases"][0]["name"], "exhibit.docx")
        self.assertEqual(
            res.json()["boundary_source"]["method"], "Boundary coordinate table"
        )
        count = len(self.client.get("/api/parcels").json())
        body["geometry"] = {
            "type": "Polygon",
            "coordinates": [[[1, 1], [1, 2], [2, 2], [1, 1]]],
        }
        self.assertEqual(
            self.client.post(
                "/api/parcels/from-lease",
                data={"parcel": json.dumps(body)},
                files=files,
            ).status_code,
            422,
        )
        self.assertEqual(len(self.client.get("/api/parcels").json()), count)
        self.assertEqual(read_boundary([(1, json.dumps(g))])["page"], 1)
        with self.assertRaises(ValueError):
            read_boundary([(1, "123 Demo Lane, APN 100.")])
        with self.assertRaises(ValueError):
            read_boundary([(1, json.dumps(g)), (2, json.dumps(g))])
