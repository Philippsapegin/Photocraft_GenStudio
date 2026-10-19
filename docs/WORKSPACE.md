# Gen Studio workspace — 0.7 alpha

Open **Filter → Plug-ins → Gen Studio…**. Qwen Image 2.1 Turbo is currently the only model. Generation works without an open document and opens every result as a new document. A batch creates one document per result.

The main reference sits to the left of the prompt. Opening the workspace with an active selection captures the visible composite inside that selection, retaining its soft mask as alpha. Without a selection the slot starts empty. Click it to choose a file, drop a file onto it, or use **Use selection** to capture the current selection again. Removing a reference uses its × button.

The row below accepts nine additional references. It starts with one empty slot; filling it reveals the next empty slot, up to ten references in total including the main image. Files load in a background worker. Inputs are color-converted to sRGB and reduced to 1024 pixels on their longest side for reference conditioning.

Drag a populated reference into the prompt to insert a highlighted **[Image N]** chip at the drop cursor. The request sends `image N`, matching the ordered reference inputs; it does not send invented tokenizer tokens. Reference chips keep their image identity when numbering changes. Removing the referenced image removes its chip. Chips remain ordinary editable text, including in monospaced mode.

Controls below the prompt:

- **Batch − / number / +**: one to eight outputs, sampled sequentially with different seeds.
- **1k / 2k**: cycle the longest output side between 1024 and 2048 pixels. The main reference determines aspect ratio; no main reference produces a square. Dimensions round to multiples of 32 and each side is at least 256 pixels.
- **Aa / { }**: switch proportional and monospaced prompt text without changing the prompt.
- **Generate / Cancel**: submit full-image generation or cancel the running workspace job.

## Settings

**Settings** is collapsed initially. It contains separate **Model** and **Engine** selectors, startup policy, shared Models folder, display preferences and engine controls. The engine status stays in a small footer even while Settings is closed. Engine startup retains its nonblocking notice and spinner.

Opening Settings or Advanced scrolls that section into view. The floating selection-fill bar is temporarily hidden while the Studio window is open; closing Studio restores it without changing the display preference.

**Advanced**, also collapsed initially, contains **Text encoder** and **VAE** selectors plus Python/runtime/connection paths. Automatic selection scans filenames inside the shared Models folder, without reading weights or starting an engine. Qwen defaults prefer the W4A8 text encoder, then INT8 ConvRot, then BF16, and the Qwen Image 2.1 BF16 VAE. Explicit choices support nested component folders; returning to **Automatic** restores discovery. Choosing a different Models folder resets explicit component overrides. Both Direct and ComfyUI use these choices.

## Implementation and verification

Full generation uses a separate `/studio/jobs` path. Both engines start with an empty target latent and ordered optional references; they do not reuse the masked-edit sampling path. Existing Remove and selection-fill behavior remains covered by regression tests. Batch sampling shares the engine work lock with inpainting, and changing engine settings is blocked while either kind of request is active.

ComfyUI full generation uses standard Qwen nodes; LanPaint is needed for the existing masked-edit tools. Reference preparation also has a 24 MP synthetic check: it decodes and reduces a camera-size input in a worker while the UI remains available.

Relevant files: `photocraft/genstudio_canvas.rs`, `photocraft/genstudio_generate.rs`, `genstudio/studio.py`, `genstudio/studio_edit.py`, and `scripts/prepare_studio.py`.

`scripts/photocraft_studio_smoke.py --exe <editor.exe> --cli <photocraft-cli.exe>` exercises the official MCP bridge, UI state, selection capture, ten reference slots, renumbering, full-batch transport, model discovery and cancellation against an isolated synthetic HTTP backend. It writes synthetic screenshots and a report under `.runtime/photocraft-studio-smoke`. It never starts an AI worker or loads weights.

Real generation and GPU quality/performance checks remain deliberately excluded at the user's request. The 0.7.0-alpha.1 release includes this workspace.
