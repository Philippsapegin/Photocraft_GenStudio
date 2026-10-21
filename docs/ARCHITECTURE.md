# Architecture

The native Rust integration adds controls to PhotoCraft and sends local HTTP requests through a small Python host. It uses the editor's command registry, background jobs, document history, color conversion and layer-mask APIs. The existing WASM plug-in sandbox is unchanged. PhotoCraft's official MCP interface remains available for automation and validation.

```text
PhotoCraft native tools and GenGate settings
                   |
        background control / edit client
                   |
         Python host 127.0.0.1:8766
            /                  \
   Direct worker :8767      local ComfyUI
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
- `scripts/package_windows.py`: self-contained Windows alpha packaging.
- `tests/`, `scripts/photocraft_*_smoke.py`: synthetic tests and official MCP validation.

## Image path

Remove uses 96–512 px of context; selection fill uses 256–1024 px. Context is clipped to the canvas and sent at no more than 768 px on its longest side. Native output is converted back to the document's depth/profile and applied through its original mask. New layers keep the generated context rectangle plus a real editable layer mask. Queue entries capture tool/selection/target settings but sample source pixels at dispatch, after preceding results.

Direct inpainting protocol 2 supplies an opaque neutral placeholder over the missing region in image 1, a separate binary edit mask as image 2, and an explicit latent `noise_mask` during sampling. Alpha remains image transparency. The Turbo schedule has eight Euler steps with CFG=1. The reference image latent preserves surrounding context. Tiny nonzero strokes survive max reduction to the latent grid. Tests mock only model components; real output quality remains unverified for this release.

AI layer variants are stored as opaque additional-layer metadata (`gsAI`, `gVxx`, `gTxx`) plus ordinary raster pixels/masks. This preserves galleries in native/PSD round trips while allowing other editors to display the selected raster result. The history snapshots share image blobs; galleries cap stored pixels and variant count.

The full-generation workspace uses `/studio/jobs`, with up to ten ordered references and a sequential batch of up to eight outputs. It starts from a zero target latent with no inpaint mask, and imports results as new layers in the source document. If no source document remains open, it creates a document containing only the generated layers. Native file preparation runs off the GUI thread; component discovery only inspects filenames. Model, engine, text-encoder and VAE choices are separate settings in Edit > Preferences > GenGate. See [the workspace preview](WORKSPACE.md).

## Lifecycle and installation

The host starts/stops only its own processes and attaches to an existing engine without taking ownership. Startup policies are independent for Direct and ComfyUI. Readiness probes do not load model weights. Settings and startup work run off the GUI thread; progress feeds persistent canvas masks and optional bars.

Setup verifies the release manifest, chooses existing shared models and writes machine-local paths only into the installation. Executables/runtime are placed in a new version subfolder on each install. The stable launcher sets `PHOTOCRAFT_CONFIG_DIR` to the installation's `Profile`; updates preserve unrelated preferences and old executables. Existing external Python environments are inspected without importing torch and never modified. A new CUDA environment, when selected, is private to that installed version.

The host listens on loopback, rejects browser-origin requests and validates input sizes. This alpha supports one shared host configuration per computer at a time; simultaneous independent installations are not isolated inference servers.

Full Studio output uses `gsST` layer metadata for the source/model prompts and dimensions. This is separate from selection-fill galleries (`gsAI`). Output surfaces are prepared on the worker in the destination colour format, then inserted atomically into the original document. The empty reference is filled through a separate bounded layer-capture job. Studio engine controls live in the native Preferences working copy, with Apply/OK/Cancel semantics.

## FLUX 3 Klein adapter

`genstudio.models` is the shared model registry for discovery, settings and dispatch. Qwen retains its original core and checkpoints; Klein uses the isolated ComfyUI v0.41.3 library under `.runtime/flux-core`. Its two accelerator dependencies live in a private `deps` overlay loaded only by the Flux Direct worker. Switching models restarts only the host-owned worker, and the readiness handshake checks model identity. No inference imports occur in the editor or discovery service.

The distilled Klein pipeline matches BFL's Comfy workflow: 16 Euler/simple steps, CFG=1, constant alpha=16, 128 latent channels at /16. Qwen3.5-9B expands prompts with the original Apache-2.0 BFL templates and the ordered references. Direct unloads/releases it before loading the image transformer, text encoder and Flux2 VAE. The Comfy adapter uses built-in nodes and checks for `flux3` CLIP support; it never silently falls back to Qwen or requires LanPaint for Klein. The existing outer pixel mask/compositing transaction remains authoritative for both editing tools.

Model limits and chip syntax are model data. Both current families support ten references. Klein chips compile to `<ref_image_0>` ... `<ref_image_9>`; Qwen chips compile to `image 1` ... `image 10`. Model changes migrate recognized chips without rewriting JSON provenance or filenames. Reference removal tracks stable image identities when renumbering. Generated layers retain the selected model and source/compiled prompts.

## Independent Direct segmentation

`photocraft/gengate_segmentation.rs` intercepts the existing Subject, Sky, Remove Background and opt-in Object Selection commands. A document-locking native job captures a color-managed input in bounded strips (maximum side 2048), invokes the existing cancellable file transport, maps the grayscale result back to the requested document rectangle and commits one history step. Empty masks preserve the previous selection without a history entry. Remove Background stores an ordinary layer mask.

Settings are nested under the backward-compatible `genStudio.segmentation` preference key; display names are GenGate. Generator configuration ignores segmentation-only changes when deciding whether its engine needs a restart. The segmentation checkbox and detector/segmenter selectors are a separate section in native Preferences. Enabling it prepares assets without importing torch.

`genstudio.segmentation` prepares hash-pinned downloads and private Ultralytics/CLIP modules, then starts one short-lived GPU process through `scripts/run_segmentation.py`. YOLO-World supplies a salient foreground box to interactive SAM3; Sky uses the SAM3 semantic predictor. Object Selection is cropped before inference, so no output can escape its rectangle. No ComfyUI server, generator host or model is involved. Cancellation terminates the owned GPU child; errors retain the document. Native masks keep full document resolution and the source document color/depth is unchanged. Real model quality and memory use remain unverified.

Display-name migration preserves command IDs, profile keys and gsST/gsAI metadata. Source preparation de-duplicates generated menu/registry entries and is content-idempotent after formatting.
