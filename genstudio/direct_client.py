from __future__ import annotations

import base64
from io import BytesIO
import time
from PIL import Image

from genfill.comfy import Cancelled
from genfill.image_ops import png_bytes
from .lifecycle import DIRECT_URL, http_json


def generate(image, mask, seed, prompt, cancel, progress):
    encode = lambda im: base64.b64encode(png_bytes(im)).decode("ascii")
    job = http_json(DIRECT_URL + "/jobs", {"image": encode(image), "mask": encode(mask),
                   "seed": seed, "prompt": prompt}, timeout=20)
    return wait(job, cancel, progress)


def generate_studio(references, seed, prompt, width, height, cancel, progress):
    if http_json(DIRECT_URL + "/health").get("studio_protocol", 0) < 1:
        raise RuntimeError("Restart the Direct engine in Gen Studio Settings to enable full-image generation.")
    job = http_json(DIRECT_URL + "/jobs", {"kind": "studio", "references": [
        base64.b64encode(png_bytes(image)).decode("ascii") for image in references],
        "prompt": prompt, "seed": seed, "width": width, "height": height}, timeout=60)
    return wait(job, cancel, progress)


def wait(job, cancel, progress):
    path = DIRECT_URL + "/jobs/" + job["id"]
    try:
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            if cancel.is_set():
                raise Cancelled()
            job = http_json(path)
            progress(job["message"])
            if job["state"] == "done":
                result = http_json(path + "/result", timeout=30)
                with Image.open(BytesIO(base64.b64decode(result["image"]))) as output:
                    return output.convert("RGBA")
            if job["state"] == "error":
                raise RuntimeError(job["message"])
            if job["state"] == "cancelled":
                raise Cancelled()
            cancel.wait(0.25)
        raise RuntimeError("Direct Qwen generation timed out.")
    finally:
        if cancel.is_set():
            http_json(path + "/cancel", {})
