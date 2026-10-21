"""Segmentation assets and private Python modules. No torch imports or model loading."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tempfile
from urllib.request import urlopen
import zipfile
import contextlib
import os
import time

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = {"enabled": False, "detector": "yolov8s-worldv2", "segmenter": "sam3", "python": ""}
WHEEL = ("https://files.pythonhosted.org/packages/5b/4a/e29f91208f783d4e372bd3bc65e2d550618dedd8af6fd10ccd00e48f843d/ultralytics-8.4.183-py3-none-any.whl",
         "0826261c54f80c18c0fa0db4afd728f581c78db49cf2d9d6f254a2ce7c10576b")
CLIP = ("https://codeload.github.com/ultralytics/CLIP/zip/7ffa84b3bfa40c42ecc1c77147a855e69cb2dd40",
        "ea6799c740f2725175edee0881de9f354086e801dd33d4f43b17d18c8238fb54")


@contextlib.contextmanager
def preparation_lock(path, cancelled=lambda: False):
    """OS lock: another editor waits; crashes release the lock automatically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        deadline = time.monotonic() + 3600
        acquired = False
        while not acquired:
            if cancelled():
                raise InterruptedError("Cancelled")
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                if time.monotonic() > deadline:
                    raise TimeoutError("Another editor is still preparing segmentation") from None
                time.sleep(0.15)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def validate(value):
    if not isinstance(value, dict) or value.keys() - DEFAULTS.keys():
        raise ValueError("Invalid segmentation settings")
    result = dict(DEFAULTS, **value)
    if type(result["enabled"]) is not bool:
        raise ValueError("Segmentation enabled must be a boolean")
    if result["detector"] not in ("yolov8s-worldv2", "yolov8m-worldv2", "yolov8l-worldv2") or result["segmenter"] != "sam3":
        raise ValueError("Unsupported segmentation model")
    if not isinstance(result["python"], str) or len(result["python"]) > 4096 or "\0" in result["python"]:
        raise ValueError("Invalid segmentation Python path")
    return result


def manifest():
    return json.loads((ROOT / "photocraft/segmentation-models.json").read_text(encoding="utf-8"))


def existing(models, entry):
    models = Path(models)
    for folder in ("segmentation", "sams", "sam3", "ultralytics", ""):
        path = models / folder / Path(entry["path"]).name
        if path.is_file() and path.stat().st_size == entry["bytes"]:
            return path
    return models / entry["path"]


def ensure_models(models, emit=lambda event: None, cancelled=lambda: False, detector="yolov8s-worldv2"):
    if not Path(models).is_dir():
        raise ValueError("Choose an existing Models folder in GenGate Settings")
    with preparation_lock(Path(models) / "segmentation/.gengate-download.lock", cancelled):
        return _ensure_models(models, emit, cancelled, detector)


def _ensure_models(models, emit, cancelled, detector):
    from scripts.download_flux3 import download
    models = Path(models).expanduser().resolve()
    if not models.is_dir():
        raise ValueError("Choose an existing Models folder in GenGate Settings")
    spec = manifest()
    result = {}
    for entry in spec["files"]:
        if entry["id"] not in (detector, "sam3"):
            continue
        if cancelled():
            raise InterruptedError("Cancelled")
        found = existing(models, entry)
        if found.is_file():
            result[entry["id"]] = str(found)
            continue
        def progress(state, count):
            if cancelled():
                raise InterruptedError("Cancelled")
            emit({"message": "Preparing " + entry["id"] + " · " + state,
                  "progress": count / entry["bytes"] * 0.4})
        partial = (models / entry["path"]).with_suffix(".pt.part")
        remaining = max(0, entry["bytes"] - (partial.stat().st_size if partial.is_file() else 0))
        if shutil.disk_usage(models).free < remaining + (128 << 20):
            raise ValueError("Not enough free space for " + entry["id"])
        download(entry, models, spec, progress)
        result[entry["id"]] = str(models / entry["path"])
    return result


def ensure_modules(emit=lambda event: None):
    """Extract two hash-pinned pure-Python packages, never pip into ComfyUI."""
    with preparation_lock(ROOT / ".runtime/segmentation/.gengate-software.lock"):
        return _ensure_modules(emit)


def _ensure_modules(emit):
    target = ROOT / ".runtime/segmentation/deps"
    marker = target / "gengate-deps.json"
    identity = [WHEEL[1], CLIP[1]]
    if marker.is_file() and json.loads(marker.read_text()) == identity:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    emit({"message": "Preparing the private segmentation software", "progress": 0.02})
    with tempfile.TemporaryDirectory(prefix="prepare-", dir=target.parent) as temporary:
        stage = Path(temporary)
        for number, (url, expected) in enumerate((WHEEL, CLIP)):
            archive = stage / f"source-{number}.zip"
            with urlopen(url, timeout=45) as response, archive.open("wb") as output:
                shutil.copyfileobj(response, output, 1 << 20)
            if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
                raise ValueError("Segmentation software checksum mismatch")
            with zipfile.ZipFile(archive) as source:
                for item in source.infolist():
                    parts = PurePosixPath(item.filename).parts
                    if number:
                        parts = parts[1:]
                        if not parts or (parts[0] != "clip" and parts[0] != "LICENSE"):
                            continue
                        if parts == ("LICENSE",):
                            parts = ("CLIP-LICENSE.txt",)
                    if not parts or PurePosixPath(item.filename).is_absolute() or ".." in parts or "\\" in item.filename or ":" in item.filename or item.file_size > 32 << 20:
                        raise ValueError("Invalid segmentation software archive")
                    destination = stage.joinpath("deps", *parts)
                    if item.is_dir():
                        destination.mkdir(parents=True, exist_ok=True)
                    else:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(source.read(item))
        # Only the private overlay is updated; base Python packages remain intact.
        shutil.copytree(stage / "deps", target, dirs_exist_ok=True)
    marker.write_text(json.dumps(identity), encoding="utf-8")
    return target
