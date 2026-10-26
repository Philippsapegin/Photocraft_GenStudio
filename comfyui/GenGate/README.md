# GenGate IN / OUT

Widget-free bridge nodes for the GenGate studio in PhotoCraft. Models and sampling belong to your workflow.

1. Copy this folder into `ComfyUI/custom_nodes/GenGate`, or use **Install / Update nodes** in **Preferences → GenGate Settings**. Restart ComfyUI after installing or updating nodes.
2. Add **GenGate IN** and **GenGate OUT** from the **GenGate** category. Connect the IN values your graph needs; connect the final image/batch to OUT.
3. In ComfyUI choose **Export (API)**. In the GenGate studio select **ComfyUI workflow**, open that JSON and Generate. Normal canvas JSON must first be exported in API format; all processing nodes remain yours.

IN exposes the main image, its inpainting mask (transparent = white), document composite, active layer, selection mask (selected = white), prompt, source prompt, target width/height, seed, batch, up to ten individual references and their masks, reference lists, preset/component names, selected LoRAs as JSON, and document metadata as JSON. Image/reference sockets without a source report a clear error when connected. Lists retain different image dimensions. No source image is required for text-to-image workflows using just prompt/dimensions/seed.

OUT accepts an image or batch, optional layer mask (white = visible), prompt, layer name, seed and LoRAs JSON. Every result becomes a new layer, with its prompt and generation recipe. A single mask broadcasts over a batch; otherwise masks match images one for one. Multiple OUT nodes are supported. Workflow output dimensions may differ from the requested target (for example, after an upscale). Limits: eight results, 8192 px per side, 32 million pixels per image and 64 million pixels per request.

The graph owns model selection, sampling and LoRA application. IN exposes Studio's adapter selection; it does not silently insert LoRA loaders into your graph. OUT's optional recipe sockets let a graph report the prompt and adapters it actually used. The native Remove Brush and quick selection Ai Fill continue using the existing preset ComfyUI workflow.

The bridge uses ComfyUI's normal upload, queue, hidden execution metadata and history APIs. It has no callback listener, editable node fields or model downloads. Transfers use request-scoped temporary PNGs; ComfyUI clears temporary storage on restart. Run the graph from GenGate rather than Queue in the ComfyUI browser, so IN receives a fresh studio request.

SPDX-License-Identifier: GPL-3.0-or-later. See LICENSE.

`examples/Connection-test.json` opens in the ComfyUI canvas with IN on the left and OUT on the right. `Connection-test-api.json` opens directly in GenGate. This diagnostic graph only inverts a reference image; it needs no model files.
