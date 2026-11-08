# Real model validation — 2026-11-08

Status: the user reviewed the generated images and accepted the current behavior
as working. These were real checkpoint/CUDA runs through PhotoCraft, not synthetic
HTTP responses. The final Ai Fill context/seam fixes were also installed locally
and tested. This is development acceptance evidence, not a new release.

The accepted context/seam implementation and recommended workflow files are
captured in commit `39eec71`.

Environment: GenGate 0.9.9 with the subsequent unreleased fixes, PhotoCraft 0.21.0,
Windows x64. The ComfyUI runs initially used 0.37.0; FLUX3 and the later three-model
comparison used the existing ComfyUI installation updated to 0.44.2. Direct uses
its private model cores. Do not confuse Direct's use of the Comfy model library
with execution on the ComfyUI server.

## Verified paths

| Model | Direct Studio | Direct selection / float-bar | ComfyUI selection / float-bar | ComfyUI Studio: active user graph |
| --- | --- | --- | --- | --- |
| Qwen Image 2.1 Turbo INT8 | Verified | Verified; repeated after the context/seam fix | Verified | Not part of these runs |
| FLUX3 Image Klein INT8 | Verified | Verified; repeated after the context/seam fix | Verified; repeated using the full-reference Studio graph | Verified through GenGate IN/OUT |
| FIBO 2 Edit Turbo INT8 | Verified with a source image | Verified | Verified; use the aligned workflow described below | Not part of these runs |

The edit comparison starts each case from the same 768×1024 image of the woman
generated with Anima + Stein. It changes her gold gown into a navy pantsuit using
the same clothing selection and prompt. The edits themselves do not apply Stein
or another LoRA. Results were returned to actual native PhotoCraft layers and
saved as PNG and editable `.pcraft` documents.

Additional real coverage:

- Subject selection on an astronaut and Object Selection on a cat, using
  YOLO-World v2 L and SAM3. Source images and magenta-outside-selection renders
  were saved. This does not establish real Sky or Remove Background coverage.
- Native Remove / Ai Fill brush and selection float-bar with Direct Qwen;
  a custom Qwen Edit 2509 GGUF brush workflow through ComfyUI.
- Active ComfyUI Studio graphs using two existing Qwen Edit 2509 GGUF variants,
  two independent references, and SDXL batch generation with negative text.
  These GGUF tests are distinct from the Qwen Image 2.1 Turbo preset tests above.
- Anima Base v1.0 in an active ComfyUI graph, with matched prompt/seed control and
  Stein (`Anima/drstein-step00002400.safetensors`, strength 1.0). GenGate Explorer's
  recipe reached `GenGate Apply LoRAs`, the node executed, and OUT returned the
  matching LoRA recipe to PhotoCraft. Both images and layer metadata were saved.
- Model/engine switching, explicit ComfyUI offload, and Direct generation again
  after ComfyUI. Timing and lifecycle logs were captured; this was not a controlled
  performance or VRAM benchmark.

## Accepted fixes and remaining limits

1. FLUX3 through ComfyUI is now a verified real inference path. The earlier nightly
   FLUX3 image was **Direct**; that earlier image alone never established ComfyUI
   support. The later Studio user-graph run and native float-bar runs do.
2. Ai Fill now retains the source pixels inside the selection. Direct FLUX3 uses
   the complete reference through the Studio edit path; Qwen retains source
   conditioning/latents while receiving a separate mask. Remove Brush keeps its
   object-removal path. The full-reference FLUX3 graph used by successful Studio
   case `01` is supplied as `FLUX3-Klein-Edit` and was selected for local Ai Fill.
3. Result-mask feathering now ramps inward from the selection edge. Three final
   real repeats used the previous seeds, source and exact native selections:
   ComfyUI FLUX3, Direct FLUX3 and Direct Qwen. All returned results successfully;
   source RGBA reached the engine unchanged and final pixels outside the selection
   matched the original exactly. The user accepted the resulting comparison.
4. The first FIBO ComfyUI test graph resized two conditioning references to
   864×1152 while sampling a 768×1024 target. It produced visible displacement and
   is **not a recommended workflow**. `FIBO2-AiFill` instead uses the native target
   grid and resizes the decoded result back to the requested canvas dimensions.
   That control also changes masked versus full-frame sampling; the comparison
   does not isolate resolution as the only cause of the visual difference.
5. SDXL brush case `08_comfy_native_ai_brush` completed transport/inference but
   produced colorful artifacts. It is not an accepted edit-quality result and
   does not validate any of the three edit presets.
6. A faint tonal seam can remain in Direct FLUX3. Qwen can still produce localized
   artifacts in the raw masked-sampling output; input preparation and composition
   also affected the visible edge. The fixes reduce seams, not guarantee perfect
   results for every mask, prompt or user-authored graph. Unselected pieces of the
   gold train in this fixture deliberately remain outside the edit.
7. **Constant LoRA remains untested with real weights.** The Explorer Stein test
   is ordinary per-generation LoRA coverage, not Constant LoRA or Turbo-step
   override coverage. Unlisted model/engine/tool combinations remain unverified.

## Evidence retained outside Git

Local root: `D:\Lemony Sniket\Develop\GenGate_Test_Results`.
These directories contain source images, PNGs, projects, workflow JSON, captions,
settings, seeds and logs; do not delete them as disposable build versions.

| Directory | Evidence |
| --- | --- |
| `nightly-20261108-111002` | 15 completed cases, selection visualizations, three Direct Studio presets, native tools, ComfyUI SDXL/GGUF paths, engine reload |
| `anima-stein-20261108-1257` | Two control/Stein generations; `verification.json` checks adapter transport, execution and returned metadata |
| `edit-context-20261108-compare` | Seven baseline cases plus the aligned FIBO control; `COMPARISON.png`, `CAPTIONS.txt`, `verification.json` |
| `edit-seams-20261108` | Three matched-seed repeats after the fixes; `BEFORE_AFTER.png`, `QWEN_RAW_EDGE.png`, `CAPTIONS.txt`, `verification.json` |

The last verification checks all RGBA channels when comparing source inputs,
compares native selections in grayscale, and measures final RGB differences
outside the selection. Successful job status is recorded separately from image
review; it is not used as an automatic visual-quality score.

Automated checks for the changes also passed: 84 focused host tests (8 skipped
without optional array/tensor dependencies), 19 workflow/transport checks (3 skipped),
and all 8 tests in the CPU inpaint suite using the existing GPU Python with
synthetic model parts and CUDA initialization prohibited. The groups overlap.

Future work should use this record as the completed inference baseline. Do not
repeat the entire model matrix for documentation or packaging-only changes.
New inference still requires the user's authorization; the runs above do not
authorize unrelated future GPU work or publishing a release.
