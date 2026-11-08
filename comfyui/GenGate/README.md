# GenGate ComfyUI bridge

Install/update this folder using GenGate Settings, restart ComfyUI and refresh its browser tab. `web/active_workflow.js` lets PhotoCraft request the currently open graph, including unsaved edits.

- **Studio:** open your graph in ComfyUI; Generate in PhotoCraft uses a fresh snapshot of that active graph.
- **Ai Fill / Remove Brush:** export separate API JSON files and choose each in PhotoCraft's GenGate Settings.
- **IN:** main image/mask, prompt, `negative_prompt`, dimensions, seed, batch, LoRAs JSON and individual additional reference/mask pairs. Encode positive/negative strings inside your graph; model/CLIP/VAE remain yours.
- **OUT:** images and optional mask/name/seed/LoRA recipe. It has no prompt input. PhotoCraft keeps both submitted texts with generated layers.
- **GenGate Apply LoRAs:** connect IN.loras and your model/optional CLIP to apply Explorer or native Constant LoRA strengths.
- **GenGate Edit Steps:** connect it to sampler steps for native tools. Blank PhotoCraft Steps uses this node's `default_steps`; an explicit override wins. Studio uses the workflow default.

Native tool graphs return exactly one image at the requested context size. Studio can accept batches and resized outputs. White IN mask pixels mean regenerate; white OUT mask pixels mean reveal.

## Recommended edit workflows

- `examples/FLUX3-Klein-Edit-api.json`: the full-reference Studio edit graph, also suitable for Ai Fill. IN.image retains the original image content; PhotoCraft applies the selection to the returned image. Requires native FLUX3 support and TextGenerateV2 in ComfyUI.
- `examples/FIBO2-AiFill-api.json`: references and generation share FIBO's trained image grid. The decoded image is resized back to IN.width/height before OUT. PhotoCraft applies the selection on the returned layer.

The matching files without `-api` open on ComfyUI's canvas. Choose the API file for native Ai Fill. These examples use the standard preset filenames; named GenGate loaders allow component overrides in Advanced. No models are included.

Ai Fill preserves the source pixels inside the selection so edit models can see the original object and its surroundings. The separate IN.mask still identifies the selected region. Remove Brush deliberately sends a missing region and uses its own workflow.

IN appends negative text without changing retained output indices. OUT's internal class is now `GenGateOUT2`; recreate it in old canvas workflows, reconnect and export again. Older API JSON migrates retained links and drops obsolete OUT prompt connections. If necessary, recreate IN to expose its new negative socket.

`examples/Connection-test.json` and its API companion invert a source image without any model. The bridge requires a PhotoCraft request; browser Queue alone has no source payload. No checkpoints are bundled or downloaded.

SPDX-License-Identifier: GPL-3.0-or-later. See LICENSE.
