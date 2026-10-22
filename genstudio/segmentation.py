"""Cancellable Direct segmentation transport, independent of the generation host."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time

from .segmentation_models import ROOT, ensure_models, ensure_modules, validate
from .processes import gpu_lease


def run(folder, settings, request, emit):
    folder = Path(folder)
    options = validate(settings.get("segmentation", {}))
    kind = request.get("kind")
    if kind not in ("prepare", "subject", "sky", "background", "object"):
        raise ValueError("Unknown segmentation request")
    cancelled = lambda: (folder / "cancel").exists()
    if cancelled():
        raise InterruptedError("Cancelled")
    if not options["enabled"] and kind != "prepare":
        raise ValueError("Enable AI segmentation in GenGate Settings first")
    emit({"message": "Preparing Direct segmentation…", "progress": 0.01})
    modules = ensure_modules(emit)
    models = ensure_models(settings["modelsDir"], emit, cancelled, options["detector"])
    if kind == "prepare":
        emit({"message": "Segmentation models ready", "done": True})
        return
    python = options["python"] or settings.get("directPython") or settings.get("comfyPython")
    if not python or not Path(python).is_file():
        raise ValueError("Choose a CUDA Python in GenGate Settings → Segmentation")
    if os.environ.get("GENGATE_NO_INFERENCE") == "1":
        raise RuntimeError("Segmentation inference is disabled for validation")
    payload = dict(kind=kind, detector=options["detector"], models=models, modules=str(modules), folder=str(folder.resolve()))
    (folder / "segmentation.json").write_text(json.dumps(payload), encoding="utf-8")
    env = os.environ.copy()
    for name in ("PYTHONHOME", "PYTHONPATH"):
        env.pop(name, None)
    env.update(PYTHONUTF8="1", YOLO_AUTOINSTALL="false", YOLO_VERBOSE="false", YOLO_OFFLINE="true",
               YOLO_CONFIG_DIR=str(ROOT / ".runtime/segmentation/config"))
    with (folder / "segmentation-errors.log").open("wb") as errors:
        child = subprocess.Popen([python, "-X", "utf8", str(ROOT / "scripts/run_segmentation.py"),
                                  str(folder / "segmentation.json")],
                                 cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                 stdout=errors, stderr=errors,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            emit({"message": "Starting Direct segmentation…", "progress": 0.45})
            last = None
            deadline = time.monotonic() + 900
            while child.poll() is None:
                if cancelled():
                    raise InterruptedError("Cancelled")
                if time.monotonic() > deadline:
                    raise TimeoutError("Segmentation timed out; the document is unchanged")
                status = folder / "segmentation-progress.json"
                try:
                    event = json.loads(status.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    event = None
                if event and event != last:
                    emit(event)
                    last = event
                time.sleep(0.15)
            if child.returncode:
                detail = (folder / "segmentation-errors.log").read_text(encoding="utf-8", errors="replace")[-3000:]
                try:
                    log=ROOT / ".runtime/segmentation/last-error.log"
                    log.parent.mkdir(parents=True,exist_ok=True)
                    log.write_text(detail,encoding="utf-8")
                except OSError:
                    pass
                message=next((line.strip() for line in reversed(detail.splitlines()) if line.strip()),"The worker exited unexpectedly")
                for prefix in ("RuntimeError: ","ValueError: ","ImportError: ","ModuleNotFoundError: "):
                    message=message.removeprefix(prefix)
                raise RuntimeError("Direct segmentation failed. " + message)
            if cancelled():
                raise InterruptedError("Cancelled")
            emit({"message": "Applying selection…", "progress": 1, "done": True})
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=15)


def choose_box(boxes, scores, size):
    """Prefer a confident, large, central foreground instance. Pure, testable policy."""
    import math
    width, height = size
    candidates = []
    for box, confidence in zip(boxes, scores):
        if len(box) != 4 or not all(math.isfinite(float(v)) for v in (*box, confidence)):
            continue
        x0, y0, x1, y1 = box
        x0, x1 = max(0, min(width, x0)), max(0, min(width, x1))
        y0, y1 = max(0, min(height, y0)), max(0, min(height, y1))
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        area = (x1 - x0) * (y1 - y0) / max(1, width * height)
        distance = ((x0 + x1) / 2 / width - 0.5)**2 + ((y0 + y1) / 2 / height - 0.5)**2
        candidates.append((confidence * math.sqrt(area) * (1 - min(0.5, distance)), [x0, y0, x1, y1]))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def worker(payload):
    """Only this short-lived GPU process imports torch/Ultralytics."""
    folder = Path(payload["folder"])
    def waiting():
        temporary = folder / "segmentation-progress.tmp"
        temporary.write_text(json.dumps({"message": "Waiting for generation to release the GPU\u2026",
                                         "progress": 0.4}), encoding="utf-8")
        temporary.replace(folder / "segmentation-progress.json")
    # The process actually using CUDA owns the lock. If the editor or transport
    # exits unexpectedly, the GPU cannot be handed to generation prematurely.
    with gpu_lease(cancelled=lambda: (folder / "cancel").exists(), waiting=waiting):
        return _worker(payload)


def _worker(payload):
    import contextlib
    import sys
    sys.path.insert(0, payload["modules"])
    folder = Path(payload["folder"])
    def progress(message, fraction):
        temporary = folder / "segmentation-progress.tmp"
        temporary.write_text(json.dumps({"message": message, "progress": fraction}), encoding="utf-8")
        temporary.replace(folder / "segmentation-progress.json")
    with contextlib.redirect_stdout(sys.stderr):
        import importlib.util
        missing = [name for name in ("torch", "torchvision", "numpy", "cv2", "PIL", "matplotlib", "scipy", "yaml", "requests", "psutil", "polars", "ftfy", "regex", "tqdm", "thop", "timm")
                   if importlib.util.find_spec(name) is None]
        if missing:
            raise RuntimeError("Segmentation Python is missing: " + ", ".join(missing) + ". See photocraft/segmentation-requirements.txt; install into a separate runtime.")
        import numpy as np
        from PIL import Image
        import torch
        from ultralytics import YOLOWorld, settings as yolo_settings
        yolo_settings.update({"sync": False})
        from ultralytics.models.sam import SAM3Predictor, SAM3SemanticPredictor
        if not torch.cuda.is_available():
            raise RuntimeError("Direct segmentation requires a CUDA-capable GPU")
        source = Image.open(folder / "input.png").convert("RGBA")
        if source.width * source.height > 2048 * 2048:
            raise ValueError("Segmentation input exceeds its size limit")
        rgba = np.array(source)
        alpha = rgba[..., 3:4].astype(np.float32) / 255
        rgb = (rgba[..., :3] * alpha + 127 * (1 - alpha)).astype(np.uint8)
        bgr = np.ascontiguousarray(rgb[..., ::-1])
        model = payload["models"]["sam3"]
        options = {"model": model, "conf": 0.25, "task": "segment", "mode": "predict", "device": "0",
                   "verbose": False, "save": False, "compile": False, "max_det": 16, "quantize": 16, "imgsz": 1008}
        with torch.inference_mode():
            if payload["kind"] == "sky":
                progress("Finding sky with SAM3…", 0.65)
                predictor = SAM3SemanticPredictor(overrides=options)
                predictor.set_image(bgr)
                results = predictor(text=["sky"])
            else:
                progress("Finding the subject with YOLO-World…", 0.55)
                detector = YOLOWorld(payload["models"][payload["detector"]])
                detections = detector.predict(bgr, conf=0.15, max_det=32, device=0, verbose=False)[0]
                boxes = detections.boxes
                box = choose_box(boxes.xyxy.cpu().tolist(), boxes.conf.cpu().tolist(), source.size) if boxes is not None else None
                # Free the detector before allocating SAM3 on a small GPU.
                del detections, detector, boxes
                import gc
                gc.collect()
                torch.cuda.empty_cache()
                progress("Building the subject mask with SAM3…", 0.72)
                predictor = SAM3Predictor(overrides=options)
                predictor.set_image(bgr)
                results = predictor(bboxes=[box or [0, 0, source.width, source.height]])
            mask = np.zeros((source.height, source.width), dtype=np.uint8)
            for result in results:
                if result.masks is not None:
                    for value in result.masks.data[:16]:
                        found = Image.fromarray((value.float().cpu().numpy() * 255).clip(0, 255).astype(np.uint8))
                        if found.size != source.size:
                            found = found.resize(source.size, Image.Resampling.BILINEAR)
                        np.maximum(mask, np.asarray(found), out=mask)
            mask = np.minimum(mask, rgba[..., 3])
            Image.fromarray(mask).save(folder / "result-mask.png")
            progress("Selection ready", 1)
