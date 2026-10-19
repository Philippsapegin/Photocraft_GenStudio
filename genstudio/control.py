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

BASE = os.environ.get("GENSTUDIO_SERVICE_URL", "http://127.0.0.1:8766").rstrip("/")
_address = urlsplit(BASE)
if (_address.scheme != "http" or _address.hostname != "127.0.0.1" or
        _address.path or _address.query or _address.fragment or _address.username or
        not _address.port):
    raise ValueError("Gen Studio service must use a local 127.0.0.1 HTTP address.")


def health():
    try:
        value = http_json(BASE + "/health", timeout=1)
        if value.get("service") != "photocraft-genstudio":
            raise RuntimeError("Port 8766 belongs to another application.")
        return value
    except (URLError, TimeoutError, ConnectionError):
        return None


def run(action, settings=None, event="manual"):
    if action == "models":
        from .models import catalog
        from .settings import validate
        return catalog(validate(settings)["modelsDir"])
    current = health()
    if not current and action in ("status", "stop"):
        return {"state": "stopped", "ready": False, "owned": False, "message": "Stopped"}
    if not current:
        if BASE != "http://127.0.0.1:8766":
            raise RuntimeError("The isolated Gen Studio service is unavailable.")
        folder = ROOT / ".runtime/genstudio"
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / "host.log", "ab", buffering=0) as out, open(folder / "host-errors.log", "ab", buffering=0) as err:
            process = hidden_process([str(ROOT / ".venv/Scripts/python.exe"), "-X", "utf8", "-m", "genstudio.service"], stdout=out, stderr=err)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not health():
            if process.poll() is not None:
                # Another editor may have won the bind race.
                if health():
                    break
                raise RuntimeError("Gen Studio host failed to start. See .runtime/genstudio/host-errors.log.")
            time.sleep(0.15)
        if not health():
            raise RuntimeError("Gen Studio host startup timed out.")
    return http_json(BASE + "/engine", {"action": action, "settings": settings, "event": event}, timeout=330)


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
