# Testing

There are three levels, cheapest first. Only level 1 runs without AWS.

## Level 1 — Local logic tests (no AWS, no GPU, free)

Tests request parsing (`input_fn`), response serialization (`output_fn`), and
mesh serialization to glb/obj/ply (`_serialize_mesh`). It stubs out `torch` and
fakes Shap-E's mesh object, so nothing GPU-related is needed.

```bash
python -m pip install trimesh numpy
python tests/test_inference_local.py        # standalone runner, no pytest needed
# or, if you have pytest:
python -m pip install pytest
pytest tests/ -v
```

Expected: `10 passed, 0 failed`.

What this does NOT cover: `model_fn` and `predict_fn` (the actual Shap-E
generation). Those need CUDA and the model weights, which only exist in the
deployed container.

## Level 2 — Deploy and smoke-test a real endpoint (AWS, costs money)

This is the only way to test real generation. It needs AWS credentials, a
SageMaker execution role, and GPU quota.

```bash
pip install -r requirements.txt
cp .env.example .env            # set SAGEMAKER_ROLE_ARN, AWS_REGION, INSTANCE_TYPE
python deploy/deploy_endpoint.py
```

First deploy takes several minutes (container start + weight download). Once it
reports `InService`:

```bash
python client/generate.py --prompt "a red toy car" --out car.glb --num-steps 32
```

A smaller `--num-steps` (e.g. 32) makes the smoke test faster. Verify:

1. The command prints `Saved N bytes to car.glb` with N in the tens-to-hundreds
   of KB range (an empty/0-byte file means generation failed).
2. Open `car.glb` in a viewer to confirm it's a real mesh:
   - Windows: the built-in **3D Viewer** app, or drag it onto https://gltf-viewer.donmccurdy.com
   - Blender: File > Import > glTF 2.0
   - VS Code: the "glTF Tools" extension

### Tear down (important)

A real-time GPU endpoint bills per hour whether idle or not.

```bash
python deploy/delete_endpoint.py
```

Then confirm in the SageMaker console that no endpoint is `InService`.

## Level 3 — Debugging a deployed endpoint

If invocation fails or returns errors, check CloudWatch logs:

- Log group: `/aws/sagemaker/Endpoints/<ENDPOINT_NAME>`
- Common issues:
  - **Timeout on first call** — weight download + cold start exceeded the
    client read timeout. Retry; the second call is fast. The deploy script
    already raises the server-side timeout to 600s.
  - **CUDA out of memory** — use a larger instance (`ml.g5.xlarge`) or lower
    `num_steps`.
  - **ModelError / ImportError for shap_e** — the container failed to install
    `model/code/requirements.txt`; check the log for the pip failure.

## Quick check of the client payload without a live endpoint

You can confirm the client builds the request correctly by pointing it at a
non-existent endpoint — it will fail at the AWS call, not before, which proves
arg parsing and payload construction work:

```bash
python client/generate.py --prompt "test" --out x.glb --endpoint does-not-exist
# expect: "Endpoint invocation failed: ..." (an AWS error, not a Python traceback)
```
