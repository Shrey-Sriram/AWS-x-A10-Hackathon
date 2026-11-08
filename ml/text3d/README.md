# Shap-E fine-tuning

Each record pairs a Cap3D caption with a pretrained Shap-E latent for one Objaverse asset. The manifests in runs/ record source object links, creators and licenses. Cite Cap3D (Luo et al.) and Objaverse (Deitke et al.) when using these examples. Keep the original object metadata and attribution with derived examples. The public feature branch contains manifests and metrics. Model checkpoints and bulk training targets are stored in the project's private S3 bucket.

The managed PyTorch job expects the published Shap-E and CLIP code in a code input channel and one weights-only tensor per UID plus manifest.json in an examples input channel. The runs used Shap-E commit 50131012ee11c9d2617f3886c10f000d3c7a3b43 and CLIP commit d05afc436d78f1c48dc0dbf8e5980a9d471f35f6. The trainable module is wrapped.clip_embed. Apply text-projection.pt to that module of the pretrained text300M model to reproduce a checkpoint.

To start a new, billed job from a completed Parcelsite parent, with AWS credentials that can read the parent, write S3 artifacts and create a SageMaker training job:

    python ml/text3d/continue_training.py PARENT_JOB --steps 384

The continuation reuses the parent's data and code channels, starts from its learned projection, preserves the validation IDs and writes metrics, attribution and matched-seed sample meshes. For a new dataset, supply a new examples channel including the parent's validation objects. Each continuation starts a fresh AdamW optimizer. The serving endpoint is managed separately from experimental checkpoints.
