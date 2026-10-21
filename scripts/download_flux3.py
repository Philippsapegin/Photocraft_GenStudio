"""Explicit, resumable FLUX 3 downloads; verifies every file against BFL hashes."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(entry, models, manifest, progress):
    target = models / entry["path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.stat().st_size == entry["bytes"] and digest(target) == entry["sha256"]:
            progress("ready", entry["bytes"])
            return
        raise ValueError("An existing model file has different contents; it was preserved: " + str(target))
    partial = target.with_suffix(target.suffix + ".part")
    url = entry.get("url") or f"https://huggingface.co/{manifest['repository']}/resolve/{manifest['revision']}/{entry['path']}"
    for attempt in range(12):
        offset = partial.stat().st_size if partial.exists() else 0
        if offset > entry["bytes"]:
            raise ValueError("The partial download is oversized: " + str(partial))
        if offset == entry["bytes"]:
            break
        headers = {"User-Agent": "PhotoCraft-GenStudio", "Accept-Encoding": "identity"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        try:
            with urlopen(Request(url, headers=headers), timeout=60) as response:
                if offset and response.status != 206:
                    offset = 0
                if response.status == 206 and not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
                    raise ValueError("Invalid download resume response")
                with partial.open("ab" if offset else "wb") as output:
                    last = 0.0
                    while data := response.read(2 << 20):
                        offset += len(data)
                        if offset > entry["bytes"]:
                            raise ValueError("The download exceeds its declared size")
                        output.write(data)
                        if time.monotonic() - last > 1:
                            progress("downloading", offset)
                            last = time.monotonic()
            if offset == entry["bytes"]:
                break
        except HTTPError as error:
            if error.code not in (408, 429, 500, 502, 503, 504):
                raise
            progress("retrying", offset)
            time.sleep(min(30, 2 ** attempt))
        except InterruptedError:
            raise
        except (URLError, TimeoutError, ConnectionError, OSError):
            progress("retrying", offset)
            time.sleep(min(30, 2 ** attempt))
    if not partial.is_file() or partial.stat().st_size != entry["bytes"]:
        raise TimeoutError("Download incomplete; rerun to resume: " + str(target))
    progress("verifying", entry["bytes"])
    if digest(partial) != entry["sha256"]:
        raise ValueError("Download checksum mismatch; the partial file was preserved: " + str(partial))
    partial.rename(target)
    progress("ready", entry["bytes"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", type=Path)
    args = parser.parse_args()
    manifest = json.loads((ROOT / "photocraft/flux3-models.json").read_text(encoding="utf-8"))
    models = args.models or Path(json.loads((ROOT / "config.photocraft.json").read_text(encoding="utf-8-sig"))["modelsDir"])
    models = models.expanduser().resolve()
    if not models.is_dir():
        raise ValueError("Choose an existing Models folder")
    remaining = sum(max(0, entry["bytes"] - (models / entry["path"]).stat().st_size) if (models / entry["path"]).exists()
                    else max(0, entry["bytes"] - (models / (entry["path"] + ".part")).stat().st_size) if (models / (entry["path"] + ".part")).exists()
                    else entry["bytes"] for entry in manifest["files"])
    if shutil.disk_usage(models).free < remaining + (256 << 20):
        raise ValueError("There is not enough free space in the Models folder")
    status = {"model": manifest["model"], "modelsDir": str(models), "state": "downloading", "files": []}
    report = ROOT / ".runtime/flux3-download.json"
    report.parent.mkdir(exist_ok=True)

    def save():
        temporary = report.with_suffix(".tmp")
        temporary.write_text(json.dumps(status, indent=2), encoding="utf-8")
        temporary.replace(report)

    try:
        for entry in manifest["files"]:
            item = {"path": entry["path"], "total": entry["bytes"], "bytes": 0, "state": "queued"}
            status["files"].append(item)
            def progress(state, count):
                item.update(state=state, bytes=count)
                save()
            print("Downloading", entry["path"], flush=True)
            download(entry, models, manifest, progress)
        status["state"] = "ready"
        save()
        print("All FLUX 3 files verified against the official BFL SHA-256 values.", flush=True)
    except Exception as error:
        status.update(state="error", message=str(error))
        save()
        raise


if __name__ == "__main__":
    main()
