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
