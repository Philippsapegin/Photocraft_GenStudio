"""Native Studio transport: full images, never the inpainting/compositing path."""
import base64
import json
from pathlib import Path
import time
from . import control
from .lifecycle import http_json


def generate(folder, settings, body, emit, *, configure=None, request=http_json, interval=0.2):
    folder = Path(folder)
    cancelled = lambda: (folder / "cancel").exists()
    if cancelled():
        raise InterruptedError("Cancelled")
    emit({"message": "Preparing generation…", "progress": 0.0})
    (configure or control.run)("configure", settings)
    if cancelled():
        raise InterruptedError("Cancelled")
    job = request(control.BASE + "/studio/jobs", body, timeout=90)
    route = control.BASE + "/studio/jobs/" + job["id"]
    try:
        deadline = time.monotonic() + 8 * 1800
        while time.monotonic() < deadline:
            if cancelled():
                raise InterruptedError("Cancelled")
            state = request(route)
            emit({"message": state["message"], "progress": state.get("progress")})
            if state["state"] == "done":
                result = request(route + "/result", timeout=120)
                images = result.get("images", [])
                if len(images) != body.get("batch", 1):
                    raise RuntimeError("The engine returned an incomplete batch.")
                for index, image in enumerate(images):
                    (folder / f"result-{index}.png").write_bytes(base64.b64decode(image["image"], validate=True))
                (folder / "results.json").write_text(json.dumps({"count": len(images)}))
                emit({"done": True})
                return
            if state["state"] in ("error", "cancelled"):
                raise RuntimeError(state["message"])
            time.sleep(interval)
        raise RuntimeError("Generation timed out.")
    finally:
        if cancelled():
            request(route + "/cancel", {})
