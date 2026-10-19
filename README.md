# Gen Studio for PhotoCraft · Alpha

Local AI removal and generative fill integrated into [PhotoCraft](https://github.com/storytold/photocraft). Paint over an object to remove it, or describe what belongs inside a selection. Results appear directly in the document, with editable masks, Undo and a gallery of variants.

**Windows x64 alpha · Qwen Image 2.1 Turbo INT8 · Direct or ComfyUI.** This is an independent integration based on PhotoCraft 0.8.2, not an official PhotoCraft/ArtCraft release. The current plug-in API cannot expose these native controls, so the download includes a modified editor and a local Python host.

## Install

1. Download **[PhotoCraft-GenStudio-0.7.0-alpha.1-windows-x64.zip](https://github.com/Philippsapegin/Photocraft_GenStudio/releases/download/v0.7.0-alpha.1/PhotoCraft-GenStudio-0.7.0-alpha.1-windows-x64.zip)** from the [alpha release](https://github.com/Philippsapegin/Photocraft_GenStudio/releases/tag/v0.7.0-alpha.1). Extract the entire ZIP, then double-click **Setup.cmd**. GitHub's “Source code” ZIP is for developers and does not contain binaries.
2. Choose an install folder on any drive and your **Models folder**. Existing ComfyUI users can select `ComfyUI\models`; everyone else can use a separate folder. Setup reads models in place and never downloads, copies or converts weights. An empty models folder is accepted so you can add files later.
3. Choose **Direct Qwen** or **ComfyUI**, then click **Install**. With Direct, reuse a CUDA Python (including ComfyUI's `python_embeded\python.exe`) or let setup install a separate runtime. A separate runtime downloads several GB of **software dependencies**, not models. With ComfyUI, select its folder and Python; the optional checked box installs the bundled LanPaint extension only if missing.
4. Open the separate **PhotoCraft Gen Studio** shortcut. All settings are in **Filter → Plug-ins → Gen Studio…**. Automatic engine startup defaults to **On first AI request**.

No administrator access, Rust, Git or separately installed host Python is needed. Setup leaves the existing PhotoCraft installation and its shortcuts alone. The editor and its lightweight host are bundled; only a new Direct GPU environment requires dependency downloads. The alpha is unsigned; Windows may show its publisher warning. Release downloads include SHA-256 checksums.

**Requirements:** Windows 10/11 x64 and an NVIDIA CUDA-capable GPU for the packaged Direct engine. Its new runtime uses CUDA 13.0 and needs a compatible recent NVIDIA driver. Existing GPU Python environments are reused without installing into them. The development machine has an RTX 3060 12 GB and 64 GB RAM; these are **not measured minimum requirements**. Model memory, generation speed and final image quality have not been benchmarked for this alpha. Keep Windows' page file enabled; insufficient commit memory can prevent model loading.

To update, save and close the Gen Studio editor, run the newer setup into the same install folder, and choose the engine/model source again. It installs a new version directory and preserves unrelated editor preferences and the previous executable. Your profile and recovery data live under `Profile` in the install folder. To remove this portable installation, close it, stop an engine it owns through Gen Studio, then delete its install folder and its two **PhotoCraft Gen Studio** shortcuts. External models and ComfyUI stay where you put them.

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

## Gen Studio settings

Open **Filter → Plug-ins → Gen Studio…**, even without an open document.

The **0.7 alpha generation workspace** includes a wide prompt, selection/file reference, nine additional references, numbered prompt chips, batches and 1k/2k output. Settings and Advanced components are collapsed by default, with separate Model and Engine selectors. See [workspace behavior and local validation](docs/WORKSPACE.md). The Windows download includes this workspace.

- **Engine:** Direct Qwen, or Qwen through ComfyUI.
- **Automatic start:** **Off**, **When PhotoCraft starts**, or **On first AI request**. Each engine remembers its own choice.
- **Models folder:** shared model source, with Browse.
- **Show progress bars under masks** and **Show floating AI prompt bar**.
- **Apply**, **Start**, **Stop**, **Refresh**, plus advanced Python, runtime and connection paths.

Starting the engine shows a nonblocking banner and spinner. A running, idle engine displays **Engine ready**. “Ready” means the worker is listening; weights load on the first generation. Gen Studio stops only an engine it started itself and never terminates an attached external ComfyUI.

**Direct** uses a pinned checkpoint-compatible Comfy model library in a separate worker; it starts no ComfyUI server, frontend or node graph. **ComfyUI** requires a current installation with Qwen 2.1 nodes and LanPaint. A managed instance normally uses `127.0.0.1:8190`; to attach to an existing instance, set its local URL in Advanced settings and make sure it already exposes the selected models and nodes. Restart an external ComfyUI yourself after adding LanPaint. The host and Direct worker use local ports `8766` and `8767`; only one Gen Studio host configuration is supported at a time.

## Alpha status

The native controls, queue, masks, variants, persistence, lifecycle and HTTP pipeline have automated synthetic coverage. Direct inpainting protocol 2 passes an explicit image/mask pair and a latent sampling mask, addressing the previous selection-fill path that could return unchanged pixels.

**Real model generation was deliberately excluded from release validation.** This release does not claim verified inpainting quality, GPU memory requirements or inference performance. Expect rough edges and report problems with the chosen engine, model filenames and a sanitized error log. Logs are in the installed version's `runtime/.runtime/genstudio` folder. Never include a private image or prompt without intending to publish it.

## Roadmap

These are **planned**, not features of this alpha:

- AI Select Subject.
- AI Background Remove.
- FLUX3 support.
- Custom providers: connect your own inference backend or API.
- SDXL-based models.

The shared engine host is intended to serve these future tools; its current engines are local Direct Qwen and ComfyUI.

## Development and licenses

See [development and reproducible packaging](docs/DEVELOPMENT.md), [architecture](docs/ARCHITECTURE.md), [release validation](docs/VALIDATION.md) and [third-party notices](THIRD_PARTY.md).

Python host, adapters and installer: **GPL-3.0-or-later**, see [LICENSE](LICENSE). Original Rust integration and translation catalogs: **MIT OR Apache-2.0**, matching PhotoCraft; see [photocraft/LICENSE-MIT](photocraft/LICENSE-MIT) and [photocraft/LICENSE-APACHE](photocraft/LICENSE-APACHE). PhotoCraft, the separate Comfy library, LanPaint, Python and bundled dependencies retain their own notices. Model weights are separate downloads under their publisher's terms.
