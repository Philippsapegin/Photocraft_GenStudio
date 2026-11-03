# GenGate 0.9.7 Beta

Windows x64 add-on for official **PhotoCraft 0.20.1**.

- Updated native integration for PhotoCraft 0.20.1, including pooled image buffers, selected colour-channel sets, Remove Brush target restrictions and the revised update dialog. The GenGate compatibility notice remains visible before starting a PhotoCraft update.
- Redesigned Setup as a frameless dark window with purple accents, custom controls and a persistent installation status, matching PhotoCraft's Studio theme.
- Fixed Browse failing before the dialog opens when its path field is empty. Folder browsing now uses an owned STA Explorer dialog with a compatibility fallback; failures leave paths editable and allow another attempt.
- Fixed a false failed status after "Installed": the previous launcher could lose a successful child's exit code. Setup now reads the actual exit code after all output is flushed. Optional shortcut or model-folder checks produce an explicit installed-with-notes result; actual installation failures retain a failed status and diagnostic log.

Install PhotoCraft 0.20.1 separately, close it, then extract the complete GenGate ZIP and run **Setup.cmd**. Existing users should select their current PhotoCraft and GenGate directories. Setup verifies the official editor hashes and preserves settings and model paths. This package contains binary patches and the local host; install the editor and model files separately.

The Direct quantized Embedding fix and local connection diagnostics from 0.9.6 are retained. The previously reported transient Direct startup timeout remains unconfirmed; its original cause has not been established.

Validation uses synthetic workers and tiny CPU tensor fixtures. Real model inference, image quality and GPU memory behavior are outside automated release checks. Detailed results: [validation report](https://github.com/Philippsapegin/Photocraft_GenStudio/blob/v0.9.7/docs/VALIDATION.md).
