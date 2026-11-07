import joblib
from ml.soil import ROOT,vector
model=joblib.load(ROOT/"soil-risk.joblib")

def soil_outlook(parcel,temp,soil,rain,point,dt):
    result=[]
    rules=[r for r in parcel["rules"] if r["metric"]=="soil_moisture"]
    for r in rules or [None]:
        limit=r["limit"] if r else .25
        below=r and r["op"]=="below"
        x=vector(temp,soil,rain,point.y,point.x,dt,limit,bool(below))
        prob=model.predict_proba([x])
        if r:
            result.append(dict(label=r["clause"]+" · next 72 hours",risk=round(float(prob[0][0,1]),3)))
        if not result or not any(item["label"].startswith("Heavy rain") for item in result):
            result.append(dict(label="Heavy rain · 7-day erosion inspection screen",risk=round(float(prob[1][0,1]),3)))
    return result
