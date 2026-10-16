# Development

The public alpha is Windows x64. The source repository contains the Python host and native integration templates, not a second copy of PhotoCraft or model weights.

## Host tests

Python 3.10+:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests use synthetic images and mock model components. Three tensor-level inpainting tests skip when torch is unavailable. With a CUDA Python that already has torch installed, they can also be run on CPU without loading any weights:

```powershell
$env:CUDA_VISIBLE_DEVICES = ''
# Replace the path with your GPU Python. No real generation is submitted.
& '<GPU-Python>\python.exe' -c "import os,sys; sys.path.insert(0,os.getcwd()); import unittest; suite=unittest.defaultTestLoader.discover('tests',pattern='test_direct_inpaint.py'); result=unittest.TextTestRunner(verbosity=2).run(suite); sys.exit(not result.wasSuccessful())"
```

## Build the native editor

Install Git, Rust **1.95+**, MSVC Build Tools with the Windows SDK, and PhotoCraft's native build prerequisites from its [pinned development guide](https://github.com/storytold/photocraft/blob/d43db3e153c76aefce9b32cef9099e458eaf17a8/docs/development.md). Then:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Build-PhotoCraft.ps1
```

This clones PhotoCraft v0.8.2 at `d43db3e153c76aefce9b32cef9099e458eaf17a8` into `.runtime/photocraft-source`, applies the templates and hooks, formats Rust and builds both executables with HEIF support. The preparer rejects changed integration points instead of silently patching another upstream version. It can be rerun on an already prepared checkout. Upstream remains under MIT OR Apache-2.0. Read its `AGENTS.md` and contribution instructions before editing upstream code.

Distributed binaries contain neutral compiled defaults. On startup they read `runtime/config.photocraft.json` next to the executable; saved PhotoCraft preferences then take precedence. The installer writes both files for the chosen machine. No developer paths or personal settings belong in a release.

Targeted native checks after changing the templates:

```powershell
$env:GENSTUDIO_NO_PROCESS = '1'
cargo test --manifest-path .runtime/photocraft-source/Cargo.toml -p photocraft-engine --lib genstudio::
cargo clippy --manifest-path .runtime/photocraft-source/Cargo.toml -p photocraft-engine --all-targets -- -D warnings
```

Follow upstream's broader checks when changing commands, document structures or UI. The existing integration was exercised with engine/UI tests and synthetic official-MCP smoke checks. Two unrelated localization assertions in the pinned upstream UI suite are known failures (Turkish New Document “8 bit” and Hungarian “Enable animations”); do not describe the whole upstream suite as green.

The `photocraft_*_smoke.py` scripts launch an isolated editor/profile and synthetic HTTP backend. Pass `--exe` when the executable is outside the default build folder; `PHOTOCRAFT_CLI` can select a matching CLI executable. Do not use their explicit real-backend start flags unless you intend to start that service. No release validation requires real inference.

## Package the Windows ZIP

```powershell
python scripts/package_windows.py
```

The packager obtains the pinned Comfy model library and LanPaint **code**, and the SHA-256-pinned embedded CPython / host wheels from `packaging/runtime-lock.json`. It never obtains model weights. Use `--core` and `--lanpaint` to reuse existing pinned source checkouts; `--editor`, `--cli` and `--upstream` select an existing build. The result is in `dist/`, along with `SHA256SUMS.txt`.

The archive contains a native editor, matching official MCP CLI, local host, Python runtime, original source for the separate GPL libraries, notices, a manifest with per-file SHA-256 values, and the graphical setup entry point. Host wheel distributions keep their own `.dist-info/licenses` files. The application uses the CPython-provided VC runtime DLL beside the executable.

The packager refuses to reuse an existing staging directory. Move a previous staging directory aside before rebuilding. Compare the manifest and run setup with a temporary destination and `--no-shortcuts` for automated verification. Do not include `config.local.json`, `config.photocraft.json`, user profiles, document screenshots, logs, model files or an entire developer `.runtime` directory in Git.

## Release

1. Finish host, native and installer checks; test the extracted archive with an isolated profile. Keep actual inference opt-in.
2. Update the version in `packaging/installer.py`, README download links and release notes together.
3. Commit the source. Create a matching prerelease tag and upload the Windows ZIP and `SHA256SUMS.txt` as GitHub Release assets. Never commit binaries or weights to Git.
4. Verify the public release metadata and asset names. Record tests and actual limitations in release notes.

GitHub Actions runs Python tests for source changes. Release packaging is currently performed on Windows with the documented pinned build; the repository does not claim a fully automated or signed release pipeline.
