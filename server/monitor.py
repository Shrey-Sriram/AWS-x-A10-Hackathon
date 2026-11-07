"""Refresh all registered parcels; one sweep runs immediately and then every 15 minutes."""
import logging
import threading
from datetime import timedelta
from . import app as api

log=logging.getLogger("leasesense.monitor")

def loop(stop:threading.Event):
    while not stop.is_set():
        with api.database() as db:
            parcels=db.execute("SELECT id,email FROM parcels").fetchall()
        for pid,email in parcels:
            if stop.is_set():
                return
            try:
                p=api.refresh(pid,email)
                latest=(api.now().date()-timedelta(days=2)).isoformat()
                if latest not in p.get("satellite",{}).get("dates",[]):
                    api.satellite(pid,email)
            except Exception as exc:
                log.warning("Parcel %s monitoring update failed: %s",pid,exc)
        stop.wait(900)
