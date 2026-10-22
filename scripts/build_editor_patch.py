"""Build checksum-bound binary patches; never include a complete editor in the add-on."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time

import bsdiff4

FILES = ("photocraft.exe", "photocraft-cli.exe")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def build(original, modified, output, host_version, upstream):
    bases = json.loads((Path(__file__).resolve().parents[1] / "photocraft/editor-bases.json").read_text(encoding="utf-8"))
    expected = bases.get(host_version)
    if not expected or expected["upstream"] != upstream:
        raise ValueError("Register and verify the official PhotoCraft base before building a delta.")
    for name in FILES:
        if digest(original / name) != expected["files"][name]:
            raise ValueError("Original PhotoCraft does not match the verified official release: " + name)
    directory = output / "patches" / ("photocraft-" + host_version)
    directory.mkdir(parents=True, exist_ok=True)
    files = {}
    for name in FILES:
        before, after = original / name, modified / name
        target = directory / (name + ".bsdiff")
        started = time.monotonic()
        print("Building delta: " + name, flush=True)
        bsdiff4.file_diff(str(before), str(after), str(target))
        # Verify both forward reconstruction and that the delta needs its base.
        rebuilt = bsdiff4.patch(before.read_bytes(), target.read_bytes())
        if hashlib.sha256(rebuilt).hexdigest() != digest(after):
            raise ValueError("Delta reconstruction failed: " + name)
        files[name] = {"baseSha256": digest(before), "baseSize": before.stat().st_size,
                       "targetSha256": digest(after), "targetSize": after.stat().st_size,
                       "patch": target.relative_to(output).as_posix(),
                       "patchSha256": digest(target)}
        print(json.dumps({"file": name, "patchBytes": target.stat().st_size,
                          "seconds": round(time.monotonic() - started, 2)}), flush=True)
    previous = output / "editor-patches.json"
    catalog = json.loads(previous.read_text(encoding="utf-8")) if previous.exists() else {"format": "gengate-editor-patches-v1", "patches": []}
    if catalog.get("format") != "gengate-editor-patches-v1":
        raise ValueError("Unknown existing editor patch catalog")
    catalog["patches"] = [entry for entry in catalog["patches"] if entry["hostVersion"] != host_version]
    catalog["patches"].append({"id": "photocraft-" + host_version + "-windows-x64", "hostVersion": host_version,
                               "upstream": upstream, "files": files})
    (output / "editor-patches.json").write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    return catalog


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--modified", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--host-version", required=True)
    parser.add_argument("--upstream", required=True)
    args = parser.parse_args()
    build(args.original.resolve(), args.modified.resolve(), args.output.resolve(), args.host_version, args.upstream)
