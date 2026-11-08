"""SageMaker inference handler for text-to-3D generation with Shap-E.

SageMaker's PyTorch serving container calls four hooks:

    model_fn(model_dir)              -> load the model once, at container start
    input_fn(request_body, type)     -> deserialize the incoming request
    predict_fn(data, model)          -> run inference
    output_fn(prediction, accept)    -> serialize the response

The request is JSON:
    {
        "prompt": "a stylized wooden chair",   # required
        "format": "glb",                        # glb | obj | ply   (default glb)
        "guidance_scale": 15.0,                  # optional, default 15.0
        "num_steps": 64,                         # optional, diffusion steps
        "seed": 0                                # optional, for reproducibility
    }

The response is JSON:
    {
        "format": "glb",
        "prompt": "...",
        "mesh_base64": "<base64-encoded mesh bytes>"
    }

The mesh is base64-encoded so it survives the JSON transport. The client decodes
it back to a binary file.
"""

import base64
import io
import json
import logging

import torch

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

SUPPORTED_FORMATS = {"glb", "obj", "ply"}
DEFAULT_FORMAT = "glb"


def model_fn(model_dir):
    """Load Shap-E once when the container starts.

    model_dir is where SageMaker unpacked model.tar.gz. We don't ship weights in
    the artifact -- Shap-E downloads them on first load and caches them. That
    keeps the artifact tiny and avoids license redistribution concerns.
    """
    import os

    # --- Make every Shap-E cache path writable ---------------------------------
    # Shap-E resolves its cache as os.getcwd()/shap_e_model_cache (see
    # shap_e.models.download.default_cache_dir). Under SageMaker the cwd is
    # /opt/ml/model/code, which is a READ-ONLY filesystem, so downloads fail with
    # OSError [Errno 30]. The text300M model also pulls CLIP, which falls back to
    # the SAME default_cache_dir() internally -- so passing cache_dir only to
    # load_model() is not enough. The reliable fix is to chdir into a writable
    # directory so every default_cache_dir() call resolves somewhere writable.
    # We also redirect HOME and other framework caches to /tmp for good measure.
    writable = os.environ.get("SHAP_E_WORKDIR", "/tmp/shap_e")
    os.makedirs(writable, exist_ok=True)
    os.environ.setdefault("HOME", writable)
    os.environ.setdefault("XDG_CACHE_HOME", os.path.join(writable, ".cache"))
    os.environ.setdefault("TORCH_HOME", os.path.join(writable, ".torch"))
    os.chdir(writable)
    logger.info("Changed working dir to writable cache root: %s", writable)
    # ---------------------------------------------------------------------------

    from shap_e.diffusion.gaussian_diffusion import diffusion_from_config
    from shap_e.diffusion.sample import sample_latents
    from shap_e.models.download import load_config, load_model

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info("Loading Shap-E models on device=%s", device)

    xm = load_model("transmitter", device=device)     # latent -> mesh decoder
    text_model = load_model("text300M", device=device)  # text -> latent prior (+CLIP)
    diffusion = diffusion_from_config(load_config("diffusion"))

    logger.info("Shap-E models loaded")
    return {
        "device": device,
        "xm": xm,
        "text_model": text_model,
        "diffusion": diffusion,
        "sample_latents": sample_latents,
    }


def input_fn(request_body, request_content_type):
    """Parse the request body into a dict."""
    if request_content_type == "application/json":
        if isinstance(request_body, (bytes, bytearray)):
            request_body = request_body.decode("utf-8")
        data = json.loads(request_body)
    else:
        raise ValueError(
            f"Unsupported content type: {request_content_type}. Use application/json."
        )

    if not data.get("prompt"):
        raise ValueError("Request must include a non-empty 'prompt' field.")

    fmt = data.get("format", DEFAULT_FORMAT).lower()
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(
            f"Unsupported format '{fmt}'. Choose one of {sorted(SUPPORTED_FORMATS)}."
        )
    data["format"] = fmt
    return data


def _create_pan_cameras(size, device):
    """Minimal camera batch for mesh decoding.

    Inlined from shap_e.util.notebooks to avoid importing that module, which
    pulls in ipywidgets (a Jupyter-only dep not present in the serving
    container). Logic mirrors the upstream Apache-2.0 helper.
    """
    import numpy as np
    from shap_e.models.nn.camera import (
        DifferentiableCameraBatch,
        DifferentiableProjectiveCamera,
    )

    origins, xs, ys, zs = [], [], [], []
    for theta in np.linspace(0, 2 * np.pi, num=20):
        z = np.array([np.sin(theta), np.cos(theta), -0.5])
        z /= np.sqrt(np.sum(z**2))
        origins.append(-z * 4)
        x = np.array([np.cos(theta), -np.sin(theta), 0.0])
        xs.append(x)
        ys.append(np.cross(z, x))
        zs.append(z)
    return DifferentiableCameraBatch(
        shape=(1, len(xs)),
        flat_camera=DifferentiableProjectiveCamera(
            origin=torch.from_numpy(np.stack(origins, axis=0)).float().to(device),
            x=torch.from_numpy(np.stack(xs, axis=0)).float().to(device),
            y=torch.from_numpy(np.stack(ys, axis=0)).float().to(device),
            z=torch.from_numpy(np.stack(zs, axis=0)).float().to(device),
            width=size,
            height=size,
            x_fov=0.7,
            y_fov=0.7,
        ),
    )


def _decode_latent_mesh(xm, latent):
    """Decode a Shap-E latent to a mesh. Inlined from shap_e.util.notebooks."""
    from shap_e.models.transmitter.base import Transmitter
    from shap_e.util.collections import AttrDict

    with torch.no_grad():
        decoded = xm.renderer.render_views(
            AttrDict(cameras=_create_pan_cameras(2, latent.device)),
            params=(xm.encoder if isinstance(xm, Transmitter) else xm).bottleneck_to_params(
                latent[None]
            ),
            options=AttrDict(rendering_mode="stf", render_with_direction=False),
        )
    return decoded.raw_meshes[0]


def predict_fn(data, model):
    """Generate a latent from the prompt, decode it to a mesh, serialize it."""
    device = model["device"]
    prompt = data["prompt"]
    fmt = data["format"]
    guidance_scale = float(data.get("guidance_scale", 15.0))
    num_steps = int(data.get("num_steps", 64))
    seed = data.get("seed")

    if seed is not None:
        torch.manual_seed(int(seed))

    logger.info("Generating latent for prompt=%r (steps=%d)", prompt, num_steps)
    latents = model["sample_latents"](
        batch_size=1,
        model=model["text_model"],
        diffusion=model["diffusion"],
        guidance_scale=guidance_scale,
        model_kwargs=dict(texts=[prompt]),
        progress=True,
        clip_denoised=True,
        use_fp16=device.type == "cuda",
        use_karras=True,
        karras_steps=num_steps,
        sigma_min=1e-3,
        sigma_max=160,
        s_churn=0,
    )

    logger.info("Decoding latent to mesh")
    tri_mesh = _decode_latent_mesh(model["xm"], latents[0]).tri_mesh()

    mesh_bytes = _serialize_mesh(tri_mesh, fmt)
    return {
        "format": fmt,
        "prompt": prompt,
        "mesh_base64": base64.b64encode(mesh_bytes).decode("utf-8"),
    }


def output_fn(prediction, accept):
    """Serialize the prediction dict to JSON."""
    return json.dumps(prediction), "application/json"


def _serialize_mesh(tri_mesh, fmt):
    """Convert a Shap-E TriMesh to bytes in the requested format.

    Shap-E's TriMesh natively writes PLY and OBJ. For GLB we hand the vertices
    and faces to trimesh, which exports a proper binary glTF.
    """
    if fmt == "ply":
        buf = io.BytesIO()
        tri_mesh.write_ply(buf)
        return buf.getvalue()

    if fmt == "obj":
        buf = io.StringIO()
        tri_mesh.write_obj(buf)
        return buf.getvalue().encode("utf-8")

    # glb
    import trimesh

    mesh = trimesh.Trimesh(vertices=tri_mesh.verts, faces=tri_mesh.faces)
    return mesh.export(file_type="glb")
