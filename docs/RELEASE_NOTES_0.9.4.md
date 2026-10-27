# GenGate 0.9.4 Beta

- ComfyUI Studio has one clickable workflow field with the muted `workflow goes here` placeholder. Direct shows no workflow control.
- GenGate IN exposes Studio values: image/mask, prompt, dimensions, seed, batch, LoRAs and individual additional reference/mask pairs. Removed model components, hidden document/layer/selection sources, duplicate reference lists, count and preset sockets. Models, encoders and VAE stay in the graph.
- **GenGate Apply LoRAs** applies Explorer selections in order with their strengths. Connect `IN.loras` and the graph's model/optional CLIP to this helper.
- LoRA Explorer uses PhotoCraft's native SVG folder icon. Object Selection no longer shows the extra instruction in its options bar.

This Windows x64 add-on targets the official **PhotoCraft 0.16.0** portable binaries. Its editor/CLI patches were rebuilt and verified for that base. Earlier bases remain available in [GenGate 0.9.3](https://github.com/Philippsapegin/Photocraft_GenStudio/releases/tag/v0.9.3).

Download **GenGate-0.9.4-windows-x64-addon.zip**, verify it against **SHA256SUMS.txt**, extract it and run **Setup.cmd** with PhotoCraft closed. Existing installations can update into the same GenGate folder. The package contains no complete editor or model weights.

After updating, restart ComfyUI. Recreate IN in older ComfyUI canvas graphs, reconnect its retained sockets and Export (API) again. Existing API JSON links for retained sockets migrate by name; removed sockets produce a re-export message. OUT's sockets are unchanged. Share the same relative LoRA filenames with ComfyUI, then connect the helper where the adapters should be applied.

Validation includes native engine/UI tests, release build, clippy, panic hunt, layers and WASM; actual CPU-only ComfyUI image transport; synthetic native workflow/LoRA checks; and the separate add-on's installation/update/restore cycle. No model inference was performed.

See [workflow setup and ports](https://github.com/Philippsapegin/Photocraft_GenStudio/blob/v0.9.4/docs/COMFY_WORKFLOWS.md), [installation and models](https://github.com/Philippsapegin/Photocraft_GenStudio/blob/v0.9.4/README.md) and [validation](https://github.com/Philippsapegin/Photocraft_GenStudio/blob/v0.9.4/docs/VALIDATION.md).
