# Release validation

## Floating prompt deletion and adaptive economy - 2026-10-23

- Floating Ai Fill prompts have a document-scoped text widget ID and explicit ownership of Backspace/Delete frames. A held key retains its text owner through a focus transition until release. A fresh canvas key after release still clears normally. Accumulated Clear autorepeats coalesce within one frame; separate key presses retain their order.
- Selection bounds and reusable-layer checks compare unchanged COW mask tiles across pixel-only edits instead of rescanning mask pixels. Changed selections and Undo/Redo invalidate the cached geometry.
- Economy mode remains off by default. Both rectangle sides at most 768 px retain every original pixel. Larger aligned rectangles use `max(0.5, sqrt(768 / longest_side))`, capped at half resolution from 3072 px. Model-grid padding is separate from uniformly resized content and is removed before restoration. Growing crops do not shrink the output around the 2x threshold.
- **128 Python tests: 123 passed, 5 tensor tests skipped.** Covers small-crop pixel identity, progressive sizes, <=2x bounds, monotonic threshold behavior, thin marks and removal of model-input padding.
- **1,924 engine and 3,270 UI tests passed**, 27 ignored and the same two upstream translation assertions excluded. New keyboard regressions cover 800 repeats with the pointer outside the prompt, deletion to empty, a focus transition during a hold, a fresh canvas delete after release, and separate presses versus autorepeat. Mask-cache coverage includes pixel edits, selection edits, Undo and deselection.
- Unit glue used opt-level 0 with optimized dependencies and 32 MiB test-thread stacks. The first unoptimized engine run exceeded Windows' default test stack in the existing `same_pixels_ignores_the_selection_but_not_layers` case; the complete run above passed with the explicit stack. Production binaries use the optimized release profile.
- The original user hang did not reproduce in baseline synthetic mouse-outside-prompt checks. The changes protect keyboard ownership and remove redundant mask work; this is not proof of the exact original hang cause. Baseline 24 MP canvas-delete checks were responsive with the optional bar both hidden and visible.
- Optimized release build, all-target clippy with warnings denied, dependency layers, WASM and the adversarial-command panic hunt passed. Official MCP verified the final executable: three pointer-outside deletion cases, full-resolution 2304x1824 fill, adaptive 1344x1056 fill, unchanged 576x480 small economy crop, preserved masks and Remove Tool. Screenshots were inspected in the Studio theme. A 24 MP native control check stayed responsive at 75-91 ms per Clear/inspect roundtrip; these are transport/UI measurements, not inference timings.
- Installed into the existing separate GenGate runtime and PhotoCraft 0.12.0, with a full profile/recovery and software backup. Model files, saved native preferences and appearance remained unchanged. The economy preference stayed off. The installed executable was reopened and its settings read back through MCP without starting an engine. Installed SHA-256: `43a5680661faf8064eee6ccd7b0521f8ecb34ab1e4e54e7c97ac4dbf1cc35c18`.
- The general `perf_scenarios` quick benchmark completed successfully after rerunning the limit-interrupted attempt. Its report remains in `.runtime/photocraft-source/target/perf/results.json`. No real generation, weight loading, CUDA model initialization or GPU-package changes were requested.

Evidence: `.runtime/backspace-economy-gates.json`, `.runtime/edit-resolution-smoke-latest.json`, `.runtime/backspace-economy-installed.json` and `.runtime/backspace-economy-live-check.json`.

## Native-resolution edits — 2026-10-22

- Removed the native edit transport's 768 px cap. The previously observed 2528×1696 crop is prepared and restored at 2528×1696 by default. Direct masked requests accept up to the existing 16 Mi pixel context limit; FLUX masked edits no longer inherit the full-workspace 2048 px side limit.
- Native selection-fill and Remove Tool context expands to multiples of 32 without moving source pixels or changing mask coverage. When canvas boundaries prevent expansion, edge padding is removed after inference. Wrong output dimensions are rejected instead of stretched.
- Added `reduceEditResolution` in Preferences > GenGate, off by default. Its checked mode expands context to multiples of 64, then reduces both axes by exactly two. Restoring removes padding and retains the original output mask. The full-generation workspace keeps its existing independent size controls.
- **125 Python tests: 120 passed, 5 tensor tests skipped.** New regressions cover exact pixel-coordinate retention, large crops, tiny masks, padding removal, checkbox types/defaults, the large Direct request path, FLUX's masked/full-workspace size distinction and full-rectangle HTTP output.
- **1,923 engine and 3,268 UI tests passed**, with 27 ignored and the same two previously documented upstream translation tests excluded. Native geometry regressions cover expansion, canvas-edge handling, both grid sizes and empty rectangles.
- Official MCP on the candidate PhotoCraft 0.12 build passed synthetic full-resolution selection fill (2304×1824), explicit reduction (1152×928 after extra alignment padding), unchanged selection bounds, unchanged pixels outside the mask, native preference persistence and Remove Tool integration. Both checkbox states were rendered and inspected in the Studio theme. No real model inference or weight loading was performed.
- Release build, engine/UI all-target clippy with warnings denied, dependency layers, WASM and the adversarial-command panic hunt passed. Checksum-bound editor/CLI deltas were reconstructed successfully from the verified official 0.12 base.
- Installed into the existing separate GenGate runtime and PhotoCraft 0.12.0 editor. The previous binaries, runtime files and complete profile/recovery data were backed up. Native preferences, appearance and shared model files were preserved; no model or GPU-package changes were made.
- Reopened the installed editor and read back Direct, FLUX 3 Image Klein, Automatic prompt upsampler and `reduceEditResolution=false` through its official MCP. Its document remains open and the UI responds. No real generation or engine startup was requested. Installed executable SHA-256: `ceacd95a5bc0d3b8026012930448355468cef64522419ea154adbafa97f5e4fe`.

Evidence: `.runtime/edit-resolution-native-gates.json`, `.runtime/edit-resolution-smoke-latest.json`, `.runtime/edit-resolution-installed.json` and `.runtime/edit-resolution-live-check.json`.

## Live FLUX prompt-upsample diagnosis — 2026-10-22

- Inspected the user's already-running job without launching inference. Its token count advanced from 53 to 161; image sampling had not started. The worker was active, rather than hung, at roughly 3.5 seconds per generated token. RTX 3060 VRAM use was 11,626/12,288 MiB.
- The selected 24.55 GiB H3 checkpoint declares `source_architecture=Qwen3-VL-32B-Instruct`, 50 retained language layers, `minimax_h3_lm_head=false` and `minimax_h3_final_norm=false`. The pinned core routes that architecture through its MiniMax H3 conditioning tokenizer/model, rather than the Qwen3.5 prompt-generation adapter. It is unsuitable for the prompt-upsample role.
- The supported Qwen3.5-9B W4A8 prompt upsampler already exists in the shared model folder (6.26 GiB). Only Safetensors headers and file sizes were read; no replacement download, model load or new generation was started.
- Added header validation that rejects known conditioning-only H3 exports before importing the model core, while retaining custom full-checkpoint support. Token callbacks now forward activity to the editor without treating the token ceiling as an image-sampling completion percentage.
- **116 Python tests: 111 passed, 5 tensor tests skipped.** Tests cover H3 rejection before core import, valid custom metadata, malformed headers, forwarded token activity, cancellation and progress-hook restoration. Synthetic worker tests do not use the user's GPU lease.
- At the user's request, cancelled only the live FLUX job, waited for acknowledgement and stopped its owned worker. PhotoCraft stayed open. Installed the Python repair and configured the generation service to use Automatic prompt-upsample selection without starting an engine. The user subsequently applied the matching native Preferences setting; its persisted value was verified during the resolution fix.

## Local engine isolation repair — 2026-10-22

- Confirmed a stale Gen Studio **0.5.0** host on port 8766. Sending current settings to its status route reproduced `Unknown engine setting: model, presets, textEncoder, vae` without inference.
- Generation now discovers only a runtime-specific endpoint with matching protocol, version, source fingerprint and installation path. Direct workers have private ports and host ownership IDs. Status is read-only and does not wait for a startup lock. Generator configuration preserves independent selection preferences.
- Direct generation and segmentation use separate processes and a cancellable cross-process GPU lease. Generation offloads before releasing it, including after errors; the selection worker acquires it before importing GPU dependencies. Crash-release, cancellation, incompatible hosts and wrong worker ownership have regression coverage.
- **115 Python tests: 110 passed, 5 tensor tests skipped.** Official MCP exercised the real installed PhotoCraft 0.12 executable with synthetic generation/selection workers: private host startup, Qwen → FLUX switching, an irregular selection on a 24 MP document, GPU queuing across documents, Use selection and prompt editing. Screenshot inspected.
- The Python repair is installed into the existing GenGate runtime with a backup. Native editor/CLI, layout and model files are unchanged. Direct was restored at the user's request; FLUX and the selected YOLO/SAM settings were retained. A second MCP check against the repaired installed runtime captured a large selection and entered English/Unicode text successfully.
- Windows recorded the reported incident as Application Hang (event 1002), not a native panic. The original hang was not reproduced; its precise cause and real GPU memory behavior remain unverified. No real inference, weight loading, CUDA initialization or GPU package changes were performed.

Reproduce the synthetic integration check with `scripts/photocraft_process_isolation_smoke.py --exe <photocraft.exe> --cli <photocraft-cli.exe>`.

## 0.9.1 add-on — 2026-10-22 (local, not published)

- **100 Python tests: 95 passed, 5 tensor tests skipped.** Includes real BSDIFF reconstruction on synthetic binaries, repeat install, supported refresh, unknown upstream updates, verified original restore, failed-write rollback and interrupted-transaction recovery.
- The add-on contains no complete PhotoCraft editor or CLI. Deltas reconstruct the exact previously validated native outputs from the hash-verified official 0.8.2 Windows x64 binaries. Native behaviour and appearance are unchanged; this is not a port to 0.12.0.
- The extracted ZIP was installed by its actual embedded Python onto an isolated official editor in a path with spaces and Cyrillic characters. First install, update, supported-editor refresh/reapply, uninstall and reinstall passed. Native preferences and original editor hashes were preserved/restored; shared model folders were not populated or copied.
- Official MCP on the patched, installed editor passed Settings, Qwen and FLUX workspaces, startup/progress/queue feedback, and segmentation with synthetic workers. No real generation, CUDA initialization or model-weight load was requested.
- PowerShell entry points parse; the new Setup host-selection row was rendered and inspected. Existing GPU Python is inspected as metadata only. Installing a fresh GPU software runtime was not repeated.
- Live migration: GenGate software installed into `D:\Programs\GenGate`, patch applied to the existing official `D:\Programs\PhotoCraft` 0.8.2, and the newest user preferences/layout migrated with a separate backup. Both native binary hashes match the validated GenGate build. Desktop GenGate launcher points to this installation; the legacy PhotoCraft shortcut now points to the normal editor path. Previous standalone files are retained. Shared model sizes/timestamps are unchanged; the old idle lightweight host was stopped and no replacement engine was started.
- Unknown editor versions fail before install/download and are never replaced by an older build. The launcher opens ordinary PhotoCraft with a compatibility notice when no supported patch exists. Uninstall retains externally updated editor files and keeps software/recovery backups for manual removal.

Reproduce the isolated distribution test with `scripts/photocraft_addon_smoke.py --archive <add-on.zip> --original <official-exe-folder> --gpu-python <existing-python.exe>`. GPU Python is inspected, not used for inference.

## 0.9.0 beta — 2026-10-22

- Python: **88 tests, 83 passed, 5 tensor tests skipped** in the lightweight host.
- The native editor and matching CLI reuse the validated preset/LoRA build: baseline 1,464 engine / 2,344 UI tests passed, followed by 6 engine, 9 Preferences UI and 8 GenGate UI regression tests on the final changes. Clippy, layering, WebAssembly, parity and panic checks passed; two known upstream localization failures remain excluded. No native behavior changed for the release version bump.
- Windows ZIP CRC and all **2,374 manifest files** verified after extraction. Packaged Python sources match this repository; host and installer both report **0.9.0**. No model weights are bundled, and the lightweight Python has no torch.
- Real installer checks: fresh installation, upgrade from published 0.8, legacy encoder/VAE migration, and a second update with both presets and Constant LoRA strengths. Startup policies, unrelated preferences, previous executables and profile backups are retained. Paths with spaces and Cyrillic characters work.
- ComfyUI setup was exercised against an isolated fixture: missing LanPaint is rejected before installation unless enabled; its bundled source installs correctly, and existing custom-node files survive updates. No ComfyUI server was started.
- The graphical Setup form and temporary Windows shortcut were checked. Shared CUDA Python package metadata and selected model-file sizes/timestamps are unchanged.
- Official MCP checks ran against the **installed beta and bundled runtime**: settings discovery/persistence/cancel, Qwen and FLUX workspaces, startup/progress/queue feedback, and the independent segmentation tools. All passed with synthetic pixels and workers. The setup window was inspected visually.
- Real inference, model quality, performance and GPU memory use were not tested. The separate CUDA runtime download was not repeated; its private-copy/install-target behavior has a regression test, while actual installation checks reused the existing CUDA Python.

Archive: `PhotoCraft-GenGate-0.9.0-windows-x64.zip`, **147,294,289 bytes**, SHA-256 `bb83ce7ae48ae242f31a1179310a04af89bd1346a2585d8a3fa3f47617ee44d3`.

Editor SHA-256: `479abde62cf0aa95635d961346a747b00be60b400caeae46faf1e782816d6482`. Compiled defaults are neutral; installation writes machine-specific paths into the separate runtime/profile.

## 0.6.1-alpha.1

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

## 2026-10-22 - PhotoCraft 0.12.0 and one PhotoCraft launcher

Ported the native overlay to official PhotoCraft v0.12.0, commit `6a2d027ee2e7ca12afd248544ed2262b0fa078dd`. The shared shell-command catalog, per-tool sampling flags, fallible Remove footprint, Properties panel and Object Finder gesture hooks now follow the new host. The selection prompt bar is hidden during Free Transform. Preferences sections scroll within their fixed-width column so the extra GenGate section does not prevent resizing.

- Lightweight Python suite: 103 tests, 98 passed and five tensor tests skipped.
- Native engine: 1,921 passed, 14 ignored. Native UI: 3,268 passed, 13 ignored, with the two previously documented upstream translation tests explicitly excluded. Initial integration failures were fixed and the full libraries rerun successfully. UI tests used an unoptimized UI crate with optimized dependencies; the shipped application is a release build.
- Engine/UI all-target clippy with warnings denied, dependency layers and the full WASM check passed. Menu parity regeneration passed at 633/633 live entries; the adversarial-command panic hunt also passed.
- The final release binaries passed all five synthetic official-MCP workflows: Preferences, Qwen Studio, FLUX Studio, queued fill feedback and segmentation. Native screenshots were visually inspected.
- The official release's four embedded `craft-fonts` assets were verified by SHA-256 and checked for byte-for-byte inclusion in both rebuilt executables. Original OFL notices are retained in the add-on.
- The extracted add-on passed installation, update, supported editor refresh/reapplication, restore and reinstall against an isolated official 0.12.0 editor, including Unicode paths. The package contains no complete PhotoCraft executable or model weights.

Installed the validated overlay on `D:/Programs/PhotoCraft`, with the separate runtime under `D:/Programs/GenGate`. Native preferences outside GenGate, the appearance file and all model file sizes/timestamps were unchanged by installation. The active preset remains Qwen Image 2.1, Direct with first-request startup, using the existing shared model directory and GPU Python.

Start and Desktop each contain one **PhotoCraft** shortcut to the same compatibility-checking launcher. Obsolete editor shortcuts are backed up outside Start/Desktop. `Get-StartApps` reports one PhotoCraft. Launching the actual Start shortcut opened PhotoCraft 0.12.0 and its installed GenGate panel with the expected runtime. The empty QA instance was closed gracefully; the same shortcut then opened a normal, responsive PhotoCraft instance for review.

Installed editor SHA-256: `c03eb01dc6140bbb68ec474032cabb17682b6f0e594cbc11643748dfafbcc7fc`. Local add-on ZIP: `GenGate-0.9.1-windows-x64-addon.zip`, 178,850,392 bytes, SHA-256 `0c09583249e56f532d7406a2919abc90aabd8baba658fa896512344dc6feead4`. No commit, push, public release, real inference, shared GPU Python change or model download/copy was performed.

Evidence: `.runtime/photocraft012-gates.json`, `.runtime/native012-mcp.json`, `.runtime/candidate012.json`, `.runtime/addon012-live-install.json` and `.runtime/shortcut012-validation/report.json`.

## 2026-10-23 - NN Switch and floating prompt hang

Reproduced the real hang in an isolated copy of the installed 0.12.0 editor,
without loading a model. CDB captured the main thread blocked inside
`DefWindowProc(WM_INPUTLANGCHANGEREQUEST)`; Windows wait-chain traversal identified
the target as the process's `wgpu-hal WGL Instance Thread`. That thread was parked
in its shutdown-channel receive, without pumping its hidden window. A DX12-only
diagnostic launch accepted both NN Switch commands. The previous synthetic
Backspace checks had not exercised this external layout-change message.

The source preparer now avoids initializing unused GL for Automatic on Windows
when native DX12 adapters exist, retaining Vulkan on non-Intel hardware and the
Intel DX12 safeguard. Explicit graphics choices and GL-only fallback are preserved.
This fixes PhotoCraft startup; NN Switch's source, running process and saved
hotkeys do not need changing.

- Host suite: 129 tests, 124 passed, five tensor-dependent tests skipped.
- Desktop application: 162 tests passed, one ignored, including the new graphics
  initialization regression. Release build, all-target clippy with warnings denied
  and dependency layers passed. Engine/UI/commands were unchanged in this fix.
- Real NN Switch plus official MCP: five Last written word cycles and five
  Selected text cycles, each English-to-Russian and back. Eight Backspace presses
  per cycle with the pointer outside the prompt left document data unchanged.
  Automatic rendering was used without a backend override; screenshots inspected.
- No inference, model downloads, shared Python changes or NN Switch reinstall.

Evidence: `.runtime/nn-gates.json`, `.runtime/nn-python-tests.log`,
`.runtime/layout-smoke-latest.json`, `.runtime/nn-photocraft-main.txt` and
`.runtime/nn-photocraft-input-thread.txt`.

Installed through the existing separate add-on's verified delta/transaction path;
editor SHA-256 `5be435fa67c3c166f7ba2f984e112a81c277670edd19510a4b885d26ea2cd4a9`.
The installed editor passed another three cycles of each NN Switch mode in both
directions, with the same Backspace and document-preservation checks. The launcher
recognizes the updated patch. Native preferences, appearance and model file
sizes/timestamps were preserved; the previous installation/profile were backed up.
Evidence: `.runtime/nn-installed.json` and `.runtime/layout-smoke-latest.json`.
