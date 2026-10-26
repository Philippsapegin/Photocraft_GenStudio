# Changelog

## 0.9.3 Beta (development)

- PhotoCraft 0.16.0 compatibility, keeping earlier verified patch bases.
- Retains floating-prompt Backspace protection alongside the new native adjustment shortcuts.
- Keeps individual AI Remove strokes in the shared cancellable queue after PhotoCraft's retouch dispatcher update.
- Studio processing selector for custom ComfyUI API workflows, with widget-free GenGate IN / OUT nodes and automatic/one-click node installation.
- Workflow images, batches and layer masks become new layers, with per-result prompt, name, seed, LoRAs and workflow path/hash metadata. Generated-layer restore reinstates the workflow choice.
- Workflow-controlled dimensions, full-size connected document/layer/selection sources and ten reference sockets; quick Ai Fill and Remove Brush keep their preset ComfyUI path.

## 0.9.2 Beta

- Separate GenGate add-on installer for official PhotoCraft 0.15.1 Windows x64; earlier 0.8.2 and 0.12.0 patch entries are retained.
- FIBO 2.0 Edit Turbo preset, alongside Qwen Image 2.1 and FLUX 3.
- Studio LoRA Explorer with strengths, aliases and saved trigger words; generated layers retain their prompt and LoRA recipe for drag-and-drop reuse.
- Consistent compact window headers, LoRA table styling and visible drag feedback.
- Floating-bar keyboard/layout-switch fixes and progressive economy mode for large AI editing crops.
- Setup preserves preset components, Constant LoRAs, segmentation and economy settings across editor/add-on upgrades.
- Shortcut creation and repair support Unicode installation paths independently of the Windows system code page.

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
