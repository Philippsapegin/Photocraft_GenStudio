"""Per-user Windows alpha installer. Never downloads or opens model weights."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import uuid

VERSION = "0.8.0-alpha.1"
REPOSITORY = "https://github.com/Philippsapegin/Photocraft_GenStudio"


def report(message):
    print(message, flush=True)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def bundle_path(bundle, relative):
    # A manifest cannot escape its package or refer to an NTFS alternate stream.
    parts = PurePosixPath(relative)
    if (not relative or parts.is_absolute() or ".." in parts.parts
            or "\\" in relative or ":" in relative):
        raise ValueError("Invalid package path: " + relative)
    path = (bundle / Path(*parts.parts)).resolve()
    if not path.is_relative_to(bundle.resolve()):
        raise ValueError("Package path leaves the bundle.")
    return path


def verify_bundle(bundle):
    manifest = read_json(bundle / "manifest.json")
    if manifest.get("version") != VERSION:
        raise ValueError("Setup and package versions differ. Extract the complete release ZIP again.")
    entries = manifest.get("files")
    if not isinstance(entries, dict) or not entries:
        raise ValueError("The package file manifest is empty.")
    for relative, digest in entries.items():
        path = bundle_path(bundle, relative)
        if not path.is_file() or sha256(path) != digest:
            raise ValueError("Package verification failed: " + relative + ". Extract the ZIP again.")
    for required in ("PhotoCraft.exe", "runtime/.venv/Scripts/python.exe", "runtime/genstudio/control.py"):
        if required not in entries:
            raise ValueError("The package is incomplete: " + required)
    return manifest


def valid_destination(value, bundle):
    if not value or not Path(value).is_absolute():
        raise ValueError("Choose an absolute installation folder.")
    path = Path(value).resolve()
    if path == Path(path.anchor) or path == Path.home().resolve():
        raise ValueError("Choose a new subfolder for PhotoCraft GenGate.")
    if path == bundle.resolve() or path.is_relative_to(bundle.resolve()) or bundle.resolve().is_relative_to(path):
        raise ValueError("Choose a folder outside the extracted installer.")
    if path.exists() and any(path.iterdir()) and not (path / "genstudio-install.json").is_file():
        raise ValueError("This folder already contains other files. Choose an empty folder or a previous GenGate installation.")
    return path


def check_python(path, direct=False):
    path = Path(path)
    if not path.is_file() or path.suffix.lower() != ".exe":
        raise ValueError("Choose the engine's python.exe, or select Install a separate Direct runtime.")
    # Inspect installed distributions without importing torch or initializing CUDA.
    code = ("import importlib.util as u,importlib.metadata as m,json,sys; "
            "names=['torch','transformers','safetensors','comfy_kitchen','einops','scipy','PIL']; "
            "missing=[n for n in names if u.find_spec(n) is None]; "
            "print(json.dumps({'version':list(sys.version_info[:2]),'missing':missing,"
            "'torch':m.version('torch') if u.find_spec('torch') else ''}))")
    result = subprocess.run([str(path), "-I", "-c", code], capture_output=True, text=True,
                            timeout=45, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise ValueError("Could not inspect the selected Python. " + result.stderr[-800:])
    value = json.loads(result.stdout)
    if value["version"] < [3, 10]:
        raise ValueError("The engine requires Python 3.10 or newer.")
    torch_version = tuple(int(part) for part in value["torch"].split("+")[0].split(".")[:2]) if value["torch"] else (0, 0)
    if direct and (value["missing"] or "+cu" not in value["torch"] or torch_version < (2, 10)):
        raise ValueError("The selected Direct Python lacks CUDA dependencies: "
                         + ", ".join(value["missing"]) + ". CUDA PyTorch 2.10+ is required. Select Install a separate Direct runtime instead.")
    return str(path.resolve())


def run_logged(command):
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8", errors="replace",
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for line in process.stdout:
        report(line.rstrip())
    if process.wait():
        raise RuntimeError("Dependency installation failed. Check your connection and setup log, then retry.")


def install_direct(runtime):
    report("Installing a separate CUDA runtime. This downloads several GB of software, no model weights.")
    target = runtime / ".runtime/qwen-direct"
    shutil.copytree(runtime / ".venv/Scripts", target)
    python = target / "python.exe"
    site = target / "site-packages"
    # Install to this private directory; never change a user's system or ComfyUI Python.
    run_logged([str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location",
                "--only-binary=:all:", "--upgrade", "--target", str(site),
                "torch==2.14.0+cu130", "torchvision==0.29.0+cu130",
                "--index-url", "https://pypi.org/simple", "--extra-index-url", "https://download.pytorch.org/whl/cu130",
                "-r", str(runtime / "photocraft/direct-requirements.txt")])
    return check_python(python, direct=True)


def settings_for(request, runtime, python):
    return {"engine": request["engine"], "comfyStartup": "firstRequest", "directStartup": "firstRequest",
            "comfyUrl": request.get("comfyUrl") or "http://127.0.0.1:8190",
            "comfyDir": request.get("comfyDir", ""),
            "comfyPython": python if request["engine"] == "comfy" else "",
            "directPython": python if request["engine"] == "direct" else "",
            "modelsDir": str(Path(request["modelsDir"]).resolve()), "runtimeRoot": str(runtime)}


def shortcut(path, target, arguments, working_dir, icon):
    # Paths travel through JSON, never interpolated into executable PowerShell code.
    data = path.with_suffix(".shortcut.json")
    write_json(data, dict(path=str(path), target=str(target), arguments=arguments,
                          working=str(working_dir), icon=str(icon)))
    try:
        subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                        str(Path(__file__).with_name("Create-Shortcut.ps1")), "-Data", str(data)],
                       check=True, capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    finally:
        data.unlink(missing_ok=True)


def install(request, bundle, *, shortcuts=True):
    report("Verifying the release package...")
    manifest = verify_bundle(bundle)
    destination = valid_destination(request.get("installDir"), bundle)
    engine = request.get("engine")
    if engine not in ("direct", "comfy"):
        raise ValueError("Choose Direct or ComfyUI.")
    if not Path(request.get("modelsDir") or "__missing_models__").is_dir():
        raise ValueError("Choose an existing Models folder. An empty folder is allowed for later setup.")
    if engine == "comfy" and not (Path(request.get("comfyDir", "")) / "main.py").is_file():
        raise ValueError("Choose the ComfyUI folder containing main.py.")
    fresh = engine == "direct" and request.get("newRuntime", False)
    python = "" if fresh else check_python(request.get("python", ""), direct=engine == "direct")
    if engine == "comfy":
        from urllib.parse import urlsplit
        address = urlsplit(request.get("comfyUrl") or "http://127.0.0.1:8190")
        if (address.scheme != "http" or address.hostname not in ("localhost", "127.0.0.1", "::1")
                or not address.port or address.path not in ("", "/") or address.username
                or address.password or address.query or address.fragment):
            raise ValueError("Use a local ComfyUI HTTP address with a port, e.g. http://127.0.0.1:8190.")
        lanpaint = Path(request["comfyDir"]) / "custom_nodes/LanPaint"
        if not lanpaint.exists() and not request.get("installLanPaint", False):
            raise ValueError("ComfyUI needs LanPaint. Enable Install missing LanPaint, or install it yourself.")
    profile = destination / "Profile"
    prefs_path = profile / "preferences.json"
    # Parse before changing anything. Preserve all unrelated editor preferences.
    preferences = read_json(prefs_path) if prefs_path.exists() else {}
    if not isinstance(preferences, dict):
        raise ValueError("Existing preferences are invalid. Back up Profile/preferences.json before retrying.")
    destination.mkdir(parents=True, exist_ok=True)
    version_dir = destination / "versions" / (VERSION + "-" + uuid.uuid4().hex[:8])
    version_dir.mkdir(parents=True)
    report("Installing PhotoCraft and the local host...")
    # Copy only files covered by the manifest, with no developer configs or caches.
    for relative in manifest["files"]:
        if not relative.startswith(("runtime/", "licenses/")) and relative not in ("PhotoCraft.exe", "photocraft-cli.exe", "README.md", "vcruntime140.dll"):
            continue
        target = version_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(bundle_path(bundle, relative), target)
    runtime = version_dir / "runtime"
    if fresh:
        python = install_direct(runtime)
    settings = settings_for(request, runtime, python)
    # An existing profile keeps its startup choice; first installs use first request.
    old_settings = preferences.get("genStudio", {})
    if not isinstance(old_settings, dict):
        old_settings = {}
    for key in ("comfyStartup", "directStartup"):
        if old_settings.get(key) in ("off", "appStart", "firstRequest"):
            settings[key] = old_settings[key]
    if old_settings.get("model") in ("qwen-image-2.1-turbo", "flux-3-image-klein"):
        settings["model"] = old_settings["model"]
    if os.path.normcase(str(Path(old_settings.get("modelsDir") or ".").resolve())) == os.path.normcase(settings["modelsDir"]):
        for key in ("textEncoder", "vae"):
            value=old_settings.get(key)
            if isinstance(value,str) and len(value)<=4096 and "\0" not in value:
                settings[key]=value
    if isinstance(old_settings.get("segmentation"),dict):
        from genstudio.segmentation_models import validate as validate_segmentation
        try:
            settings["segmentation"]=validate_segmentation(old_settings["segmentation"])
        except ValueError:
            pass
    write_json(runtime / "config.photocraft.json", settings)
    if engine == "comfy" and not lanpaint.exists():
        report("Installing the bundled, pinned LanPaint extension into the selected ComfyUI...")
        shutil.copytree(runtime / "third_party/LanPaint", lanpaint)
    preferences["genStudio"] = settings
    if prefs_path.exists():
        shutil.copyfile(prefs_path, prefs_path.with_suffix(".before-setup.json"))
    write_json(prefs_path, preferences)
    shutil.copyfile(bundle / "Launch.ps1", destination / "Launch.ps1")
    shutil.copyfile(bundle / "PhotoCraft.cmd", destination / "PhotoCraft.cmd")
    write_json(destination / "genstudio-install.json", {"version": VERSION,
               "executable": str(version_dir / "PhotoCraft.exe"), "profile": str(profile),
               "source": REPOSITORY, "sha256": manifest["files"]["PhotoCraft.exe"]})
    if shortcuts:
        report("Creating a separate PhotoCraft GenGate shortcut...")
        powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        args = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + str(destination / "Launch.ps1") + '"'
        menu = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/PhotoCraft GenGate.lnk"
        shortcut(menu, powershell, args, destination, version_dir / "PhotoCraft.exe")
        if request.get("desktopShortcut", True):
            # The real Desktop can be redirected to OneDrive.
            script = "[Environment]::GetFolderPath('Desktop')"
            desktop = subprocess.check_output(["powershell.exe", "-NoProfile", "-Command", script],
                        text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).strip()
            shortcut(Path(desktop) / "PhotoCraft GenGate.lnk", powershell, args, destination, version_dir / "PhotoCraft.exe")
    # File checks only: no model imports, no CUDA, no engine start.
    report("Checking the model folder without loading weights...")
    sys.path.insert(0, str(runtime))
    from genstudio.models import resolve
    try:
        resolve(settings["modelsDir"],settings.get("textEncoder",""),settings.get("vae",""),settings.get("model","qwen-image-2.1-turbo"))
        report("Model files found. No weights were loaded.")
    except ValueError as error:
        report("Setup completed; models still need attention: " + str(error))
    report("Installed. Open PhotoCraft GenGate, then Filter > Plug-ins > GenGate.")
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--bundle", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--no-shortcuts", action="store_true")
    args = parser.parse_args()
    try:
        install(read_json(args.request), args.bundle.resolve(), shortcuts=not args.no_shortcuts)
    except Exception as error:
        report("ERROR: " + str(error))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
