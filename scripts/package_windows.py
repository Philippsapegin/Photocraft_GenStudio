"""Build the Windows release ZIP; downloads code/runtime only, never model weights."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("installer", ROOT / "packaging/installer.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
CORE_REV = "b0f4b7b294ce482a2e071d9d762c133d38c7aa07"
LANPAINT_REV = "2d7912f9a5efe5ece8de334c7ca18317b8288c39"


def download(entry):
    target = ROOT / ".runtime/downloads" / entry["filename"]
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and installer.sha256(target) == entry["sha256"]:
        return target
    part = target.with_suffix(target.suffix + ".part")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(entry["url"], timeout=90) as response, part.open("wb") as stream:
                shutil.copyfileobj(response, stream)
            if installer.sha256(part) != entry["sha256"]:
                raise ValueError("Download checksum mismatch: " + entry["filename"])
            part.replace(target)
            return target
        except (OSError, ValueError):
            part.unlink(missing_ok=True)
            if attempt == 2:
                raise
            time.sleep(2)


def extract(archive, target, prefix=""):
    with zipfile.ZipFile(archive) as source:
        for entry in source.infolist():
            if entry.is_dir() or not entry.filename.startswith(prefix):
                continue
            relative = entry.filename[len(prefix):]
            if not relative:
                continue
            path = installer.bundle_path(target, relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(source.read(entry))


def copy_tree(source, target, exclude=()):
    for path in source.rglob("*"):
        if not path.is_file() or any(part in (".git", "__pycache__", ".pytest_cache") for part in path.relative_to(source).parts):
            continue
        if path.relative_to(source).parts[0] in exclude:
            continue
        if path.suffix.lower() in (".pyc", ".pyo", ".log", ".safetensors", ".ckpt", ".gguf", ".pt"):
            continue
        destination = target / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)


def lanpaint_source(source):
    if source:
        revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        if revision != LANPAINT_REV:
            raise ValueError("Expected the pinned LanPaint revision.")
        return source
    target = ROOT / ".runtime/LanPaint"
    if not (target / "LICENSE").is_file():
        archive = ROOT / ".runtime/downloads" / ("LanPaint-" + LANPAINT_REV + ".zip")
        archive.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(f"https://codeload.github.com/scraed/LanPaint/zip/{LANPAINT_REV}", timeout=90) as response:
            archive.write_bytes(response.read())
        extract(archive, target, prefix=f"LanPaint-{LANPAINT_REV}/")
    return target


def build(args):
    upstream = args.upstream.resolve()
    from genstudio.editor_patch import catalog_for
    patches = args.patches.resolve()
    catalog = catalog_for(patches)
    target = ROOT / "dist" / ("GenGate-" + installer.VERSION + "-windows-x64-addon")
    # A new package is staged next to earlier packages, never recursively removed.
    if target.exists():
        raise ValueError("Package staging directory already exists; move it aside before rebuilding: " + str(target))
    target.mkdir(parents=True)
    runtime = target / "runtime"
    runtime.mkdir()
    shutil.copyfile(patches / "editor-patches.json", target / "editor-patches.json")
    copy_tree(patches / "patches", target / "patches")
    for directory in ("genfill", "genstudio"):
        copy_tree(ROOT / directory, runtime / directory)
    copy_tree(ROOT / "comfyui", runtime / "comfyui")
    for name in ("config.example.json", "requirements.txt", "LICENSE"):
        shutil.copyfile(ROOT / name, runtime / name)
    for name in ("run_direct.py", "run_segmentation.py", "download_segmentation.py", "prepare_qwen_core.py", "prepare_flux_core.py", "prepare_fibo_core.py", "download_flux3.py", "download_fibo2.py", "Setup-Direct.ps1"):
        destination = runtime / "scripts" / name
        destination.parent.mkdir(exist_ok=True)
        shutil.copyfile(ROOT / "scripts" / name, destination)
    destination = runtime / "photocraft"
    destination.mkdir()
    shutil.copyfile(ROOT / "photocraft/direct-requirements.txt", destination / "direct-requirements.txt")
    shutil.copyfile(ROOT / "photocraft/flux-requirements.txt", destination / "flux-requirements.txt")
    shutil.copyfile(ROOT / "photocraft/flux3-models.json", destination / "flux3-models.json")
    for name in ('fibo2-models.json', 'fibo-requirements.txt'):
        shutil.copyfile(ROOT / 'photocraft' / name, destination / name)
    for name in ("segmentation-models.json", "segmentation-requirements.txt"):
        shutil.copyfile(ROOT / "photocraft" / name, destination / name)
    from genstudio.segmentation_models import ensure_modules
    copy_tree(ensure_modules(), runtime / ".runtime/segmentation/deps")
    copy_tree(ROOT / "packaging", runtime / "packaging")
    for name in ("Setup.cmd", "Setup.ps1", "Launch.ps1", "PhotoCraft.cmd", "Uninstall.cmd", "Uninstall.ps1"):
        shutil.copyfile(ROOT / "packaging" / name, target / name)
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    # The package contains the user guide, while developer/source links should
    # open the matching public tag instead of pointing at absent local files.
    def source_link(match):
        link = match.group(1)
        if "://" in link or link.startswith("#"):
            return match.group(0)
        return "](" + installer.REPOSITORY + "/blob/v" + installer.VERSION + "/" + link + ")"
    (target / "README.md").write_text(re.sub(r"\]\(([^)]+)\)", source_link, readme), encoding="utf-8")
    lock = json.loads((ROOT / "packaging/runtime-lock.json").read_text())
    inputs = [lock["python"], *lock["wheels"]]
    print("Fetching checksum-pinned Python and lightweight host wheels (no torch, no models)...", flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        downloads = list(pool.map(download, inputs))
    python_dir = runtime / ".venv/Scripts"
    extract(downloads[0], python_dir)
    # Both host and the optional GPU Python live two directories below runtime.
    (python_dir / "python313._pth").write_text("python313.zip\n.\nsite-packages\n..\\..\nimport site\n", encoding="utf-8")
    for wheel in downloads[1:]:
        extract(wheel, python_dir / "site-packages")
    if args.core:
        core = args.core.resolve()
    else:
        from prepare_qwen_core import prepare
        core = prepare()
    if json.loads((core / "genstudio-core.json").read_text())["revision"] != CORE_REV:
        raise ValueError("Expected the pinned Direct model library.")
    copy_tree(core, runtime / ".runtime/qwen-core")
    from prepare_flux_core import REVISION as FLUX_REV
    from prepare_qwen_core import prepare as prepare_core
    flux_core = prepare_core(revision=FLUX_REV, directory="flux-core")
    copy_tree(flux_core, runtime / ".runtime/flux-core", exclude=("deps",))
    # Bundle the two small accelerator dependencies in a private overlay. This
    # never upgrades an attached ComfyUI/Qwen Python and ships no torch or weights.
    subprocess.run([str(python_dir / "python.exe"), "-m", "pip", "install", "--disable-pip-version-check",
        "--only-binary=:all:", "--no-deps", "--target", str(runtime / ".runtime/flux-core/deps"),
        "-r", str(runtime / "photocraft/flux-requirements.txt")], check=True)
    from prepare_fibo_core import REVISION as FIBO_REV
    fibo_core = prepare_core(revision=FIBO_REV, directory='fibo-core')
    copy_tree(fibo_core, runtime / '.runtime/fibo-core', exclude=('deps',))
    subprocess.run([str(python_dir / 'python.exe'), '-m', 'pip', 'install', '--disable-pip-version-check',
        '--only-binary=:all:', '--no-deps', '--target', str(runtime / '.runtime/fibo-core/deps'),
        '-r', str(runtime / 'photocraft/fibo-requirements.txt')], check=True)
    # LanPaint is source code, including its license. Install it into ComfyUI only
    # if the user explicitly selects the setup checkbox and it does not exist.
    copy_tree(lanpaint_source(args.lanpaint), runtime / "third_party/LanPaint",
              exclude=("examples", "example_workflows", "Example.JPG", "Nodes.JPG", ".github", ".vscode"))
    licenses = target / "licenses"
    licenses.mkdir()
    for name in ("LICENSE-MIT", "LICENSE-APACHE", "NOTICE", "ATTRIBUTION.md"):
        shutil.copyfile(upstream / name, licenses / ("PhotoCraft-" + name))
    license_candidates = [path for directory in ("assets", "crates") for path in (upstream / directory).rglob("*")]
    for path in license_candidates:
        if not path.is_file() or path.suffix.lower() not in (".txt", "", ".md"):
            continue
        relative = path.relative_to(upstream)
        if not relative.parts or relative.parts[0] not in ("assets", "crates"):
            continue
        if not (path.name.startswith(("LICENSE", "OFL-", "COPYING"))):
            continue
        destination = licenses / "PhotoCraft" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
    shutil.copyfile(ROOT / "LICENSE", licenses / "GenGate-GPL-3.0.txt")
    shutil.copyfile(ROOT / "THIRD_PARTY.md", licenses / "THIRD_PARTY.md")
    if any(entry["hostVersion"] in ("0.12.0", "0.15.1", "0.16.0") for entry in catalog["patches"]):
        from prepare_craft_fonts import prepare as prepare_fonts
        fonts = prepare_fonts()
        for path in (fonts / "fonts").rglob("OFL.txt"):
            destination = licenses / "craft-fonts" / path.relative_to(fonts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
    shutil.copyfile(python_dir / "LICENSE.txt", licenses / "Python-LICENSE.txt")
    # The embedded wheel dist-info directories retain all Python dependency
    # license texts; GPL model library and LanPaint retain original source.
    manifest = {"version": installer.VERSION, "installMode": "overlay",
                "upstream": [entry["upstream"] for entry in catalog["patches"]],
                "includes_model_weights": False, "files": {}}
    for path in sorted(target.rglob("*")):
        if path.is_file() and "__pycache__" not in path.relative_to(target).parts and path.suffix.lower() not in (".pyc", ".pyo"):
            if path.suffix.lower() in (".safetensors", ".ckpt", ".gguf", ".pt"):
                raise ValueError("A model weight was found in the package: " + str(path))
            manifest["files"][path.relative_to(target).as_posix()] = installer.sha256(path)
    installer.write_json(target / "manifest.json", manifest)
    installer.verify_bundle(target)
    subprocess.run([str(python_dir / "python.exe"), "-c",
        "import sys, pathlib, importlib.util; import genstudio.service,genstudio.core_pipeline; "
        "from genstudio.settings import ROOT; assert ROOT == pathlib.Path(sys.argv[1]).resolve(); "
        "assert importlib.util.find_spec('torch') is None; print('Bundled host imports pass; no torch or weights.')",
        str(runtime)], check=True, cwd=str(target))
    archive = target.parent / (target.name + ".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
        for relative in sorted([*manifest["files"], "manifest.json"]):
            zipped.write(target / relative, target.name + "/" + relative)
    checksum = archive.parent / "SHA256SUMS.txt"
    checksum.write_text(installer.sha256(archive) + "  " + archive.name + "\n", encoding="utf-8")
    print(json.dumps({"archive": str(archive), "bytes": archive.stat().st_size,
                      "sha256": installer.sha256(archive), "files": len(manifest["files"])}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    base = ROOT / ".runtime/photocraft-0.16.0"
    parser.add_argument("--upstream", type=Path, default=base)
    parser.add_argument("--patches", type=Path, required=True, help="Verified editor-patches.json and binary deltas")
    parser.add_argument("--core", type=Path)
    parser.add_argument("--lanpaint", type=Path)
    build(parser.parse_args())
