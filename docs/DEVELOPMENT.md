# Development

The public beta is Windows x64. The source repository contains the Python host and native integration templates, not a second copy of PhotoCraft or model weights.

## Host tests

Python 3.10+:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests use synthetic images and mock model components. Five tensor-level tests skip when torch is unavailable. With a CUDA Python that already has torch installed, they can also be run on CPU without loading any weights:

```powershell
$env:CUDA_VISIBLE_DEVICES = ''
# Replace the path with your GPU Python. No real generation is submitted.
& '<GPU-Python>\python.exe' -c "import os,sys; sys.path.insert(0,os.getcwd()); import unittest; suite=unittest.defaultTestLoader.discover('tests',pattern='test_direct_inpaint.py'); result=unittest.TextTestRunner(verbosity=2).run(suite); sys.exit(not result.wasSuccessful())"
```

## Build the native editor

Install Git, Rust **1.95+**, MSVC Build Tools with the Windows SDK, and PhotoCraft's native build prerequisites from its [pinned development guide](https://github.com/storytold/photocraft/blob/b1794c6e0608ccfc887213dcf58398977c5cb75e/docs/development.md). Then:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Build-PhotoCraft.ps1
```

This builds pinned PhotoCraft v0.16.0 at `b1794c6e0608ccfc887213dcf58398977c5cb75e` in `.runtime/photocraft-0.16.0`, applies the templates and hooks, formats Rust and builds both executables with HEIF support. Pass `-HostVersion 0.12.0` or `-HostVersion 0.8.2` for older supported bases; the latter uses `.runtime/photocraft-source`; `-TargetDir` can reuse an existing Cargo cache. The preparer rejects changed integration points rather than silently patching another upstream version and can be rerun. Upstream remains under MIT OR Apache-2.0. Read its `AGENTS.md` and contribution instructions before editing upstream code.

For 0.12.0, 0.15.1 and 0.16.0 the script also obtains the official release's `craft-fonts` revision `abb83316d96aa59c1cf64784289e378fe9fa5695`, verifies the four font hashes and sets `CRAFT_FONTS_DIR` with `CRAFT_FONTS_REQUIRED=1`. This preserves the editor's embedded Japanese text fallbacks. Font files stay in the ignored build cache; the add-on retains their OFL copyright/license notices.

The locally reconstructed patched binaries contain neutral compiled defaults. On startup they read `runtime/config.photocraft.json` next to the existing PhotoCraft executable; saved PhotoCraft preferences then take precedence. Setup points these settings at the separately installed GenGate runtime. No developer paths or personal settings belong in a release.

Targeted native checks after changing the templates:

```powershell
$env:GENSTUDIO_NO_PROCESS = '1'
cargo test --manifest-path .runtime/photocraft-0.16.0/Cargo.toml -p photocraft-engine --lib genstudio::
cargo clippy --manifest-path .runtime/photocraft-0.16.0/Cargo.toml -p photocraft-engine --all-targets -- -D warnings
```

Follow upstream's broader checks when changing commands, document structures or UI. Native validation uses engine/UI tests and synthetic official-MCP checks. The older 0.12.0 suite had two unrelated localization assertion failures; current results and any limitations are recorded in [VALIDATION.md](VALIDATION.md).

The `photocraft_*_smoke.py` scripts launch an isolated editor/profile and synthetic HTTP backend. Pass `--exe` when the executable is outside the default build folder; `PHOTOCRAFT_CLI` can select a matching CLI executable. Do not use their explicit real-backend start flags unless you intend to start that service. No release validation requires real inference.

### Windows layout-switch regression

PhotoCraft 0.12.0 automatically initialized wgpu's WGL backend even when rendering
through a native backend. Its hidden WGL window lives on a thread that waits on a
channel without pumping Windows messages. A `WM_INPUTLANGCHANGEREQUEST`, including
the one sent by NN Switch after text correction, can then block the main window
while Windows notifies that hidden window. The 0.12 preparer excludes unused GL
initialization from Automatic when DX12 adapters exist. Intel retains its native
DX12 protection; explicit backend overrides and GL-only fallback remain available.

To exercise both actual NN Switch modes, keep NN Switch running with its saved
English/Russian bindings and run on Windows:

```powershell
.venv\Scripts\python.exe scripts/photocraft_layout_smoke.py --exe '<PhotoCraft>/photocraft.exe' --cli '<PhotoCraft>/photocraft-cli.exe'
```

This uses a disposable profile, a white test document, real keyboard input and the
official MCP bridge. It checks both conversion directions, repeated Backspace with
the pointer outside the prompt, unchanged document data and continued response.
Engine startup and inference are disabled. It briefly takes focus; do not type in
another application during the check. Reports/screenshots stay in `.runtime`.

## Package the Windows ZIP

```powershell
python scripts/build_editor_patch.py --original .runtime/official/0.16.0/photocraft-0.16.0-windows-x64-portable --modified .runtime/photocraft-0.16.0/target/release --output .runtime/addon-patches --host-version 0.16.0 --upstream b1794c6e0608ccfc887213dcf58398977c5cb75e
python scripts/package_windows.py --patches .runtime/addon-patches
```

Obtain the official Windows portable archive recorded in `photocraft/editor-bases.json`, verify its archive hash and extract the original editor/CLI into `.runtime/official/<version>`. Select the actual Cargo output directory with `--modified`. The patch builder checks both input file hashes against that record, builds deltas and verifies exact forward reconstruction; existing entries for other supported bases are retained. New upstream versions require porting and validating the native integration, recording new official hashes and building another catalog entry; relabeling an old patch is not compatibility.

The packager obtains the pinned Comfy model library and LanPaint **code**, and the SHA-256-pinned embedded CPython / host wheels from `packaging/runtime-lock.json`. It never obtains model weights. Use `--core` and `--lanpaint` to reuse existing pinned source checkouts; `--upstream` selects the licensed source build. The result is in `dist/`, along with `SHA256SUMS.txt`.

The archive contains binary deltas and their catalog, local host, Python runtime, original source for the separate GPL libraries, notices, a manifest with per-file SHA-256 values, graphical setup and an uninstall entry point. Complete PhotoCraft editor/CLI files are forbidden by package verification. Host wheel distributions, including the BSD-licensed `bsdiff4` decoder, keep their license files.

The packager refuses to reuse an existing staging directory. Move a previous staging directory aside before rebuilding. Compare the manifest and run setup with a temporary destination and `--no-shortcuts` for automated verification. Do not include `config.local.json`, `config.photocraft.json`, user profiles, document screenshots, logs, model files or an entire developer `.runtime` directory in Git.

## Release

1. Finish host, native and installer checks; test the extracted archive with an isolated profile. Keep actual inference opt-in.
2. Update the version in `packaging/installer.py`, `genstudio/__init__.py`, `packaging/Setup.ps1`, README download links and release notes together.
3. Commit the source. Create a matching prerelease tag and upload the Windows ZIP and `SHA256SUMS.txt` as GitHub Release assets. Never commit binaries or weights to Git.
4. Verify the public release metadata and asset names. Record tests and actual limitations in release notes.

GitHub Actions runs Python tests for source changes. Release packaging is currently performed on Windows with the documented pinned build; the repository does not claim a fully automated or signed release pipeline.

## Workflow bridge checks

`tests/test_comfy_workflow.py` checks the graph/file/provenance contract. Its three pixel tests use NumPy from an existing ComfyUI Python; the lightweight host skips those tests without adding NumPy to its runtime. They use synthetic tensors and PNGs, without importing torch.

```powershell
.venv\Scripts\python.exe scripts/comfy_workflow_smoke.py --comfy '<ComfyUI>' --python '<ComfyUI Python>\python.exe'
.venv\Scripts\python.exe scripts/photocraft_workflow_smoke.py --exe '<patched PhotoCraft>\photocraft.exe' --cli '<patched PhotoCraft>\photocraft-cli.exe'
```

The first is explicitly CPU-only, installs the managed bridge and runs core ImageInvert/EmptyImage nodes on a dedicated server with isolated I/O/user folders. The second uses official PhotoCraft MCP and a synthetic host, including variable output sizes/batches, masks, recipe restore and connected source capture. Both exclude checkpoint inference. See [COMFY_WORKFLOWS.md](COMFY_WORKFLOWS.md) for the user-facing protocol and limits.
