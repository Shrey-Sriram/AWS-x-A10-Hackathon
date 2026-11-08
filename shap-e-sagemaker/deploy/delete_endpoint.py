"""Delete the SageMaker endpoint, its config, and the model to stop billing.

Run:  python deploy/delete_endpoint.py
"""

import os

import boto3
from dotenv import load_dotenv

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _safe_delete(label, fn):
    try:
        fn()
        print(f"Deleted {label}.")
    except Exception as exc:  # noqa: BLE001 - best-effort cleanup
        print(f"Skipped {label}: {exc}")


def main():
    load_dotenv(os.path.join(REPO_ROOT, ".env"))
    region = os.environ.get("AWS_REGION", "us-east-1")
    endpoint_name = os.environ.get("ENDPOINT_NAME", "text-to-3d-shape")

    sm = boto3.client("sagemaker", region_name=region)

    _safe_delete(
        f"endpoint '{endpoint_name}'",
        lambda: sm.delete_endpoint(EndpointName=endpoint_name),
    )
    _safe_delete(
        f"endpoint config '{endpoint_name}'",
        lambda: sm.delete_endpoint_config(EndpointConfigName=endpoint_name),
    )
    print("\nDone. Verify in the SageMaker console that nothing is InService.")


if __name__ == "__main__":
    main()
