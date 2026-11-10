"""Installer preflight for the external verified editor updater."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from genstudio import editor_patch as ep


def version_tuple(value):
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:\D|$)", str(value))
    return tuple(map(int, match.groups())) if match else None


def installed_version(host, known):
    current = {name: ep.sha256(host / name) for name in ep.FILES}
    for version, base in known.items():
        if base.get("files") == current:
            return version
    try:
        state = ep.state_for(host)
    except (OSError, ValueError):
        state = {}
    files = state.get("files", {})
    if files and all(files.get(name, {}).get("targetSha256") == digest for name, digest in current.items()):
        return state.get("hostVersion", "")
    # VERSIONINFO is read without executing an unrecognized binary.
    if os.name == "nt":
        env = {**os.environ, "GENGATE_VERSION_FILE": str(host / "photocraft.exe")}
        result = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
            "[Diagnostics.FileVersionInfo]::GetVersionInfo($env:GENGATE_VERSION_FILE).ProductVersion"],
            capture_output=True, text=True, env=env, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        value = result.stdout.strip()
        if version_tuple(value):
            return ".".join(map(str, version_tuple(value)))
    return ""


def preflight(host, bundle, *, known=None, check_open=True, target=None):
    catalog = ep.catalog_for(bundle)
    required = sorted({p["hostVersion"] for p in catalog["patches"]}, key=version_tuple)
    try:
        host = ep.host_folder(host)
    except (OSError, ValueError):
        return {'state':'getPhotoCraft','compatible':False,'running':False,'required':required,
                'message':f'Install PhotoCraft {required[-1]}, then select its folder in Setup.',
                'url':'https://github.com/storytold/photocraft/releases/tag/v'+required[-1]}
    if known is None:
        path = Path(__file__).resolve().parents[1] / "photocraft/editor-bases.json"
        known = ep.read(path) if path.is_file() else {}
    result = {"required": required, "host": str(host), "compatible": False, "running": False}
    from genstudio.user_install import personal_required
    if personal_required(host) and (not target or target in required):
        current=installed_version(host,known)
        if current and version_tuple(current)>version_tuple(required[-1]):
            return {**result,'state':'updateGenGate','installed':current,'message':'This PhotoCraft is newer than the supported personal copy. Update GenGate first.','url':'https://github.com/Philippsapegin/Photocraft_GenStudio/releases/latest'}
        if check_open:
            try:ep.assert_closed(host)
            except ValueError:result['running']=True
        return {**result,'state':'ready','compatible':True,'personalCopy':True,'installed':current,
                'message':'Setup will prepare a verified personal PhotoCraft copy without changing this shared installation. No administrator rights needed.'}
    if target and target not in required:
        return {**result,'state':'updateGenGate','installed':installed_version(host,known),
                'message':f'Update GenGate before installing PhotoCraft {target}. This GenGate supports {required[-1]}.',
                'url':'https://github.com/Philippsapegin/Photocraft_GenStudio/releases/latest'}
    try:
        entry, _, _ = ep.choose_patch(host, catalog)
        result.update(state="ready", compatible=True, installed=entry["hostVersion"],
                      message="PhotoCraft is compatible. GenGate can be installed.")
        if check_open:
            try:
                ep.assert_closed(host)
            except ValueError:
                result.update(running=True, message="Save your work and close PhotoCraft, then continue here.")
        return result
    except ep.CompatibilityError:
        current = installed_version(host, known)
    result["installed"] = current
    target = required[-1]
    if current and version_tuple(current) < version_tuple(required[0]):
        result.update(state="updatePhotoCraft", message=f"PhotoCraft {current} is installed. GenGate needs PhotoCraft {target}. Update PhotoCraft, then continue here.")
    elif current and version_tuple(current) > version_tuple(target):
        result.update(state="updateGenGate", message=f"PhotoCraft {current} is newer than this GenGate supports ({target}). Get a compatible GenGate update.",
                      url="https://github.com/Philippsapegin/Photocraft_GenStudio/releases/latest")
    else:
        result.update(state="getPhotoCraft", message=f"This PhotoCraft build does not match the official {target} release. Select a compatible PhotoCraft folder or get the supported release.",
                      url="https://github.com/storytold/photocraft/releases/tag/v" + target)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument('--target')
    args = parser.parse_args()
    try:
        value = preflight(args.host, args.bundle, target=args.target)
        print("GENGATE_SETUP_COMPAT " + json.dumps(value, ensure_ascii=True), flush=True)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print("GENGATE_SETUP_COMPAT " + json.dumps({"state":"error","message":str(error)},ensure_ascii=True),flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
