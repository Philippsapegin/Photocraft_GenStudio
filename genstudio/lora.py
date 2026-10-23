"""Separate Constant/edit and Studio adapter stacks; imports stay inside inference jobs."""
from .models import resolve_lora


def validate_studio(value, root=None):
    """Portable identities; never let a layer escape Models/loras."""
    import math
    from pathlib import Path, PurePosixPath
    if not isinstance(value, list) or len(value) > 64:
        raise ValueError('Choose up to 64 LoRAs.')
    selected, seen = [], set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError('Invalid LoRA selection.')
        path, strength = item.get('path'), item.get('strength', 1.0)
        if not isinstance(path, str) or not path or len(path) > 2048 or '\0' in path or '\\' in path or ':' in path:
            raise ValueError('Choose a LoRA inside Models/loras.')
        relative = PurePosixPath(path)
        if relative.is_absolute() or any(p in ('', '.', '..') for p in path.split('/')) or relative.suffix.lower() != '.safetensors':
            raise ValueError('Choose a Safetensors LoRA inside Models/loras.')
        if path.casefold() in seen:
            raise ValueError('A LoRA was selected more than once.')
        if isinstance(strength, bool) or not isinstance(strength, (int, float)) or not math.isfinite(strength) or not -20 <= strength <= 20:
            raise ValueError('LoRA strength must be between -20 and 20.')
        seen.add(path.casefold())
        if root is not None:
            base = (Path(root).expanduser().resolve() / 'loras').resolve()
            file = (base / path).resolve()
            if not file.is_relative_to(base) or not file.is_file() or not file.stat().st_size:
                raise ValueError('LoRA is missing or outside Models/loras: ' + path)
        selected.append({'path': path, 'strength': float(strength)})
    return selected


def apply_studio(model, clip, root, selected, progress):
    from pathlib import Path
    selected = validate_studio(selected or [], root)
    if not selected:
        return model, clip
    import comfy.sd
    import comfy.utils
    base = (Path(root) / 'loras').resolve()
    for item in selected:
        if not item['strength']:
            continue
        progress('Applying LoRA: ' + Path(item['path']).stem)
        weights = comfy.utils.load_torch_file(str(base / item['path']), safe_load=True)
        model, clip = comfy.sd.load_lora_for_models(model, clip, weights, item['strength'], item['strength'])
    return model, clip


def studio_graph(nodes, model, clip, selected):
    for index, item in enumerate(validate_studio(selected or [])):
        if not item['strength']:
            continue
        node = str(500 + index)
        nodes[node] = {'class_type': 'LoraLoader', 'inputs': {'model': model, 'clip': clip,
            'lora_name': item['path'], 'strength_model': item['strength'], 'strength_clip': item['strength']}}
        model, clip = [node, 0], [node, 1]
    return model, clip


def apply_constant(model, clip, root, components, progress):
    selected = resolve_lora(root, components)
    if selected is None:
        return model, clip
    import comfy.sd
    import comfy.utils
    progress("Applying Constant LoRA…")
    weights = comfy.utils.load_torch_file(selected["path"], safe_load=True)
    strength = components["constantLoraStrength"]
    # Comfy clones patchers; the cached base model and encoder stay unmodified.
    return comfy.sd.load_lora_for_models(model, clip, weights, strength, strength)


def graph(config, nodes, model, clip):
    """Return patched links for a masked workflow; Studio never calls this."""
    if not config.get("constant_lora") or not config.get("constant_lora_strength", 1.0):
        return model, clip
    strength = config.get("constant_lora_strength", 1.0)
    nodes["15"] = {"class_type": "LoraLoader", "inputs": {"model": model, "clip": clip,
        "lora_name": config["constant_lora"], "strength_model": strength, "strength_clip": strength}}
    return ["15", 0], ["15", 1]
