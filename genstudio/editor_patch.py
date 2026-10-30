"""Version-specific PhotoCraft overlays, rollback and update detection. No inference."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import uuid

FILES = ("photocraft.exe", "photocraft-cli.exe")
MAX_BINARY = 256 * 1024 * 1024


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def encoded(value):
    return (json.dumps(value, indent=2, ensure_ascii=True) + "\n").encode("utf-8")


def safe_path(root, relative):
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise ValueError("Invalid add-on package path")
    parts = PurePosixPath(relative)
    target = (root / relative).resolve()
    if parts.is_absolute() or ".." in parts.parts or not target.is_relative_to(root.resolve()):
        raise ValueError("Add-on package path leaves its directory")
    return target


def host_folder(value):
    path = Path(value).resolve()
    if path.is_file():
        path = path.parent
    if not all((path / name).is_file() for name in FILES):
        raise ValueError("Choose an existing PhotoCraft folder containing photocraft.exe and photocraft-cli.exe.")
    return path


def profile_folder(host):
    override = os.environ.get("PHOTOCRAFT_CONFIG_DIR")
    if override:
        return Path(override).resolve()
    if any((host / marker).is_file() for marker in ("portable.txt", "PhotoCraft.portable")):
        return host / "PhotoCraftData"
    if os.name == "nt":
        return Path(os.environ["APPDATA"]) / "Photocraft"
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "photocraft"


def state_path(host):
    return host / ".gengate/state.json"


def state_for(host):
    path = state_path(host)
    value = read(path) if path.is_file() else {}
    if not isinstance(value, dict) or value.get("host", str(host)) != str(host):
        raise ValueError("Invalid GenGate installation record")
    return value


def catalog_for(package):
    catalog = read(package / "editor-patches.json")
    if catalog.get("format") != "gengate-editor-patches-v1" or not 1 <= len(catalog.get("patches", [])) <= 32:
        raise ValueError("Invalid editor patch catalog")
    for entry in catalog["patches"]:
        if set(entry.get("files", {})) != set(FILES):
            raise ValueError("Editor patch must describe both editor and CLI")
        for spec in entry["files"].values():
            for key in ("baseSha256", "targetSha256", "patchSha256"):
                if not re.fullmatch(r"[0-9a-f]{64}", spec.get(key, "")):
                    raise ValueError("Invalid editor patch checksum")
            for key in ("baseSize", "targetSize"):
                if type(spec.get(key)) is not int or not 0 < spec[key] <= MAX_BINARY:
                    raise ValueError("Invalid editor size")
            patch = safe_path(package, spec["patch"])
            if not patch.is_file() or sha256(patch) != spec["patchSha256"]:
                raise ValueError("Editor patch verification failed: " + spec["patch"])
    return catalog


def backup_file(host, digest):
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Invalid backup checksum")
    return host / ".gengate/backups" / digest


def choose_patch(host, catalog):
    current = {name: sha256(host / name) for name in FILES}
    previous = state_for(host)
    for entry in catalog["patches"]:
        sources = {}
        for name, spec in entry["files"].items():
            if current[name] == spec["baseSha256"]:
                sources[name] = host / name
            elif current[name] == spec["targetSha256"] or (
                previous.get("files", {}).get(name, {}).get("targetSha256") == current[name]
                and previous["files"][name].get("baseSha256") == spec["baseSha256"]
            ):
                original = backup_file(host, spec["baseSha256"])
                if original.is_file() and sha256(original) == spec["baseSha256"]:
                    sources[name] = original
        if len(sources) == len(FILES):
            return entry, sources, current
    versions = ", ".join(p["hostVersion"] for p in catalog["patches"])
    raise ValueError("This PhotoCraft build has no compatible GenGate patch (supported: " + versions
                     + "). PhotoCraft may have updated. Get a compatible GenGate update; the editor will not be downgraded.")


def assert_closed(host):
    if os.name != "nt":
        return
    # Read-only: never close the editor, its documents or any other application.
    script = "@(Get-Process -Name photocraft,photocraft-cli -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Path) | ConvertTo-Json -Compress"
    result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script],
                            capture_output=True, text=True, timeout=30,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise ValueError("Could not check whether PhotoCraft is open. Close it before installing GenGate.")
    paths = json.loads(result.stdout.strip() or "[]")
    if isinstance(paths, str):
        paths = [paths]
    if any(Path(p).resolve().parent == host for p in paths if p):
        raise ValueError("Save your work and close this PhotoCraft before changing its GenGate patch.")


@contextmanager
def locked(host):
    root = host / ".gengate"
    root.mkdir(exist_ok=True)
    # An OS-held lock is released on a crash; no stale PID locks to delete.
    with (root / "lock").open("a+b") as stream:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise ValueError("Another GenGate setup or restore is running.") from error
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def _atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".gengate-" + uuid.uuid4().hex + ".tmp")
    try:
        if isinstance(data, Path):
            shutil.copyfile(data, temporary)
        else:
            temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def recover(host, profile):
    journal = host / ".gengate/journal.json"
    if not journal.exists():
        return
    plan = read(journal)
    allowed = {host / name for name in FILES} | {
        host / "runtime/config.photocraft.json", state_path(host), profile / "preferences.json"}
    # Validate every path and rollback source before making any recovery changes.
    for item in plan:
        path = Path(item["path"])
        if path not in allowed or path.resolve() != path:
            raise ValueError("Invalid GenGate recovery path")
        current = sha256(path) if path.is_file() else None
        if current not in (item["before"], item["after"]):
            raise ValueError("A file changed after interrupted setup. Recovery will not overwrite it: " + str(path))
        if item["before"]:
            backup = backup_file(host, item["before"])
            if not backup.is_file() or sha256(backup) != item["before"]:
                raise ValueError("GenGate recovery backup is missing or corrupt")
    for item in reversed(plan):
        path = Path(item["path"])
        if item["before"]:
            _atomic(path, backup_file(host, item["before"]))
        else:
            path.unlink(missing_ok=True)
    journal.unlink()


def transaction(host, profile, changes):
    recover(host, profile)
    plan = []
    for path, data in changes:
        before = sha256(path) if path.is_file() else None
        after = sha256(data) if isinstance(data, Path) else hashlib.sha256(data).hexdigest() if data is not None else None
        if before == after:
            continue
        if before:
            backup = backup_file(host, before)
            if backup.exists() and sha256(backup) != before:
                raise ValueError("A GenGate backup was modified")
            if not backup.exists():
                _atomic(backup, path)
        plan.append({"path": str(path), "before": before, "after": after})
    journal = host / ".gengate/journal.json"
    _atomic(journal, encoded(plan))
    try:
        for path, data in changes:
            item = next((item for item in plan if item["path"] == str(path)), None)
            if item is None:
                continue
            if (sha256(path) if path.is_file() else None) != item["before"]:
                raise ValueError("PhotoCraft files changed during setup. Retry after its update finishes.")
            if data is None:
                path.unlink(missing_ok=True)
            else:
                _atomic(path, data)
        journal.unlink()
    except BaseException:
        recover(host, profile)
        raise


def apply(package, host, settings, preferences, profile, version):
    import bsdiff4
    host = host_folder(host)
    profile = profile.resolve()
    assert_closed(host)
    with locked(host):
        recover(host, profile)
        entry, sources, current = choose_patch(host, catalog_for(package))
        old = state_for(host)
        prepared = []
        scratch = host / ".gengate/staging"
        scratch.mkdir(exist_ok=True)
        try:
            for name, spec in entry["files"].items():
                source = sources[name]
                if source.stat().st_size != spec["baseSize"] or sha256(source) != spec["baseSha256"]:
                    raise ValueError("Original PhotoCraft changed during setup")
                backup = backup_file(host, spec["baseSha256"])
                if not backup.exists():
                    _atomic(backup, source)
                if current[name] == spec["targetSha256"]:
                    continue
                patch = safe_path(package, spec["patch"])
                # Reject corrupt/absurd output sizes before entering the C decoder.
                with patch.open("rb") as stream:
                    header = stream.read(32)
                if len(header) != 32 or header[:8] != b"BSDIFF40" or int.from_bytes(header[24:32], "little") != spec["targetSize"]:
                    raise ValueError("Invalid binary delta header")
                output = scratch / (uuid.uuid4().hex + ".tmp")
                prepared.append((host / name, output))
                bsdiff4.file_patch(str(source), str(output), str(patch))
                if output.stat().st_size != spec["targetSize"] or sha256(output) != spec["targetSha256"]:
                    raise ValueError("Patched PhotoCraft verification failed")
            # Recheck all live binaries before any config or executable changes.
            if any(sha256(host / name) != current[name] for name in FILES):
                raise ValueError("PhotoCraft updated during setup. Retry when the update is finished.")
            config = host / "runtime/config.photocraft.json"
            prefs = profile / "preferences.json"
            previous_preferences = read(prefs) if prefs.exists() else {}
            new_state = {"format": 1, "version": version, "host": str(host), "profile": str(profile),
                         "package": str(package.resolve()), "runtime": settings["runtimeRoot"],
                         "patchId": entry["id"], "hostVersion": entry["hostVersion"], "files": entry["files"],
                         "previousGenStudio": old.get("previousGenStudio", previous_preferences.get("genStudio")),
                         "previousConfig": old.get("previousConfig", config.read_text(encoding="utf-8") if config.exists() else None)}
            changes = [(config, encoded(settings)), (prefs, encoded(preferences)), *prepared,
                       (state_path(host), encoded(new_state))]
            transaction(host, profile, changes)
        finally:
            for _, output in prepared:
                output.unlink(missing_ok=True)
    return new_state


def restore(host):
    host = host_folder(host)
    assert_closed(host)
    with locked(host):
        state = state_for(host)
        profile = Path(state.get("profile", profile_folder(host))).resolve()
        recover(host, profile)
        state = state_for(host)
        if not state:
            return {"restored": False, "reason": "GenGate is not installed in this PhotoCraft."}
        changes = []
        retained = []
        for name, spec in state["files"].items():
            if name not in FILES:
                raise ValueError("Invalid original editor path")
            path = host / name
            current = sha256(path)
            if current == spec["targetSha256"]:
                original = backup_file(host, spec["baseSha256"])
                if not original.is_file() or sha256(original) != spec["baseSha256"]:
                    raise ValueError("Original PhotoCraft backup is missing or corrupt. No files were restored.")
                changes.append((path, original))
            elif current != spec["baseSha256"]:
                retained.append(name)
        prefs = profile / "preferences.json"
        values = read(prefs) if prefs.exists() else {}
        if values.get("genStudio", {}).get("runtimeRoot") == state["runtime"]:
            if state["previousGenStudio"] is None:
                values.pop("genStudio", None)
            else:
                values["genStudio"] = state["previousGenStudio"]
            changes.append((prefs, encoded(values)))
        config = host / "runtime/config.photocraft.json"
        if config.exists() and read(config).get("runtimeRoot") == state["runtime"]:
            changes.append((config, state["previousConfig"].encode("utf-8") if state["previousConfig"] is not None else None))
        changes.append((state_path(host), None))
        transaction(host, profile, changes)
    return {"restored": True, "updatedFilesRetained": retained}


def previous_settings(host, preferences):
    """An official editor can drop the unknown add-on section when it saves prefs."""
    if "genStudio" in preferences:
        if not isinstance(preferences["genStudio"], dict):
            raise ValueError("Existing GenGate preferences are invalid. Keep a copy before reinstalling.")
        return preferences["genStudio"]
    state = state_for(host)
    runtime = state.get("runtime")
    if not isinstance(runtime, str) or not runtime:
        return {}
    path = next((path for path in (Path(runtime) / "config.photocraft.json", host / "runtime/config.photocraft.json") if path.is_file()), None)
    if path is None:
        return {}
    if path.stat().st_size > 128 * 1024:
        raise ValueError("The saved GenGate runtime settings exceed 128 KB.")
    settings = read(path)
    if not isinstance(settings, dict):
        raise ValueError("The saved GenGate runtime settings are invalid.")
    return settings


def ensure_current(host):
    """Called by the GenGate launcher. Unknown updates are never downgraded."""
    host = host_folder(host)
    state = state_for(host)
    profile = Path(state.get("profile", profile_folder(host))).resolve()
    with locked(host):
        if (host / ".gengate/journal.json").exists():
            assert_closed(host)
            recover(host, profile)
            state = state_for(host)
    if not state:
        return {"state": "notInstalled"}
    entry, _, current = choose_patch(host, catalog_for(Path(state["package"])))
    if all(current[name] == entry["files"][name]["targetSha256"] for name in FILES):
        return {"state": "applied", "hostVersion": entry["hostVersion"]}
    prefs = read(profile / "preferences.json")
    settings = previous_settings(host, prefs)
    if not settings:
        raise ValueError("GenGate settings are missing. Reinstall the compatible add-on to configure it again.")
    prefs["genStudio"] = settings
    apply(Path(state["package"]), host, settings, prefs, profile, state["version"])
    return {"state": "reapplied", "hostVersion": entry["hostVersion"]}
