# Text-to-3D on AWS SageMaker

Generate 3D models from text prompts using a pre-existing open-source model
([OpenAI Shap-E](https://github.com/openai/shap-e)) deployed to an Amazon
SageMaker real-time endpoint.

```
text prompt  ─►  SageMaker endpoint (Shap-E)  ─►  3D mesh (.glb / .obj / .ply)
```

## Why Shap-E

Shap-E is an open-source, conditional generative model that produces 3D assets
directly from text. It is Apache-2.0 licensed and runs on a single GPU, which
makes it a good fit for a single SageMaker GPU instance
(`ml.g5.xlarge` / `ml.g4dn.xlarge`).

## Project layout

```
.
├── README.md
├── requirements.txt            # local/client dependencies
├── model/
│   ├── code/
│   │   ├── inference.py         # SageMaker inference handler (model_fn, predict_fn, ...)
│   │   └── requirements.txt     # deps installed inside the serving container
├── deploy/
│   ├── deploy_endpoint.py       # builds model.tar.gz and deploys the endpoint
│   └── delete_endpoint.py       # tears the endpoint down (stop paying for it)
├── client/
│   └── generate.py              # CLI: prompt -> 3D file
└── .env.example                 # configuration template
```

## Prerequisites

1. An AWS account with SageMaker access.
2. A SageMaker **execution role** ARN (the role the endpoint assumes). See
   https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-roles.html
3. Service quota for a GPU inference instance (e.g. `ml.g5.xlarge`). New accounts
   often need to request this via Service Quotas.
4. Python 3.10+, the AWS CLI configured (`aws configure`), and Docker not required
   (we use the managed PyTorch Deep Learning Container).

## Quick start

```bash
# 1. install local tooling
pip install -r requirements.txt

# 2. configure
cp .env.example .env
#   edit .env: set SAGEMAKER_ROLE_ARN, AWS_REGION, S3_BUCKET

# 3. deploy the endpoint (uploads model code to S3 + creates the endpoint)
python deploy/deploy_endpoint.py

# 4. generate a 3D model from text
python client/generate.py --prompt "a stylized wooden chair" --out chair.glb

# 5. when finished, delete the endpoint so you stop being billed
python deploy/delete_endpoint.py
```

## Cost warning

A real-time GPU endpoint bills **per hour while it exists**, whether or not you
send requests. Always run `deploy/delete_endpoint.py` when you are done. For
spiky workloads consider SageMaker Asynchronous Inference or Serverless instead
(notes at the bottom of `deploy/deploy_endpoint.py`).
