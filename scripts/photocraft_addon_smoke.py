"""Exercise an extracted add-on on an isolated official editor; no inference."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from genstudio import editor_patch as ep


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--gpu-python", type=Path, required=True, help="Inspected as metadata only; never modified")
    args = parser.parse_args()
    folder = ROOT / ".runtime" / ("addon-validation-" + str(time.time_ns()))
    folder.mkdir(parents=True)
    # Exercise non-ASCII paths in the embedded interpreter, native editor and delta decoder.
    work = folder / "проверка установки"
    work.mkdir()
    with zipfile.ZipFile(args.archive) as archive:
        if archive.testzip() is not None:
            raise ValueError("ZIP CRC verification failed")
        for entry in archive.infolist():
            if not entry.is_dir():
                target = ep.safe_path(work, entry.filename)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(entry))
    bundle = next(work.glob("GenGate-*-addon"))
    assert not any(p.name.lower() in ep.FILES for p in bundle.rglob("*"))
    host, destination, models = work / "Existing PhotoCraft", work / "Separate GenGate", work / "Shared models"
    host.mkdir()
    models.mkdir()
    originals = {}
    for name in ep.FILES:
        shutil.copyfile(args.original / name, host / name)
        originals[name] = ep.sha256(host / name)
    (host / "portable.txt").write_text("")
    profile = host / "PhotoCraftData"
    profile.mkdir()
    values = {"interface": {"language": "en"}, "unrelatedSentinel": "preserve",
              "genStudio": {"directStartup": "off", "comfyStartup": "off"}}
    prefs = profile / "preferences.json"
    prefs.write_bytes(ep.encoded(values))
    interpreter = bundle / "runtime/.venv/Scripts/python.exe"
    request = folder / "request.json"
    request.write_bytes(ep.encoded({"installDir": str(destination), "photocraftDir": str(host),
        "modelsDir": str(models), "engine": "direct", "newRuntime": False,
        "python": str(args.gpu_python.resolve()), "desktopShortcut": False}))
    env = os.environ.copy()
    env.pop("PHOTOCRAFT_CONFIG_DIR", None)
    env["CUDA_VISIBLE_DEVICES"] = ""

    def run(name, command):
        with (folder / (name + ".log")).open("w", encoding="utf-8") as log:
            result = subprocess.run([str(p) for p in command], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=180)
        if result.returncode:
            raise RuntimeError(name + " failed; see " + str(folder / (name + ".log")))

    install = [interpreter, "-X", "utf8", bundle / "runtime/packaging/installer.py",
               "--request", request, "--bundle", bundle, "--no-shortcuts"]
    print("Install on the isolated official editor", flush=True)
    run("install", install)
    metadata = ep.read(destination / "genstudio-install.json")
    runtime = Path(metadata["runtime"])
    target_hashes = {name: ep.sha256(host / name) for name in ep.FILES}
    catalog = ep.catalog_for(bundle)
    selected = next(entry for entry in catalog["patches"] if all(originals[name] == spec["baseSha256"] for name, spec in entry["files"].items()))
    assert target_hashes == {name: spec["targetSha256"] for name, spec in selected["files"].items()}
    assert ep.read(prefs)["unrelatedSentinel"] == "preserve"
    assert ep.read(prefs)["genStudio"]["directStartup"] == "off"
    assert not list(destination.rglob("photocraft.exe"))
    assert not list(models.iterdir())

    run("host", [runtime / ".venv/Scripts/python.exe", "-c",
        "import importlib.util; import genstudio,genstudio.editor_patch; assert genstudio.VERSION=='0.9.3'; assert importlib.util.find_spec('torch') is None"])
    print("Update, editor refresh, restore and reinstall", flush=True)
    run("update", install)
    updated = ep.read(destination / "genstudio-install.json")
    assert updated["executable"] == metadata["executable"]
    assert updated["runtime"] != metadata["runtime"]
    metadata, runtime = updated, Path(updated["runtime"])
    manager = [runtime / ".venv/Scripts/python.exe", "-X", "utf8", runtime / "packaging/manage.py",
               "--metadata", destination / "genstudio-install.json"]
    for name in ep.FILES:
        shutil.copyfile(args.original / name, host / name)
    run("refresh", manager)
    assert {name: ep.sha256(host / name) for name in ep.FILES} == target_hashes
    run("uninstall", [*manager, "--restore"])
    assert {name: ep.sha256(host / name) for name in ep.FILES} == originals
    assert ep.read(prefs)["unrelatedSentinel"] == "preserve"
    run("reinstall", install)
    metadata = ep.read(destination / "genstudio-install.json")
    report = {"passed": True, "folder": str(folder), "install": metadata, "runtime": metadata["runtime"],
              "originals": originals, "targets": target_hashes, "model_inference": False,
              "no_editor_in_package": True, "update_restore_reapply_passed": True}
    (folder / "report.json").write_bytes(ep.encoded(report))
    (ROOT / ".runtime/addon-validation.json").write_bytes(ep.encoded(report))
    print(json.dumps({"passed": True, "report": str(folder / "report.json")}), flush=True)


if __name__ == "__main__":
    main()
