"""Fit a 72-hour heat-threshold classifier from a contiguous ERA5 time slice."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
import json
import joblib
import numpy as np
import requests
from numcodecs import Blosc
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

ROOT = Path(__file__).resolve().parent
URL = "https://storage.googleapis.com/weatherbench2/datasets/era5/1959-2023_01_10-6h-64x32_equiangular_conservative.zarr"
START, STOP = 827, 887
ORIGIN = datetime(1959, 1, 1)
FEATURES = ["temperature_c", "threshold_c", "margin_c", "latitude", "longitude", "season_sin", "season_cos", "hour_sin", "hour_cos"]


def features(temp, limit, lat, lon, when):
    season = 2 * np.pi * when.timetuple().tm_yday / 365.25
    hour = 2 * np.pi * when.hour / 24
    return [temp, limit, temp-limit, lat, lon, np.sin(season), np.cos(season), np.sin(hour), np.cos(hour)]


def read(key, dtype):
    response = requests.get(f"{URL}/{key}", timeout=45)
    response.raise_for_status()
    return np.frombuffer(Blosc().decode(response.content), dtype=dtype)


def train():
    latitudes = read("latitude/0", "<f8")
    longitudes = read("longitude/0", "<f8")
    cells = [(i, j) for i, lon in enumerate(longitudes) for j, lat in enumerate(latitudes)
             if 235 <= lon <= 290 and 25 <= lat <= 49]
    def chunk(k):
        grid = read(f"2m_temperature/{k}.0.0", "<f4").reshape(100,64,32)
        return np.stack([grid[:,i,j] for i,j in cells], axis=1) - 273.15
    with ThreadPoolExecutor(max_workers=6) as pool:
        temps = np.concatenate(list(pool.map(chunk, range(START, STOP))))
    rows, labels, split, persistence = [], [], [], []
    for t in range(0, len(temps)-12, 3):
        cutoff = len(temps)-1460
        if cutoff-12 <= t < cutoff:
            continue
        when = ORIGIN + timedelta(hours=6*(100*START+t))
        for col,(i,j) in enumerate(cells):
            limit = [25,30,35][(t//4+col)%3]
            temp = float(temps[t,col])
            rows.append(features(temp,limit,latitudes[j],longitudes[i]-360,when))
            labels.append(max(temps[t+1:t+13,col]) > limit)
            split.append(t < cutoff)
            persistence.append(temp > limit)
    x,y,mask = np.asarray(rows),np.asarray(labels),np.asarray(split)
    model = RandomForestClassifier(n_estimators=80,max_depth=12,min_samples_leaf=12,random_state=73,n_jobs=-1)
    model.fit(x[mask],y[mask])
    p = model.predict_proba(x[~mask])[:,1]
    date = lambda t: (ORIGIN + timedelta(hours=6*(100*START+t))).isoformat()
    metrics = dict(source=URL,variable="2m_temperature",cells=len(cells),start_utc=date(0),holdout_start_utc=date(cutoff),
                   end_utc=date(len(temps)-1),train_rows=int(mask.sum()),test_rows=int((~mask).sum()),
                   positive_rate=float(y[~mask].mean()),roc_auc=float(roc_auc_score(y[~mask],p)),
                   average_precision=float(average_precision_score(y[~mask],p)),brier=float(brier_score_loss(y[~mask],p)),
                   persistence_brier=float(brier_score_loss(y[~mask],np.asarray(persistence)[~mask].astype(float))),
                   features=FEATURES)
    joblib.dump(model, ROOT/"heat-risk.joblib",compress=3)
    (ROOT/"evaluation.json").write_text(json.dumps(metrics,indent=2)+"\n")
    print(metrics)


if __name__ == "__main__":
    train()
