import json
import logging
import os
import boto3
from server.terrain_job import measure

s3 = boto3.client("s3")
bucket = os.environ["TERRAIN_BUCKET"]


def put(key, data, mime):
    s3.put_object(Bucket=bucket, Key=key, Body=data, ContentType=mime)


def handler(event, context):
    for record in event["Records"]:
        job = json.loads(record["body"])
        prefix = job["prefix"]
        try:
            samples, model, info = measure(job["geometry"])
            put(prefix + "/grid.json", json.dumps(samples).encode(), "application/json")
            put(prefix + "/scene.glb", model, "model/gltf-binary")
            status = {"state": "ready", **info}
        except Exception:
            logging.exception("Elevation processing failed")
            if int(record["attributes"]["ApproximateReceiveCount"]) < 2:
                raise
            status = {"state": "failed", "message": "Could not build terrain from USGS coverage. Try again."}
        put(prefix + "/status.json", json.dumps(status).encode(), "application/json")
