# Architecture

The native Rust integration adds controls to PhotoCraft and sends local HTTP requests through a small Python host. It uses the editor's command registry, background jobs, document history, color conversion and layer-mask APIs. The existing WASM plug-in sandbox is unchanged. PhotoCraft's official MCP interface remains available for automation and validation.

```text
PhotoCraft native tools and Gen Studio settings
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

The unreleased full-generation workspace uses `/studio/jobs`, with up to ten ordered references and a sequential batch of up to eight outputs. It starts from a zero target latent with no inpaint mask, and imports results as new documents. Native file preparation runs off the GUI thread; component discovery only inspects filenames. Model, engine, text-encoder and VAE choices are separate settings. See [the workspace preview](WORKSPACE.md).

## Lifecycle and installation

The host starts/stops only its own processes and attaches to an existing engine without taking ownership. Startup policies are independent for Direct and ComfyUI. Readiness probes do not load model weights. Settings and startup work run off the GUI thread; progress feeds persistent canvas masks and optional bars.

Setup verifies the release manifest, chooses existing shared models and writes machine-local paths only into the installation. Executables/runtime are placed in a new version subfolder on each install. The stable launcher sets `PHOTOCRAFT_CONFIG_DIR` to the installation's `Profile`; updates preserve unrelated preferences and old executables. Existing external Python environments are inspected without importing torch and never modified. A new CUDA environment, when selected, is private to that installed version.

The host listens on loopback, rejects browser-origin requests and validates input sizes. This alpha supports one shared host configuration per computer at a time; simultaneous independent installations are not isolated inference servers.
