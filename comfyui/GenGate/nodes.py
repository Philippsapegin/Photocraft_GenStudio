"""Official Comfy queue/history transport, without callbacks or model imports.

SPDX-License-Identifier: GPL-3.0-or-later
"""
import hashlib
import json
import math
from pathlib import Path
import re
import uuid

PROTOCOL = "gengate-comfy-v3"
PORTS = [
    ("image", "IMAGE"), ("mask", "MASK"), ("prompt", "STRING"),
    ("width", "INT"), ("height", "INT"), ("seed", "INT"), ("batch", "INT"), ("loras", "STRING"),
] + [(name, kind) for i in range(2, 11) for name, kind in ((f"reference_{i}", "IMAGE"), (f"reference_{i}_mask", "MASK"))] + [("negative_prompt", "STRING")]


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
    DESCRIPTION = "Studio image/mask, prompt, dimensions, seed, batch and individual references. 'loras' is a JSON recipe with file paths and strengths; connect it to GenGate Apply LoRAs. Models, CLIP and VAE come from your workflow."

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
            names = {"image", "mask"} if index == 0 else {f"reference_{index + 1}", f"reference_{index + 1}_mask"}
            if not used or needed & names:
                image, mask = load_image(name, value["request_id"])
            else:
                image, mask = None, None
            images.append(image); masks.append(mask)
        result = {"image": images[0] if images else None, "mask": masks[0] if masks else None,
                  "prompt": value.get("prompt", ""), "negative_prompt": value.get("negative_prompt", ""),
                  "width": value.get("width", 1024), "height": value.get("height", 1024),
                  "seed": value.get("seed", 0), "batch": value.get("batch", 1),
                  "loras": json.dumps(value.get("loras", []), ensure_ascii=False)}
        for i in range(2, 11):
            result[f"reference_{i}"] = images[i - 1] if len(images) >= i else None
            result[f"reference_{i}_mask"] = masks[i - 1] if len(masks) >= i else None
        for key in needed:
            if result.get(key) is None:
                raise ValueError("GenGate input '" + key + "' is not available. Load its image or selection in PhotoCraft.")
        return tuple(result.get(name) for name, _ in PORTS)


class GenGateApplyLoRAs:
    CATEGORY = "GenGate"
    FUNCTION = "apply"
    RETURN_TYPES = ("MODEL", "CLIP")
    RETURN_NAMES = ("model", "clip")
    DESCRIPTION = "Apply the LoRAs selected in PhotoCraft's Explorer, in order, with their strengths. Connect IN.loras and your workflow's model; CLIP is optional. ComfyUI must see the same relative files under its loras folders. An empty selection passes the model/CLIP through."

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"model": ("MODEL",), "loras": ("STRING", {"forceInput": True})},
                "optional": {"clip": ("CLIP",)}}

    def apply(self, model, loras, clip=None):
        import folder_paths
        from nodes import LoraLoader
        if not isinstance(loras, str) or len(loras) > 200 << 10:
            raise ValueError("Connect GenGate IN's loras recipe to GenGate Apply LoRAs.")
        selected = json.loads(loras)
        if not isinstance(selected, list) or len(selected) > 64:
            raise ValueError("Choose up to 64 LoRAs in PhotoCraft.")
        available = {name.replace("\\", "/"): name for name in folder_paths.get_filename_list("loras")}
        checked, seen = [], set()
        for item in selected:
            path = item.get("path") if isinstance(item, dict) else None
            strength = item.get("strength", 1.0) if isinstance(item, dict) else None
            if (not isinstance(path, str) or not path or len(path) > 2048 or any(c in path for c in ("\\", ":", "\0"))
                    or any(p in ("", ".", "..") for p in path.split("/")) or not path.lower().endswith(".safetensors")
                    or path.casefold() in seen):
                raise ValueError("Invalid or duplicate GenGate LoRA path.")
            if isinstance(strength, bool) or not isinstance(strength, (int, float)) or not math.isfinite(strength) or not -20 <= strength <= 20:
                raise ValueError("LoRA strength must be between -20 and 20.")
            seen.add(path.casefold())
            if strength:
                if path not in available:
                    raise ValueError("ComfyUI cannot find LoRA '" + path + "'. Share the Models/loras folder with ComfyUI.")
                checked.append((available[path], strength))
        loader = LoraLoader()
        for path, strength in checked:
            model, clip = loader.load_lora(model, clip, path, strength, strength)
        return model, clip


class GenGateEditSteps:
    CATEGORY = 'GenGate'
    FUNCTION = 'steps'
    RETURN_TYPES = ('INT',)
    RETURN_NAMES = ('steps',)
    DESCRIPTION = 'Connect to the sampler step count. Native Ai Fill/Remove uses the optional Steps override from PhotoCraft; when blank (or for Studio), use this workflow default.'

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'default_steps': ('INT', {'default': 8, 'min': 1, 'max': 200})},
                'hidden': {'extra_pnginfo': 'EXTRA_PNGINFO'}}

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float('nan')

    def steps(self, default_steps, extra_pnginfo=None):
        override = context(extra_pnginfo).get('steps')
        steps = default_steps if override is None else override
        if isinstance(steps, bool) or not isinstance(steps, int) or not 1 <= steps <= 200:
            raise ValueError('Steps must be between 1 and 200.')
        return (steps,)


class GenGateOUT:
    CATEGORY = "GenGate"
    FUNCTION = "send"
    RETURN_TYPES = ()
    OUTPUT_NODE = True
    DESCRIPTION = "Return images and optional layer masks/recipe data to the requesting GenGate studio. No files are written into your document directly."

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"images": ("IMAGE",)},
                "optional": {"mask": ("MASK",),
                             "layer_name": ("STRING", {"forceInput": True}), "seed": ("INT", {"forceInput": True}),
                             "loras": ("STRING", {"forceInput": True})},
                "hidden": {"extra_pnginfo": "EXTRA_PNGINFO", "unique_id": "UNIQUE_ID"}}

    @classmethod
    def IS_CHANGED(cls, extra_pnginfo=None, **kwargs):
        return float("nan")

    def send(self, images, mask=None, layer_name=None, seed=None, loras=None, extra_pnginfo=None, unique_id=None):
        import folder_paths
        import numpy as np
        from PIL import Image
        value = context(extra_pnginfo)
        shape = tuple(images.shape)
        if (len(shape) != 4 or not 1 <= shape[0] <= 8 or shape[3] not in (3, 4)
                or min(shape[1:3]) < 1 or max(shape[1:3]) > 8192
                or shape[1] * shape[2] > 32 << 20 or shape[0] * shape[1] * shape[2] > 64 << 20):
            raise ValueError("GenGate OUT supports 1–8 images, up to 8192 px per side and 64 million pixels in total.")
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
            row = {"image": descriptor,
                   "name": layer_name or "GenGate", "seed": value.get("seed", 0) + index if seed is None else seed + index,
                   "loras": adapters}
            if masks is not None:
                filename = filename.removesuffix(".png") + "-mask.png"
                Image.fromarray(np.rint(np.clip(masks[min(index, len(masks) - 1)], 0, 1) * 255).astype(np.uint8)).save(folder / filename)
                row["mask"] = {"filename": filename, "subfolder": "gengate", "type": "temp"}
            records.append(row); previews.append(descriptor)
        return {"ui": {"images": previews, "gengate": [{"protocol": PROTOCOL, "request_id": value["request_id"], "images": records}]}}
