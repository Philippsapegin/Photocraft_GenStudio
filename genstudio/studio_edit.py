"""Native Studio transport: full images, never the inpainting/compositing path."""
import base64
import json
from pathlib import Path
import time
from . import control
from .lifecycle import http_json
from .transport import poll


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
    complete=False
    try:
        deadline = time.monotonic() + 8 * 1800
        while time.monotonic() < deadline:
            if cancelled():
                raise InterruptedError("Cancelled")
            state = poll(route,request=request,cancelled=cancelled,pause=time.sleep,progress=lambda m:emit({"message":m}))
            emit({"message": state["message"], "progress": state.get("progress")})
            if state["state"] == "done":
                result = poll(route + "/result",request=request,cancelled=cancelled,pause=time.sleep,timeout=30)
                images = result.get("images", [])
                from .comfy_workflow import enabled
                custom = enabled(settings)
                if not 1 <= len(images) <= 8 or not custom and len(images) != body.get("batch", 1):
                    raise RuntimeError("The engine returned an incomplete batch.")
                for index, image in enumerate(images):
                    (folder / f"result-{index}.png").write_bytes(base64.b64decode(image["image"], validate=True))
                    if custom and image.get("mask"):
                        (folder / f"result-{index}-mask.png").write_bytes(base64.b64decode(image["mask"], validate=True))
                metadata = {"count": len(images)}
                if custom:
                    metadata.update(workflow=result.get("workflow", {}), images=[{k: v for k, v in row.items() if k not in ("image", "mask")} | {"hasMask": bool(row.get("mask"))} for row in images])
                (folder / "results.json").write_text(json.dumps(metadata), encoding="utf-8")
                complete=True
                emit({"done": True})
                return
            if state["state"] in ("error", "cancelled"):
                raise RuntimeError(state["message"])
            time.sleep(interval)
        raise RuntimeError("Generation timed out.")
    finally:
        if not complete:
            try:request(route + "/cancel", {},timeout=2)
            except Exception:pass
