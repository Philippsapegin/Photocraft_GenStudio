"""Ownership of incomplete setup work, without deleting user files on retry."""
import json
from pathlib import Path
import uuid

MARKER = ".gengate-setup.json"
ROOT_FILES = ("Launch.ps1", "PhotoCraft.cmd", "Uninstall.cmd", "Uninstall.ps1", "genstudio-install.json")


def pending(root):
    path = root / MARKER
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Invalid incomplete GenGate installation record")
    stage = Path(value.get("package", "")).resolve()
    if (value.get("format") != 1 or value.get("root") != str(root.resolve())
            or stage.parent != (root / "versions").resolve() or stage == stage.parent
            or not Path(value.get("host", "")).is_absolute()
            or not Path(value.get("profile", "")).is_absolute()):
        raise ValueError("Invalid incomplete GenGate installation record. Choose another folder.")
    return value


def stage(root, host, profile, version):
    value = pending(root)
    if value:
        if value["host"] != str(host) or value["profile"] != str(profile):
            raise ValueError("This incomplete setup belongs to another PhotoCraft installation.")
        metadata = root / "genstudio-install.json"
        installed = json.loads(metadata.read_text(encoding="utf-8-sig")) if metadata.is_file() else {}
        if installed.get("package") != value["package"] and Path(value["package"]).name.startswith(version + "-"):
            # Reuse only unfinished work from this version, never a live runtime.
            return Path(value["package"])
    root.mkdir(parents=True, exist_ok=True)
    package = root / "versions" / (version + "-" + uuid.uuid4().hex[:8])
    from .editor_patch import _atomic, encoded
    _atomic(root / MARKER, encoded({"format": 1, "root": str(root), "host": str(host),
                                  "profile": str(profile), "package": str(package)}))
    return package
