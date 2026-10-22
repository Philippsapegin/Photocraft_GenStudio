import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from genstudio.windows_shortcuts import repair

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("shortcut_installer", ROOT / "packaging/installer.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


@unittest.skipUnless(os.name == "nt", "Windows shell shortcuts")
class WindowsShortcutsTests(unittest.TestCase):
    def test_duplicate_editor_links_converge_and_uninstall_keeps_plain_editor(self):
        with tempfile.TemporaryDirectory(prefix="GenGate shortcuts ") as folder:
            root = Path(folder) / "проверка"
            host, addon = root / "PhotoCraft", root / "GenGate"
            host.mkdir(parents=True)
            addon.mkdir()
            programs, desktop, common = root / "Start", root / "Desktop", root / "Common"
            for p in (programs, desktop, common):
                p.mkdir()
            exe, old = host / "photocraft.exe", host / "photocraft-genstudio-old.exe"
            exe.write_bytes(b"fixture, never execute")
            old.write_bytes(b"fixture, never execute")
            for link in (programs / "PhotoCraft.lnk", desktop / "PhotoCraft.lnk", common / "PhotoCraft Old.lnk", desktop / "Preview.lnk"):
                installer.shortcut(link, old, "", host, exe)
            powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
            installer.shortcut(programs / "PhotoCraft GenGate.lnk", powershell,
                               '-File "' + str(addon / "Launch.ps1") + '"', addon, exe)
            unrelated = desktop / "My project.lnk"
            installer.shortcut(unrelated, root / "document.pcraft", "", root, exe)
            untouched = unrelated.read_bytes()
            locations = {"locations": list(map(str, (programs, desktop, common))),
                         "programs": str(programs), "desktopPath": str(desktop)}
            result = repair(addon, host, locations=locations)
            self.assertEqual(set(result["canonical"]), {str(programs / "PhotoCraft.lnk"), str(desktop / "PhotoCraft.lnk")})
            self.assertEqual(len(result["removed"]), 3)
            self.assertEqual(unrelated.read_bytes(), untouched)
            self.assertEqual(len(list(root.rglob("*.lnk"))), 8)  # two launchers, project and five saved originals
            # Repeating a setup keeps the same two identities, not a new GenGate app.
            self.assertEqual(repair(addon, host, locations=locations)["removed"], [])
            repair(addon, host, installed=False, locations=locations)
            request = root / "inspect.json"
            request.write_text(json.dumps({"paths": [str(programs / "PhotoCraft.lnk"), str(desktop / "PhotoCraft.lnk")]}), encoding="utf-8")
            script = "$v=Get-Content -LiteralPath $args[0] -Raw -Encoding UTF8|ConvertFrom-Json; $w=New-Object -ComObject WScript.Shell; @($v.paths|ForEach-Object {$s=$w.CreateShortcut($_); @{target=$s.TargetPath;arguments=$s.Arguments}})|ConvertTo-Json -Compress"
            # Pass the data filename through an environment variable, not shell interpolation.
            env = os.environ.copy()
            env["GENGATE_SHORTCUT_TEST_DATA"] = str(request)
            script = script.replace("$args[0]", "$env:GENGATE_SHORTCUT_TEST_DATA")
            values = json.loads(subprocess.check_output([str(powershell), "-NoProfile", "-Command", script], env=env))
            self.assertTrue(all(Path(v["target"]) == exe and v["arguments"] == "" for v in values))
