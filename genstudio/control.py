"""A small JSON control client. The editor runs this on a background thread."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

from .lifecycle import hidden_process, http_json
from .settings import ROOT
from .processes import identity, process_lock

DEFAULT_BASE = "http://127.0.0.1:8766"  # Legacy placeholder; never discovered by probing this port.
BASE = os.environ.get("GENSTUDIO_SERVICE_URL", DEFAULT_BASE).rstrip("/")
ENDPOINT = ROOT / ".runtime/genstudio/endpoint.json"


def local_url(value):
    address = urlsplit(value)
    if (address.scheme != "http" or address.hostname != "127.0.0.1" or
            address.path or address.query or address.fragment or address.username or not address.port):
        raise ValueError("GenGate service must use a local 127.0.0.1 HTTP address.")
    return value


def discover():
    global BASE
    if os.environ.get("GENSTUDIO_SERVICE_URL"):
        BASE = local_url(os.environ["GENSTUDIO_SERVICE_URL"].rstrip("/"))
        return
    BASE = DEFAULT_BASE
    try:
        record = json.loads(ENDPOINT.read_text(encoding="utf-8"))
        if record.get("identity") == identity():
            BASE = local_url(record["url"])
    except (OSError, ValueError, KeyError, TypeError):
        pass


def health():
    if BASE == DEFAULT_BASE and not os.environ.get("GENSTUDIO_SERVICE_URL"):
        discover()
        if BASE == DEFAULT_BASE:
            return None
    try:
        value = http_json(local_url(BASE) + "/health", timeout=1)
        if value.get("service") != "photocraft-genstudio" or value.get("identity") != identity():
            if os.environ.get("GENSTUDIO_SERVICE_URL"):
                raise RuntimeError("The configured GenGate service belongs to another version or installation.")
            return None
        return value
    except (URLError, TimeoutError, ConnectionError):
        return None


def run(action, settings=None, event="manual"):
    if action == "models":
        from .models import catalog
        from .settings import validate
        selected = validate(settings)
        return catalog(selected["modelsDir"], selected["model"])
    current = health()
    if not current and action in ("status", "stop"):
        return {"state": "stopped", "ready": False, "owned": False, "message": "Stopped"}
    if not current:
        if os.environ.get("GENSTUDIO_SERVICE_URL"):
            raise RuntimeError("The isolated GenGate service is unavailable.")
        folder = ROOT / ".runtime/genstudio"
        folder.mkdir(parents=True, exist_ok=True)
        with process_lock(folder / "startup.lock", timeout=20):
            discover()
            if not health():
                with open(folder / "host.log", "ab", buffering=0) as out, open(folder / "host-errors.log", "ab", buffering=0) as err:
                    process = hidden_process([str(ROOT / ".venv/Scripts/python.exe"), "-X", "utf8", "-m", "genstudio.service"], stdout=out, stderr=err)
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    discover()
                    if health():
                        break
                    if process.poll() is not None:
                        raise RuntimeError("GenGate host failed to start. See .runtime/genstudio/host-errors.log.")
                    time.sleep(0.15)
                else:
                    raise RuntimeError("GenGate host startup timed out.")
    # Segmentation is editor-owned and uses a separate Direct process. Keep its
    # preferences out of the generation-service protocol (including older hosts).
    engine_settings = {key: value for key, value in settings.items() if key != "segmentation"} if settings is not None else None
    return http_json(BASE + "/engine", {"action": action, "settings": engine_settings, "event": event},
                     timeout=330 if action in ("start", "ensure") else 30)


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        request = json.load(sys.stdin)
        result = run(request["action"], request.get("settings"), request.get("event", "manual"))
        print(json.dumps(result))
    except Exception as error:
        if isinstance(error, HTTPError):
            try:
                detail = json.load(error).get("error", str(error))
            except Exception:
                detail = str(error)
        else:
            detail = str(error)
        print(json.dumps({"state": "error", "ready": False, "message": detail}))
        sys.exit(1)


if __name__ == "__main__":
    main()
