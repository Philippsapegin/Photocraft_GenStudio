# Third-party runtime code

| Local path | Source | License | Use |
| --- | --- | --- | --- |
| `.runtime/qwen-core` (generated, not committed) | [ComfyUI, Comfy Org and contributors](https://github.com/Comfy-Org/ComfyUI/tree/b0f4b7b294ce482a2e071d9d762c133d38c7aa07) | GPL-3.0; original `LICENSE` exported alongside code | Checkpoint loading, Qwen architecture, quantized kernels and sampling in the separate Python worker. No UI/server code or model weights is exported. |
| `.runtime/photocraft-source` (build input) | [PhotoCraft v0.8.2, storytold and contributors](https://github.com/storytold/photocraft/tree/d43db3e153c76aefce9b32cef9099e458eaf17a8) | MIT OR Apache-2.0; upstream license files retained | Native editor and MCP-compatible engine/settings integration. |

Model files remain in the user-selected source directory and are not redistributed by this project. Their license is provided by [Qwen](https://huggingface.co/Qwen/Qwen-Image-2.1-Turbo).

The original GenGate menu catalogs `menu-locales.json`, `progress-locales.json` and `selection-locales.json` are by GenGate contributors, licensed MIT OR Apache-2.0 with the Rust integration. The preparer adds their entries to PhotoCraft's catalogs and records them in its `ATTRIBUTION.md`, retaining existing translation licenses.

The current native build targets [PhotoCraft v0.16.0](https://github.com/storytold/photocraft/tree/b1794c6e0608ccfc887213dcf58398977c5cb75e), under the same MIT OR Apache-2.0 licenses. Supported official release hashes are recorded in `editor-bases.json`; native source and font inputs remain outside the add-on archive.
