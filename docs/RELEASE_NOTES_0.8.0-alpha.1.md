# GenGate for PhotoCraft 0.8.0 - Alpha

Gen Studio is now **GenGate**. Open the workspace from **Filter > Plug-ins > GenGate** and configure it in **Edit > Preferences > GenGate Settings**. Existing profiles, command IDs and generated-layer metadata remain compatible.

- **Qwen Image 2.1 Turbo and FLUX 3 Image Klein**, with independent Direct / ComfyUI selection, shared model discovery, automatic text encoder/VAE selection and model-specific reference chips.
- **Full image generation:** prompt, main reference and nine extra references, batches, text-only resolution/aspect controls and persistent creation prompts. Every output becomes a new layer; without a document, a new one is created.
- **Workspace fixes:** adaptive canvas/prompt layout, theme-aware buttons and file-drop outlines, a left-aligned `GenGate | Model` title, and Use selection over a populated main reference.
- **Independent Direct segmentation:** YOLO-World v2 S/M/L + SAM3. Enable it in GenGate Settings to replace Select Subject, Sky and Remove Background. Object Selection gains **Ai Select**, which submits rectangle drags and ignores clicks. Native sampling options, modifiers, masks and Undo/Redo are preserved.
- **Model preparation:** the default YOLO-World S + SAM3 set downloads when segmentation is enabled, into the shared Models folder (about 3.48 GB). Downloads resume and verify SHA-256. A private SDK overlay preserves an existing ComfyUI Python.
- **Windows setup:** a separate GenGate shortcut and profile; updates retain the chosen model, component overrides, startup policy and segmentation settings.

Extract the complete Windows ZIP and run **Setup.cmd**. The ZIP includes the modified PhotoCraft editor, matching official MCP CLI, lightweight host and supporting source/licenses. Generative and segmentation model weights are separate downloads. See the [README](https://github.com/Philippsapegin/Photocraft_GenStudio/blob/v0.8.0-alpha.1/README.md) for model links and setup.

Validation uses unit tests, synthetic images and the official PhotoCraft MCP bridge. **No real generation or segmentation inference was run.** Actual model quality, memory use and inference speed remain unverified. This is an unsigned Windows x64 alpha based on PhotoCraft 0.8.2, independently maintained; it is not an official PhotoCraft release.

See [validation details](https://github.com/Philippsapegin/Photocraft_GenStudio/blob/v0.8.0-alpha.1/docs/VALIDATION.md) and [third-party notices](https://github.com/Philippsapegin/Photocraft_GenStudio/blob/v0.8.0-alpha.1/THIRD_PARTY.md).
