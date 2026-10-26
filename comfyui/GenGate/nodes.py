"""Official Comfy queue/history transport, without callbacks or model imports.

SPDX-License-Identifier: GPL-3.0-or-later
"""
import hashlib
import json
from pathlib import Path
import re
import uuid

PROTOCOL = "gengate-comfy-v1"
PORTS = [
    ("image", "IMAGE"), ("mask", "MASK"), ("document", "IMAGE"), ("layer", "IMAGE"),
    ("selection", "MASK"), ("prompt", "STRING"), ("width", "INT"), ("height", "INT"),
    ("seed", "INT"), ("batch", "INT"), ("source_prompt", "STRING"), ("reference_count", "INT"),
    ("references", "IMAGE"), ("reference_masks", "MASK"), ("preset", "STRING"),
    ("model", "STRING"), ("text_encoder", "STRING"), ("vae", "STRING"),
    ("prompt_upsampler", "STRING"), ("loras", "STRING"), ("metadata", "STRING"),
] + [(name, kind) for i in range(2, 11) for name, kind in ((f"reference_{i}", "IMAGE"), (f"reference_{i}_mask", "MASK"))]


def context(extra):
    value = extra.get("gengate") if isinstance(extra, dict) else None
    if not isinstance(value, dict) or value.get("protocol") != PROTOCOL:
        raise ValueError("Run this workflow from GenGate in PhotoCraft.")
    if not isinstance(value.get("request_id"), str) or not re.fullmatch(r"[a-f0-9]{32}", value["request_id"]):
        raise ValueError("Invalid GenGate request identity.")
    return value


def asset_path(name, request_id):
    import folder_paths
    if not isinstance(name, str) or not name.endswith(" [temp]"):
        raise ValueError("GenGate inputs must be request-scoped temporary images.")
    name = name[:-7]
    if "\\" in name or ":" in name or Path(name).is_absolute() or ".." in Path(name).parts:
        raise ValueError("Invalid GenGate input image path.")
    if not Path(name).name.startswith("GenGate-" + request_id + "-"):
        raise ValueError("An input image belongs to another GenGate request.")
    root = Path(folder_paths.get_temp_directory()).resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > 64 << 20:
        raise ValueError("The GenGate input image is missing or too large.")
    return path


def load_image(name, request_id, grayscale=False):
    import numpy as np
    import torch
    from PIL import Image
    with Image.open(asset_path(name, request_id)) as image:
        if max(image.size) > 32768 or image.width * image.height > 32 << 20:
            raise ValueError("GenGate inputs support up to 32 million pixels.")
        if grayscale:
            return torch.from_numpy(np.array(image.convert("L"), dtype=np.float32) / 255.0)[None, ...]
        rgba = np.array(image.convert("RGBA"), dtype=np.float32) / 255.0
    return torch.from_numpy(rgba[..., :3].copy())[None, ...], torch.from_numpy(1.0 - rgba[..., 3])[None, ...]


class GenGateIN:
    CATEGORY = "GenGate"
    FUNCTION = "receive"
    RETURN_TYPES = tuple(kind for _, kind in PORTS)
    RETURN_NAMES = tuple(name for name, _ in PORTS)
    OUTPUT_IS_LIST = tuple(name in ("references", "reference_masks") for name, _ in PORTS)
    DESCRIPTION = "Inputs supplied by the GenGate studio in PhotoCraft. Connect only the sockets your workflow needs."

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {}, "hidden": {"extra_pnginfo": "EXTRA_PNGINFO", "prompt_graph": "PROMPT", "unique_id": "UNIQUE_ID"}}

    @classmethod
    def IS_CHANGED(cls, extra_pnginfo=None, **kwargs):
        # Some Comfy versions probe the cache without hidden execution metadata.
        # These endpoint nodes must always receive this request's data anyway.
        return float("nan")

    def receive(self, extra_pnginfo=None, prompt_graph=None, unique_id=None):
        value = context(extra_pnginfo)
        refs = value.get("references", [])
        if not isinstance(refs, list) or len(refs) > 10:
            raise ValueError("GenGate supports up to ten references.")
        used = set()
        for node in (prompt_graph or {}).values():
            for item in node.get("inputs", {}).values():
                if isinstance(item, list) and len(item) == 2 and str(item[0]) == str(unique_id) and isinstance(item[1], int):
                    used.add(item[1])
        needed = {PORTS[i][0] for i in used if 0 <= i < len(PORTS)}
        images, masks = [], []
        for index, name in enumerate(refs):
            # All-reference outputs preserve different reference dimensions as a
            # Comfy list, rather than resizing them into an invented batch.
            names = {"image", "mask"} if index == 0 else {f"reference_{index + 1}", f"reference_{index + 1}_mask"}
            if not used or needed & (names | {"references", "reference_masks"}):
                image, mask = load_image(name, value["request_id"])
            else:
                image, mask = None, None
            images.append(image); masks.append(mask)
        result = {"image": images[0] if images else None, "mask": masks[0] if masks else None,
                  "references": images, "reference_masks": masks, "reference_count": len(refs),
                  "prompt": value.get("prompt", ""), "source_prompt": value.get("source_prompt", value.get("prompt", "")),
                  "width": value.get("width", 1024), "height": value.get("height", 1024),
                  "seed": value.get("seed", 0), "batch": value.get("batch", 1),
                  "loras": json.dumps(value.get("loras", []), ensure_ascii=False),
                  "metadata": json.dumps(value.get("document_info", {}), ensure_ascii=False)}
        for key in ("preset", "model", "text_encoder", "vae", "prompt_upsampler"):
            result[key] = value.get(key, "")
        for key in ("document", "layer", "selection"):
            name = value.get(key)
            result[key] = None
            if name and (not used or key in needed):
                loaded = load_image(name, value["request_id"], key == "selection")
                result[key] = loaded if key == "selection" else loaded[0]
        for i in range(2, 11):
            result[f"reference_{i}"] = images[i - 1] if len(images) >= i else None
            result[f"reference_{i}_mask"] = masks[i - 1] if len(masks) >= i else None
        for key in needed:
            if result.get(key) is None or key in ("references", "reference_masks") and not refs:
                raise ValueError("GenGate input '" + key + "' is not available. Load its image or selection in PhotoCraft.")
        return tuple(result.get(name) for name, _ in PORTS)


class GenGateOUT:
    CATEGORY = "GenGate"
    FUNCTION = "send"
    RETURN_TYPES = ()
    OUTPUT_NODE = True
    DESCRIPTION = "Return images and optional layer masks/recipe data to the requesting GenGate studio. No files are written into your document directly."

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"images": ("IMAGE",)},
                "optional": {"mask": ("MASK",), "prompt": ("STRING", {"forceInput": True}),
                             "layer_name": ("STRING", {"forceInput": True}), "seed": ("INT", {"forceInput": True}),
                             "loras": ("STRING", {"forceInput": True})},
                "hidden": {"extra_pnginfo": "EXTRA_PNGINFO", "unique_id": "UNIQUE_ID"}}

    @classmethod
    def IS_CHANGED(cls, extra_pnginfo=None, **kwargs):
        return float("nan")

    def send(self, images, mask=None, prompt=None, layer_name=None, seed=None, loras=None, extra_pnginfo=None, unique_id=None):
        import folder_paths
        import numpy as np
        from PIL import Image
        value = context(extra_pnginfo)
        shape = tuple(images.shape)
        if (len(shape) != 4 or not 1 <= shape[0] <= 8 or shape[3] not in (3, 4)
                or min(shape[1:3]) < 1 or max(shape[1:3]) > 8192
                or shape[1] * shape[2] > 32 << 20 or shape[0] * shape[1] * shape[2] > 64 << 20):
            raise ValueError("GenGate OUT supports 1–8 images, up to 8192 px per side and 64 million pixels in total.")
        if prompt is not None and (not isinstance(prompt, str) or len(prompt.encode("utf-8")) > 12000 or "\0" in prompt):
            raise ValueError("The returned prompt is too large or invalid.")
        if layer_name is not None and (not isinstance(layer_name, str) or len(layer_name) > 120 or "\0" in layer_name):
            raise ValueError("The returned layer name is invalid.")
        if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**53):
            raise ValueError("The returned seed is invalid.")
        adapters = value.get("loras", []) if loras is None else json.loads(loras)
        if not isinstance(adapters, list) or len(adapters) > 64 or len(json.dumps(adapters)) > 200 << 10:
            raise ValueError("Invalid returned LoRA recipe.")
        masks = None
        if mask is not None:
            if len(mask.shape) == 2:
                mask = mask[None, ...]
            if tuple(mask.shape) not in ((1, shape[1], shape[2]), (shape[0], shape[1], shape[2])):
                raise ValueError("The layer mask must match the returned image dimensions and batch.")
            masks = mask.detach().float().cpu().numpy()
            if not np.isfinite(masks).all():
                raise ValueError("The returned mask contains non-finite pixels.")
        folder = Path(folder_paths.get_temp_directory()) / "gengate"
        folder.mkdir(parents=True, exist_ok=True)
        node = hashlib.sha256(str(unique_id).encode()).hexdigest()[:12]
        invocation = uuid.uuid4().hex[:12]
        records, previews = [], []
        for index, tensor in enumerate(images):
            pixels = tensor.detach().float().cpu().numpy()
            if not np.isfinite(pixels).all():
                raise ValueError("The returned image contains non-finite pixels.")
            filename = f"GenGate-{value['request_id']}-{node}-{invocation}-{index}.png"
            Image.fromarray(np.rint(np.clip(pixels, 0, 1) * 255).astype(np.uint8)).save(folder / filename)
            descriptor = {"filename": filename, "subfolder": "gengate", "type": "temp"}
            row = {"image": descriptor, "prompt": value.get("source_prompt", value.get("prompt", "")) if prompt is None else prompt,
                   "name": layer_name or "GenGate", "seed": value.get("seed", 0) + index if seed is None else seed + index,
                   "loras": adapters}
            if masks is not None:
                filename = filename.removesuffix(".png") + "-mask.png"
                Image.fromarray(np.rint(np.clip(masks[min(index, len(masks) - 1)], 0, 1) * 255).astype(np.uint8)).save(folder / filename)
                row["mask"] = {"filename": filename, "subfolder": "gengate", "type": "temp"}
            records.append(row); previews.append(descriptor)
        return {"ui": {"images": previews, "gengate": [{"protocol": PROTOCOL, "request_id": value["request_id"], "images": records}]}}
