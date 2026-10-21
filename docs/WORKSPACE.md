# GenGate workspace

Open **Filter → Plug-ins → GenGate…**. Choose Qwen Image 2.1 Turbo or FLUX 3 Image Klein in Preferences. The left-aligned title is `GenGate | <model>`; the row above canvas and prompt is reserved for future tools. Every output becomes a new raster layer in the document where the request started, whether or not there is a selection or main reference. Without an open document, Studio creates a document containing the generated image; a batch adds one layer per result. Existing pixels and selections are retained. If the user switches tabs during generation, the result goes to the original document. Closing the source document preserves the result in a new document instead.

The main reference sits to the left of the prompt. Opening the workspace with an active selection captures the visible composite inside that selection, retaining its soft mask as alpha. Without a selection the slot starts empty. Click it to choose a file, drop a file onto it, or use **Use selection** inside the main slot whenever the document has a selection, including over a loaded image or generated result. Removing a reference uses its × button. When the main slot was empty at submission, successful generation fills it with the generated image. A reference loaded by the user while the request is running takes priority.

The row below accepts nine additional references. It starts with one empty slot; filling it reveals the next empty slot, up to ten references in total including the main image. Files load in a background worker. Inputs are color-converted to sRGB and reduced to 1024 pixels on their longest side for reference conditioning.

Drag a populated reference into the prompt to insert a highlighted **[Image N]** chip at the drop cursor. The request sends `image N`, matching the ordered reference inputs; it does not send invented tokenizer tokens. Reference chips keep their image identity when numbering changes. Removing the referenced image removes its chip. Chips remain ordinary editable text, including in monospaced mode.

Generated layers retain both the displayed prompt and the compiled model prompt in their metadata. Properties displays the creation prompt; `.pcraft` and PSD preserve it. Batch insertion into an existing document is one Undo/Redo step. Full generation does not use the current selection as an output mask.

The canvas and prompt fill the space when resizing the Studio window. A shared 8-point gap separates the canvas, prompt and controls; the controls share one height and bottom edge with the canvas. File hover uses a thicker outline in the current theme’s accent; Generate uses the same accent with a contrasting label.

Controls below the prompt:

- **− / number / +**, grouped under one border: one to eight outputs, sampled sequentially with different seeds.
- **1k / 2k**: cycle the longest output side between 1024 and 2048 pixels. This control appears only when the main slot is empty. The adjacent aspect selector offers 1:1, 3:2, 5:4, 16:9, 2:1 and their portrait variants. A populated main reference determines aspect ratio instead. Dimensions round to multiples of 32 and each side is at least 256 pixels.
- **Aa / {Aa}**: switch proportional and monospaced prompt text without changing the prompt.
- **Generate / Cancel**: submit full-image generation or cancel the running workspace job.

## Settings

Open **Edit → Preferences → GenGate Settings**. It contains separate **Model** and **Engine** selectors, startup policy, shared Models folder, display preferences and engine controls. The engine status stays in a small footer in the main workspace. Engine startup retains its nonblocking notice and spinner.

The Preferences tab keeps a working copy: Apply and OK save changes, Cancel discards unapplied changes. Advanced remains collapsed initially and scrolls into view when expanded. The floating selection-fill bar is temporarily hidden while the Studio window is open; closing Studio restores it without changing the display preference.

**Advanced**, also collapsed initially, contains **Text encoder** and **VAE** selectors plus Python/runtime/connection paths. Automatic selection scans filenames inside the shared Models folder, without reading weights or starting an engine. Qwen defaults prefer the W4A8 text encoder, then INT8 ConvRot, then BF16, and the Qwen Image 2.1 BF16 VAE. Explicit choices support nested component folders; returning to **Automatic** restores discovery. Choosing a different Models folder resets explicit component overrides. Both Direct and ComfyUI use these choices.

## Implementation and verification

Full generation uses a separate `/studio/jobs` path. Both engines start with an empty target latent and ordered optional references; they do not reuse the masked-edit sampling path. Existing Remove and selection-fill behavior remains covered by regression tests. Batch sampling shares the engine work lock with inpainting, and changing engine settings is blocked while either kind of request is active.

ComfyUI full generation uses standard Qwen nodes; LanPaint is needed for the existing masked-edit tools. Reference preparation also has a 24 MP synthetic check: it decodes and reduces a camera-size input in a worker while the UI remains available.

Relevant files: `photocraft/genstudio_canvas.rs`, `photocraft/genstudio_generate.rs`, `genstudio/studio.py`, `genstudio/studio_edit.py`, and `scripts/prepare_studio.py`.

`scripts/photocraft_studio_smoke.py --exe <editor.exe> --cli <photocraft-cli.exe>` exercises the official MCP bridge, UI state, selection capture, ten reference slots, renumbering, full-batch transport, model discovery and cancellation against an isolated synthetic HTTP backend. It writes synthetic screenshots and a report under `.runtime/photocraft-studio-smoke`. It never starts an AI worker or loads weights.

Real generation and GPU quality/performance checks remain deliberately excluded at the user's request. Version 0.8.0-alpha.1 publishes the GenGate name, native Preferences integration, adaptive workspace, full generation and FLUX 3 support.

Busy local-worker read timeouts are retried against the same job, without resubmitting a generation. Retries remain cancellable and stop after a bounded period without a response.

## FLUX 3 Image Klein

Preferences has a separate Model selector for Qwen Image 2.1 Turbo / FLUX 3 Image Klein, independently of Direct / ComfyUI. Changing the model resets component overrides to Automatic; discovery chooses that model's text encoder and VAE from the shared folder. Klein additionally discovers its official prompt upsampler (W4A8 preferred, then INT8, then BF16). Advanced remains collapsed by default.

The Studio header and reference labels follow the selected model. Both models officially accept ten references; there is no artificial Klein limit reduction. Dragging inserts `<ref_image_0>`-style chips for Klein, with zero-based reference order. Switching models converts chips and reference removal renumbers them while preserving assignments. BFL's bounding-box syntax is documented at https://docs.bfl.ai/flux_3/flux3_image_bounding_boxes.

Klein works through the same generated-layer, batch, cancellation, selection and masked-tool paths. Direct uses its own pinned source library; ComfyUI needs 0.41.3+. No live model generation or memory benchmark has been performed. The four explicitly queued downloads total 17.75 GB; the background downloader resumes partial files and publishes them to discovery only after official SHA-256 verification.

## Segmentation

GenGate Settings has an independent Segmentation section: Enable AI segmentation, YOLO-World v2 S/M/L detector, SAM3 segmenter and optional CUDA Python. Enabling it prepares only the selected detector plus SAM3. Defaults are S + SAM3; weights share the chosen Models folder. This always uses a short-lived Direct process, independent of generative engine/model and startup policy.

Subject and Remove Background use a YOLO foreground box followed by SAM3; Sky uses SAM3 text segmentation. Object Selection exposes Ai Select. With master segmentation and that checkbox enabled, only rectangle drags submit requests; clicks are ignored. ROI clipping, Sample All Layers and replace/add/subtract/intersect are preserved. Background removal keeps pixels and writes a native layer mask. Cancel/failure preserve the document. No real segmentation inference has been validated.

Automation: existing select.subject, select.sky and layer.removeBackground IDs route automatically when enabled. select.object accepts aiSelect:true; ui.set accepts objectAiSelect:true for the real Object Selection gesture. gengate.segmentation.prepare downloads/prepares resources without loading a model. The synthetic official-MCP regression is scripts/photocraft_segmentation_smoke.py.
