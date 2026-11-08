"""Package the inference code and deploy it to a SageMaker real-time endpoint.

What this does:
  1. Reads config from the environment (.env).
  2. Builds a proper model.tar.gz with this layout:
         model.tar.gz
         ├── code/
         │   ├── inference.py       <- our custom model_fn/predict_fn/...
         │   └── requirements.txt   <- Shap-E + trimesh, installed at startup
         (no weights -- Shap-E downloads its own on first load)
     Packaging inference.py under code/ is what makes the SageMaker PyTorch
     inference toolkit call OUR model_fn instead of falling back to its default
     handler (which errors with "Exactly one .pth or .pt file is required").
  3. Uploads the artifact to S3.
  4. Uses the managed SageMaker PyTorch Deep Learning Container as the base
     image (no Docker build needed).
  5. Creates a real-time GPU endpoint.

Run:  python deploy/deploy_endpoint.py
"""

import os
import sys
import tarfile
import tempfile

from dotenv import load_dotenv

import sagemaker
from sagemaker.pytorch import PyTorchModel

# Resolve paths relative to the repo root so the script works from any cwd.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODE_DIR = os.path.join(REPO_ROOT, "model", "code")


def require_env(name):
    value = os.environ.get(name)
    if not value:
        sys.exit(
            f"Missing required env var {name!r}. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


def build_model_tarball(dest_path):
    """Create model.tar.gz containing code/inference.py and code/requirements.txt."""
    with tarfile.open(dest_path, "w:gz") as tar:
        for fname in ("inference.py", "requirements.txt"):
            src = os.path.join(CODE_DIR, fname)
            if not os.path.exists(src):
                sys.exit(f"Expected {src} to exist.")
            tar.add(src, arcname=f"code/{fname}")
    return dest_path


def main():
    load_dotenv(os.path.join(REPO_ROOT, ".env"))

    region = os.environ.get("AWS_REGION", "us-east-1")
    role_arn = require_env("SAGEMAKER_ROLE_ARN")
    endpoint_name = os.environ.get("ENDPOINT_NAME", "text-to-3d-shape")
    instance_type = os.environ.get("INSTANCE_TYPE", "ml.g5.xlarge")
    bucket = os.environ.get("S3_BUCKET") or None

    os.environ["AWS_DEFAULT_REGION"] = region
    session = sagemaker.Session(default_bucket=bucket)
    default_bucket = session.default_bucket()

    print(f"Region          : {region}")
    print(f"Endpoint name   : {endpoint_name}")
    print(f"Instance type   : {instance_type}")
    print(f"Code directory  : {CODE_DIR}")
    print(f"Default bucket  : {default_bucket}")

    # Build + upload the artifact.
    tmpdir = tempfile.mkdtemp(prefix="t2d-")
    tarball = build_model_tarball(os.path.join(tmpdir, "model.tar.gz"))
    print(f"Built artifact  : {tarball}")

    model_data = session.upload_data(
        path=tarball,
        bucket=default_bucket,
        key_prefix="text-to-3d/model",
    )
    print(f"Uploaded to     : {model_data}")

    # entry_point must match the file name inside code/. No source_dir here --
    # the code already lives in the artifact we just built.
    model = PyTorchModel(
        model_data=model_data,
        role=role_arn,
        entry_point="inference.py",
        framework_version="2.1",       # PyTorch DLC version
        py_version="py310",
        sagemaker_session=session,
        env={
            # Give model download + first inference room to breathe.
            "SAGEMAKER_MODEL_SERVER_TIMEOUT": "900",
            "TS_DEFAULT_RESPONSE_TIMEOUT": "900",
        },
    )

    print("\nDeploying endpoint. First deploy takes several minutes while the "
          "container starts and Shap-E weights download...\n")

    predictor = model.deploy(
        initial_instance_count=1,
        instance_type=instance_type,
        endpoint_name=endpoint_name,
    )

    print(f"\nEndpoint '{predictor.endpoint_name}' is InService.")
    print("Generate a model with:")
    print('  python client/generate.py --prompt "a red sports car" --out car.glb')
    print("Delete it when done to stop billing:")
    print("  python deploy/delete_endpoint.py")


# --------------------------------------------------------------------------
# Alternatives to a real-time endpoint (edit the deploy() call above):
#
#  * Asynchronous inference (GPU, good for long jobs, scales to zero):
#       from sagemaker.async_inference import AsyncInferenceConfig
#       model.deploy(initial_instance_count=1, instance_type=instance_type,
#           async_inference_config=AsyncInferenceConfig(
#               output_path=f"s3://{session.default_bucket()}/async-out/"))
# --------------------------------------------------------------------------


if __name__ == "__main__":
    main()
