"""Per-user Windows installer. Never downloads or opens model weights."""
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

VERSION = "0.10.0"
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
    if manifest.get("installMode") != "overlay":
        raise ValueError("This setup requires a separate GenGate add-on package, not a bundled editor.")
    if any(path.name.lower() in ("photocraft.exe", "photocraft-cli.exe") for path in bundle.rglob("*") if path.is_file()):
        raise ValueError("An add-on package must not contain a complete PhotoCraft executable.")
    for required in ("editor-patches.json", "runtime/.venv/Scripts/python.exe", "runtime/genstudio/control.py",
                     "Launch.ps1", "PhotoCraft.cmd", "Uninstall.cmd", "Uninstall.ps1"):
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
    from genstudio.installation import pending
    owned = pending(path)
    if path.exists() and any(path.iterdir()) and not (path / "genstudio-install.json").is_file() and not owned:
        raise ValueError("This folder already contains other files. Choose an empty folder or a previous GenGate installation.")
    if (path / "genstudio-install.json").is_file() and read_json(path / "genstudio-install.json").get("installMode") != "overlay":
        raise ValueError("This is an old standalone editor installation. Choose a new GenGate add-on folder; the old installation will be kept.")
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


def run_logged(command, cancel=None):
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8", errors="replace",
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        if cancel is None:
            for line in process.stdout:
                report(line.rstrip())
        else:
            import queue
            import threading
            lines = queue.Queue()
            def read_output():
                for line in process.stdout:
                    lines.put(line)
                lines.put(None)
            threading.Thread(target=read_output, daemon=True).start()
            while True:
                cancel()
                try:line = lines.get(timeout=.2)
                except queue.Empty:continue
                if line is None:break
                report(line.rstrip())
        if process.wait():
            raise RuntimeError("Dependency installation failed. Check your connection and setup log, then retry.")
    except BaseException:
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        raise


def install_direct(runtime, cancel=None):
    report("Installing a separate CUDA runtime. This downloads several GB of software, no model weights.")
    target = runtime / ".runtime/qwen-direct"
    if (target / 'python.exe').is_file():
        try:
            return check_python(target / 'python.exe', direct=True)
        except (ValueError, OSError, subprocess.SubprocessError):
            pass
    shutil.copytree(runtime / ".venv/Scripts", target, dirs_exist_ok=True)
    python = target / "python.exe"
    site = target / "site-packages"
    # Install to this private directory; never change a user's system or ComfyUI Python.
    run_logged([str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location",
                "--only-binary=:all:", "--upgrade", "--target", str(site),
                "torch==2.14.0+cu130", "torchvision==0.29.0+cu130",
                "--index-url", "https://pypi.org/simple", "--extra-index-url", "https://download.pytorch.org/whl/cu130",
                "-r", str(runtime / "photocraft/direct-requirements.txt")], **({'cancel':cancel} if cancel is not None else {}))
    return check_python(python, direct=True)


def settings_for(request, runtime, python, previous=None):
    from genstudio.settings import validate
    result = validate(previous or {})
    result.update(engine=request["engine"], modelsDir=str(Path(request["modelsDir"]).resolve()), runtimeRoot=str(runtime))
    modern = "installDirect" in request
    if modern:
        result["directPython"] = python
        if not request["installDirect"]:
            result["directStartup"] = "off"
        if request["configureComfy"]:
            result.update(comfyPython=request["comfyPython"], comfyDir=request["comfyDir"])
    else:
        result["directPython" if request["engine"] == "direct" else "comfyPython"] = python
    for key in ("comfyDir", "comfyUrl"):
        if key in request:
            if modern and key == "comfyDir":
                continue
            # Direct setup hides this field; an empty GUI value must not erase
            # the saved configuration of the inactive ComfyUI engine.
            if key == "comfyDir" and request["engine"] == "direct" and request[key] == "" and result[key]:
                continue
            result[key] = request[key]
    if previous and os.path.normcase(str(Path(previous.get("modelsDir") or ".").resolve())) != os.path.normcase(result["modelsDir"]):
        result["textEncoder"] = result["vae"] = ""
    return validate(result)


def engine_request(request, previous):
    """Resolve the checkbox-based setup without changing external Python installs."""
    if "installDirect" not in request:
        return request  # Older command-line requests remain supported.
    direct = request["installDirect"]
    if not isinstance(direct, bool):
        raise ValueError("Install Direct must be enabled or disabled.")
    folder = str(request.get("comfyDir") or "").strip()
    python = str(request.get("comfyPython") or "").strip()
    comfy = bool(folder or python)
    if (comfy or not direct) and (not folder or not python):
        raise ValueError("Select both the ComfyUI folder and its python.exe. ComfyUI is required when Direct is disabled.")
    if comfy:
        if not (Path(folder) / "main.py").is_file():
            raise ValueError("Choose the ComfyUI folder containing main.py.")
        python = check_python(python)
    existing = previous.get("directPython", "") if direct else ""
    if existing and Path(existing).is_file():
        try:
            existing = check_python(existing, direct=True)
        except (ValueError, OSError, subprocess.SubprocessError):
            existing = ""
    else:
        existing = ""
    active = "direct" if direct else "comfy"
    if direct and previous.get("engine") == "comfy" and (comfy or previous.get("comfyPython")):
        active = "comfy"
    return {**request, "engine": active, "configureComfy": comfy, "comfyDir": folder,
            "comfyPython": python, "python": existing, "newRuntime": direct and not existing}


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


def install(request, bundle, *, shortcuts=True, warnings=None, editor_update=None):
    warnings = [] if warnings is None else warnings
    report("Verifying the release package...")
    manifest = verify_bundle(bundle)
    destination = valid_destination(request.get("installDir"), bundle)
    from genstudio import editor_patch
    host = editor_patch.host_folder(request.get("photocraftDir") or "__missing_photocraft__")
    if destination == host or host.is_relative_to(destination) or destination.is_relative_to(host):
        raise ValueError("Choose a separate folder for GenGate, outside the PhotoCraft application files.")
    profile = editor_patch.profile_folder(host)
    if editor_update is not None:
        editor_update.validate_host(host, bundle)
    if editor_update is None and not (host / ".gengate/journal.json").exists():
        editor_patch.choose_patch(host, editor_patch.catalog_for(bundle))
    with editor_patch.locked(host):
        editor_patch.assert_closed(host)
        editor_patch.recover(host, profile)
        if editor_update is None:
            editor_patch.choose_patch(host, editor_patch.catalog_for(bundle))
        else:
            editor_update.validate_host(host, bundle)
        return _install_locked(request, bundle, manifest, destination, host, profile, shortcuts, warnings, editor_update)


def _install_locked(request, bundle, manifest, destination, host, profile, shortcuts, warnings, editor_update=None):
    from genstudio import editor_patch
    prefs_path = profile / "preferences.json"
    # Parse and validate everything before installing dependencies or patching files.
    preferences = read_json(prefs_path) if prefs_path.exists() else {}
    if not isinstance(preferences, dict):
        raise ValueError("Existing preferences are invalid. Back up Profile/preferences.json before retrying.")
    old_settings = editor_patch.previous_settings(host, preferences)
    if not old_settings:
        from genstudio.setup_state import for_host
        old_settings = for_host(host, str(destination)).get("settings", {})
    if request.get("migrationProfile"):
        previous = read_json(Path(request["migrationProfile"]) / "preferences.json")
        old_settings = previous.get("genStudio", old_settings)
    old_settings = old_settings if isinstance(old_settings, dict) else {}
    request = engine_request(request, old_settings)
    modern = "installDirect" in request
    engine = request.get("engine")
    if engine not in ("direct", "comfy"):
        raise ValueError("Choose Direct or ComfyUI.")
    if not Path(request.get("modelsDir") or "__missing_models__").is_dir():
        raise ValueError("Choose an existing Models folder. An empty folder is allowed for later setup.")
    configure_comfy = request["configureComfy"] if modern else engine == "comfy"
    if configure_comfy and not (Path(request.get("comfyDir", "")) / "main.py").is_file():
        raise ValueError("Choose the ComfyUI folder containing main.py.")
    fresh = request["newRuntime"] if modern else engine == "direct" and request.get("newRuntime", False)
    python = request["python"] if modern else "" if fresh else check_python(request.get("python", ""), direct=engine == "direct")
    if configure_comfy:
        from urllib.parse import urlsplit
        address = urlsplit(request.get("comfyUrl") or "http://127.0.0.1:8190")
        if (address.scheme != "http" or address.hostname not in ("localhost", "127.0.0.1", "::1")
                or not address.port or address.path not in ("", "/") or address.username
                or address.password or address.query or address.fragment):
            raise ValueError("Use a local ComfyUI HTTP address with a port, e.g. http://127.0.0.1:8190.")
        lanpaint = Path(request["comfyDir"]) / "custom_nodes/LanPaint"
    from genstudio.installation import stage, MARKER
    version_dir = stage(destination, host, profile, VERSION)
    version_dir.mkdir(parents=True, exist_ok=True)
    report("Installing the GenGate add-on and local host. PhotoCraft is already installed.")
    # Copy only files covered by the manifest, with no developer configs or caches.
    for relative in manifest["files"]:
        target = version_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(bundle_path(bundle, relative), target)
    write_json(version_dir / 'manifest.json', manifest)
    runtime = version_dir / "runtime"
    if fresh:
        python = install_direct(runtime, **({'cancel':editor_update.cancel} if editor_update is not None else {}))
    settings = settings_for(request, runtime, python, old_settings if isinstance(old_settings, dict) else None)
    write_json(runtime / "config.photocraft.json", settings)
    if configure_comfy and not lanpaint.exists() and request.get("installLanPaint", modern):
        report("Installing the bundled, pinned LanPaint extension into the selected ComfyUI...")
        shutil.copytree(runtime / "third_party/LanPaint", lanpaint)
    if configure_comfy:
        report("Installing GenGate IN / OUT nodes into the selected ComfyUI (no models)...")
        from genstudio.comfy_workflow import install_nodes
        install_nodes(request["comfyDir"])
    preferences["genStudio"] = settings
    report("Applying the verified PhotoCraft patch and keeping original files for restore...")
    def installation_changes(installed):
        metadata = {"version": VERSION,
                    "installMode": "overlay", "executable": str(host / "photocraft.exe"),
                    "host": str(host), "hostVersion": installed["hostVersion"], "profile": str(profile),
                    "runtime": str(runtime), "package": str(version_dir),
                    "shortcutsInstalled": False, "desktopShortcut": True,
                    "directInstalled": request["installDirect"] if modern else bool(settings["directPython"]),
                    "source": REPOSITORY, "sha256": installed["files"]["photocraft.exe"]["targetSha256"]}
        return [(destination / name, bundle / name) for name in ("Launch.ps1", "PhotoCraft.cmd", "Uninstall.cmd", "Uninstall.ps1")] + [
            (destination / "genstudio-install.json", editor_patch.encoded(metadata))]
    official = editor_update.before_apply() if editor_update is not None else None
    editor_patch.apply(version_dir, host, settings, preferences, profile, VERSION,
                       installation_root=destination, installation_changes=installation_changes, _lock_held=True, official=official)
    try:
        (destination / MARKER).unlink(missing_ok=True)
    except OSError as error:
        message = "Could not clear the completed setup record: " + str(error)
        warnings.append(message)
        report("NOTE: " + message)
    if shortcuts:
        report("Updating the PhotoCraft launcher and removing obsolete PhotoCraft shortcuts...")
        try:
            from genstudio.windows_shortcuts import repair
            repair(destination, host, desktop=True, **({'locations':request['_shortcutLocations']} if request.get('_shortcutLocations') else {}))
            metadata = read_json(destination / "genstudio-install.json")
            metadata["shortcutsInstalled"] = True
            write_json(destination / "genstudio-install.json", metadata)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            message = "Shortcut setup needs attention: " + str(error)
            warnings.append(message)
            report("NOTE: " + message)
    if modern and not request["installDirect"]:
        report("ComfyUI model selection is controlled by its workflows.")
        report("Installed. Open PhotoCraft GenGate, then Filter > Plugins > GenGate.")
        return destination
    # File checks only: no model imports, no CUDA, no engine start.
    report("Checking the model folder without loading weights...")
    sys.path.insert(0, str(runtime))
    from genstudio.models import resolve
    try:
        if settings.get("presets"):
            from genstudio.models import resolve_components
            from genstudio.settings import active_components
            resolve_components(settings["modelsDir"], active_components(settings), settings["model"])
        else:
            resolve(settings["modelsDir"],settings.get("textEncoder",""),settings.get("vae",""),settings.get("model","qwen-image-2.1-turbo"))
        report("Model files found. No weights were loaded.")
    except (ValueError, OSError) as error:
        message = "Models still need attention: " + str(error)
        warnings.append(message)
        report("NOTE: " + message)
    report("Installed. Open PhotoCraft GenGate, then Filter > Plugins > GenGate.")
    return destination


def main():
    from genstudio.editor_patch import CompatibilityError
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--bundle", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--no-shortcuts", action="store_true")
    args = parser.parse_args()
    warnings = []
    try:
        install(read_json(args.request), args.bundle.resolve(), shortcuts=not args.no_shortcuts, warnings=warnings)
    except CompatibilityError:
        from genstudio.setup_compatibility import preflight
        request = read_json(args.request)
        try:
            compatibility = preflight(request.get('photocraftDir', ''), args.bundle.resolve())
        except (OSError, ValueError, subprocess.SubprocessError):
            compatibility = {'state':'checkPhotoCraft', 'compatible':False,
                'message':'PhotoCraft files are changing. Finish its update, then continue here.'}
        report('GENGATE_SETUP_RESULT ' + json.dumps({'status':'needsPhotoCraft', 'compatibility':compatibility}))
        report(compatibility['message'])
        return 2
    except Exception as error:
        report("GENGATE_SETUP_RESULT " + json.dumps({"status": "failed", "message": str(error)}))
        report("ERROR: " + str(error))
        return 1
    report("GENGATE_SETUP_RESULT " + json.dumps({"status": "installedWithWarnings" if warnings else "installed", "warnings": warnings}))
    report("Installed successfully" + (" (with notes; see above)." if warnings else "."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
