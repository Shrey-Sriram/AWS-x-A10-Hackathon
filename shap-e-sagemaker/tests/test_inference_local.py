"""Local tests for the parts of inference.py that don't need a GPU or Shap-E.

These cover request parsing (input_fn), response serialization (output_fn),
and mesh serialization (_serialize_mesh) for the glb/obj/ply formats.

model_fn and predict_fn are NOT tested here: they require CUDA + the Shap-E
weights, which only exist inside the deployed SageMaker container.

Run:
    pip install pytest trimesh numpy
    pytest tests/ -v

If pytest isn't installed, this file also runs standalone:
    python tests/test_inference_local.py
"""

import importlib.util
import json
import os
import sys
import types

# inference.py imports torch at module level, but the functions we test here
# (input_fn, output_fn, _serialize_mesh) never call into it. Inject a lightweight
# stub so the module imports without a real torch/CUDA install. If torch IS
# installed locally, we leave it alone.
if "torch" not in sys.modules and importlib.util.find_spec("torch") is None:
    _torch_stub = types.ModuleType("torch")
    _torch_stub.cuda = types.SimpleNamespace(is_available=lambda: False)
    _torch_stub.device = lambda name: types.SimpleNamespace(type=name)
    _torch_stub.manual_seed = lambda seed: None
    sys.modules["torch"] = _torch_stub

# Load model/code/inference.py directly by path (it isn't an installed package).
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INFERENCE_PATH = os.path.join(REPO_ROOT, "model", "code", "inference.py")
spec = importlib.util.spec_from_file_location("inference", INFERENCE_PATH)
inference = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inference)


# --------------------------------------------------------------------------
# input_fn
# --------------------------------------------------------------------------

def test_input_fn_parses_valid_json():
    body = json.dumps({"prompt": "a chair", "format": "obj"})
    data = inference.input_fn(body, "application/json")
    assert data["prompt"] == "a chair"
    assert data["format"] == "obj"


def test_input_fn_accepts_bytes_body():
    body = json.dumps({"prompt": "a lamp"}).encode("utf-8")
    data = inference.input_fn(body, "application/json")
    assert data["prompt"] == "a lamp"
    assert data["format"] == "glb"  # default


def test_input_fn_lowercases_format():
    data = inference.input_fn(json.dumps({"prompt": "x", "format": "GLB"}),
                              "application/json")
    assert data["format"] == "glb"


def test_input_fn_rejects_missing_prompt():
    try:
        inference.input_fn(json.dumps({"format": "glb"}), "application/json")
    except ValueError as e:
        assert "prompt" in str(e)
    else:
        raise AssertionError("expected ValueError for missing prompt")


def test_input_fn_rejects_bad_format():
    try:
        inference.input_fn(json.dumps({"prompt": "x", "format": "stl"}),
                           "application/json")
    except ValueError as e:
        assert "stl" in str(e)
    else:
        raise AssertionError("expected ValueError for unsupported format")


def test_input_fn_rejects_wrong_content_type():
    try:
        inference.input_fn("prompt=x", "text/plain")
    except ValueError as e:
        assert "content type" in str(e).lower()
    else:
        raise AssertionError("expected ValueError for bad content type")


# --------------------------------------------------------------------------
# output_fn
# --------------------------------------------------------------------------

def test_output_fn_returns_json():
    payload = {"format": "glb", "prompt": "x", "mesh_base64": "abc"}
    body, content_type = inference.output_fn(payload, "application/json")
    assert content_type == "application/json"
    assert json.loads(body) == payload


# --------------------------------------------------------------------------
# _serialize_mesh
# --------------------------------------------------------------------------

class _FakeTriMesh:
    """Mimics the bits of Shap-E's TriMesh that _serialize_mesh touches."""

    def __init__(self):
        # A single triangle.
        self.verts = [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
        self.faces = [[0, 1, 2]]

    def write_ply(self, buf):
        buf.write(b"ply\nformat ascii 1.0\nelement vertex 3\n")

    def write_obj(self, buf):
        buf.write("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")


def test_serialize_mesh_ply():
    out = inference._serialize_mesh(_FakeTriMesh(), "ply")
    assert isinstance(out, bytes)
    assert out.startswith(b"ply")


def test_serialize_mesh_obj():
    out = inference._serialize_mesh(_FakeTriMesh(), "obj")
    assert isinstance(out, bytes)
    assert b"v 0 0 0" in out


def test_serialize_mesh_glb():
    # Needs trimesh + numpy installed locally.
    out = inference._serialize_mesh(_FakeTriMesh(), "glb")
    assert isinstance(out, (bytes, bytearray))
    # GLB files start with the ASCII magic "glTF".
    assert out[:4] == b"glTF"


# --------------------------------------------------------------------------
# Standalone runner (so you don't strictly need pytest)
# --------------------------------------------------------------------------

def _run_standalone():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            print(f"FAIL {t.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_standalone())
