"""Shared model discovery and explicit component overrides; never opens weights."""
from pathlib import Path

MODEL = "qwen-image-2.1-turbo"
FILES = {
    "diffusion_model": ("qwen_image_2.1_turbo_int8_convrot.safetensors", ("diffusion_models", "unet")),
    "text_encoder": ("qwen3vl_8b_w4a8.safetensors", ("text_encoders", "clip")),
    "vae": ("qwen_image_2.1_vae_bf16.safetensors", ("vae",)),
}
TEXT_ENCODERS = ("qwen3vl_8b_w4a8.safetensors", "qwen3vl_8b_int8_convrot.safetensors", "qwen3vl_8b_bf16.safetensors")
FLUX = "flux-3-image-klein"
MODELS = {
    MODEL: {"label": "Qwen Image 2.1 Turbo", "references": 10, "grid": 32,
            "files": FILES, "text_encoders": TEXT_ENCODERS},
    FLUX: {"label": "FLUX 3 Image Klein", "references": 10, "grid": 16,
           "files": {
               "diffusion_model": ("flux3-image-klein-int8-convrot.safetensors", ("diffusion_models", "unet")),
               "text_encoder": ("qwen3vl_4b_int8_convrot.safetensors", ("text_encoders", "clip")),
               "vae": ("flux2-vae.safetensors", ("vae",)),
               "prompt_upsampler": ("qwen3.5_9b_flux3_prompt_upsampler_w4a8.safetensors", ("text_encoders", "clip")),
           },
           "text_encoders": ("qwen3vl_4b_int8_convrot.safetensors", "qwen3vl_4b_bf16.safetensors")},
}
UPSAMPLERS = ("qwen3.5_9b_flux3_prompt_upsampler_w4a8.safetensors",
              "qwen3.5_9b_flux3_prompt_upsampler_int8_convrot.safetensors",
              "qwen3.5_9b_flux3_prompt_upsampler_bf16.safetensors")


def specification(model=MODEL):
    if model not in MODELS:
        raise ValueError("Unsupported model: " + str(model))
    return MODELS[model]


def catalog(root, model=MODEL):
    spec = specification(model)
    files, encoders = spec["files"], spec["text_encoders"]
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("Choose an existing Models folder in Settings.")
    result = {"model": model, "referenceLimit": spec["references"], "modelsDir": str(root), "textEncoders": [], "vaes": []}
    for key, output in (("text_encoder", "textEncoders"), ("vae", "vaes")):
        name, folders = files[key]
        seen = set()
        candidates = []
        for folder in folders:
            base = root / folder
            if base.is_dir():
                candidates.extend((p, p.relative_to(base).as_posix()) for p in base.rglob("*.safetensors"))
        candidates.extend((p, p.name) for p in root.glob("*.safetensors") if p.name in (*encoders, files["vae"][0]))
        for path, comfy_name in candidates:
            resolved = path.resolve()
            if not path.is_file() or not path.stat().st_size:
                continue
            identity = str(resolved).casefold()
            if identity in seen:
                continue
            seen.add(identity)
            compatible = path.name in encoders if key == "text_encoder" else path.name == name
            result[output].append({"value": path.relative_to(root).as_posix(), "name": comfy_name,
                                   "compatible": compatible})
        names = encoders if key == "text_encoder" else (name,)
        result[output].sort(key=lambda item: (names.index(Path(item["value"]).name)
                            if Path(item["value"]).name in names else 100, item["value"].casefold()))
        result[output] = result[output][:2048]
        compatible = [item for item in result[output] if item["compatible"]]
        result["defaultTextEncoder" if key == "text_encoder" else "defaultVae"] = compatible[0]["value"] if compatible else ""
    return result


def resolve(root, text_encoder="", vae="", model=MODEL):
    spec = specification(model)
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("Choose an existing Models folder in GenGate.")
    overrides = {"text_encoder": text_encoder, "vae": vae}
    found = {}
    for key, (name, folders) in spec["files"].items():
        override = overrides.get(key, "")
        if not isinstance(override, str):
            raise ValueError("Invalid model component selection.")
        if override and (Path(override).is_absolute() or ".." in Path(override.replace(chr(92), "/")).parts or ":" in override):
            raise ValueError("Choose a component inside the Models folder.")
        names = ((override,) if override else spec["text_encoders"] if key == "text_encoder"
                 else UPSAMPLERS if key == "prompt_upsampler" else (name,))
        candidates = []
        for candidate_name in names:
            explicit = root / candidate_name
            if override and explicit.is_file():
                folder = next((f for f in folders if explicit.is_relative_to(root / f)), None)
                candidates.append((explicit, explicit.relative_to(root / folder).as_posix() if folder else explicit.name))
            else:
                for folder in folders:
                    base = root / folder
                    if base.is_dir():
                        candidates.extend((path, path.relative_to(base).as_posix()) for path in base.rglob(candidate_name) if path.is_file())
                flat = root / candidate_name
                if flat.is_file():
                    candidates.append((flat, candidate_name))
            if candidates:
                name = candidate_name
                break
        unique = {str(path.resolve()).casefold(): (path, relative) for path, relative in candidates}
        if not unique:
            raise ValueError(f"Models folder is missing {folders[0]}/{override or name}. No weights were downloaded.")
        if len(unique) > 1:
            raise ValueError(f"Models folder contains multiple copies of {name}; choose a component in Advanced settings.")
        path, relative = next(iter(unique.values()))
        # Existing ComfyUI installations often use model-directory junctions.
        # The choice is lexically inside the selected source; follow those links
        # in place instead of forcing users to duplicate their checkpoint files.
        if not path.stat().st_size:
            raise ValueError(f"Model file is empty: {path}")
        found[key] = {"path": str(path.resolve()), "name": relative}
    return found


def extra_paths(root):
    root = str(Path(root).expanduser().resolve())
    return {"genstudio": {"base_path": root, "is_default": True,
                          "diffusion_models": "diffusion_models\nunet\n.",
                          "text_encoders": "text_encoders\nclip\n.", "vae": "vae\n."}}
