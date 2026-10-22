"""One PhotoCraft identity in Start and Desktop, using the installed GenGate launcher."""
import json
import os
from pathlib import Path
import subprocess
import tempfile


def repair(destination, host, *, desktop=True, installed=True, locations=None):
    root = Path(__file__).resolve().parents[1]
    host, destination = Path(host).resolve(), Path(destination).resolve()
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    data = {"host": str(host), "desktop": desktop, "icon": str(host / "photocraft.exe"),
            "target": str(powershell) if installed else str(host / "photocraft.exe"),
            "arguments": '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + str(destination / "Launch.ps1") + '"' if installed else "",
            "working": str(destination if installed else host),
            "description": "PhotoCraft with GenGate" if installed else "PhotoCraft"}
    if locations is not None:
        data.update(locations)
    with tempfile.TemporaryDirectory(prefix="GenGate shortcuts ") as folder:
        request = Path(folder) / "request.json"
        request.write_text(json.dumps(data), encoding="utf-8")
        result = subprocess.run([str(powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                 str(root / "packaging/Repair-Shortcuts.ps1"), "-Data", str(request)],
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise RuntimeError("Could not consolidate PhotoCraft shortcuts: " + result.stderr[-1200:])
    # PowerShell 5 stdout can use the OEM page for non-ASCII paths. The report is
    # informational; canonical links themselves are written through Unicode COM.
    return json.loads(result.stdout.strip().splitlines()[-1])
