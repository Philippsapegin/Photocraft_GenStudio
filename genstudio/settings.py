from __future__ import annotations

import json
import math
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = ROOT / "config.photocraft.json"
ENGINES = ("comfy", "direct")
MODEL = "qwen-image-2.1-turbo"
POLICIES = ("off", "appStart", "firstRequest")


def defaults():
    from genfill.server import load_config
    old = load_config()
    from .segmentation_models import DEFAULTS
    return {"segmentation": dict(DEFAULTS), "reduceEditResolution": False,
            "model": MODEL, "textEncoder": "", "vae": "", "presets": {},
            "studioMode": "preset", "studioWorkflow": "", "fillWorkflow": "", "removeWorkflow": "",
            "engine": "comfy", "comfyStartup": "firstRequest", "directStartup": "firstRequest",
            "comfyUrl": old["comfy_url"], "comfyDir": old.get("comfy_dir", ""),
            "comfyPython": old.get("comfy_python", ""),
            "directPython": old.get("comfy_python", str(ROOT / ".runtime/qwen-direct/Scripts/python.exe")),
            "modelsDir": str(Path(old.get("comfy_dir", str(ROOT / ".runtime/comfy"))) / "models"),
            "runtimeRoot": str(ROOT)}


def validate(value):
    if not isinstance(value, dict):
        raise ValueError("Engine settings must be an object.")
    value = dict(value)
    # The early Diffusers prototype used a separate model directory. Preserve preferences
    # when opening that build's profile, but never download or convert weights implicitly.
    previous = value.pop("directModel", None)
    if previous is not None and not isinstance(previous, str):
        raise ValueError("Invalid engine setting: directModel")
    if previous and "modelsDir" not in value and Path(previous).is_dir():
        value["modelsDir"] = previous
    result = defaults()
    unknown = value.keys() - result.keys()
    if unknown:
        raise ValueError("Unknown engine setting: " + ", ".join(sorted(unknown)))
    result.update(value)
    from .segmentation_models import validate as validate_segmentation
    result["segmentation"] = validate_segmentation(result["segmentation"])
    from .models import MODELS
    presets = result["presets"]
    if not isinstance(presets, dict) or presets.keys() - MODELS.keys():
        raise ValueError("Unknown model preset settings.")
    result["presets"] = {key: validate_components(item) for key, item in presets.items()}
    for key, item in result.items():
        if key == "reduceEditResolution":
            if not isinstance(item, bool):
                raise ValueError("Reduce edit resolution must be a checkbox value.")
            continue
        if key in ("segmentation", "presets"):
            continue
        if not isinstance(item, str) or len(item) > 4096 or "\x00" in item:
            raise ValueError("Invalid engine setting: " + key)
    if result["engine"] not in ENGINES:
        raise ValueError("Unknown engine.")
    if result["studioMode"] not in ("preset", "workflow"):
        raise ValueError("Unknown Studio processing mode.")
    from .models import specification
    specification(result["model"])
    if any(result[key] not in POLICIES for key in ("comfyStartup", "directStartup")):
        raise ValueError("Unknown automatic start policy.")
    url = urlparse(result["comfyUrl"])
    if (url.scheme != "http" or url.hostname not in ("localhost", "127.0.0.1", "::1")
            or url.username or url.password or url.query or url.fragment or url.path not in ("", "/")):
        raise ValueError("ComfyUI must use a local HTTP address, for example http://127.0.0.1:8190.")
    try:
        if not url.port:
            raise ValueError()
    except ValueError:
        raise ValueError("ComfyUI address must contain a valid port.") from None
    return result


def component_defaults():
    return {"modelFile": "", "textEncoder": "", "promptUpsampler": "", "vae": "", "constantLora": "", "constantLoraStrength": 1.0, "constantLoraSteps": None}


def validate_components(value):
    result = component_defaults()
    if not isinstance(value, dict) or value.keys() - result.keys():
        raise ValueError("Invalid preset components.")
    result.update(value)
    for key, item in result.items():
        if key == "constantLoraSteps":
            if item is not None and (isinstance(item, bool) or not isinstance(item, int) or not 1 <= item <= 200):
                raise ValueError("Edit steps must be empty or an integer between 1 and 200.")
        elif key == "constantLoraStrength":
            if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item) or not -20 <= item <= 20:
                raise ValueError("Constant LoRA strength must be a number between -20 and 20.")
        elif not isinstance(item, str) or len(item) > 4096 or "\x00" in item:
            raise ValueError("Invalid preset component: " + key)
    return result


def active_components(settings):
    presets = settings.get("presets", {})
    if presets:
        return validate_components(presets.get(settings["model"], {}))
    # Migrate the existing flat encoder/VAE preferences without leaking them to
    # another family once the first per-preset entry has been saved.
    return validate_components({"textEncoder": settings.get("textEncoder", ""), "vae": settings.get("vae", "")})


def load(path=SETTINGS_PATH):
    return validate(json.loads(path.read_text(encoding="utf-8-sig"))) if path.exists() else defaults()


def save(value, path=SETTINGS_PATH):
    data = validate(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temp.replace(path)
    return data
