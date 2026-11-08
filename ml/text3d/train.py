"""Small SageMaker pilot: adapt the text projection of pretrained Shap-E."""
import json
import os
import random
import subprocess
import sys
from pathlib import Path

def main():
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "blobfile", "ftfy", "regex", "fire", "humanize"], check=True)
    import torch
    import clip
    from shap_e.models import download
    from shap_e.diffusion.gaussian_diffusion import diffusion_from_config

    # Use the Azure origin while retaining upstream's SHA-256 checks.
    origin = lambda u: u.replace("openaipublic.azureedge.net", "openaipublic.blob.core.windows.net")
    download.URL_HASHES.update({origin(u): h for u, h in download.URL_HASHES.items()})
    for locations in (download.MODEL_PATHS, download.CONFIG_PATHS, clip.clip._MODELS):
        for name, url in locations.items():
            locations[name] = origin(url)
    os.chdir("/tmp")
    torch.manual_seed(37)
    model = download.load_model("text300M", device=torch.device("cuda"), progress=False, chunk_size=2**20).eval()
    model.requires_grad_(False)
    projection = model.wrapped.clip_embed.requires_grad_(True)
    parent = Path("/opt/ml/input/data/parent")
    parent_metrics = None
    if parent.is_dir():
        parent_metrics = json.loads((parent/"metrics.json").read_text())
        projection.load_state_dict(torch.load(parent/"text-projection.pt", weights_only=True))
    optimizer = torch.optim.AdamW(projection.parameters(), lr=1e-5)
    diffusion = diffusion_from_config(download.load_config("diffusion"))
    data = Path("/opt/ml/input/data/examples")
    rows = sorted(json.loads((data/"manifest.json").read_text()), key=lambda r: r["uid"])
    random.Random(37).shuffle(rows)
    if parent_metrics:
        previous = parent_metrics["validation"]
        previous_train = set(parent_metrics["train"])
        by_id = {row["uid"]: row for row in rows}
        fresh = [row["uid"] for row in rows if row["uid"] not in previous_train and row["uid"] not in previous]
        held = previous + fresh[:max(0, len(rows)//5-len(previous))]
        rows = [by_id[uid] for uid in held] + [row for row in rows if row["uid"] not in held]
        validation_count = len(held)
    else:
        validation_count = 3
    steps = int(os.environ.get("TRAIN_STEPS", "64"))
    examples = []
    for row in rows:
        latent = torch.load(data/(row["uid"]+".pt"), weights_only=True).float().reshape(1,-1).cuda()
        assert latent.shape == (1,1048576) and torch.isfinite(latent).all()
        cond = model.cached_model_kwargs(1, {"texts":[row["caption"]]})
        examples.append((latent,cond))

    def loss(sample, seed=None):
        latent, cond = sample
        generator = None if seed is None else torch.Generator(device="cuda").manual_seed(seed)
        time = torch.randint(16,1008,(1,),device="cuda",generator=generator)
        noise = torch.randn(latent.shape,device="cuda",generator=generator)
        noisy = diffusion.q_sample(latent,time,noise)
        prediction = model(noisy,time,**cond).chunk(2,dim=1)[0]
        return (prediction-latent).square().mean()

    @torch.no_grad()
    def evaluate():
        return sum(loss(sample, 800+i).item() for i,sample in enumerate(examples[:validation_count]))/validation_count

    before = evaluate()
    rng = random.Random(256)
    torch.manual_seed(256)
    order = []
    for step in range(1, steps+1):
        if not order:
            order = list(range(validation_count,len(rows)))
            rng.shuffle(order)
        optimizer.zero_grad()
        value = loss(examples[order.pop()])
        if not torch.isfinite(value):
            raise ValueError("Nonfinite denoising loss")
        value.backward()
        torch.nn.utils.clip_grad_norm_(projection.parameters(), 1.0)
        optimizer.step()
        if step == 1 or step % 8 == 0:
            print(f"step={step} train_mse={value.item():.6f}",flush=True)
    target = Path("/opt/ml/model")
    target.mkdir(parents=True, exist_ok=True)
    torch.save(projection.state_dict(), target/"text-projection.pt")
    report = dict(base="Shap-E text300M",steps=steps,cumulative_steps=steps+(parent_metrics.get("cumulative_steps",parent_metrics["steps"]) if parent_metrics else 0),train=[r["uid"] for r in rows[validation_count:]],
                  validation=[r["uid"] for r in rows[:validation_count]],baseline_mse=before,final_mse=evaluate())
    (target/"metrics.json").write_text(json.dumps(report,indent=2))
    (target/"attribution.json").write_text(json.dumps(rows,indent=2))
    print(json.dumps(report),flush=True)
    if os.environ.get("GENERATE_COMPARISON") != "1" or not parent_metrics:
        return
    from inference import predict_fn
    from shap_e.diffusion.sample import sample_latents
    import base64
    decoder = download.load_model("decoder", device=torch.device("cuda"), progress=False, chunk_size=2**20)
    with torch.no_grad():
        for name, weights in [("continued", target/"text-projection.pt"), ("previous", parent/"text-projection.pt")]:
            projection.load_state_dict(torch.load(weights, weights_only=True))
            prediction = predict_fn(dict(prompt="A wooden equipment shed with a pitched roof", format="ply", seed=37, num_steps=32),
                                    dict(device=torch.device("cuda"),xm=decoder,text_model=model,diffusion=diffusion,sample_latents=sample_latents))
            (target/(name+".ply")).write_bytes(base64.b64decode(prediction["mesh_base64"]))


if __name__ == "__main__":
    main()
