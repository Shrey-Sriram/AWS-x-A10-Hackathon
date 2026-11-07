"""Generate parcel concept PNGs through a real-time FLUX endpoint on SageMaker."""

import base64
import json
import os
import secrets
from contextlib import closing
from io import BytesIO

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
from fastapi import HTTPException
from PIL import Image

REGION = os.getenv("PARCELSITE_SAGEMAKER_REGION", "us-east-1")
ENDPOINT = os.getenv("PARCELSITE_SAGEMAKER_ENDPOINT", "parcelsite-flux-klein-4b")
MODEL = "black-forest-labs/FLUX.2-klein-4B"
SIZES = {"1:1": "1024x1024", "16:9": "1024x576", "9:16": "576x1024"}


def client():
    return boto3.client(
        "sagemaker-runtime",
        region_name=REGION,
        config=Config(
            connect_timeout=6, read_timeout=75, retries={"total_max_attempts": 1}
        ),
    )


def image(prompt, ratio):
    seed = secrets.randbits(31)
    payload = dict(
        model=MODEL, prompt=prompt, size=SIZES[ratio], num_inference_steps=4, seed=seed
    )
    try:
        with closing(client()) as runtime:
            response = runtime.invoke_endpoint(
                EndpointName=ENDPOINT,
                Body=json.dumps(payload),
                ContentType="application/json",
                Accept="application/json",
                CustomAttributes="route=/v1/images/generations",
            )
            with closing(response["Body"]) as stream:
                data = json.load(stream)
        raw = base64.b64decode(data["data"][0]["b64_json"], validate=True)
        with Image.open(BytesIO(raw)) as pic:
            if pic.format != "PNG":
                raise ValueError("Expected PNG")
            pic.verify()
    except NoCredentialsError as exc:
        raise HTTPException(
            503, "Configure AWS credentials for the parcelsite server."
        ) from exc
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in {"AccessDenied", "AccessDeniedException"}:
            raise HTTPException(
                403,
                f"The server needs sagemaker:InvokeEndpoint on {ENDPOINT} in {REGION}.",
            ) from exc
        if code in {"ValidationError", "ModelNotReadyException", "ServiceUnavailable"}:
            raise HTTPException(
                503,
                f"Check that SageMaker endpoint {ENDPOINT} in {REGION} is InService.",
            ) from exc
        raise HTTPException(
            502, f"SageMaker could not generate the image ({code})."
        ) from exc
    except BotoCoreError as exc:
        raise HTTPException(
            503, "The server could not reach SageMaker Runtime."
        ) from exc
    except (KeyError, IndexError, OSError, ValueError, TypeError) as exc:
        raise HTTPException(
            502, "The SageMaker image endpoint returned an invalid PNG response."
        ) from exc
    return {"seed": seed, "endpoint": ENDPOINT}, raw
