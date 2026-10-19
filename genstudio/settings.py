from __future__ import annotations

import json
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
    return {"model": MODEL, "textEncoder": "", "vae": "",
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
    for key, item in result.items():
        if not isinstance(item, str) or len(item) > 4096 or "\x00" in item:
            raise ValueError("Invalid engine setting: " + key)
    if result["engine"] not in ENGINES:
        raise ValueError("Unknown engine.")
    if result["model"] != MODEL:
        raise ValueError("Unsupported model. Select Qwen Image 2.1 Turbo.")
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


def load(path=SETTINGS_PATH):
    return validate(json.loads(path.read_text(encoding="utf-8-sig"))) if path.exists() else defaults()


def save(value, path=SETTINGS_PATH):
    data = validate(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temp.replace(path)
    return data
