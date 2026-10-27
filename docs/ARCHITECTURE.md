# Architecture

The native Rust integration adds controls to PhotoCraft and sends local HTTP requests through a small Python host. It uses the editor's command registry, background jobs, document history, color conversion and layer-mask APIs. The existing WASM plug-in sandbox is unchanged. PhotoCraft's official MCP interface remains available for automation and validation.

```text
PhotoCraft native tools and GenGate settings
                   |
        background control / edit client
                   |
         Python host (private loopback port)
            /                  \
   Direct worker (own port) local ComfyUI
   pinned model library     Qwen nodes + LanPaint
            \                  /
            shared Models folder
```

## Source map

- `genstudio/`: engine settings, lifecycle, Direct worker/pipeline and editor transport.
- `genfill/`: shared image preparation, compositing, asynchronous jobs and Comfy adapter.
- `photocraft/*.rs`: native engine and UI integration templates, copied into pinned upstream by the preparers.
- `photocraft/*-locales.json`: original native menu/feedback catalogs.
- `scripts/prepare_*.py`: repeatable integration of templates and hooks.
- `packaging/`: graphical Windows setup, safe per-user installation, runtime lock and launcher.
- `scripts/package_windows.py`: self-contained Windows release packaging.
- `tests/`, `scripts/photocraft_*_smoke.py`: synthetic tests and official MCP validation.

## Image path

Remove uses 96–512 px of context; selection fill uses 256–1024 px. The context rectangle expands to multiples of 32 using existing canvas pixels, without changing the selection or resampling its contents. At an unaligned canvas boundary, transport replicates the missing outside border and removes that padding from the result. Edits keep their native pixel resolution by default; there is no 768 px cap. Preferences > GenGate exposes `reduceEditResolution`, off by default. Its economy mode keeps rectangles with both sides at most 768 px unchanged. Above that threshold the uniform scale is `max(0.5, sqrt(768 / aligned_longest_side))`: output size grows more slowly until 3072 px, then stays at half resolution. Content dimensions round upward to individual pixels; separate padding aligns the model input to 32, rather than stretching content to the grid. Restoration removes model-input padding before upsampling, then source-edge padding, and applies the original mask. Native output is converted back to the document's depth/profile. New layers keep the generated context rectangle plus a real editable layer mask. Queue entries capture tool/selection/target settings but sample source pixels at dispatch, after preceding results. Native context and Direct masked requests remain bounded to 16 Mi pixels; full-workspace generation retains its separate resolution controls.

Direct inpainting protocol 2 supplies an opaque neutral placeholder over the missing region in image 1, a separate binary edit mask as image 2, and an explicit latent `noise_mask` during sampling. Alpha remains image transparency. The Turbo schedule has eight Euler steps with CFG=1. The reference image latent preserves surrounding context. Tiny nonzero strokes survive max reduction to the latent grid. Tests mock only model components; real output quality remains unverified for this release.

AI layer variants are stored as opaque additional-layer metadata (`gsAI`, `gVxx`, `gTxx`) plus ordinary raster pixels/masks. This preserves galleries in native/PSD round trips while allowing other editors to display the selected raster result. The history snapshots share image blobs; galleries cap stored pixels and variant count.

The full-generation workspace uses `/studio/jobs`, with up to ten ordered references and a sequential batch of up to eight outputs. It starts from a zero target latent with no inpaint mask, and imports results as new layers in the source document. If no source document remains open, it creates a document containing only the generated layers. Native file preparation runs off the GUI thread; component discovery only inspects filenames. Model, engine, text-encoder and VAE choices are separate settings in Edit > Preferences > GenGate. See [the workspace preview](WORKSPACE.md).

## Lifecycle and installation

The host starts/stops only its own Direct worker and may attach to an external ComfyUI without taking ownership. Startup policies are independent for Direct and ComfyUI. Readiness probes do not load model weights. Settings and startup work run off the GUI thread; progress feeds persistent canvas masks and optional bars. Status is read-only and remains available during startup.

Setup installs GenGate separately from an existing PhotoCraft. The add-on contains checksum-bound BSDIFF deltas, without complete editor executables. Both editor and CLI are reconstructed locally from the exact supported upstream files. A recoverable transaction installs them at the original PhotoCraft paths and retains verified originals under `.gengate/backups`. Existing PhotoCraft shortcuts and its ordinary profile remain in use. GenGate runtime versions live in the separately selected GenGate folder; a small `runtime/config.photocraft.json` beside PhotoCraft points to that runtime. Existing external Python environments are inspected without importing torch and never modified. A new CUDA environment, when selected, is private to the add-on.

The PhotoCraft launcher checks for upstream file replacements. It reapplies a patch only when the complete base hashes match a supported catalog entry; unknown releases remain untouched, launch as ordinary PhotoCraft and need patch adaptation. Uninstall restores owned binaries and settings but preserves externally updated files and unrelated preferences. This is a local editor patch, not the official WASM filter API. Supported hosts: official PhotoCraft 0.8.2, 0.12.0 and 0.15.1 Windows x64. See [migration decisions](ADDON_MIGRATION.md).

Setup uses the single PhotoCraft name in Start and Desktop and backs up/removes obsolete editor launcher duplicates. Both shortcuts use the same stable GenGate launcher; uninstall repoints them to the ordinary editor executable. The 0.12 integration keeps shell commands in upstream's shared engine table, uses per-tool sampling options and propagates the new fallible Remove footprint API.

The host listens on an OS-assigned loopback port, rejects browser-origin requests and validates input sizes. Each runtime publishes its endpoint under `.runtime/genstudio/endpoint.json`; clients verify the protocol, version, runtime path and source fingerprint before forwarding settings. An OS lock prevents concurrent editors from starting duplicate hosts. Direct workers use separate ports and host-specific ownership IDs. Old services on ports 8766/8767 are never discovered or reused.

Full Studio output uses `gsST` layer metadata for the source/model prompts and dimensions. This is separate from selection-fill galleries (`gsAI`). Output surfaces are prepared on the worker in the destination colour format, then inserted atomically into the original document. The empty reference is filled through a separate bounded layer-capture job. Studio engine controls live in the native Preferences working copy, with Apply/OK/Cancel semantics.

## FLUX 3 Klein adapter

`genstudio.models` is the shared model registry for discovery, settings and dispatch. Qwen retains its original core and checkpoints; Klein uses the isolated ComfyUI v0.41.3 library under `.runtime/flux-core`. Its two accelerator dependencies live in a private `deps` overlay loaded only by the Flux Direct worker. Switching models restarts only the host-owned worker, and the readiness handshake checks model identity. No inference imports occur in the editor or discovery service.

The distilled Klein pipeline matches BFL's Comfy workflow: 16 Euler/simple steps, CFG=1, constant alpha=16, 128 latent channels at /16. Qwen3.5-9B expands prompts with the original Apache-2.0 BFL templates and the ordered references. Direct unloads/releases it before loading the image transformer, text encoder and Flux2 VAE. The Comfy adapter uses built-in nodes and checks for `flux3` CLIP support; it never silently falls back to Qwen or requires LanPaint for Klein. The existing outer pixel mask/compositing transaction remains authoritative for both editing tools.

Model limits and chip syntax are model data. Both current families support ten references. Klein chips compile to `<ref_image_0>` ... `<ref_image_9>`; Qwen chips compile to `image 1` ... `image 10`. Model changes migrate recognized chips without rewriting JSON provenance or filenames. Reference removal tracks stable image identities when renumbering. Generated layers retain the selected model and source/compiled prompts.

## Independent Direct segmentation

`photocraft/gengate_segmentation.rs` intercepts the existing Subject, Sky, Remove Background and opt-in Object Selection commands. A document-locking native job captures a color-managed input in bounded strips (maximum side 2048), invokes the existing cancellable file transport, maps the grayscale result back to the requested document rectangle and commits one history step. Empty masks preserve the previous selection without a history entry. Remove Background stores an ordinary layer mask.

Settings are nested under the backward-compatible `genStudio.segmentation` preference key; display names are GenGate. Generator configuration ignores segmentation-only changes when deciding whether its engine needs a restart. The segmentation checkbox and detector/segmenter selectors are a separate section in native Preferences. Enabling it prepares assets without importing torch.

`genstudio.segmentation` prepares hash-pinned downloads and private Ultralytics/CLIP modules, then starts one short-lived GPU process through `scripts/run_segmentation.py`. YOLO-World supplies a salient foreground box to interactive SAM3; Sky uses the SAM3 semantic predictor. Object Selection is cropped before inference, so no output can escape its rectangle. No ComfyUI server, generator host or model is involved. Cancellation terminates the owned GPU child; errors retain the document. Native masks keep full document resolution and the source document color/depth is unchanged. Real model quality and memory use remain unverified.

Direct generation and selection share only a cancellable per-user GPU lease. They keep separate processes, model settings and Python dependencies. The generation worker offloads cached models before releasing the lease, including after inference errors; selection releases its GPU allocations by exiting. Waiting selection requests can be cancelled before importing GPU dependencies or opening weights. The process using CUDA owns the lease; the OS releases it if that worker crashes. This coordination does not control unrelated external ComfyUI jobs.

Display-name migration preserves command IDs, profile keys and gsST/gsAI metadata. Source preparation de-duplicates generated menu/registry entries and is content-idempotent after formatting.

The floating Ai Fill prompt owns delete keys independently of pointer hover. A held Backspace/Delete retains its text-field owner through a focus transition until release. Pure delete frames bypass canvas shortcut dispatch; accumulated canvas Clear autorepeats are coalesced within one frame while separate presses keep their order. Selection bounds and reusable AI-layer checks compare COW mask tile identities after pixel-only document changes, avoiding repeated mask scans.

FIBO 2.0 Edit Turbo has an independent preset and pinned model library under `.runtime/fibo-core`, plus a private accelerator overlay. It reuses the Qwen3-VL-4B encoder and FLUX.2 VAE in the selected Models folder. Direct uses the native FIBO encoder, four Euler/simple steps and CFG 1; no remote prompt service is involved. The preset accepts one to four references with `<image_1>` numbering. Native ComfyUI support uses `TextEncodeFibo2`; model weights remain separate downloads.

The LoRA Explorer uses a bounded background directory command rooted at `models/loras`. Canonical paths reject escapes, including links leading outside the root. The browser virtualizes rows; aliases, trigger words and enabled strengths live in the editor UI profile, keyed by model-source folder, without changing checkpoint files. Studio adapter stacks are independent of Constant LoRA. Direct patches cloned model/encoder objects per request, and ComfyUI inserts the equivalent ordered loader chain. The cached base models remain unchanged.

Generated-layer `gsST` metadata retains portable adapter paths and strengths alongside its prompt and preset; older layers default to no adapters. Both native and PSD saves preserve the record. An ordinary layer drop sets its rendered image and clears the Studio recipe. A generated layer drop restores its result, prompt, preset and adapters. Drops into extra reference slots import only the rendered pixels. Source extraction runs as a background command against the original document/layer identity and does not move or reorder the document layer.

## Studio ComfyUI workflows

The ComfyUI engine selects Studio workflow processing directly; `studioWorkflow` retains its file choice across engine changes. `studioMode` remains accepted only for preference compatibility. The native worker snapshots the API graph and forwards visible Studio values and reference slots, without capturing hidden document/layer/selection sources. Studio references retain their existing preview preparation; Python applies no additional preset grid/resize or reference-count restriction to workflows.

`genstudio.comfy_workflow` validates and snapshots the graph, uses normal Comfy uploads and injects a request-scoped `gengate-comfy-v2` envelope through `extra_data.extra_pnginfo`. `comfyui/GenGate` supplies widget-free IN and OUT nodes. IN exposes individual references/masks and Studio values; an optional GenGate Apply LoRAs node consumes its JSON recipe and chains ComfyUI LoraLoader with the graph's model/optional CLIP; OUT writes temporary PNGs and history records. Endpoint cache probes always invalidate, because hidden metadata is not available to every Comfy cache probe. The host imports only expected OUT node IDs with the matching request identity and validates files, dimensions, masks and recipe fields. No callback listener or fixed sampler/model/output-node ID is added to user workflows.

Workflow jobs execute the whole graph once. Its output determines result count and dimensions, bounded to eight images and 64 million pixels in total. Native file transport returns explicit per-image records and masks. New layers preserve names and prompt/seed/adapter/workflow-path/hash metadata; a batch is one undo step. The graph stays in the chosen file. Existing masked-edit transport and preset Comfy graph builders remain the quick tools' path.

Node installation copies only managed files and examples, preserves unknown files and rejects locally modified owned files. A GenGate-owned workflow server enables user custom nodes; engine changes restart only its owned process. Retained old API sockets migrate by name; the v2 IN class id prevents old canvas connections being silently reinterpreted. An external server is inspected, never restarted or globally interrupted. Source/model folders and the selected Direct Python are reused without downloading weights or changing dependencies.
