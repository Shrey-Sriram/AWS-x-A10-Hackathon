"""Continue an existing Parcelsite SageMaker job with its dataset and checkpoint."""
import argparse
import io
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import boto3


def launch(parent_name, region, steps):
    session = boto3.Session(region_name=region)
    training = session.client("sagemaker")
    storage = session.client("s3")
    parent = training.describe_training_job(TrainingJobName=parent_name)
    if parent["TrainingJobStatus"] != "Completed":
        raise ValueError("Select a completed training job.")
    artifact = urlparse(parent["ModelArtifacts"]["S3ModelArtifacts"])
    response = storage.get_object(Bucket=artifact.netloc, Key=artifact.path.lstrip("/"))
    with response["Body"] as body:
        archive = io.BytesIO(body.read())
    job = "parcelsite-shape-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    prefix = "parcelsite-text3d/" + job
    def upload(key, data):
        storage.put_object(Bucket=artifact.netloc, Key=prefix + "/" + key, Body=data)
    with tarfile.open(fileobj=archive) as contents:
        for file in ("metrics.json", "text-projection.pt"):
            upload("parent/" + file, contents.extractfile(file).read())
    for file in ("train.py", "inference.py"):
        upload("runner/" + file, Path(__file__).with_name(file).read_bytes())
    channels = [ch for ch in parent["InputDataConfig"] if ch["ChannelName"] in {"code", "examples"}]
    if len(channels) != 2:
        raise ValueError("Parent must have Shap-E code and captioned example channels.")
    for name in ("runner", "parent"):
        source = dict(S3DataType="S3Prefix", S3Uri=f"s3://{artifact.netloc}/{prefix}/{name}/",
                      S3DataDistributionType="FullyReplicated")
        channels.append(dict(ChannelName=name, DataSource=dict(S3DataSource=source)))
    algorithm = parent["AlgorithmSpecification"].copy()
    algorithm["ContainerEntrypoint"] = ["python", "-u", "/opt/ml/input/data/runner/train.py"]
    result = training.create_training_job(
        TrainingJobName=job, RoleArn=parent["RoleArn"], AlgorithmSpecification=algorithm,
        InputDataConfig=channels, Environment=dict(TRAIN_STEPS=str(steps), GENERATE_COMPARISON="1",
                                                   PYTHONPATH="/opt/ml/input/data/code"),
        OutputDataConfig=dict(S3OutputPath=f"s3://{artifact.netloc}/{prefix}/output"),
        ResourceConfig=dict(InstanceType="ml.g5.xlarge", InstanceCount=1, VolumeSizeInGB=40),
        StoppingCondition=dict(MaxRuntimeInSeconds=1800))
    return result["TrainingJobArn"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("parent")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--steps", type=int, default=384)
    args = parser.parse_args()
    if args.steps <= 0:
        parser.error("steps must be positive")
    print(launch(args.parent, args.region, args.steps))
