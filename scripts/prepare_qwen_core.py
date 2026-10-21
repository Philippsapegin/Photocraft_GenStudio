"""Export only the pinned GPL model library, never a UI/server or model weights."""
import argparse
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import subprocess
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REVISION = "b0f4b7b294ce482a2e071d9d762c133d38c7aa07"
PARTS = ("comfy", "comfy_config", "folder_paths.py", "node_helpers.py", "LICENSE")


def prepare(source=None, *, revision=REVISION, directory="qwen-core"):
    target = ROOT / ".runtime" / directory
    stamp = target / "genstudio-core.json"
    if stamp.exists() and (target / "comfy/sd.py").is_file() and json.loads(stamp.read_text())["revision"] == revision:
        return target
    if source and (Path(source) / ".git").exists():
        archive = subprocess.run(["git", "-C", str(source), "archive", "--format=zip", revision, *PARTS],
                                 check=True, stdout=subprocess.PIPE).stdout
        prefix = ""
    else:
        url = f"https://codeload.github.com/Comfy-Org/ComfyUI/zip/{revision}"
        with urllib.request.urlopen(url, timeout=120) as response:
            archive = response.read()
        prefix = f"ComfyUI-{revision}/"
    with zipfile.ZipFile(BytesIO(archive)) as bundle:
        for entry in bundle.infolist():
            if not entry.filename.startswith(prefix):
                continue
            relative = PurePosixPath(entry.filename[len(prefix):])
            if relative.is_absolute() or ".." in relative.parts or not relative.parts or relative.parts[0] not in PARTS:
                continue
            if entry.is_dir():
                continue
            path = target.joinpath(*relative.parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(bundle.read(entry))
    stamp.write_text(json.dumps({"revision": revision, "license": "GPL-3.0",
        "source": "https://github.com/Comfy-Org/ComfyUI", "includes_weights": False}, indent=2))
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    print(prepare(parser.parse_args().source))
