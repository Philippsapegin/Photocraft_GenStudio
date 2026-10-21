# GenGate for PhotoCraft · Alpha

Local AI image generation, editing, object removal and segmentation integrated into [PhotoCraft](https://github.com/storytold/photocraft). Results appear directly in the document as layers, selections or editable masks, with Undo/Redo.

**0.8.0-alpha.1 · Windows x64 · Qwen Image 2.1 Turbo / FLUX 3 Image Klein · Direct or ComfyUI.** GenGate was previously called Gen Studio; existing profiles, commands and generated-layer metadata remain compatible. This is an independent integration based on PhotoCraft 0.8.2. Because its current plug-in API cannot expose these native controls, the download includes a modified editor and a local Python runtime.

## Install

1. Download **[PhotoCraft-GenGate-0.8.0-alpha.1-windows-x64.zip](https://github.com/Philippsapegin/Photocraft_GenStudio/releases/download/v0.8.0-alpha.1/PhotoCraft-GenGate-0.8.0-alpha.1-windows-x64.zip)** from the [alpha release](https://github.com/Philippsapegin/Photocraft_GenStudio/releases/tag/v0.8.0-alpha.1). Extract the complete ZIP and double-click **Setup.cmd**. GitHub's Source code archives contain no executable.
2. Choose an installation folder and a **Models folder**. Reuse `ComfyUI/models` or choose a separate directory. Generative weights are read in place; setup does not duplicate or convert them.
3. Choose **Direct** or **ComfyUI**. Direct can reuse an existing CUDA Python, including ComfyUI's embedded Python, or install a separate GPU runtime. A new runtime downloads several GB of software. Reusing a Python never upgrades its packages in place.
4. Open the **PhotoCraft GenGate** shortcut. Open **Filter → Plug-ins → GenGate…**. All configuration is under **Edit → Preferences → GenGate Settings**. Select a model independently from its engine.

No administrator access, Rust, Git or separate host Python is required. The installation has its own editor, profile and shortcuts. The alpha is unsigned; releases include SHA-256 checksums.

**Requirements:** Windows 10/11 x64 and an NVIDIA CUDA GPU for Direct generation and segmentation. A newly installed runtime uses CUDA 13.0 and needs a compatible driver. Klein Direct requires Python 3.12+. Development uses an RTX 3060 12 GB and 64 GB RAM; these are not measured minimum requirements. Keep the Windows page file enabled.

To update, save and close this editor and run setup into the same install folder. Version directories preserve the previous executable; the editor profile lives under `Profile`. To uninstall, close the editor, stop any generation engine it owns, then remove this installation folder and its GenGate shortcuts. Shared models and external ComfyUI remain where you put them.

## Generation workspace

Open GenGate with or without an open document. Its left-aligned title identifies the current model: **GenGate | FLUX 3 Image Klein**. The row below it is reserved for future tools.

- Enter a prompt and click **Generate**. Every image becomes a new layer with its creation prompt stored in the project. With no open document, GenGate creates a document containing only the generated image.
- The main reference accepts a file click, drag and drop, or **Use selection**. The selection button remains available when a reference or an earlier result is already present. An empty reference is filled with the generated image when generation completes.
- Add up to nine extra references below the main canvas. Filled slots can be dragged into the prompt to insert model-specific reference chips. Changing models converts chips while keeping their image assignments.
- The compact controls provide batch size, text-only 1k/2k resolution and aspect ratio, and an **Aa / {Aa}** font toggle. Reference-based generation uses the reference's proportions. Canvas and prompt resize with the window.

## Settings

**Edit → Preferences → GenGate Settings** contains independent Model and Engine selectors, a shared Models folder, engine startup policy, progress-bar and floating-prompt-bar toggles. **Advanced**, collapsed by default, contains text encoder, VAE, Python and connection settings. Compatible encoders and VAEs are discovered automatically. Apply/OK save changes; Cancel discards them.

Automatic start is selectable per generation engine: **Off**, **When PhotoCraft starts**, or **On first AI request**. Starting shows a nonblocking banner and spinner. An idle available worker displays **Engine ready**; model weights load on the first request. GenGate stops only generation processes it started and never terminates an attached external ComfyUI.

**Direct** uses isolated checkpoint-compatible model libraries in a worker process, without a ComfyUI server or graph. **ComfyUI** needs the selected model's nodes; Klein requires 0.41.3+. Qwen inpainting uses LanPaint, installed only when selected in setup. Advanced can attach to an existing local ComfyUI URL. The host and generation worker use ports `8766` and `8767`; one shared generation-host configuration is supported at a time.

## Models

Get these **single-file Comfy-format checkpoints**, rather than the full Diffusers repository. The links below pin the exact file revision used by this project; the [official Qwen Turbo model card](https://huggingface.co/Qwen/Qwen-Image-2.1-Turbo) describes the underlying model and its license. Model weights have their own terms and are not included in this repository or installer.

| Component | Download | Approx. download size |
| --- | --- | --- |
| **Required diffusion model** | [Qwen Image 2.1 Turbo INT8 ConvRot](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/72a49122613a4f9afbcc443426d6c765c0c383cf/diffusion_models/qwen_image_2.1_turbo_int8_convrot.safetensors?download=true) | 7.26 GB |
| **Recommended text encoder — the compressed setup used in development** | [Qwen3-VL 8B W4A8](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/72a49122613a4f9afbcc443426d6c765c0c383cf/text_encoders/qwen3vl_8b_w4a8.safetensors?download=true) | 6.31 GB |
| Alternative text encoder | [Qwen3-VL 8B INT8 ConvRot](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/72a49122613a4f9afbcc443426d6c765c0c383cf/text_encoders/qwen3vl_8b_int8_convrot.safetensors?download=true) | 9.35 GB |
| **Standard, full precision text encoder** | [Qwen3-VL 8B BF16](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/72a49122613a4f9afbcc443426d6c765c0c383cf/text_encoders/qwen3vl_8b_bf16.safetensors?download=true) | 17.53 GB |
| **Required VAE** | [Qwen Image 2.1 VAE BF16](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/72a49122613a4f9afbcc443426d6c765c0c383cf/vae/qwen_image_2.1_vae_bf16.safetensors?download=true) | 0.68 GB |

Download **one** text encoder. If several formats are present, discovery prefers W4A8, then INT8 ConvRot, then BF16. These are download sizes, not VRAM estimates. The recommended three-file set totals about **14.25 GB**. BF16 is a larger alternative; it is not a second required download.

```text
Models/
  diffusion_models/
    qwen_image_2.1_turbo_int8_convrot.safetensors
  text_encoders/
    qwen3vl_8b_w4a8.safetensors
  vae/
    qwen_image_2.1_vae_bf16.safetensors
```

Nested subfolders, `unet` / `clip` aliases and a flat directory also work. Duplicate files of the same chosen format are rejected with an explanation. Point both engines at the same folder; neither engine needs its own copy. The model and file details are published by [Comfy Org](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/tree/72a49122613a4f9afbcc443426d6c765c0c383cf).

## FLUX 3 Image Klein

Choose **Model → FLUX 3 Image Klein** in GenGate Settings. Both Direct and ComfyUI are supported.

The distilled checkpoint uses **16 steps / INT8 ConvRot**. The model accepts **10 references**, just like Qwen 2.1. Dragged prompt chips use the official zero-based tokens `<ref_image_0>` through `<ref_image_9>`; switching models converts chips while retaining their image assignments. Removing a reference renumbers chips. BFL documents the syntax in [Bounding boxes with FLUX 3 Image](https://docs.bfl.ai/flux_3/flux3_image_bounding_boxes).

Required files, in the same shared Models folder. These direct links use a pinned public mirror; the downloader verifies the byte sizes and SHA-256 values against BFL originals:

| Folder | File | Size |
| --- | --- | --- |
| `diffusion_models` | [flux3-image-klein-int8-convrot.safetensors](https://huggingface.co/ml-resources/flux-3-image-klein-comfy/resolve/3ba0f7064cbb1666a71e2820d3cc5c2273bb6cd0/diffusion_models/flux3-image-klein-int8-convrot.safetensors?download=true) | 5.83 GB |
| `text_encoders` | [qwen3vl_4b_int8_convrot.safetensors](https://huggingface.co/ml-resources/flux-3-image-klein-comfy/resolve/3ba0f7064cbb1666a71e2820d3cc5c2273bb6cd0/text_encoders/qwen3vl_4b_int8_convrot.safetensors?download=true) | 4.86 GB |
| `vae` | [flux2-vae.safetensors](https://huggingface.co/ml-resources/flux-3-image-klein-comfy/resolve/3ba0f7064cbb1666a71e2820d3cc5c2273bb6cd0/vae/flux2-vae.safetensors?download=true) | 0.34 GB |
| `text_encoders` | [qwen3.5_9b_flux3_prompt_upsampler_w4a8.safetensors](https://huggingface.co/ml-resources/flux-3-image-klein-comfy/resolve/3ba0f7064cbb1666a71e2820d3cc5c2273bb6cd0/text_encoders/qwen3.5_9b_flux3_prompt_upsampler_w4a8.safetensors?download=true) | 6.72 GB |

The prompt upsampler expands the user's instruction using the references before rendering, as [BFL recommends](https://huggingface.co/black-forest-labs/flux-3-image-klein-comfy). Direct releases it before loading the image pipeline. Discovery can also reuse the official INT8 or BF16 upsampler if W4A8 is absent. The UI keeps the existing 1k/2k options for both models.

Explicit download (also available in the installed runtime): `.venv\Scripts\python.exe scripts/download_flux3.py --models "D:\path\to\models"`. This queues the four files (17.75 GB), resumes `.part` downloads and checks their SHA-256 values against the official publication before exposing them to model discovery. Existing final files are verified and reused; differing files are preserved. Generative model weights are downloaded explicitly. Download provenance is pinned in [flux3-models.json](photocraft/flux3-models.json).

## Tools

### Remove Tool → Ai Fill

Select the native **Remove Tool (J)** and enable **Ai Fill**, the last checkbox in its options bar. Paint over the object and release the brush. The tool sends a crop with surrounding context and a mask to the selected AI engine. Its hidden prompt asks for the scene **without the object**, with no replacement objects.

The existing options keep working:

- **Sample All Layers** chooses the visible composite instead of the active layer as context.
- **Create a new layer** inserts the generated context rectangle with an editable mask. When off, only masked pixels are painted into the active target.
- **Remove after each stroke** submits on release. When off, accumulate strokes and submit with **Enter / ✓**, or discard them with **Esc / ⊘**.

There is no separate brush dialog. With **Ai Fill** off, PhotoCraft's original content-aware Remove behavior is available.

### Selection Ai Fill

Make a selection, type in the floating prompt bar and click **Generate**. The bar is enabled by default. Each result appears as an AI layer with the original selection as its editable mask.

Alternatively, **Edit → Ai Fill**, above Fill / Stroke / Content-Aware Fill, creates an empty AI layer from the selection. The command is disabled without a selection. Select that layer to find **Prompt**, **Generate** and the **variant gallery** in Properties. Click a variant to restore both its pixels and its original prompt; generate again to add another variant. Scroll or resize the Properties dock if the gallery is below its visible area.

Layer creation, generation and variant switching support Undo/Redo. Variants and prompts survive `.pcraft` and PSD round trips. Other editors display the ordinary raster layer and mask; they do not expose this gallery. Galleries are bounded to 32 variants / 128 MiB per layer.

### Queue and feedback

Several AI strokes or fills can wait in a shared queue. Pending and running masks remain visible on the canvas. A small **×** below a waiting mask cancels it before it starts. Thin progress bars show engine startup activity, then sampling progress without numbers. The source for each queued job is read when it starts, so preceding results become part of its context.

Results are applied immediately in the document. Failed or cancelled requests leave it unchanged. Native compositing retains document bit depth, color profile and the original edit mask. Model input is a context crop limited to 768 pixels on its longest side; large areas therefore have limited AI detail in this alpha.

## AI segmentation

Enable **AI segmentation** in **Preferences → GenGate Settings → Segmentation**. This feature always uses **Direct**, independently of the selected generative model, engine and automatic-start policy.

- **Select → Subject** detects the foreground subject with YOLO-World and builds its mask with SAM3.
- **Select → Sky** segments the sky using a SAM3 text prompt.
- **Remove Background**, in layer Properties, adds a nondestructive layer mask from the detected subject. Pixel data is retained.
- **Object Selection Tool → Ai Select** searches only within a dragged rectangle and creates a selection. Single clicks and sub-2-pixel drags do nothing while enabled. Sample All Layers and add/subtract/intersect modifiers remain available. Turning AI segmentation off restores the native algorithms.

Defaults are **YOLO-World v2 S + SAM3**. YOLO-World M and L can be selected as larger detector alternatives. Enabling segmentation prepares the selected detector and SAM3 automatically in `Models/segmentation` (defaults: about **3.48 GB**). Downloads resume and verify SHA-256; existing common model paths are reused. Other detector sizes download only when selected. The GPU process exits after each request to release its memory. Cancel leaves the document unchanged.

| Component | Source | Default size |
| --- | --- | --- |
| YOLO-World v2 S | [Official Ultralytics weights](https://github.com/ultralytics/assets/releases/download/v8.4.0/yolov8s-worldv2.pt) | 25.9 MB |
| SAM3 | [Meta model card and terms](https://huggingface.co/facebook/sam3), [pinned public checkpoint mirror](https://huggingface.co/AB498/sam3/tree/84bd0040bde4d9dd9312dd8beab0d15068d95ce8) | 3.45 GB |

The default SAM3 downloader uses a public third-party mirror, pinned independently; it is not endorsed by Meta. You can instead obtain `sam3.pt` from Meta after access approval and put it in the shared Models folder. Provenance and hashes are in [segmentation-models.json](photocraft/segmentation-models.json). Weights retain their publisher's license.

Leave **CUDA Python** empty to reuse the configured Direct or ComfyUI Python; this starts no ComfyUI process. GenGate keeps its pinned Ultralytics/CLIP software in a private overlay. New GPU runtimes include its dependencies. If a reused environment lacks dependencies, select a separate Python with [segmentation-requirements.txt](photocraft/segmentation-requirements.txt), rather than upgrading a working ComfyUI environment.

## Alpha status and roadmap

Native controls, masks, queues, reference handling, persistent prompts, lifecycle and transport have automated synthetic coverage. **Real generation and segmentation inference are excluded from validation at the user's request.** Image quality, memory requirements and model performance remain unverified. Report the selected model/engine and a sanitized error log; avoid publishing private images or prompts unintentionally.

Planned work:

- Validate segmentation and inpainting quality and memory use on real models.
- Additional segmentation models and FLUX 3 controls.
- Custom inference providers and APIs.
- SDXL-based models.

## Development and licenses

See [development and packaging](docs/DEVELOPMENT.md), [architecture](docs/ARCHITECTURE.md), [validation](docs/VALIDATION.md) and [third-party notices](THIRD_PARTY.md).

Python host, adapters and installer: **GPL-3.0-or-later**, see [LICENSE](LICENSE). Original Rust integration: **MIT OR Apache-2.0**, matching PhotoCraft; see [MIT](photocraft/LICENSE-MIT) and [Apache-2.0](photocraft/LICENSE-APACHE). Ultralytics is AGPL-3.0 and runs in a separate Python process. Upstream software, model weights and trademarks retain their own terms.
