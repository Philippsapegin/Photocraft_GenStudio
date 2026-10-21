"""File/JSON transport for native editor jobs. Model work stays in the common host."""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import sys
import time
from urllib.error import HTTPError

from . import control
from .lifecycle import http_json
from genfill.image_ops import decode_png, png_bytes

REMOVE_PROMPT = (
    "The same scene without the masked object. Remove that object completely. "
    "Show only the background that was behind it in the transparent masked area. "
    "Do not replace, redraw or recreate the object, including a different or similar version of it. "
    "Leave this area free of objects, filled with a seamless opaque continuation of the surrounding background. "
    "Do not add any new objects, people, text or decorations. Keep everything outside the mask unchanged."
)


def edit(folder, settings, emit, *, request=http_json, configure=None, interval=0.2, prompt=None):
    folder = Path(folder)
    if prompt is not None and (not isinstance(prompt, str) or len(prompt.encode("utf-8")) > 6000 or "\0" in prompt):
        raise ValueError("Prompt must contain at most 6000 UTF-8 bytes.")
    cancelled = lambda: (folder / "cancel").exists()
    if cancelled():
        raise InterruptedError("Cancelled")
    # Configure starts the small host only. The selected engine's own first-request
    # policy and nonblocking startup message are handled by EngineHost.generate.
    emit({"message": "Connecting to the local AI engine…"})
    (configure or control.run)("configure", settings)
    if cancelled():
        raise InterruptedError("Cancelled")
    body = {"image": base64.b64encode((folder / "input.png").read_bytes()).decode("ascii"),
            "mask": base64.b64encode((folder / "mask.png").read_bytes()).decode("ascii"),
            "mode": "fill" if prompt is not None else "remove", "prompt": prompt if prompt is not None else REMOVE_PROMPT,
            "preserve_roi": True, "return_rectangle": True, "masked_input": True,
            "max_size": 768, "grow": 8, "feather": 2}
    job = None
    complete = False
    try:
        job = request(control.BASE + "/jobs", body, timeout=30)["id"]
        last = None
        deadline = time.monotonic() + 3600
        while time.monotonic() < deadline:
            if cancelled():
                raise InterruptedError("Cancelled")
            state = request(control.BASE + "/jobs/" + job, timeout=2)
            message = state.get("message", "Generating…")
            event = {"message": message, "progress": state.get("progress")}
            if event != last:
                emit(event)
                last = event
            if state["state"] == "done":
                result = request(control.BASE + "/jobs/" + job + "/result", timeout=30)
                if cancelled():
                    raise InterruptedError("Cancelled")
                image = decode_png(result["image"], 16 << 20)
                mask = decode_png(result["mask"], 16 << 20)
                if image.size != mask.size or result.get("x") != 0 or result.get("y") != 0:
                    raise ValueError("The engine returned an invalid result rectangle.")
                (folder / "result.png").write_bytes(png_bytes(image))
                (folder / "result-mask.png").write_bytes(png_bytes(mask))
                complete = True
                emit({"done": True, "width": image.width, "height": image.height})
                return
            if state["state"] in ("error", "cancelled"):
                raise RuntimeError(message)
            time.sleep(interval)
        raise TimeoutError("Generation timed out. The document is unchanged.")
    finally:
        if job and not complete:
            try:
                request(control.BASE + "/jobs/" + job + "/cancel", {}, timeout=2)
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, required=True)
    args = parser.parse_args()
    def emit(value):
        print(json.dumps(value), flush=True)
    try:
        payload = json.load(sys.stdin)
        if isinstance(payload, dict) and "segmentation" in payload:
            from .segmentation import run
            run(args.folder, payload["settings"], payload["segmentation"], emit)
        elif isinstance(payload, dict) and "studio" in payload:
            from .studio_edit import generate
            generate(args.folder, payload["settings"], payload["studio"], emit)
        elif isinstance(payload, dict) and "settings" in payload:
            edit(args.folder, payload["settings"], emit, prompt=payload.get("prompt"))
        else:
            edit(args.folder, payload, emit)
    except Exception as error:
        if isinstance(error, HTTPError):
            try:
                message = json.load(error).get("error", str(error))
            except Exception:
                message = str(error)
        else:
            message = str(error)
        emit({"error": message})
        sys.exit(1)


if __name__ == "__main__":
    main()
