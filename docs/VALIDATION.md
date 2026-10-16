# Alpha validation — 0.6.1-alpha.1

Release checks on Windows x64, 2026-10-16. **No real generation, model download, model loading or CUDA inference was performed.** Test documents contain synthetic pixels only.

| Check | Result |
| --- | --- |
| Host, image/mask transport, lifecycle, model discovery and installer unit tests | 41 tests: 38 passed; three tensor tests skipped in the lightweight host |
| Direct inpaint component tests using the installed engine Python, tiny CPU tensors and fake model parts | All six passed; CUDA initialization explicitly forbidden |
| Native PhotoCraft engine library suite | 1,454 passed, 13 ignored, no failures |
| New installed-defaults regression checks | Covered inside the native suite: missing, malformed, non-object, oversized and BOM-prefixed settings |
| Native engine clippy, all targets, warnings denied | Passed |
| Engine WASM check | Passed; existing dead-code warning for native status bookkeeping on WASM |
| Native editor + matching official MCP CLI release build | Passed |
| Bundled embedded CPython 3.13.16 and host imports | Passed, with no torch installed in the host |
| Windows setup form construction and rendering; shortcut creation with spaces and an apostrophe in the path | Passed in isolated test folders |
| Extracted ZIP integrity and per-file manifest | Passed |
| Actual installation from the extracted ZIP using an existing CUDA Python | Passed in a new test folder; no shortcuts or external services changed |
| ComfyUI setup/update and optional LanPaint copy | Passed against a temporary fake ComfyUI folder; no server started |
| First editor launch with no preferences | Passed: adjacent install settings supply the correct runtime, engine and model source without engine startup |
| Selection fill through the installed editor, installed host and official MCP, using an isolated synthetic backend | Passed: floating prompt bar, Properties variants, prompt restoration, mask/layer output, Undo/Redo and native-format persistence |
| Remove / queue feedback through the installed editor and installed host, using an isolated synthetic backend | Passed: startup feedback, persistent masks, FIFO, waiting cancellation and progress controls |
| GitHub Actions Windows host tests and PowerShell parsing | Passed |

Model files used by installation checks are tiny sentinel fixtures, never actual checkpoints. Original user editor, ComfyUI and engine processes remain untouched. Test subprocesses use separate profiles and isolated HTTP ports.

The native UI integration also has earlier synthetic coverage; its entire upstream UI suite was not rerun for this installer change. Two unrelated localization assertions in the pinned upstream suite were previously known to fail (Turkish New Document “8 bit” and Hungarian “Enable animations”).

Real neural output quality, VRAM/RAM requirements, inference timing and hardware compatibility remain unverified. The optional fresh CUDA dependency-download path is supplied but was not exercised by downloading a second GPU environment during these checks; installation validation reused the existing CUDA Python. This alpha does not claim those checks have passed.
