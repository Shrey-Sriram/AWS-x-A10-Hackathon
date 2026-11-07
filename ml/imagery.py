"""Seasonal model for weekly regional RGB change, using NASA MODIS observations."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path

import joblib
import numpy as np
import requests
from PIL import Image
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import average_precision_score, brier_score_loss

ROOT = Path(__file__).resolve().parent
GIBS = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"
SITES = [(-99.0, 35.0), (-101.5, 32.5), (-97.5, 38.5), (-119.5, 36.5)]
START = date(2022, 1, 1)


def image_features(frame, previous_change, lon, lat, day):
    rgb = frame.reshape(-1, 3) / 255
    angle = 2 * np.pi * day.timetuple().tm_yday / 365.25
    return [
        *rgb.mean(axis=0),
        *rgb.std(axis=0),
        previous_change,
        lon,
        lat,
        np.sin(angle),
        np.cos(angle),
    ]


def frame_for(lon, lat, day):
    # A fixed extent gives every historical pair the same field of view.
    res = requests.get(
        GIBS,
        params=dict(
            SERVICE="WMS",
            REQUEST="GetMap",
            VERSION="1.1.1",
            LAYERS="MODIS_Terra_CorrectedReflectance_TrueColor",
            STYLES="",
            FORMAT="image/jpeg",
            SRS="EPSG:4326",
            BBOX=f"{lon - 0.05},{lat - 0.05},{lon + 0.05},{lat + 0.05}",
            WIDTH=64,
            HEIGHT=64,
            TIME=day.isoformat(),
        ),
        timeout=35,
    )
    res.raise_for_status()
    return np.array(Image.open(BytesIO(res.content)).convert("RGB"), dtype=float)


def changed(a, b):
    return float(
        (np.abs(a.astype(float) - b.astype(float)).mean(axis=2) > 35).mean() * 100
    )


def train():
    rows, labels, train_mask = [], [], []
    days = [START + timedelta(weeks=i) for i in range(126)]
    cache = ROOT.parent / "data" / "training-imagery"
    cache.mkdir(parents=True, exist_ok=True)
    for lon, lat in SITES:

        def read(day):
            path = cache / f"{lon}_{lat}_{day}.npy"
            if path.exists():
                return np.load(path)
            try:
                frame = frame_for(lon, lat, day)
            except (OSError, requests.RequestException):
                return None
            np.save(path, frame.astype("uint8"))
            return frame

        with ThreadPoolExecutor(max_workers=4) as pool:
            frames = list(pool.map(read, days))
        for i in range(1, len(days) - 1):
            if i == 95 or any(
                frame is None or frame.max() < 10 for frame in frames[i - 1 : i + 2]
            ):
                continue
            rows.append(
                image_features(
                    frames[i], changed(frames[i - 1], frames[i]), lon, lat, days[i]
                )
            )
            labels.append(changed(frames[i], frames[i + 1]) >= 25)
            train_mask.append(i < 95)
    x, y, m = np.array(rows), np.array(labels), np.array(train_mask)
    model = RandomForestClassifier(
        n_estimators=64, min_samples_leaf=8, max_depth=6, random_state=73, n_jobs=-1
    )
    model.fit(x[m], y[m])
    pr = model.predict_proba(x[~m])[:, 1]
    metrics = dict(
        source=GIBS,
        layer="MODIS_Terra_CorrectedReflectance_TrueColor",
        sites=SITES,
        start=START.isoformat(),
        end=days[-1].isoformat(),
        test_start=days[96].isoformat(),
        train_rows=int(m.sum()),
        test_rows=int((~m).sum()),
        positive_rate=float(y[~m].mean()),
        average_precision=float(average_precision_score(y[~m], pr)),
        brier=float(brier_score_loss(y[~m], pr)),
    )
    joblib.dump(model, ROOT / "imagery-risk.joblib", compress=3)
    (ROOT / "imagery-evaluation.json").write_text(json.dumps(metrics, indent=2) + "\n")
    print(metrics)


if __name__ == "__main__":
    train()
