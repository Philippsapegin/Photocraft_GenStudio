"""Read-only setup discovery and the last successfully installed location. No engines."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from genstudio import editor_patch as ep


def location_file():
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share")) / "GenGate/setup.json"


def object_at(path, *, preferences=False):
    if not path.is_file():
        return {}
    limit = 8 * 1024 * 1024 if preferences else 128 * 1024
    if path.stat().st_size > limit:
        raise ValueError("Saved settings exceed the allowed size: " + str(path))
    value = ep.read(path)
    if not isinstance(value, dict):
        raise ValueError("Saved setup settings must be an object.")
    return value


def valid_host(value):
    return bool(value) and all((Path(value) / name).is_file() for name in ep.FILES)


def installation(value):
    if not value:
        return {}
    value = Path(value).resolve()
    meta = object_at(value / "genstudio-install.json")
    if meta.get("installMode") == "overlay" and valid_host(meta.get("host")):
        return {**meta, "installDir": str(value)}
    return {}


def root_for_runtime(runtime):
    if not runtime:
        return None
    path = Path(runtime).resolve()
    return path.parent.parent.parent if path.name == "runtime" and path.parent.parent.name == "versions" else None


def for_host(host, install=""):
    host = ep.host_folder(host)
    state = ep.state_for(host)
    profile = ep.profile_folder(host)
    prefs = object_at(profile / "preferences.json", preferences=True)
    settings = ep.previous_settings(host, prefs)
    if not settings:
        settings = object_at(host / "runtime/config.photocraft.json")
    root = root_for_runtime(state.get("runtime") or settings.get("runtimeRoot"))
    if root is None and state.get("package"):
        package = Path(state["package"]).resolve()
        if package.parent.name == "versions":
            root = package.parent.parent
    meta = installation(root or install)
    if meta and Path(meta["host"]).resolve() != host:
        meta = {}
    if not settings and meta.get("runtime"):
        settings = object_at(Path(meta["runtime"]) / "config.photocraft.json")
    if not settings and not meta:
        return {"found": False}
    direct = bool(settings["directPython"]) if "directPython" in settings else meta.get("directInstalled", True)
    return {"found": True, "host": str(host), "installDir": meta.get("installDir", str(root) if root else ""),
            "settings": settings, "installDirect": bool(direct),
            "directReusable": direct and bool(settings.get("directPython")) and Path(settings["directPython"]).is_file()}


def discover(host="", install="", *, location=None, profile=None, hosts=None, installs=None):
    def attempt(function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (OSError, ValueError, TypeError, KeyError):
            return {}
    if host:
        return attempt(for_host, host, install) if attempt(valid_host, host) else {"found": False}
    if install:
        meta = attempt(installation, install)
        return attempt(for_host, meta["host"], install) if meta else {"found": False}
    try:
        last = object_at(location or location_file())
        meta = installation(last.get("installDir"))
    except (OSError, ValueError):
        last, meta = {}, {}
    if meta and Path(meta["host"]).resolve() == Path(last.get("host", "")).resolve():
        found = attempt(for_host, meta["host"], meta["installDir"])
        if found.get("found"):
            return found
    # The native profile can locate a nonstandard/portable installation through
    # its runtime, even when this older setup never wrote a location record.
    profile = profile or ep.profile_folder(Path("__nonportable__"))
    prefs = attempt(object_at, profile / "preferences.json", preferences=True)
    values = prefs.get("genStudio", {})
    if isinstance(values, dict):
        meta = attempt(installation, root_for_runtime(values.get("runtimeRoot")))
        if meta:
            found = attempt(for_host, meta["host"], meta["installDir"])
            if found.get("found"):
                return found
    if installs is None:
        installs = [Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Programs/GenGate"]
    for folder in installs:
        meta = attempt(installation, folder)
        if meta:
            found = attempt(for_host, meta["host"], meta["installDir"])
            if found.get("found"):
                return found
    if hosts is None:
        hosts = [Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Programs/ArtCraft/PhotoCraft-GenGate",
                 Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Programs/ArtCraft/PhotoCraft",
                 Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "ArtCraft/PhotoCraft",
                 Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Programs/PhotoCraft",
                 Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "PhotoCraft"]
        if os.name == "nt":
            hosts += [Path(letter + ":/Programs/PhotoCraft") for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"]
    for folder in hosts:
        if attempt(valid_host, folder):
            found = attempt(for_host, folder)
            if found.get("found"):
                return found
    return {"found": False}


def remember(host, install, *, location=None):
    meta = installation(install)
    if not meta or Path(meta["host"]).resolve() != Path(host).resolve():
        raise ValueError("No completed GenGate installation was found at this location.")
    path = location or location_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_bytes(ep.encoded({"format": 1, "host": str(Path(host).resolve()), "installDir": meta["installDir"]}))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="")
    parser.add_argument("--install", default="")
    parser.add_argument("--remember", action="store_true")
    args = parser.parse_args()
    try:
        if args.remember:
            remember(args.host, args.install)
            value = {"remembered": True}
        else:
            value = discover(args.host, args.install)
        print(json.dumps(value, ensure_ascii=True))
        return 0
    except (OSError, ValueError, TypeError) as error:
        print(json.dumps({"found": False, "error": str(error)}, ensure_ascii=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
