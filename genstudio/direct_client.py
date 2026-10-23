from __future__ import annotations

import base64
from io import BytesIO
import time
from PIL import Image

from genfill.comfy import Cancelled
from genfill.image_ops import png_bytes
from .lifecycle import DIRECT_URL, http_json
from .transport import poll


def generate(image, mask, seed, prompt, cancel, progress, *, base=DIRECT_URL):
    encode = lambda im: base64.b64encode(png_bytes(im)).decode("ascii")
    job = http_json(base + "/jobs", {"image": encode(image), "mask": encode(mask),
                   "seed": seed, "prompt": prompt}, timeout=20)
    return wait(job, cancel, progress, base=base)


def generate_studio(references, seed, prompt, width, height, cancel, progress, *, base=DIRECT_URL, loras=None):
    if http_json(base + "/health").get("studio_protocol", 0) < (2 if loras else 1):
        raise RuntimeError("Restart the Direct engine in Preferences > GenGate to enable full-image generation.")
    job = http_json(base + "/jobs", {"kind": "studio", "references": [
        base64.b64encode(png_bytes(image)).decode("ascii") for image in references],
        "prompt": prompt, "seed": seed, "width": width, "height": height, "loras": loras or []}, timeout=60)
    return wait(job, cancel, progress, base=base)


def wait(job, cancel, progress, *, base=DIRECT_URL):
    path = base + "/jobs/" + job["id"]
    complete=False
    try:
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            if cancel.is_set():
                raise Cancelled()
            job = poll(path,request=http_json,cancelled=cancel.is_set,pause=cancel.wait,progress=progress)
            progress(job["message"])
            if job["state"] == "done":
                result = poll(path + "/result",request=http_json,cancelled=cancel.is_set,pause=cancel.wait,timeout=30)
                with Image.open(BytesIO(base64.b64decode(result["image"]))) as output:
                    image=output.convert("RGBA")
                    complete=True
                    return image
            if job["state"] == "error":
                raise RuntimeError(job["message"])
            if job["state"] == "cancelled":
                raise Cancelled()
            cancel.wait(0.25)
        raise RuntimeError("Direct generation timed out.")
    finally:
        if not complete:
            try: http_json(path + "/cancel", {},timeout=2)
            except Exception: pass
