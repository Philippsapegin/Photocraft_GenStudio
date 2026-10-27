# Studio workflows

With the ComfyUI engine selected, Studio shows one clickable workflow field above the canvas. Its empty placeholder is **workflow goes here**; choosing a file shows its name. Direct has no workflow field. The workflow controls models, sampling, transformations, LoRA loading and result dimensions. Direct Studio uses the selected preset. Quick selection Ai Fill and Remove Brush use the selected editing preset through the existing ComfyUI path.

## Set up

1. Choose **ComfyUI** in **Edit → Preferences → GenGate Settings**. Set its folder, Python and local server address in Advanced, then Apply.
2. Click **Install / Update nodes**. Setup also installs the nodes when ComfyUI is selected. Restart an already running ComfyUI after changing node files. A server started by GenGate loads the bridge automatically for workflow mode.
3. Add **GenGate IN** and **GenGate OUT** from the **GenGate** category in ComfyUI. Both have sockets only. Connect the data your graph uses and its final image/batch to OUT.
4. Choose **Export (API)** in ComfyUI. In GenGate click the workflow field, select that JSON, and Generate. Click the same field to replace the file. API JSON with arbitrary processing nodes is supported. Ordinary canvas JSON first needs the API export; GenGate gives an explanatory error for it.

For a connection check without any model, load `custom_nodes/GenGate/examples/Connection-test.json` in ComfyUI. Open its companion `Connection-test-api.json` in GenGate and add a reference image. The graph simply inverts its colours.

An existing external ComfyUI server stays under its owner's control. Install any nodes used by the graph and restart that server yourself when necessary. A GenGate-owned server follows the existing automatic-start selector. A GenGate-owned ComfyUI loads user custom nodes for Studio workflows. Quick editing continues to validate its selected preset separately.

## IN ports

| Ports | Type | Data |
| --- | --- | --- |
| `image`, `mask` | IMAGE, MASK | Main Studio reference and its inpainting mask: transparent pixels are white. |
| `prompt` | STRING | Studio text, including reference chips as written. No preset-specific prompt syntax is applied. |
| `width`, `height`, `seed`, `batch` | INT | Requested target dimensions, request seed and Studio batch control. Connect `batch` to the graph's latent/image batch size if desired. |
| `reference_2` … `reference_10` and `_mask` partners | IMAGE, MASK | Individual additional references. The main `image` is reference 1. |
| `loras` | STRING (JSON) | Selected Studio adapters: `[{"path":"style.safetensors","strength":0.7}]`. Connect to **GenGate Apply LoRAs.loras**. |

Text-to-image graphs can use prompt/dimensions/seed without an image or open document. A connected image socket with no available source produces a clear error. Use **Use selection** in the main Studio slot to send selected document pixels as `image`, with its editable alpha/inpainting `mask`. Extra image slots supply individual reference/mask pairs. Masks preserve transparency: white marks transparent pixels for inpainting. Reference preparation follows the Studio's existing reference-size limit. The graph receives colour-managed sRGB; imported results are converted into the target document's colour/depth.

## Studio LoRAs

Connect **IN.loras** to **GenGate Apply LoRAs.loras**, your graph's model to `model`, and optionally its CLIP to `clip`. Use the returned model/CLIP in the rest of the graph. The helper applies enabled Explorer entries in order, using each strength for both model and CLIP. An empty selection passes through; zero strength skips that adapter. It loads no base model or VAE.

The socket carries JSON such as `[{"path":"styles/ink.safetensors","strength":0.7}]`, so custom nodes can also consume it. ComfyUI must see the same relative LoRA filenames in its configured `loras` folders; share the model folder or configure ComfyUI's extra model paths. Missing files produce a named error. Studio selection alone does not apply adapters to an arbitrary graph: wire the helper where you want them applied.

## Updating older workflows

The simplified IN uses a new internal class id (`GenGateIN2`) to prevent old numeric links from silently acquiring another meaning. Recreate IN in old ComfyUI canvas workflows, reconnect the retained sockets and Export (API) again. GenGate automatically migrates existing API JSON links for retained values by name, including `source_prompt` to `prompt`. If the graph used a removed socket, it asks for re-export. No preset/component, document/layer/selection, metadata, reference-list or reference-count sockets are exposed. OUT's contract is unchanged.

## OUT ports

| Input | Behaviour |
| --- | --- |
| `images` | Required IMAGE or batch; creates new layers. Multiple OUT nodes are supported. |
| `mask` | Optional layer mask: white reveals. One mask broadcasts over a batch; otherwise count and dimensions must match the images. |
| `prompt` | Optional prompt to retain with the result, for example after a prompt-expansion node. Defaults to the submitted source prompt. |
| `layer_name` | Optional result-layer name. |
| `seed` | Optional actual workflow seed; otherwise the request seed is recorded. |
| `loras` | Optional JSON adapter recipe to record; otherwise records the Studio selection exposed by IN. The graph controls actual adapter application. |

The workflow determines the number and dimensions of results, including upscales. Limits: 1–8 images in total, 8192 px per side, 32 million pixels per image, 64 million pixels per request. Existing document dimensions stay as they are; without a document, GenGate creates a canvas large enough for the returned images. A batch imports as one undoable action.

Generated-layer metadata records the returned prompt, seed, adapter strengths and workflow path/SHA-256. Dragging that layer into the Studio restores its result and workflow file choice. Keep the workflow file alongside your project; the graph itself is not embedded in the image layer. Dropping into an extra reference slot imports pixels only.

## Transport and installation

`comfyui/GenGate` is a standalone custom-node package with its own GPL-3.0-or-later notice. The installer copies only its managed files into `ComfyUI/custom_nodes/GenGate`, preserves unrelated files and refuses to overwrite local edits. Python packages, model folders and other custom nodes are not upgraded.

The bridge uses standard ComfyUI upload/prompt/history/view APIs. Request data reaches IN through hidden execution metadata. OUT writes request-scoped temporary PNGs and history records. Only matching GenGate OUT results are imported; another SaveImage node or cached request cannot substitute a result. ComfyUI clears temporary storage when restarted. Cancellation drains only this request's pending queue entry; it never sends a global interrupt against another client's work.

Validation: `scripts/comfy_workflow_smoke.py` runs actual IN → ImageInvert → OUT and EmptyImage batches on a dedicated CPU-only ComfyUI with isolated user/input/output folders, without checkpoints. `scripts/photocraft_workflow_smoke.py` checks the native UI, layer masks/metadata, save/reopen and selection references through official PhotoCraft MCP using synthetic responses.
