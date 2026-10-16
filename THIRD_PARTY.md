# Third-party notices

This is an independent Gen Studio integration, not an official release or endorsement by the PhotoCraft/ArtCraft team or Qwen.

| Component | Source / pin | License / distribution |
| --- | --- | --- |
| PhotoCraft | [v0.8.2, d43db3e153c76aefce9b32cef9099e458eaf17a8](https://github.com/storytold/photocraft/tree/d43db3e153c76aefce9b32cef9099e458eaf17a8) | MIT OR Apache-2.0. Build input is fetched separately; native integration changes are provided here as templates and repeatable patches. Release packages retain the upstream license, NOTICE, attribution and embedded-asset notices. |
| Comfy checkpoint-compatible model library | [b0f4b7b294ce482a2e071d9d762c133d38c7aa07](https://github.com/Comfy-Org/ComfyUI/tree/b0f4b7b294ce482a2e071d9d762c133d38c7aa07) | GPL-3.0. The separate worker uses source under `runtime/.runtime/qwen-core`, with the original license and revision metadata. No ComfyUI server/frontend/node graph is started in Direct mode. |
| LanPaint | [2d7912f9a5efe5ece8de334c7ca18317b8288c39](https://github.com/scraed/LanPaint/tree/2d7912f9a5efe5ece8de334c7ca18317b8288c39) | GPL-3.0. Original source and license bundled under `runtime/third_party/LanPaint`; copied into ComfyUI only when requested and absent. |
| CPython | [3.13.16](https://www.python.org/downloads/release/python-31316/) | PSF license and included third-party terms; original `LICENSE.txt` retained. |
| Pillow, websocket-client, pip | Exact wheel URLs and SHA-256 values in [packaging/runtime-lock.json](packaging/runtime-lock.json) | Their original license files remain in the bundled distribution metadata. Pillow uses its HPND license; websocket-client is Apache-2.0; pip is MIT and includes separately licensed vendored dependencies. |
| Optional CUDA PyTorch and Direct dependencies | [PyTorch](https://pytorch.org/), `photocraft/direct-requirements.txt` | Downloaded from the original software package sources only when the user selects a new Direct runtime. Not bundled in the alpha ZIP. |
| Qwen model checkpoints | [Qwen Image 2.1 Turbo](https://huggingface.co/Qwen/Qwen-Image-2.1-Turbo), [Comfy Org files](https://huggingface.co/Comfy-Org/Qwen-Image-2.1) | Separate model license; never redistributed or downloaded by the installer. Consult the model card and publisher's license. |

The Gen Studio Python host, adapters and installer are GPL-3.0-or-later; see [LICENSE](LICENSE). Original Rust integration and localization catalogs are MIT OR Apache-2.0, matching PhotoCraft. The PhotoCraft kitsune application icon is covered by its upstream MIT OR Apache-2.0 asset license. The restricted ArtCraft marketing logos from `docs/brand` are not included in this repository or Windows package.

The source for the separate GPL components is shipped as Python source in the release archive; exact upstream source revisions are linked above. This project does not relicense upstream code, dependency binaries, model weights or trademarks.
