"""Resolve the same local Turbo checkpoints for both engines, without copying weights."""
from pathlib import Path

FILES = {
    "diffusion_model": ("qwen_image_2.1_turbo_int8_convrot.safetensors", ("diffusion_models", "unet")),
    "text_encoder": ("qwen3vl_8b_w4a8.safetensors", ("text_encoders", "clip")),
    "vae": ("qwen_image_2.1_vae_bf16.safetensors", ("vae",)),
}
TEXT_ENCODERS = ("qwen3vl_8b_w4a8.safetensors", "qwen3vl_8b_int8_convrot.safetensors", "qwen3vl_8b_bf16.safetensors")


def resolve(root):
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("Choose an existing Models folder in Gen Studio.")
    found = {}
    for key, (name, folders) in FILES.items():
        names = TEXT_ENCODERS if key == "text_encoder" else (name,)
        candidates = []
        for candidate_name in names:
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
        # Junctions/symlinks may expose the same file more than once.
        unique = {str(path.resolve()).casefold(): (path, relative) for path, relative in candidates}
        if not unique:
            raise ValueError(f"Models folder is missing {folders[0]}/{name}. No weights were downloaded.")
        if len(unique) > 1:
            raise ValueError(f"Models folder contains multiple copies of {name}; choose a more specific folder.")
        path, relative = next(iter(unique.values()))
        if not path.stat().st_size:
            raise ValueError(f"Model file is empty: {path}")
        found[key] = {"path": str(path.resolve()), "name": relative}
    return found


def extra_paths(root):
    """ComfyUI's own YAML format; paths are serialized with JSON (a YAML subset)."""
    root = str(Path(root).expanduser().resolve())
    return {"genstudio": {"base_path": root, "is_default": True,
                          "diffusion_models": "diffusion_models\nunet\n.",
                          "text_encoders": "text_encoders\nclip\n.", "vae": "vae\n."}}
