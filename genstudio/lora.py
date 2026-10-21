"""Edit-only adapters. Import model libraries only inside an actual masked job."""
from .models import resolve_lora


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
