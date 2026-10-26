# GenGate 0.9.3 Beta (development)

- Supports the official PhotoCraft 0.16.0 Windows x64 portable release, pinned at `b1794c6e0608ccfc887213dcf58398977c5cb75e`.
- Preserves native adjustment shortcuts while keeping the floating-prompt Backspace/autorepeat protection.
- Adapts the new retouch dispatcher so successive AI Remove strokes keep their queue and mask feedback.
- Remains a separate add-on; existing preferences, models and documents are reused.
- Studio can run a custom ComfyUI API workflow using GenGate IN / OUT. The workflow controls models, sampling, batch output and result dimensions. Images and optional masks are imported as new layers with prompt/seed/adapter/workflow metadata.
- Setup installs the bridge for ComfyUI; Preferences also has an Install / Update nodes button. Native Remove Brush and quick Ai Fill keep using the preset workflow.

See [workflow setup and port reference](COMFY_WORKFLOWS.md).

No model inference is required for compatibility validation.
