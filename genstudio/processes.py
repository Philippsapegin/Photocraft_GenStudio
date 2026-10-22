"""Process identity and cancellable OS locks; no GPU imports."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import time

from . import VERSION
from .settings import ROOT

PROTOCOL = 2


def identity():
    digest = hashlib.sha256()
    for folder in ("genstudio", "genfill"):
        for path in sorted((ROOT / folder).glob("*.py")):
            digest.update(path.relative_to(ROOT).as_posix().encode())
            digest.update(path.read_bytes())
    return {"version": VERSION, "protocol": PROTOCOL,
            "runtimeRoot": os.path.normcase(str(ROOT.resolve())), "code": digest.hexdigest()}


@contextmanager
def process_lock(path, *, cancelled=lambda: False, timeout=30, waiting=lambda: None):
    """The OS releases the lock even if its owning process crashes."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.write(b"\0")
        stream.flush()
        deadline = time.monotonic() + timeout
        notified = False
        while True:
            if cancelled():
                raise InterruptedError("Cancelled")
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Timed out waiting for another GenGate process")
                if not notified:
                    waiting()
                    notified = True
                time.sleep(0.1)
        try:
            if cancelled():
                raise InterruptedError("Cancelled")
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def gpu_lease(*, cancelled=lambda: False, waiting=lambda: None):
    # Shared across installed versions, but generation and selection keep their
    # own processes, dependencies, settings and model lifetimes.
    folder = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".cache"))) / "GenGate"
    return process_lock(folder / "gpu.lock", cancelled=cancelled, waiting=waiting, timeout=1800)


def release_gpu():
    """Offload cached Direct models before selection can acquire the GPU."""
    import sys
    memory = sys.modules.get("comfy.model_management")
    if memory is not None:
        memory.unload_all_models()
        import gc
        gc.collect()
        memory.soft_empty_cache()
