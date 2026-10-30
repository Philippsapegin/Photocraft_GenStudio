# GenGate 0.9.5 Beta

- Support official PhotoCraft **0.17.0 Windows x64** with verified editor and CLI patches.
- Add native GenGate update checks and a compatibility warning before PhotoCraft's own updater replaces the editor controls.
- Studio's **Neg** button splits the prompt area. Generated layers retain both texts through save/reopen and recipe restoration; ComfyUI IN exposes `negative_prompt` and OUT no longer requires prompt text.
- ComfyUI Studio runs a fresh snapshot of the active browser graph, including unsaved changes. Ai Fill and Remove Brush use separate workflow files in Advanced.
- Explicit model-component choices override only loaders titled `GenGate: Model`, `GenGate: Text encoder`, `GenGate: VAE` or `GenGate: Prompt upsampler` in the submitted graph copy. Automatic retains the workflow values. Saved files and the browser graph remain unchanged.
- Add an optional step count beside Constant LoRA; an empty field keeps the preset's default.
- Explain disabled Generate, including FIBO Edit Turbo's source-image requirement. Distinguish the running Direct service from model loading and loaded components; starting the service does not load weights.

Download **GenGate-0.9.5-windows-x64-addon.zip**, verify **SHA256SUMS.txt**, extract and run **Setup.cmd** with PhotoCraft closed. Update into the existing GenGate folder. This package contains binary deltas, its runtime and bridge nodes, with no complete PhotoCraft editor or model weights.

This package targets PhotoCraft **0.17.0**. For official PhotoCraft 0.16.0, use [GenGate 0.9.4](https://github.com/Philippsapegin/Photocraft_GenStudio/releases/tag/v0.9.4). Unknown editor builds are rejected without downgrading them.

After updating, restart ComfyUI and refresh its browser tab to load the new nodes and active-graph extension. Recreate old IN nodes to expose the new sockets; reconnect OUT without the removed prompt input. Use GenGate Apply LoRAs to apply Explorer selections to the graph's model and optional CLIP. See [workflow setup and migration](https://github.com/Philippsapegin/Photocraft_GenStudio/blob/v0.9.5/docs/COMFY_WORKFLOWS.md).

**Known issue:** Direct FIBO loading has a reported `_apply(): Couldn't swap Embedding.weight` failure. That fix is being investigated separately after this release. FIBO requires a source image; Qwen and FLUX also support text-only Studio requests.

Validation uses synthetic workers and CPU-only image graphs: native engine/UI coverage, optimized builds, Clippy, WASM, workflow transport, recipe persistence, readiness states and installer update/restore cycles. Real model inference, output quality and GPU memory use have not been validated. Seven rendering integration failures also reproduce on the clean official 0.17.0 base; details are recorded in [validation](https://github.com/Philippsapegin/Photocraft_GenStudio/blob/v0.9.5/docs/VALIDATION.md).
