# GenGate 0.9.2 Beta

GenGate is still beta software. This release installs as a separate add-on over an existing PhotoCraft installation.

- Supports official PhotoCraft 0.15.1 Windows x64, pinned at `60c87b9884fdae0e464614d482a3bc435cbbc578`.
- Includes the user-tested FIBO 2.0 Edit Turbo preset, Studio LoRA explorer and layer recipe restoration.
- Compact window headers, Preferences-style LoRA checkboxes, table dividers, left-aligned names and visible drag feedback. Only saved trigger words are inserted.
- Setup preserves the selected FIBO preset, per-preset components and Constant LoRA strengths, segmentation and economy-mode settings.

Install PhotoCraft separately, close it and run **Setup.cmd** from the extracted **GenGate-0.9.2-windows-x64-addon.zip**. Reuse the existing GenGate installation and Models folders. The add-on contains verified version-specific binary deltas and its local host, no complete editor or model weights. Earlier official 0.8.2 and 0.12.0 patch entries are retained. Unknown editor builds are never patched or downgraded.

Validation uses synthetic images and isolated profiles; real model inference is deliberately excluded. See [VALIDATION.md](VALIDATION.md) for results.
