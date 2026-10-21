"""Synthetic packaging checks. No model loading, downloads, or engine start."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("installer", Path(__file__).resolve().parents[1] / "packaging/installer.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="Gen Studio install with spaces ")
        self.root = Path(self.temp.name)
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        self.destination = self.root / "installed"
        self.models = self.root / "shared models"
        self.models.mkdir()
        files = {"PhotoCraft.exe": b"fake exe", "runtime/.venv/Scripts/python.exe": b"fake python",
                 "runtime/genstudio/control.py": b"# fixture"}
        for name, content in files.items():
            p = self.bundle / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)
        installer.write_json(self.bundle / "manifest.json", {"version": installer.VERSION,
            "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}})

    def tearDown(self):
        self.temp.cleanup()

    def test_manifest_rejects_tampering_and_path_traversal(self):
        self.assertEqual(installer.verify_bundle(self.bundle)["version"], installer.VERSION)
        (self.bundle / "PhotoCraft.exe").write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "verification failed"):
            installer.verify_bundle(self.bundle)
        for name in ("../secret", "C:/secret", "/secret", "a\\secret", "x:stream"):
            with self.assertRaises(ValueError):
                installer.bundle_path(self.bundle, name)

    def test_install_never_overlays_an_unrelated_folder_or_its_own_package(self):
        for path in (self.bundle, self.root, Path(self.root.anchor), Path.home()):
            with self.assertRaises(ValueError):
                installer.valid_destination(str(path), self.bundle)
        self.destination.mkdir()
        keep = self.destination / "my document.psd"
        keep.write_bytes(b"untouched")
        with self.assertRaisesRegex(ValueError, "other files"):
            installer.valid_destination(str(self.destination), self.bundle)
        self.assertEqual(keep.read_bytes(), b"untouched")

    def test_settings_use_install_paths_and_shared_models(self):
        runtime = self.destination / "versions/alpha/runtime"
        request = {"engine": "direct", "modelsDir": str(self.models)}
        settings = installer.settings_for(request, runtime, "external/python.exe")
        self.assertEqual(settings["runtimeRoot"], str(runtime))
        self.assertEqual(settings["modelsDir"], str(self.models.resolve()))
        self.assertEqual(settings["directPython"], "external/python.exe")
        self.assertEqual(settings["directStartup"], "firstRequest")
        self.assertEqual(list(self.models.iterdir()), [])

    def test_fresh_cuda_runtime_installs_only_into_its_private_copy(self):
        runtime = self.root / "runtime"
        host = runtime / ".venv/Scripts"
        host.mkdir(parents=True)
        (host / "python.exe").write_bytes(b"synthetic interpreter")
        (host / "python313._pth").write_text("python313.zip\n.\nsite-packages\n..\\..\nimport site\n")
        before = {p.name: p.read_bytes() for p in host.iterdir()}
        private = runtime / ".runtime/qwen-direct"
        with patch.object(installer, "run_logged") as install, \
             patch.object(installer, "check_python", return_value=str(private / "python.exe")) as inspect:
            result = installer.install_direct(runtime)
        self.assertEqual(result, str(private / "python.exe"))
        self.assertEqual(before, {p.name: p.read_bytes() for p in host.iterdir()})
        self.assertEqual((private / "python.exe").read_bytes(), before["python.exe"])
        args = install.call_args.args[0]
        self.assertEqual(args[0], str(private / "python.exe"))
        self.assertEqual(args[args.index("--target") + 1], str(private / "site-packages"))
        inspect.assert_called_once_with(private / "python.exe", direct=True)

    def test_first_install_and_update_preserve_preferences_and_weights(self):
        for name in ("Launch.ps1", "PhotoCraft.cmd"):
            (self.bundle / name).write_text("fixture")
        request = {"installDir": str(self.destination), "modelsDir": str(self.models),
                   "engine": "direct", "python": "existing/python.exe"}
        sentinel = self.models / "do not copy.safetensors"
        sentinel.write_bytes(b"synthetic sentinel")
        # Use the real file-copy/install/metadata transaction, mocking only the
        # external interpreter probe and optional model discovery.
        with patch.object(installer, "check_python", return_value="existing/python.exe"), \
             patch("genstudio.models.resolve", return_value={}) as discover:
            installer.install(request, self.bundle, shortcuts=False)
            prefs_path = self.destination / "Profile/preferences.json"
            preferences = installer.read_json(prefs_path)
            preferences["general"] = {"theme": "light"}
            preferences["genStudio"]["directStartup"] = "off"
            preferences["genStudio"].update(model="flux-3-image-klein",textEncoder="text_encoders/custom.safetensors",vae="vae/custom.safetensors",
                segmentation={"enabled":True,"detector":"yolov8m-worldv2","segmenter":"sam3","python":"private/python.exe"})
            installer.write_json(prefs_path, preferences)
            before = installer.read_json(self.destination / "genstudio-install.json")
            installer.install(request, self.bundle, shortcuts=False)
            after = installer.read_json(self.destination / "genstudio-install.json")
            discover.assert_called_with(str(self.models.resolve()),"text_encoders/custom.safetensors","vae/custom.safetensors","flux-3-image-klein")
        self.assertNotEqual(before["executable"], after["executable"])
        self.assertTrue(Path(before["executable"]).is_file())
        updated = installer.read_json(prefs_path)
        self.assertEqual(updated["general"], {"theme": "light"})
        self.assertEqual(updated["genStudio"]["directStartup"], "off")
        self.assertEqual(updated["genStudio"]["model"], "flux-3-image-klein")
        self.assertEqual(updated["genStudio"]["textEncoder"], "text_encoders/custom.safetensors")
        self.assertEqual(updated["genStudio"]["vae"], "vae/custom.safetensors")
        self.assertTrue(updated["genStudio"]["segmentation"]["enabled"])
        self.assertEqual(updated["genStudio"]["segmentation"]["detector"], "yolov8m-worldv2")
        self.assertTrue(prefs_path.with_suffix(".before-setup.json").is_file())
        self.assertEqual(list(self.models.iterdir()), [sentinel])
        self.assertFalse(list(self.destination.rglob("*.safetensors")))

        # Later updates must also retain each preset's manual files and edit-only
        # adapter, without copying weights into the application directory.
        from genstudio.settings import component_defaults
        presets = {
            "qwen-image-2.1-turbo": {**component_defaults(), "modelFile": "diffusion_models/qwen-custom.safetensors",
                                     "constantLora": "loras/qwen-edit.safetensors", "constantLoraStrength": .65},
            "flux-3-image-klein": {**component_defaults(), "promptUpsampler": "text_encoders/custom-upsampler.safetensors",
                                   "constantLora": "loras/flux-edit.safetensors", "constantLoraStrength": .4},
        }
        updated["genStudio"]["presets"] = presets
        installer.write_json(prefs_path, updated)
        with patch.object(installer, "check_python", return_value="existing/python.exe"), \
             patch("genstudio.models.resolve_components", return_value={}) as discover:
            installer.install(request, self.bundle, shortcuts=False)
            discover.assert_called_once_with(str(self.models.resolve()), presets["flux-3-image-klein"], "flux-3-image-klein")
        self.assertEqual(installer.read_json(prefs_path)["genStudio"]["presets"], presets)
        self.assertEqual(list(self.models.iterdir()), [sentinel])
        self.assertFalse(list(self.destination.rglob("*.safetensors")))


if __name__ == "__main__":
    unittest.main()
