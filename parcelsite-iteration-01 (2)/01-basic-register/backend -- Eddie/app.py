"""Local parcel register. Run this file from any directory."""
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
app = FastAPI(title="parcelsite register")


class Parcel(BaseModel):
    name: str = Field(min_length=1, max_length=120, pattern=r"\S")
    acres: float = Field(gt=0, allow_inf_nan=False)


def connect():
    path = Path(os.getenv("PARCELSITE_REGISTER_DB", ROOT / "data" / "register.sqlite"))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS parcels(id INTEGER PRIMARY KEY, name TEXT, acres REAL)")
    return db


@app.get("/api/parcels")
def list_parcels():
    with closing(connect()) as db:
        return [dict(id=id, name=name, acres=acres)
                for id, name, acres in db.execute("SELECT id,name,acres FROM parcels ORDER BY id")]


@app.post("/api/parcels", status_code=201)
def add_parcel(parcel: Parcel):
    with closing(connect()) as db, db:
        row = db.execute("INSERT INTO parcels(name, acres) VALUES (?, ?)", (parcel.name.strip(), parcel.acres))
        return {"id": row.lastrowid, "name": parcel.name.strip(), "acres": parcel.acres}


app.mount("/", StaticFiles(directory=ROOT / "frontend -- Arnav", html=True))
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8080)
