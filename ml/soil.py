"""Forecast 72-hour soil threshold crossings and a 7-day heavy-rain inspection proxy."""
from datetime import datetime
import json
import joblib
import numpy as np
import requests
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import average_precision_score, brier_score_loss
from pathlib import Path

ROOT=Path(__file__).resolve().parent
URL="https://archive-api.open-meteo.com/v1/archive"
SITES=[(35,-99),(32,-101),(39,-98),(41,-96),(36,-119),(38,-105)]

def vector(temp,soil,rain,lat,lon,date,limit,below):
    season=2*np.pi*date.timetuple().tm_yday/365.25
    return [temp,soil,rain,lat,lon,np.sin(season),np.cos(season),limit,int(below)]

def train():
    x,y,split=[],[],[]
    for lat,lon in SITES:
        res=requests.get(URL,params=dict(latitude=lat,longitude=lon,start_date="2019-01-01",end_date="2023-12-31",
            hourly="temperature_2m,soil_moisture_0_to_7cm",models="era5_land"),timeout=120)
        res.raise_for_status()
        h=res.json()["hourly"]
        wet_response=requests.get(URL,params=dict(latitude=lat,longitude=lon,start_date="2019-01-01",end_date="2023-12-31",
            hourly="precipitation",models="era5"),timeout=120)
        wet_response.raise_for_status()
        wet_data=wet_response.json()["hourly"]
        if h["time"]!=wet_data["time"]:
            raise ValueError("ERA5 and ERA5-Land time grids differ")
        temp,soil=(np.array(h[key],dtype=float) for key in ("temperature_2m","soil_moisture_0_to_7cm"))
        rain=np.array(wet_data["precipitation"],dtype=float)
        for i in range(24,len(temp)-168,6):
            dt=datetime.fromisoformat(h["time"][i])
            if "2022-12-24"<=h["time"][i]<"2023-01-01":
                continue
            wet=rain[i-23:i+1].sum()
            ahead=soil[i+1:i+73]
            # Seven future 24-hour rain totals. This target screens for inspection.
            if not (np.isfinite(ahead).all() and np.isfinite(rain[i+1:i+169]).all()):
                continue
            heavy=rain[i+1:i+169].reshape(7,24).sum(axis=1).max()>=25
            limit=[.12,.18,.25,.32][(i//6)%4]
            below=limit<=.18
            x.append(vector(temp[i],soil[i],wet,lat,lon,dt,limit,below))
            y.append([ahead.min()<limit if below else ahead.max()>limit,heavy])
            split.append(dt.year<2023)
    x,y,split=np.asarray(x),np.asarray(y),np.asarray(split)
    valid=np.isfinite(x).all(axis=1)
    train_rows=valid&split
    test=valid&~split
    model=RandomForestClassifier(n_estimators=64,max_depth=12,min_samples_leaf=14,n_jobs=-1,random_state=73)
    model.fit(x[train_rows],y[train_rows])
    pred=model.predict_proba(x[test])
    result=dict(source=URL,model="ERA5-Land topsoil and temperature, ERA5 precipitation",sites=SITES,train="2019–2022",holdout="2023",train_rows=int(train_rows.sum()),test_rows=int(test.sum()))
    for j,name in enumerate(["soil_threshold","heavy_rain_screen"]):
        pr=pred[j][:,1]; truth=y[test,j]
        result[name]=dict(positive_rate=float(truth.mean()),average_precision=float(average_precision_score(truth,pr)),brier=float(brier_score_loss(truth,pr)))
    joblib.dump(model,ROOT/"soil-risk.joblib",compress=3)
    (ROOT/"soil-evaluation.json").write_text(json.dumps(result,indent=2)+"\n")
    print(result)

if __name__=="__main__":
    train()
