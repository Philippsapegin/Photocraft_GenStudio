# ComfyUI workflows

The development integration uses ComfyUI workflows for Studio, Ai Fill and Remove Brush. Direct retains its native presets. The ComfyUI graph owns model loading and sampling; Advanced can explicitly override marked loader filenames for an individual run.

## Setup

1. Choose **ComfyUI** in **Preferences > GenGate Settings**, and configure its local address, folder and Python.
2. Use **Install / Update nodes**, restart ComfyUI, then refresh its browser tab. The installed package includes the IN/OUT nodes and a small frontend extension.
3. Build the graph with **GenGate IN** and **GenGate OUT**. Connect only the PhotoCraft values it uses.

LanPaint is optional; install it if your chosen graph uses it. Setup keeps existing custom-node files and Python packages.

## Studio: the active graph

Studio's **Active workflow in ComfyUI** field opens the configured local ComfyUI address. Every Generate requests a fresh API snapshot of the workflow currently open in ComfyUI, including unsaved widgets and connections. There is no file-export step for Studio.

The frontend serializes the active workflow with ComfyUI's `app.graphToPrompt()` API. A request-scoped websocket message and local HTTP reply deliver that snapshot to the host. If several browser tabs respond, the most recently used tab wins. If no frontend responds or serialization fails, generation reports an error; an old file or cached graph is not substituted. Keep the browser tab open, and refresh it after updating the extension.

The snapshot's SHA-256 is stored with results. Since the graph is live, restoring a generated layer restores its source image, texts and adapter recipe; a later Generate uses the graph currently open in ComfyUI. Export/save graphs in ComfyUI when you want to keep a particular recipe.

## Ai Fill and Remove Brush: separate files

**Preferences > GenGate Settings > Advanced** has separate **Ai Fill workflow** and **Remove Brush workflow** fields for the ComfyUI engine. Export each graph with ComfyUI **Export (API)** and choose its JSON file. The file is read anew for a request; ordinary canvas JSON needs an API export first.

These graphs receive the tool's prepared context image, inpainting mask and editing instruction through IN. They must return exactly one image at the requested context dimensions. PhotoCraft keeps the original document coordinates and restricts the imported edit with its editable mask. Studio graphs can instead return different sizes or batches.

## Optional component overrides

**Automatic** leaves that component exactly as defined in the workflow. Selecting a file in Advanced replaces the corresponding filename only in a graph copy submitted for that run, for both Studio and native tools. GenGate never saves these substitutions into the JSON file or the open browser graph. The result's workflow hash describes the effective graph after substitution.

Rename exactly one intended loader for each overridden component using its ComfyUI node title:

| Advanced field | Exact node title | Supported loaders / input |
| --- | --- | --- |
| Model | `GenGate: Model` | `UNETLoader`, `UnetLoaderGGUF`, `UnetLoaderGGUFAdvanced`: `unet_name`; `CheckpointLoaderSimple`: `ckpt_name` |
| Text encoder | `GenGate: Text encoder` | `CLIPLoader`, `CLIPLoaderGGUF`: `clip_name` |
| VAE | `GenGate: VAE` | `VAELoader`: `vae_name` |
| FLUX prompt upsampler | `GenGate: Prompt upsampler` | `CLIPLoader`, `CLIPLoaderGGUF`: `clip_name` |

Keep the loader's filename as a widget, rather than converting it into a linked input. The selected file must live in the matching shared Models subfolder and be listed by ComfyUI under its exact relative name. Add that Models folder to ComfyUI's model paths and restart ComfyUI if necessary. GenGate does not load weights to perform this substitution.

Missing/duplicate titles, unsupported loaders, linked filename inputs and unlisted filenames produce an error before queueing. Other loaders, dtype settings, connections and sampler settings remain unchanged. For custom or multi-encoder loaders, keep Automatic and configure them in ComfyUI. This contract requires no additional custom nodes or frontend changes.

## IN sockets

| Socket | Type | Data |
| --- | --- | --- |
| `image`, `mask` | IMAGE, MASK | Main Studio reference or native tool context. White mask pixels mark the area to regenerate. |
| `prompt` | STRING | Submitted text; connect it to your graph's text encoder. |
| `negative_prompt` | STRING | Negative text; connect it to the negative text encoder. The graph owns the resulting CONDITIONING. |
| `width`, `height`, `seed`, `batch` | INT | Requested dimensions, request seed and batch. |
| `reference_2` through `reference_10`, with `_mask` partners | IMAGE, MASK | Individual additional Studio references. The main `image` is reference 1. |
| `loras` | STRING (JSON) | Studio Explorer selections, or the native tool's Constant LoRA: relative paths and strengths. |

Text-only graphs can leave image sockets disconnected. A connected image socket with no source gives an explanatory error. Studio's **Use selection** supplies selected pixels and their mask. Source images use the existing bounded, colour-managed sRGB preparation; imports convert into the document's colour mode/depth.

## Negative text and layer recipes

**Neg**, immediately before the font button, splits the prompt area into upper positive and lower negative fields. Hiding the lower field preserves its text but submits an empty negative prompt. Both source texts are saved in generated-layer metadata and restored from a generated layer. Older layers default to an empty negative prompt.

Direct passes negative text into its conditioning path. Distilled CFG=1 models can ignore negative conditioning; storing the text does not make every model support it. In ComfyUI, connect `negative_prompt` to an appropriate encoder and sampler branch.

OUT has no prompt input. The host retains the submitted positive and negative source texts, regardless of any prompt processing inside the graph.

## Adapters and steps

Connect **IN.loras** to **GenGate Apply LoRAs.loras**, plus your graph's model and optional CLIP. Use the returned model/CLIP downstream. The helper applies entries in order with their strengths; empty selections pass through. ComfyUI must see the same relative filenames in its configured `loras` folders. Example recipe: `[{"path":"styles/ink.safetensors","strength":0.7}]`.

The **Steps** field beside Constant LoRA is optional and stored per preset. Empty keeps Direct's original edit schedule (Qwen 8, FLUX 16, FIBO 4); an integer from 1 to 200 overrides Ai Fill/Remove generation steps. Qwen resamples its preset sigma schedule with unchanged endpoints. Studio preset generation keeps its own default schedule.

For native ComfyUI tool graphs, connect **GenGate Edit Steps** to the sampler's step count. Its `default_steps` remains the graph default whenever the PhotoCraft field is empty. An explicit PhotoCraft override takes precedence. Studio always uses the helper's workflow default. Graphs that do not wire this helper retain their own sampling settings. No arbitrary sampler nodes are rewritten.

## OUT sockets

| Input | Behaviour |
| --- | --- |
| `images` | Required image/batch; Studio imports new layers. |
| `mask` | Optional layer mask: white reveals. One mask broadcasts over a batch; otherwise dimensions/count must match. |
| `layer_name` | Optional result-layer name. |
| `seed` | Optional actual graph seed for metadata. |
| `loras` | Optional adapter recipe to record; otherwise records the submitted selection. |

Studio supports multiple OUT nodes, up to eight images total, 8192 px per side, 32 million pixels per image and 64 million pixels per request. A batch imports as one undoable action. The graph determines result dimensions; without a document, Studio creates a canvas large enough for the results.

## Updating graphs

IN retains its existing socket indices and appends `negative_prompt`. If it does not appear in an older canvas node, recreate IN. OUT uses the new internal class `GenGateOUT2` because removing the prompt input shifts later sockets: recreate OUT and reconnect it in older canvas graphs. The host migrates older API JSON by name and removes obsolete OUT prompt links. Legacy removed IN sockets produce a re-export error.

The bridge uses the `gengate-comfy-v3` envelope through normal upload/prompt/history/view APIs. Results are request-scoped temporary PNGs; a cached or unrelated OUT cannot supply another request's result. Cancellation removes only this request's queued entry and never globally interrupts an external ComfyUI server.

## Validation

`scripts/comfy_workflow_smoke.py` runs actual CPU-only IN/OUT image operations, both tool files, the step helper and the active-frontend websocket/HTTP exchange without checkpoints. JavaScript module and HTTP tests verify fresh graphs and tab selection. `scripts/photocraft_workflow_smoke.py` verifies the native Neg UI, metadata/save/restore, settings controls and active-graph generation using synthetic responses.
