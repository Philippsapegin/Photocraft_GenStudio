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


## 0.7.0-alpha.1 workspace — validation, 2026-10-19

The release uses the tested native binary. The installed review binary was checked through the official MCP bridge with a synthetic backend; no real model generation, weight download or CUDA inference was performed.

| Check | Result |
| --- | --- |
| Lightweight host suite | 49 tests: 44 passed, five tensor tests skipped in this environment |
| Tiny CPU inpainting and full-generation sampling with fake model parts | All eight passed, with CUDA initialization forbidden |
| Native engine library | 1,457 passed, 13 ignored |
| Native UI library | 2,329 passed, 11 ignored; the two previously known translation failures were explicitly excluded |
| Final Gen Studio UI regressions after the last layout changes | All 15 passed: asynchronous selection capture, reference drag insertion, settings visibility, stable window height, and selection-bar suppression/restoration |
| Engine and UI clippy, all targets, warnings denied | Passed |
| Layer checks, generated menu parity, full WASM checks and adversarial command panic hunt | Passed; WASM retains the existing unused native-bookkeeping warning |
| Fresh-source preparer and locked metadata | Passed, including repeat application after formatting |
| Installed editor + matching CLI + official MCP | Passed: no-document text generation, two-result batch, unsaved new documents, selection capture, ten reference slots, chip renumbering, 2k sizing, monospaced prompt, component discovery and cancellation |
| 24 MP synthetic reference, prepared as 1024 × 683 | 0.294 s including transport/polling; UI inspection answered in 0.019 s while loading |
| Existing GPU Python and actual shared model paths | Dependencies and default filenames found without importing torch or reading weights |

The reference-preparation path is new, so there is no earlier implementation timing for comparison. The 24 MP timing is a local synthetic measurement, not an inference or hardware benchmark. Review screenshots and the detailed MCP report are under `.runtime/photocraft-studio-smoke`.

The excluded upstream translation assertions are Turkish New Document “8 bit” and Hungarian “Enable animations”. No new failures remain. Real output quality, generation time and GPU memory use are still unverified.

Review executable SHA-256: `2eca83201e853ce593cd2a0de2019d6a87be9d3f479d0c0fbde01f061a8db2ab`.


## Follow-up UI review after 0.7.0-alpha.1

The working 0.7 workspace was committed as `ad28820`, pushed, and published as `v0.7.0-alpha.1` before these UI changes. GitHub reports the same SHA-256 as the locally verified Windows ZIP: `15038667a648f2fbdb640e92861d7a19e7b92e3de5e294be848228862439bae3`.

The next UI changes remain local: a separate Settings window/menu, grouped image count, theme-accent Generate, t2i aspect ratios, font button, selection action inside the empty reference, file-hover outlines, and adaptive canvas/prompt sizes. Long prompts scroll within the prompt field.

- All 19 Gen Studio UI tests passed, including pointer resizing of the actual window, reference drag insertion, font/counter button clicks, conditional controls, and a long multiline prompt.
- Full UI library: 2,333 passed, 11 ignored. The same two known upstream translation assertions were explicitly excluded.
- Engine and UI all-target clippy with warnings denied passed.
- Offscreen fixtures cover all 12 themes at 720 × 460 and 1200 × 800, plus file-hover outlines. The full Studio image and both theme contact sheets were visually inspected. Generate labels meet a 4.5:1 contrast check against the theme accent.
- Fresh-source integration applies repeatedly after formatting; locked dependency metadata resolves.

The native editor and matching CLI release build, layering checks, menu parity, full WASM checks and adversarial command panic hunt passed. WASM retains the same existing unused native-bookkeeping warning.

The installed executable passed the full official MCP workflow against an isolated synthetic server: 9:16 / 2k text generation transport, batches, selection capture, ten reference slots, reference prompt chips, font mode, separate Settings, component discovery and cancellation. A 24 MP synthetic reference was prepared in 0.357 s, with UI inspection answering in 0.012 s during loading. These are local transport/preparation measurements while compiling, not inference benchmarks or a controlled performance comparison.

Installed review executable SHA-256: `e9b7e396cce4149a6eaeaf7d6a423aa7672742922d493f154e166a30cfa1dab7`. The desktop Review shortcut points at the new executable and a separate profile copied from the previous Review installation. Existing running user instances were preserved. Reports: `.runtime/studio-ui-final-checks.json` and `.runtime/photocraft-studio-smoke/report.json`.

No real model generation, weight download/copy or CUDA inference was performed.


## Preferences, generated layers and pixel alignment - local review, 2026-10-20

The current local changes align the prompt/control row, move all Studio settings into the native Preferences working copy, and import full-generation outputs as new layers with persistent creation prompts. Empty references receive the generated image; requests without a source document create a document with only the output layers. The published alpha has not been replaced by this review.

- Lightweight host: 53 tests, 48 passed and five tensor tests skipped. Both Direct polling and the native Studio transport recover from a simulated read timeout without resubmitting the job; cancellation and bounded failure remain covered.
- Native engine library: 1,458 passed, 13 ignored. Coverage includes prompt metadata through native and PSD round trips, atomic batch Undo/Redo, and retaining the original destination after a tab switch.
- Native UI library: 2,334 passed, 11 ignored. The same two upstream translation assertions were excluded (Turkish New Document bit-depth label and Hungarian Enable animations).
- All 20 Gen Studio UI tests passed. Geometry checks cover all 12 themes at small and large sizes: equal 8-point gaps, aligned prompt/Generate right edges, aligned control heights/bottom edges, and an unchanged footer during resizing. The native Preferences tab checks deferred Apply/Cancel behavior, separate model/engine controls and initially collapsed Advanced settings.
- Engine/UI all-target clippy passed with warnings denied.
- Reapplying the integration to pinned fresh sources after formatting preserves the normalized source and locked dependency metadata.

The broader integration run reached two failures in PhotoCraft's unmodified `arbitrary_rotation_preview` fixture: its assertion that the GPU canvas is active fails before the rotation behavior is exercised. One failure also reproduces in isolation. The successful library/Gen Studio checks do not establish that this GPU integration suite passes in this environment. Its output is retained in `.runtime/studio-layers-tests.log` and `.runtime/studio-layers-rotation-isolated.log`.

No real model generation, weight download/copy, or CUDA inference was performed. The owned idle local engine/service was stopped to make the polling fix take effect on the next request; user editor processes were preserved.

The installed editor and matching CLI passed the official MCP workflow against an isolated synthetic backend. It covers one-layer no-document generation, new layers in an existing document without a selection, batches and Undo/Redo, selection/reference inputs, creation-prompt inspection and native-format reopening, the generated reference surviving Studio reopening, Preferences with Advanced collapsed/expanded, catalog discovery and cancellation. Native screenshots of the aligned workspace, generated reference, layer Properties prompt and both Preferences states were visually inspected.

A 24 MP synthetic reference was reduced to 1024 x 683 in 0.260 s; UI inspection answered in 0.006 s during loading. This is a local preparation/transport measurement while compiling, not an inference benchmark.

The native release build, dependency-layer check, generated menu parity and full WASM check passed. The existing WASM unused native-bookkeeping warning remains. Installed executable SHA-256: `751779783055ab22cc6aa8c2e474a8610db5da11206a364e310343f2745e6ae3`. Reports are in `.runtime/studio-layers-final-checks.json` and `.runtime/photocraft-studio-smoke/report.json`.

The final adversarial-command panic hunt passed. The desktop **PhotoCraft Gen Studio Review** shortcut now launches `D:/Programs/PhotoCraft/GenStudioReviewLayers/photocraft.exe`, with a separate profile copied from the previous Review installation. The copied model/engine settings match the previous profile; the real model directory is used in place. The shortcut's earlier target is backed up locally. Current changes remain uncommitted and unpublished.

## 2026-10-20: FLUX 3 Image Klein, local review

- Added model-aware settings/discovery, Direct worker identity and dispatch, and a ComfyUI 0.41.3+ adapter. The distilled INT8 ConvRot pipeline uses 16 Euler/simple steps, CFG=1 and BFL workflow alpha=16 with the Flux2 128-channel /16 latent grid.
- Added the official Qwen3.5-9B prompt upsampler and original BFL Apache-2.0 templates. Direct releases the LLM before loading the image pipeline; a bounded one-caption cache reuses deterministic top-k=1 expansion across batch seeds. Cache identity includes prompt, ordered pixels and output dimensions.
- Both Qwen and FLUX3 support ten references. Native header/labels/chips follow the selected model; Klein uses `<ref_image_0>` through `<ref_image_9>`. Model switches migrate recognized chips and reference removal renumbers them by identity. Component catalog cache keys include the model. Text encoder/VAE reset to Automatic when the model changes.
- Qwen keeps its original model-library pin. Klein uses ComfyUI v0.41.3 revision 8713616fcc321624e306bbd36be5906203dcb0dc and a private comfy-kitchen 0.2.37 / comfy-aimdo 0.5.6 overlay. The user's existing GPU Python and ComfyUI packages were not upgraded. A real-interpreter readiness check imported no torch and reported only the currently incomplete model download.
- Host suite: 70 tests, 65 passed and five torch-dependent tests skipped. All 17 new Flux/download tests passed. One full-suite attempt had a Windows loopback WinError 10053 after a cancellation endpoint logged HTTP 200; the isolated cancellation test and the subsequent complete suite passed. Original failure log retained in .runtime/flux3-python-tests-socket-abort.log.
- Native targeted tests: 19 engine + 22 UI passed. Clippy all targets with -D warnings, release build, layers, menu parity (633/633), complete WASM and adversarial panic hunt passed. The previously documented WASM native-bookkeeping dead-code warning remains. The broader GPU/localization baseline tests listed above were not rerun for this change.
- Installed executable and matching CLI passed the official MCP suite with isolated synthetic backends for BOTH model choices: text-only/new-document generation, existing-document layers, batches/Undo/Redo, selection and ten references, correct compiled chips, persistent source prompts and selected-model metadata, native-format reopening, generated main reference, Preferences component discovery and cancellation. Screenshots of the Klein workspace and Preferences were visually inspected. A 24 MP reference took 0.258 s with a 0.006 s UI response; this is a preparation measurement, not inference.
- Review installed at D:/Programs/PhotoCraft/GenStudioReviewFlux3, copied from the previous Review profile. Desktop PhotoCraft Gen Studio Review shortcut updated. Executable SHA-256: 9d6a323ae462c96b84445389678c6744147e5820fb2c3fc8d496504dc829f218. The previous running editor and documents were preserved. Main settings still point at the user's existing shared Models folder; no weights were copied between engines.
- Explicit background download PID recorded in .runtime/flux3-download-pid.txt. Manifest pins a public mirror whose four file hashes/sizes match BFL revision feef60b0aaf63a4773ace96636ebbcf43b881312. The 5.83 GB INT8 image model has completed and passed its SHA-256 check; the text encoder is downloading, with VAE and W4A8 upsampler queued (17.75 GB total). Partial files remain undiscoverable until verification. Live progress: .runtime/flux3-download.json.
- No real model inference, weight interpretation or CUDA initialization was performed. Image quality, VRAM/RAM use and inference speed remain unverified at the user's request. Changes are local/uncommitted; the published alpha ZIP is unchanged.

Evidence: .runtime/flux3-final-checks.json, .runtime/photocraft-flux3-smoke/report.json and .runtime/photocraft-studio-smoke/report.json.


## 2026-10-21 - GenGate 0.8.0-alpha.1

GenGate replaces the public Gen Studio name while retaining profile keys, command IDs and layer metadata. The workspace has a left-aligned model title and a reserved tool row. Use selection is available over a populated main reference. Native Preferences is named GenGate Settings.

Independent Direct segmentation adds YOLO-World S/M/L plus SAM3, Subject/Sky/Remove Background routing and the Object Selection Ai Select rectangle gesture. Default S and SAM3 downloads completed and passed their manifest SHA-256 checks. No model weights are included in source or the release archive.

- Lightweight Python suite: 79 total, 74 passed, five tensor tests skipped; no torch in the host environment.
- Native engine library: 1,463 passed, 13 ignored. New cases cover ROI clipping, mask coordinates, empty results, color/depth capture and nondestructive mask Undo/Redo.
- Workspace/UI group: 22 passed, including populated-reference selection access and all-theme layout checks. Segmentation gesture/control group: three passed.
- Full native UI library: 2,339 passed, 11 ignored, two unchanged upstream localization failures: new_doc_ui::tests::new_document_depth_labels_and_tooltips_are_translated (Turkish 8 bit), prefs_ui::tests::preference_labels_are_translated (Hungarian Enable animations). No new failures.
- Engine/UI clippy all-targets with warnings denied passed. Source preparation was run twice with identical resulting content, including localization catalogs and command registrations.

Real generation and segmentation inference remain deliberately excluded. CUDA capability, actual model quality, memory peaks and speed are not validated by synthetic tests.

During the final host run, the earlier Windows queued-cancellation socket abort reproduced. The Studio cancel route left its small POST body unread; Windows could reset the HTTP/1.0 socket after the server logged HTTP 200. It now drains a bounded body before acquiring the jobs lock. A regression checks full consumption, oversized bodies and truncation; the final 79-test host run passed. This does not retry generation submissions.

Final title correction: the caption uses an explicitly left-to-right header layout. A new regression checks its left edge and live model changes. The final UI groups pass 22 workspace + four title/segmentation tests; the complete UI library passes 2,340 tests with 11 ignored and the same two upstream translation assertions explicitly excluded. Final engine/UI clippy and wasm checks pass; menu parity is 633/633 and the adversarial command test passes.

Both generative families pass official-MCP smoke checks on the final release executable, including a real UI click on Use selection over a loaded reference. Independent segmentation passes synthetic Direct-worker tests at 8/16/32-bit depth: Subject, Sky, rectangle-limited Object Selection, add/subtract/intersect, real tool click suppression, layer masks, Undo/Redo, cancellation and failure preservation. A 24 MP native capture/transport/mask-mapping check takes 3.64 s, with a 15 ms UI response; these are synthetic transport timings, not inference measurements. Workspace, Preferences and Object Selection screenshots were inspected visually.

Windows archive: PhotoCraft-GenGate-0.8.0-alpha.1-windows-x64.zip, 147244499 bytes, SHA-256 455d7f232869cb6e69c3004a1ca3f323a657acf629b94049edb24ec13df94d21. Executable SHA-256 bfffe235dc49c392376cf104139802056f22b89887838770a66cb83aef2d1fd1. ZIP CRC, per-file manifest and neutral compiled defaults pass. All 33 bundled host/installer source files match the repository. The extracted installer creates a separate GenGate installation and shortcuts using the existing CUDA Python and shared models, without loading weights; the old editor remains running.

The installed executable and its bundled embedded-Python runtime also pass both Qwen and Klein MCP regressions using --runtime <installed runtime>. This verifies the extracted release independently of the development host environment. The local GenGate profile retains FLUX 3 Image Klein, the existing shared model source and first-request startup.
