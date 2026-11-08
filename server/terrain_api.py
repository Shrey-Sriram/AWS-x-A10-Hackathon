"""Authenticated terrain jobs. Cloud results are cached locally for repeat views."""
from contextlib import closing
import json
import os
from datetime import datetime, timezone
from uuid import uuid4

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException



def client(service):
    return boto3.client(service, region_name=os.getenv("PARCELSITE_TERRAIN_REGION", "us-east-1"))


def persist(db, pid, key, info, samples=None, model=None):
    if samples is not None:
        db.execute("INSERT OR REPLACE INTO parcel_elevations VALUES(?,?)", (pid, json.dumps(samples)))
    db.execute("INSERT OR REPLACE INTO terrain_jobs VALUES(?,?,?,?,?)",
               (pid, key, json.dumps(info), model, datetime.now(timezone.utc).isoformat()))
    return info


def status(db, parcel):
    pid = parcel["id"]
    row = db.execute("SELECT job,info,updated FROM terrain_jobs WHERE parcel=?", (pid,)).fetchone()
    if not row:
        legacy = db.execute("SELECT 1 FROM parcel_elevations WHERE parcel=?", (pid,)).fetchone()
        return {"state": "ready", "source": "USGS 3DEP"} if legacy else {"state": "empty"}
    key, raw, updated = row
    info = json.loads(raw)
    if info["state"] != "pending":
        return info
    bucket = os.environ["PARCELSITE_TERRAIN_BUCKET"]
    def read(s3, name):
        body = s3.get_object(Bucket=bucket, Key=key + "/" + name)["Body"]
        try:
            return body.read()
        finally:
            body.close()
    try:
        with closing(client("s3")) as s3:
            result = json.loads(read(s3, "status.json"))
            if result["state"] == "ready":
                return persist(db, pid, key, result, json.loads(read(s3, "grid.json")), read(s3, "scene.glb"))
            return persist(db, pid, key, result)
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in {"NoSuchKey", "404"}:
            raise HTTPException(503, "Could not read the terrain job from AWS.") from exc
    except BotoCoreError as exc:
        raise HTTPException(503, "Could not reach AWS for terrain status.") from exc
    if (datetime.now(timezone.utc) - datetime.fromisoformat(updated)).total_seconds() > 1200:
        return persist(db, pid, key, {"state": "failed", "message": "Terrain job timed out. Try again."})
    return info


def start(db, parcel):
    pid = parcel["id"]
    current = status(db, parcel)
    if current["state"] in ("ready", "pending"):
        return current
    queue = os.getenv("PARCELSITE_TERRAIN_QUEUE")
    key = "terrain/" + pid + "/" + uuid4().hex
    if not queue:
        raise HTTPException(503, "The terrain worker is not configured.")
    if not os.getenv("PARCELSITE_TERRAIN_BUCKET"):
        raise HTTPException(503, "The terrain storage bucket is not configured.")
    try:
        with closing(client("sqs")) as sqs:
            sqs.send_message(QueueUrl=queue, MessageBody=json.dumps({"prefix": key, "geometry": parcel["geometry"]}))
    except (BotoCoreError, ClientError) as exc:
        raise HTTPException(503, "Could not queue the terrain job on AWS.") from exc
    return persist(db, pid, key, {"state": "pending"})
