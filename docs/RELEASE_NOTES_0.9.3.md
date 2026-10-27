# GenGate 0.9.3 Beta

- Supports the official PhotoCraft 0.16.0 Windows x64 portable release, pinned at `b1794c6e0608ccfc887213dcf58398977c5cb75e`.
- Preserves native adjustment shortcuts while keeping the floating-prompt Backspace/autorepeat protection.
- Adapts the new retouch dispatcher so successive AI Remove strokes keep their queue and mask feedback.
- Remains a separate add-on; existing preferences, models and documents are reused.
- Studio can run a custom ComfyUI API workflow using GenGate IN / OUT. The workflow controls models, sampling, batch output and result dimensions. Images and optional masks are imported as new layers with prompt/seed/adapter/workflow metadata.
- Setup installs the bridge for ComfyUI; Preferences also has an Install / Update nodes button. Native Remove Brush and quick Ai Fill keep using the preset workflow.

Download **GenGate-0.9.3-windows-x64-addon.zip**, extract it and run **Setup.cmd** with PhotoCraft closed. Install PhotoCraft separately; this is an add-on, with no complete editor or model weights bundled. Existing installations can update into the same GenGate folder.

The new Studio workflow UI requires PhotoCraft 0.16.0. Choose ComfyUI in GenGate Settings, install/update the nodes, then open a ComfyUI **Export (API)** JSON from the Studio's **Processing → ComfyUI workflow** controls.

See [workflow setup and ports](https://github.com/Philippsapegin/Photocraft_GenStudio/blob/v0.9.3/docs/COMFY_WORKFLOWS.md), [installation and models](https://github.com/Philippsapegin/Photocraft_GenStudio/blob/v0.9.3/README.md) and [validation](https://github.com/Philippsapegin/Photocraft_GenStudio/blob/v0.9.3/docs/VALIDATION.md).
