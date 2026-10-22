"""Tiny real BSDIFF fixtures; no editor, model or network required."""
import hashlib
import json

import bsdiff4

from genstudio import editor_patch


def fixture(bundle, host, suffix=b"new native GenGate UI"):
    bundle.mkdir(parents=True, exist_ok=True)
    host.mkdir(parents=True, exist_ok=True)
    originals, outputs, entries = {}, {}, {}
    for name in editor_patch.FILES:
        original = (b"MZ synthetic PhotoCraft " + name.encode()) * 50
        output = original[:120] + suffix + original[120:]
        (host / name).write_bytes(original)
        patch = bundle / "patches" / (name + ".bsdiff")
        patch.parent.mkdir(exist_ok=True)
        patch.write_bytes(bsdiff4.diff(original, output))
        entries[name] = {"baseSha256": hashlib.sha256(original).hexdigest(), "baseSize": len(original),
                         "targetSha256": hashlib.sha256(output).hexdigest(), "targetSize": len(output),
                         "patch": patch.relative_to(bundle).as_posix(), "patchSha256": editor_patch.sha256(patch)}
        originals[name], outputs[name] = original, output
    catalog = {"format": "gengate-editor-patches-v1", "patches": [
        {"id": "fixture-0.8.2", "hostVersion": "0.8.2", "upstream": "fixture", "files": entries}]}
    (bundle / "editor-patches.json").write_bytes(editor_patch.encoded(catalog))
    (host / "portable.txt").write_text("")
    return originals, outputs, catalog
