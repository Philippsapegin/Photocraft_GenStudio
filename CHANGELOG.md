# Changelog

## 0.9.2 Beta

- Separate GenGate add-on installer for official PhotoCraft 0.15.1 Windows x64; earlier 0.8.2 and 0.12.0 patch entries are retained.
- FIBO 2.0 Edit Turbo preset, alongside Qwen Image 2.1 and FLUX 3.
- Studio LoRA Explorer with strengths, aliases and saved trigger words; generated layers retain their prompt and LoRA recipe for drag-and-drop reuse.
- Consistent compact window headers, LoRA table styling and visible drag feedback.
- Floating-bar keyboard/layout-switch fixes and progressive economy mode for large AI editing crops.
- Setup preserves preset components, Constant LoRAs, segmentation and economy settings across editor/add-on upgrades.

See [release notes](docs/RELEASE_NOTES_0.9.2.md) and [validation](docs/VALIDATION.md).

## 0.6.1-alpha.1

First public Gen Studio alpha, based on PhotoCraft 0.8.2.

- Native Remove Tool **Ai Fill**, with the existing sampling, layer and stroke controls.
- Selection fill from the floating prompt bar or **Edit → Ai Fill**, plus masked AI layers and prompt/variant galleries in Properties.
- Shared FIFO queue, persistent canvas masks, queued cancellation and optional progress bars.
- **Filter → Plug-ins → Gen Studio…** settings, Direct / ComfyUI engines, per-engine startup policies and startup/ready feedback.
- Shared model-source directory, with W4A8 / INT8 / BF16 text-encoder discovery.
- Direct inpainting protocol 2: separate canvas/mask references and a latent sampling mask.
- Windows x64 setup with folder selection, optional separate CUDA environment, an embedded lightweight host, independent profile/shortcuts and versioned installation.
- Source, model download links, third-party notices and roadmap.

Validation uses synthetic inputs and mock model components. Real inference quality, GPU memory requirements and inference speed remain unverified in this alpha.
