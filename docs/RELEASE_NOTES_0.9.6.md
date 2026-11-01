# GenGate 0.9.6 Beta

Windows x64 add-on for official **PhotoCraft 0.18.0**.

- Updated native integration for PhotoCraft 0.18.0, including its revised updater transport. PhotoCraft's updater, GenGate compatibility notice and separate add-on release checks remain available.
- Fixed Direct quantized Embedding conversion failures such as `_apply(): Couldn't swap Embedding.weight`. The compatibility adapter uses the private model core's quantized transfer implementation; it does not modify an existing ComfyUI installation.
- Local control and Direct HTTP requests bypass system proxies. Readiness failures now retain connection errors, mismatched worker settings and dependency problems in status, timeout messages and the host error log.

The previously reported transient startup timeout is not currently reproducible. The new diagnostics make a recurrence actionable; proxy routing has not been established as the cause of that incident.

Install PhotoCraft 0.18.0 separately, close it, then extract the GenGate ZIP and run **Setup.cmd**. Existing users should select their current PhotoCraft and GenGate directories. Setup verifies the official editor hashes, preserves settings/model paths and refuses unknown builds. This package contains binary patches and the local host, not a complete PhotoCraft editor or model weights.

The public 0.9.5 archive remains unchanged. Its release notes document the Embedding issue present in that older archive.

Validation uses synthetic workers and tiny CPU tensor fixtures. Real model inference, image quality and GPU memory behavior are not part of automated release checks. Detailed results: [validation report](https://github.com/Philippsapegin/Photocraft_GenStudio/blob/v0.9.6/docs/VALIDATION.md).
