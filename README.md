# GenGate
### Photocraft ai-gen studio

A work-in-progress suite for [PhotoCraft](https://github.com/storytold/photocraft), adding AI fill, selection and object removal, alongside a straightforward interface for image generation.

## Features

- **Two engines:** Direct for users without ComfyUI, and experimental ComfyUI startup and connection for future workflow integrations.
- **Preset-based setup:** compatible model components are selected automatically. Advanced settings let you choose your own model, text encoder, VAE and FLUX prompt upsampler, including INT8 and other supported formats.
- **Native tools, uncluttered workspace:** editing tools stay inside PhotoCraft; configuration lives in **Edit → Preferences → GenGate Settings**.
- **Constant LoRA:** an optional adapter with adjustable strength for Ai Fill and Remove Brush. It is separate from full image generation.

## Tools

| Tool | Where / what it does |
| --- | --- |
| **GenGate** | **Filter → Plug-ins → GenGate**. Text-to-image and reference-based generation, drag-and-drop references, prompt chips, batches and new layers. Works without an open document. |
| **Ai Fill** | Floating prompt bar or **Edit → Ai Fill**. Generate inside a selection; keep prompts and variants in layer Properties. |
| **AI Remove Brush** | Enable **Ai Fill** in the native Remove Tool. Paint an object to remove it; existing sampling and new-layer options still work. |
| **AI selection** | **Select Subject**, **Sky** and **Remove Background** use the independent selection engine when enabled. |
| **Ai Select** | Enable it in the native Object Selection Tool, then drag a rectangle around the subject. |

## WIP status

**0.9.0 is the first beta.** Model support and ComfyUI integration are still in progress.

Planned:

- Convenient LoRA syntax in the main generation window.
- More presets for leading open-source image models.
- Custom providers.
- ComfyUI nodes for experimenting with node spaghetti.
- Many UX improvements.

## Installation

**Windows x64 · NVIDIA CUDA GPU for local AI processing.**

1. Download **[GenGate 0.9.0 for Windows](https://github.com/Philippsapegin/Photocraft_GenStudio/releases/download/v0.9.0/PhotoCraft-GenGate-0.9.0-windows-x64.zip)**, extract the complete ZIP and run **Setup.cmd**. The package includes the modified PhotoCraft editor and lightweight host; model weights are separate.
2. Choose an installation folder and a **Models folder**. Reuse your `ComfyUI/models` directory or create a separate one. Models are used in place, without duplicate downloads.
3. Choose **Direct** or **ComfyUI**. Direct can install a separate CUDA runtime or reuse an existing compatible CUDA Python. A fresh runtime downloads several GB of software.
4. Open the **PhotoCraft GenGate** shortcut and choose a **Preset** in **Preferences → GenGate Settings**. Engine startup can be off, on application launch, or on the first AI request.

To update, save and close GenGate, then run Setup into the same installation folder. Your profile is preserved.

### Model files

Download the components for the preset you want. Use the linked single-file checkpoints, not a full Diffusers repository. Choose **one** Qwen text encoder; W4A8 is the smaller recommended option.

**Qwen Image 2.1**

| Component | File | Download |
| --- | --- | --- |
| Model | `qwen_image_2.1_turbo_int8_convrot.safetensors` | [INT8 · 7.26 GB](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/72a49122613a4f9afbcc443426d6c765c0c383cf/diffusion_models/qwen_image_2.1_turbo_int8_convrot.safetensors?download=true) |
| Text encoder — recommended | `qwen3vl_8b_w4a8.safetensors` | [W4A8 · 6.31 GB](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/72a49122613a4f9afbcc443426d6c765c0c383cf/text_encoders/qwen3vl_8b_w4a8.safetensors?download=true) |
| Text encoder — alternative | `qwen3vl_8b_int8_convrot.safetensors` | [INT8 · 9.35 GB](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/72a49122613a4f9afbcc443426d6c765c0c383cf/text_encoders/qwen3vl_8b_int8_convrot.safetensors?download=true) |
| Text encoder — full precision | `qwen3vl_8b_bf16.safetensors` | [BF16 · 17.53 GB](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/72a49122613a4f9afbcc443426d6c765c0c383cf/text_encoders/qwen3vl_8b_bf16.safetensors?download=true) |
| VAE | `qwen_image_2.1_vae_bf16.safetensors` | [BF16 · 0.68 GB](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/72a49122613a4f9afbcc443426d6c765c0c383cf/vae/qwen_image_2.1_vae_bf16.safetensors?download=true) |

**FLUX 3**

| Component | File | Download |
| --- | --- | --- |
| Model | `flux3-image-klein-int8-convrot.safetensors` | [INT8 · 5.83 GB](https://huggingface.co/ml-resources/flux-3-image-klein-comfy/resolve/3ba0f7064cbb1666a71e2820d3cc5c2273bb6cd0/diffusion_models/flux3-image-klein-int8-convrot.safetensors?download=true) |
| Text encoder | `qwen3vl_4b_int8_convrot.safetensors` | [INT8 · 4.86 GB](https://huggingface.co/ml-resources/flux-3-image-klein-comfy/resolve/3ba0f7064cbb1666a71e2820d3cc5c2273bb6cd0/text_encoders/qwen3vl_4b_int8_convrot.safetensors?download=true) |
| Prompt upsampler | `qwen3.5_9b_flux3_prompt_upsampler_w4a8.safetensors` | [W4A8 · 6.72 GB](https://huggingface.co/ml-resources/flux-3-image-klein-comfy/resolve/3ba0f7064cbb1666a71e2820d3cc5c2273bb6cd0/text_encoders/qwen3.5_9b_flux3_prompt_upsampler_w4a8.safetensors?download=true) |
| VAE | `flux2-vae.safetensors` | [0.34 GB](https://huggingface.co/ml-resources/flux-3-image-klein-comfy/resolve/3ba0f7064cbb1666a71e2820d3cc5c2273bb6cd0/vae/flux2-vae.safetensors?download=true) |

**Independent Selection Engine**

| Component | Download |
| --- | --- |
| YOLO-World v2 S | [Official weights · 25.9 MB](https://github.com/ultralytics/assets/releases/download/v8.4.0/yolov8s-worldv2.pt) |
| SAM3 | [Official model and terms](https://huggingface.co/facebook/sam3) · [pinned third-party mirror · 3.45 GB](https://huggingface.co/AB498/sam3/resolve/84bd0040bde4d9dd9312dd8beab0d15068d95ce8/sam3.pt) |

Enabling AI segmentation downloads its selected components automatically and reuses existing files.

### Models folder

```text
models/
  diffusion_models/   # Qwen / FLUX image model
  text_encoders/      # Text encoder and FLUX prompt upsampler
  vae/               # VAE
  loras/             # Optional adapters, selected manually
  segmentation/      # YOLO-World and SAM3 .pt files
```

Both presets can share this folder. Nested folders, `unet` / `clip` aliases and a flat folder also work. If discovery misses a component, select it using **Advanced → Browse**. Sizes above are download sizes, not VRAM requirements.

## Development and licenses

See [development and packaging](docs/DEVELOPMENT.md), [architecture](docs/ARCHITECTURE.md), [validation](docs/VALIDATION.md) and [third-party notices](THIRD_PARTY.md).

Python host, adapters and installer: **GPL-3.0-or-later** ([LICENSE](LICENSE)). Original Rust integration: **MIT OR Apache-2.0**, matching PhotoCraft ([MIT](photocraft/LICENSE-MIT), [Apache-2.0](photocraft/LICENSE-APACHE)). Ultralytics uses AGPL-3.0 and runs in a separate process. Model weights retain their publishers' terms.
