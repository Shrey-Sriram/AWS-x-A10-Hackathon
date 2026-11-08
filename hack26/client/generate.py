"""CLI client: send a text prompt to the endpoint, save the returned 3D file.

Run:
    python client/generate.py --prompt "a stylized wooden chair" --out chair.glb
    python client/generate.py --prompt "a potted plant" --format obj --out plant.obj
"""

import argparse
import base64
import json
import os
import sys

import boto3
from botocore.config import Config
from dotenv import load_dotenv

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Shap-E generation (especially the first, cold call) easily exceeds boto3's
# default 60s read timeout, which otherwise raises ReadTimeoutError while the
# endpoint is still working. Give it generous headroom.
_BOTO_CONFIG = Config(
    read_timeout=900,       # 15 min: covers cold start + generation
    connect_timeout=60,
    retries={"max_attempts": 1},   # don't silently re-trigger a long generation
)


def parse_args():
    p = argparse.ArgumentParser(description="Generate a 3D model from text via SageMaker.")
    p.add_argument("--prompt", required=True, help="Text description of the 3D object.")
    p.add_argument("--out", required=True, help="Output file path, e.g. chair.glb")
    p.add_argument("--format", choices=["glb", "obj", "ply"], default=None,
                   help="Mesh format. Inferred from --out extension if omitted.")
    p.add_argument("--guidance-scale", type=float, default=15.0,
                   help="Higher = follow the prompt more strictly (default 15).")
    p.add_argument("--num-steps", type=int, default=64,
                   help="Diffusion steps; more = higher quality + slower (default 64).")
    p.add_argument("--seed", type=int, default=None, help="Seed for reproducibility.")
    p.add_argument("--endpoint", default=None,
                   help="Endpoint name (defaults to ENDPOINT_NAME from .env).")
    return p.parse_args()


def main():
    load_dotenv(os.path.join(REPO_ROOT, ".env"))
    args = parse_args()

    region = os.environ.get("AWS_REGION", "us-east-1")
    endpoint_name = args.endpoint or os.environ.get("ENDPOINT_NAME", "text-to-3d-shape")

    fmt = args.format
    if fmt is None:
        ext = os.path.splitext(args.out)[1].lstrip(".").lower()
        fmt = ext if ext in {"glb", "obj", "ply"} else "glb"

    payload = {
        "prompt": args.prompt,
        "format": fmt,
        "guidance_scale": args.guidance_scale,
        "num_steps": args.num_steps,
    }
    if args.seed is not None:
        payload["seed"] = args.seed

    runtime = boto3.client("sagemaker-runtime", region_name=region, config=_BOTO_CONFIG)

    print(f"Invoking '{endpoint_name}' with prompt: {args.prompt!r} (format={fmt})")
    try:
        response = runtime.invoke_endpoint(
            EndpointName=endpoint_name,
            ContentType="application/json",
            Accept="application/json",
            Body=json.dumps(payload),
        )
    except runtime.exceptions.ClientError as exc:
        sys.exit(f"Endpoint invocation failed: {exc}")

    body = json.loads(response["Body"].read().decode("utf-8"))
    mesh_bytes = base64.b64decode(body["mesh_base64"])

    with open(args.out, "wb") as f:
        f.write(mesh_bytes)

    print(f"Saved {len(mesh_bytes):,} bytes to {args.out}")


if __name__ == "__main__":
    main()
