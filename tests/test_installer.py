"""Synthetic packaging checks. No model loading, downloads, or engine start."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from io import StringIO
from contextlib import redirect_stdout
from unittest.mock import patch
from installer_fixtures import fixture
from genstudio import editor_patch

spec = importlib.util.spec_from_file_location("installer", Path(__file__).resolve().parents[1] / "packaging/installer.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def test_incompatible_editor_returns_guidance_instead_of_installation_failure(self):
        guidance = {'state':'updatePhotoCraft','compatible':False,'message':'Update PhotoCraft first.'}
        output = StringIO()
        with patch('sys.argv',['installer','--request','fixture.json']), patch.object(installer,'read_json',return_value=self.request()), \
             patch.object(installer,'install',side_effect=editor_patch.CompatibilityError('mismatch')), \
             patch('genstudio.setup_compatibility.preflight',return_value=guidance), redirect_stdout(output):
            code = installer.main()
        self.assertEqual(code, 2)
        self.assertNotIn('ERROR:', output.getvalue())
        self.assertIn('needsPhotoCraft', output.getvalue())
        self.assertEqual(output.getvalue().splitlines()[-1], guidance['message'])

    def comfy_fixture(self):
        folder = self.root / 'ComfyUI'
        (folder / 'custom_nodes/LanPaint').mkdir(parents=True)
        (folder / 'main.py').write_text('# synthetic ComfyUI, never execute')
        return folder

    def test_checkbox_setup_installs_direct_and_comfy_together(self):
        comfy = self.comfy_fixture()
        request = self.request(installDirect=True, comfyDir=str(comfy), comfyPython='comfy/python.exe')
        with patch.object(installer, 'check_python', return_value='comfy/python.exe') as inspect, \
             patch.object(installer, 'install_direct', return_value='private/direct.exe') as direct, \
             patch('genstudio.models.resolve'):
            installer.install(request, self.bundle, shortcuts=False)
        settings = installer.read_json(self.profile / 'preferences.json')['genStudio']
        self.assertEqual(settings['engine'], 'direct')
        self.assertEqual(settings['directPython'], 'private/direct.exe')
        self.assertEqual(settings['comfyPython'], 'comfy/python.exe')
        self.assertEqual(settings['comfyDir'], str(comfy))
        self.assertTrue((comfy / 'custom_nodes/GenGate/gengate-nodes.json').is_file())
        direct.assert_called_once()
        inspect.assert_called_once_with('comfy/python.exe')

    def test_checkbox_comfy_only_skips_cuda_and_uses_one_photocraft_identity(self):
        comfy = self.comfy_fixture()
        installer.write_json(self.profile / 'preferences.json', {'genStudio': {
            'directPython': 'old/direct.exe', 'directStartup': 'appStart'}})
        request = self.request(installDirect=False, comfyDir=str(comfy), comfyPython='comfy/python.exe', desktopShortcut=False)
        with patch.object(installer, 'check_python', return_value='comfy/python.exe') as inspect, \
             patch.object(installer, 'install_direct', side_effect=AssertionError('No CUDA installation')), \
             patch('genstudio.windows_shortcuts.repair') as repair, \
             patch('genstudio.models.resolve', side_effect=AssertionError('ComfyUI workflow owns its models')):
            installer.install(request, self.bundle)
        settings = installer.read_json(self.profile / 'preferences.json')['genStudio']
        self.assertEqual(settings['engine'], 'comfy')
        self.assertEqual(settings['directPython'], '')
        self.assertEqual(settings['directStartup'], 'off')
        self.assertEqual(settings['comfyPython'], 'comfy/python.exe')
        metadata = installer.read_json(self.destination / 'genstudio-install.json')
        self.assertFalse(metadata['directInstalled'])
        self.assertTrue(metadata['desktopShortcut'])
        repair.assert_called_once_with(self.destination.resolve(), self.host.resolve(), desktop=True)
        inspect.assert_called_once_with('comfy/python.exe')

    def test_checkbox_comfy_configuration_is_validated_before_any_install(self):
        for direct, folder, python in ((False, '', ''), (False, 'folder', ''), (True, '', 'python.exe'), (True, 'missing', 'python.exe')):
            with self.subTest(direct=direct, folder=folder), patch.object(installer, 'install_direct') as download:
                with self.assertRaisesRegex(ValueError, 'ComfyUI'):
                    installer.install(self.request(installDirect=direct, comfyDir=folder, comfyPython=python), self.bundle, shortcuts=False)
                download.assert_not_called()
            self.assertFalse(self.destination.exists())
            self.assertEqual({name: (self.host/name).read_bytes() for name in editor_patch.FILES}, self.originals)

    def test_checkbox_update_reuses_direct_and_keeps_active_comfy(self):
        existing = self.root / 'private Python.exe'
        existing.write_bytes(b'never execute')
        previous = {'engine': 'comfy', 'directPython': str(existing), 'comfyPython': 'comfy/python.exe',
                    'comfyDir': 'my ComfyUI', 'directStartup': 'off', 'fillWorkflow': 'keep.json'}
        installer.write_json(self.profile / 'preferences.json', {'genStudio': previous, 'unrelated': 'keep'})
        with patch.object(installer, 'check_python', return_value=str(existing)) as inspect, \
             patch.object(installer, 'install_direct', side_effect=AssertionError('Existing Direct must be reused')), \
             patch('genstudio.models.resolve'):
            installer.install(self.request(installDirect=True, comfyDir='', comfyPython=''), self.bundle, shortcuts=False)
        prefs = installer.read_json(self.profile / 'preferences.json')
        for key, value in previous.items():
            self.assertEqual(prefs['genStudio'][key], value)
        self.assertEqual(prefs['unrelated'], 'keep')
        inspect.assert_called_once_with(str(existing), direct=True)

    def test_update_recovers_python_and_folders_from_installed_runtime(self):
        existing = self.root / 'private Python.exe'
        existing.write_bytes(b'never execute')
        prior_runtime = self.destination / 'versions/previous/runtime'
        prior_runtime.mkdir(parents=True)
        saved = {'engine':'direct', 'directPython':str(existing), 'directStartup':'off',
                 'modelsDir':str(self.models), 'comfyDir':'saved comfy', 'comfyPython':'saved python',
                 'fillWorkflow':'keep.json', 'runtimeRoot':str(prior_runtime)}
        installer.write_json(prior_runtime / 'config.photocraft.json', saved)
        installer.write_json(self.destination / 'genstudio-install.json', {'installMode':'overlay',
            'host':str(self.host), 'runtime':str(prior_runtime)})
        installer.write_json(self.profile / 'preferences.json', {'unrelated':'keep'})
        with patch.object(installer, 'check_python', return_value=str(existing)), \
             patch.object(installer, 'install_direct', side_effect=AssertionError('No repeated Direct download')), \
             patch('genstudio.models.resolve'):
            installer.install(self.request(installDirect=True, comfyDir='', comfyPython=''), self.bundle, shortcuts=False)
        value = installer.read_json(self.profile / 'preferences.json')
        self.assertEqual(Path(value['genStudio']['modelsDir']).resolve(), Path(saved['modelsDir']).resolve())
        for key in ('comfyDir','comfyPython','directPython','fillWorkflow'):
            self.assertEqual(value['genStudio'][key], saved[key])
        self.assertEqual(value['unrelated'], 'keep')

    def test_prepared_direct_runtime_is_reused_after_a_retry(self):
        runtime=self.root/'prepared runtime';python=runtime/'.runtime/qwen-direct/python.exe'
        python.parent.mkdir(parents=True);python.write_bytes(b'never execute')
        with patch.object(installer,'check_python',return_value=str(python)) as inspect, patch.object(installer,'run_logged') as download:
            self.assertEqual(installer.install_direct(runtime),str(python))
        inspect.assert_called_once_with(python,direct=True)
        download.assert_not_called()

    def test_checkbox_default_prepares_private_direct_without_comfy(self):
        with patch.object(installer, 'check_python', side_effect=AssertionError('No external Python selected')), \
             patch.object(installer, 'install_direct', return_value='private/direct.exe') as download, \
             patch('genstudio.models.resolve'):
            installer.install(self.request(installDirect=True, comfyDir='', comfyPython=''), self.bundle, shortcuts=False)
        settings = installer.read_json(self.profile / 'preferences.json')['genStudio']
        self.assertEqual(settings['engine'], 'direct')
        self.assertEqual(settings['directPython'], 'private/direct.exe')
        self.assertEqual(settings['comfyPython'], '')
        download.assert_called_once()

    def test_terminal_result_distinguishes_installed_notes_and_real_failure(self):
        for warnings, failure, status, code in (([], None, 'installed', 0),
                                               (['synthetic shortcut warning'], None, 'installedWithWarnings', 0),
                                               ([], ValueError('synthetic invalid editor'), 'failed', 1)):
            def install(*args, **kwargs):
                kwargs['warnings'].extend(warnings)
                if failure:
                    raise failure
            output = StringIO()
            with self.subTest(status=status), patch('sys.argv', ['installer', '--request', 'synthetic.json']), \
                 patch.object(installer, 'read_json', return_value={}), patch.object(installer, 'install', side_effect=install), redirect_stdout(output):
                self.assertEqual(installer.main(), code)
            lines = output.getvalue().splitlines()
            result = json.loads(next(line.removeprefix('GENGATE_SETUP_RESULT ') for line in lines if line.startswith('GENGATE_SETUP_RESULT ')))
            self.assertEqual(result['status'], status)
            self.assertTrue(lines[-1].startswith('ERROR:' if failure else 'Installed successfully'))

    def test_optional_shortcut_and_model_scan_failures_leave_a_successful_install(self):
        request = {"installDir": str(self.destination), "photocraftDir": str(self.host), "modelsDir": str(self.models),
                   "engine": "direct", "python": "existing/python.exe"}
        warnings = []
        with patch.object(installer, "check_python", return_value="existing/python.exe"), \
             patch("genstudio.windows_shortcuts.repair", side_effect=OSError("synthetic shell failure")), \
             patch("genstudio.models.resolve", side_effect=PermissionError("synthetic model directory denied")):
            result = installer.install(request, self.bundle, warnings=warnings)
        self.assertEqual(result.resolve(), self.destination.resolve())
        metadata = installer.read_json(self.destination / "genstudio-install.json")
        self.assertEqual(metadata["version"], installer.VERSION)
        self.assertFalse(metadata["shortcutsInstalled"])
        self.assertEqual(len(warnings), 2)
        self.assertIn("synthetic shell failure", warnings[0])
        self.assertIn("synthetic model directory denied", warnings[1])
        self.assertEqual({name: editor_patch.sha256(self.host / name) for name in editor_patch.FILES},
                         {name: hashlib.sha256(content).hexdigest() for name, content in self.outputs.items()})

    def request(self, **values):
        return {"installDir": str(self.destination), "photocraftDir": str(self.host), "modelsDir": str(self.models),
                "engine": "direct", "python": "existing/python.exe", **values}

    def test_retry_after_interrupted_dependency_install_reuses_owned_stage(self):
        with patch.object(installer, "install_direct", side_effect=RuntimeError("download interrupted")):
            with self.assertRaisesRegex(RuntimeError, "download interrupted"):
                installer.install(self.request(newRuntime=True), self.bundle, shortcuts=False)
        stages = list((self.destination / "versions").iterdir())
        self.assertEqual(len(stages), 1)
        with patch.object(installer, "install_direct", return_value="private/python.exe"), patch("genstudio.models.resolve"):
            installer.install(self.request(newRuntime=True), self.bundle, shortcuts=False)
        self.assertEqual(list((self.destination / "versions").iterdir()), stages)
        self.assertFalse((self.destination / ".gengate-setup.json").exists())

    def test_launcher_metadata_and_native_files_roll_back_together(self):
        for upgrade in (False, True):
            with self.subTest(upgrade=upgrade), patch.object(installer, "check_python", return_value="existing/python.exe"), patch("genstudio.models.resolve"):
                if upgrade:
                    installer.install(self.request(), self.bundle, shortcuts=False)
                tracked = [self.host / name for name in editor_patch.FILES] + [self.host / "runtime/config.photocraft.json",
                           self.profile / "preferences.json", editor_patch.state_path(self.host)] + [self.destination / name for name in
                           ("Launch.ps1", "PhotoCraft.cmd", "Uninstall.cmd", "Uninstall.ps1", "genstudio-install.json")]
                before = {p: p.read_bytes() if p.exists() else None for p in tracked}
                atomic = editor_patch._atomic
                failed = False
                def fail_once(path, data):
                    nonlocal failed
                    if path.resolve() == (self.profile / "preferences.json").resolve() and not failed:
                        failed = True
                        raise OSError("synthetic disk failure")
                    return atomic(path, data)
                # Force a preferences change on upgrade as well.
                with patch.object(editor_patch, "_atomic", side_effect=fail_once):
                    with self.assertRaisesRegex(OSError, "synthetic disk failure"):
                        installer.install(self.request(), self.bundle, shortcuts=False)
                self.assertEqual({p: p.read_bytes() if p.exists() else None for p in tracked}, before)
                self.assertFalse((self.host / ".gengate/journal.json").exists())
                installer.install(self.request(), self.bundle, shortcuts=False)

    def test_first_install_write_failures_at_every_commit_boundary_are_retryable(self):
        from genstudio.installation import ROOT_FILES
        tracked = [self.destination / name for name in ROOT_FILES] + [self.host / "runtime/config.photocraft.json",
                   self.profile / "preferences.json", *[self.host / name for name in editor_patch.FILES], editor_patch.state_path(self.host)]
        before = {p: p.read_bytes() if p.exists() else None for p in tracked}
        atomic = editor_patch._atomic
        for boundary in tracked:
            failed = False
            def fail_once(path, data):
                nonlocal failed
                if path.resolve() == boundary.resolve() and not failed:
                    failed = True
                    raise PermissionError("synthetic commit failure")
                return atomic(path, data)
            with self.subTest(boundary=boundary.name), patch.object(installer, "check_python", return_value="existing/python.exe"), patch.object(editor_patch, "_atomic", side_effect=fail_once):
                with self.assertRaisesRegex(PermissionError, "synthetic commit failure"):
                    installer.install(self.request(), self.bundle, shortcuts=False)
            self.assertTrue(failed)
            self.assertEqual({p: p.read_bytes() if p.exists() else None for p in tracked}, before)
        with patch.object(installer, "check_python", return_value="existing/python.exe"), patch("genstudio.models.resolve"):
            installer.install(self.request(), self.bundle, shortcuts=False)
        editor_patch.restore(self.host)
        self.assertEqual({name:(self.host/name).read_bytes() for name in editor_patch.FILES},self.originals)

    def test_completed_runtime_is_never_reused_when_marker_cleanup_was_denied(self):
        marker = self.destination / ".gengate-setup.json"
        unlink = Path.unlink
        denied = False
        def fail_cleanup(path, *args, **kwargs):
            nonlocal denied
            if path.resolve() == marker.resolve() and not denied:
                denied = True
                raise PermissionError("synthetic marker cleanup failure")
            return unlink(path, *args, **kwargs)
        warnings = []
        with patch.object(installer, "check_python", return_value="existing/python.exe"), patch("genstudio.models.resolve"):
            with patch.object(Path, "unlink", fail_cleanup):
                installer.install(self.request(), self.bundle, shortcuts=False, warnings=warnings)
            old = installer.read_json(self.destination / "genstudio-install.json")
            old_config = Path(old["runtime"]) / "config.photocraft.json"
            before = old_config.read_bytes()
            self.assertTrue(marker.exists()); self.assertEqual(len(warnings),1)
            installer.install(self.request(), self.bundle, shortcuts=False)
            new = installer.read_json(self.destination / "genstudio-install.json")
            self.assertNotEqual(old["runtime"],new["runtime"])
            self.assertEqual(old_config.read_bytes(),before)
            self.assertFalse(marker.exists())

    def test_restart_recovery_restores_launcher_metadata_and_host_then_retries(self):
        from genstudio.installation import stage
        self.destination.mkdir()
        package = stage(self.destination.resolve(), self.host.resolve(), self.profile.resolve(), installer.VERSION)
        package.mkdir(parents=True)
        native = self.host / "photocraft.exe"
        before = editor_patch.sha256(native)
        editor_patch._atomic(editor_patch.backup_file(self.host, before), native)
        native.write_bytes(self.outputs[native.name])
        launcher = self.destination / "Launch.ps1"; launcher.write_text("interrupted launcher")
        metadata = self.destination / "genstudio-install.json"
        installer.write_json(metadata, {"installMode": "overlay", "runtime": str(package / "runtime")})
        plan = [{"path":str(native.resolve()), "before":before, "after":editor_patch.sha256(native)},
                {"path":str(launcher.resolve()), "before":None, "after":editor_patch.sha256(launcher)},
                {"path":str(metadata.resolve()), "before":None, "after":editor_patch.sha256(metadata)}]
        editor_patch._atomic(self.host / ".gengate/journal.json", editor_patch.encoded({"format":2,
                             "installationRoot":str(self.destination.resolve()), "changes":plan}))
        with editor_patch.locked(self.host):
            editor_patch.recover(self.host.resolve(), self.profile.resolve())
        self.assertEqual(native.read_bytes(), self.originals[native.name])
        self.assertFalse(launcher.exists()); self.assertFalse(metadata.exists())
        with patch.object(installer, "check_python", return_value="existing/python.exe"), patch("genstudio.models.resolve"):
            installer.install(self.request(), self.bundle, shortcuts=False)
        self.assertTrue(metadata.exists())

    def test_uninstall_recovers_interrupted_update_before_checking_runtime_owner(self):
        with patch.object(installer, "check_python", return_value="existing/python.exe"), patch("genstudio.models.resolve"):
            installer.install(self.request(), self.bundle, shortcuts=False)
        metadata = self.destination / "genstudio-install.json"
        old = installer.read_json(metadata); before = editor_patch.sha256(metadata)
        editor_patch._atomic(editor_patch.backup_file(self.host, before), metadata)
        from genstudio.installation import stage
        package = stage(self.destination.resolve(), self.host.resolve(), self.profile.resolve(), installer.VERSION)
        installer.write_json(metadata, {**old,"package":str(package),"runtime":str(package / "runtime")})
        editor_patch._atomic(self.host / ".gengate/journal.json", editor_patch.encoded({"format":2,
            "installationRoot":str(self.destination.resolve()), "changes":[{"path":str(metadata.resolve()),
            "before":before,"after":editor_patch.sha256(metadata)}]}))
        spec = importlib.util.spec_from_file_location("gengate_manage",Path(__file__).resolve().parents[1]/"packaging/manage.py")
        manager = importlib.util.module_from_spec(spec);spec.loader.exec_module(manager)
        with patch('sys.argv',['manage','--metadata',str(metadata),'--restore']), redirect_stdout(StringIO()):
            self.assertEqual(manager.main(),0)
        self.assertEqual({name:(self.host/name).read_bytes() for name in editor_patch.FILES},self.originals)
        self.assertEqual(installer.read_json(metadata),old)

    def test_cancelled_preparation_does_not_claim_a_foreign_folder_or_lose_user_files(self):
        with patch.object(installer, "install_direct", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                installer.install(self.request(newRuntime=True), self.bundle, shortcuts=False)
        keep = self.destination / "my-notes.txt"; keep.write_text("keep")
        with patch.object(installer, "install_direct", return_value="private/python.exe"), patch("genstudio.models.resolve"):
            installer.install(self.request(newRuntime=True), self.bundle, shortcuts=False)
        self.assertEqual(keep.read_text(), "keep")
        foreign = self.root / "foreign"; foreign.mkdir(); (foreign / "document.psd").write_bytes(b"keep")
        with self.assertRaisesRegex(ValueError, "other files"):
            installer.valid_destination(str(foreign), self.bundle)

    def test_direct_update_keeps_the_hidden_comfy_directory(self):
        previous = {"engine":"direct","comfyDir":str(self.root / "ComfyUI"),"comfyPython":"comfy/python.exe", "modelsDir":str(self.models)}
        installer.write_json(self.profile / "preferences.json", {"genStudio":previous})
        with patch.object(installer,"check_python",return_value="direct/python.exe"),patch("genstudio.models.resolve"):
            installer.install(self.request(comfyDir=""),self.bundle,shortcuts=False)
        settings=installer.read_json(self.profile / "preferences.json")["genStudio"]
        self.assertEqual(settings["comfyDir"],previous["comfyDir"])
        self.assertEqual(settings["comfyPython"],previous["comfyPython"])

    def test_update_preserves_both_engines_and_complete_preset_settings(self):
        previous = {"engine": "comfy", "comfyUrl": "http://127.0.0.1:8188", "comfyPython": "comfy/python.exe",
                    "directPython": "direct/python.exe", "comfyStartup": "off", "model": "fibo-2-edit-turbo",
                    "modelsDir": str(self.models), "presets": {"fibo-2-edit-turbo": {"constantLora": "turbo.safetensors", "constantLoraSteps": 6}},
                    "fillWorkflow": "my-fill.json", "removeWorkflow": "my-remove.json"}
        installer.write_json(self.profile / "preferences.json", {"genStudio": previous, "unrelated": {"keep": True}})
        comfy = self.root / "ComfyUI"; comfy.mkdir(); (comfy / "main.py").write_text("# fixture")
        with patch.object(installer, "check_python", return_value="new-comfy/python.exe"), patch("genstudio.comfy_workflow.install_nodes"), patch("genstudio.models.resolve_components"):
            installer.install(self.request(engine="comfy", comfyDir=str(comfy)), self.bundle, shortcuts=False)
        prefs = installer.read_json(self.profile / "preferences.json")
        settings = prefs["genStudio"]
        for key in ("comfyUrl", "directPython", "comfyStartup", "model", "fillWorkflow", "removeWorkflow"):
            self.assertEqual(settings[key], previous[key])
        self.assertEqual(settings["presets"]["fibo-2-edit-turbo"]["constantLoraSteps"], 6)
        self.assertTrue(prefs["unrelated"]["keep"])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="Gen Studio install with spaces ")
        self.root = Path(self.temp.name)
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        self.destination = self.root / "installed"
        self.models = self.root / "shared models"
        self.models.mkdir()
        self.host = self.root / "existing PhotoCraft"
        self.originals, self.outputs, self.catalog = fixture(self.bundle, self.host)
        self.guard = patch.object(editor_patch, "assert_closed")
        self.guard.start()
        self.profile = self.host / "PhotoCraftData"
        self.profile.mkdir()
        files = {"runtime/.venv/Scripts/python.exe": b"fake python",
                 "runtime/genstudio/control.py": b"# fixture"}
        for name, content in files.items():
            p = self.bundle / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)
        for name in ("Launch.ps1", "PhotoCraft.cmd", "Uninstall.cmd", "Uninstall.ps1"):
            (self.bundle / name).write_text("fixture")
        installer.write_json(self.bundle / "manifest.json", {"version": installer.VERSION, "installMode": "overlay",
            "files": {p.relative_to(self.bundle).as_posix(): installer.sha256(p) for p in self.bundle.rglob("*") if p.is_file()}})

    def tearDown(self):
        self.guard.stop()
        self.temp.cleanup()

    def test_manifest_rejects_tampering_and_path_traversal(self):
        self.assertEqual(installer.verify_bundle(self.bundle)["version"], installer.VERSION)
        (self.bundle / "runtime/genstudio/control.py").write_bytes(b"corrupt")
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

    def test_comfy_setup_installs_bridge_without_starting_a_process_or_downloading_models(self):
        comfy = self.root / "ComfyUI"
        (comfy / "custom_nodes/LanPaint").mkdir(parents=True)
        (comfy / "main.py").write_text("# fixture, never execute")
        request = {"installDir": str(self.destination), "photocraftDir": str(self.host), "modelsDir": str(self.models),
                   "engine": "comfy", "comfyDir": str(comfy), "python": "existing/python.exe"}
        with patch.object(installer, "check_python", return_value="existing/python.exe"), patch("subprocess.Popen", side_effect=AssertionError("Setup must not start a process")) as spawn, patch("urllib.request.urlopen", side_effect=AssertionError("No downloads")):
            installer.install(request, self.bundle, shortcuts=False)
            spawn.assert_not_called()
        marker = json.loads((comfy / "custom_nodes/GenGate/gengate-nodes.json").read_text())
        self.assertIn("nodes.py", marker["files"])
        self.assertEqual(list(self.models.iterdir()), [])

    def test_unknown_editor_and_legacy_bundle_are_rejected_before_install_or_download(self):
        (self.host / "photocraft.exe").write_bytes(b"new official editor version")
        request = {"installDir": str(self.destination), "photocraftDir": str(self.host),
                   "modelsDir": str(self.models), "engine": "direct", "newRuntime": True}
        with patch.object(installer, "install_direct") as download:
            with self.assertRaisesRegex(ValueError, "no compatible"):
                installer.install(request, self.bundle, shortcuts=False)
            download.assert_not_called()
        self.assertFalse(self.destination.exists())
        self.assertFalse((self.host / ".gengate").exists())
        manifest = installer.read_json(self.bundle / "manifest.json")
        p = self.bundle / "PhotoCraft.exe"
        p.write_bytes(b"must not ship")
        manifest["files"][p.name] = installer.sha256(p)
        installer.write_json(self.bundle / "manifest.json", manifest)
        with self.assertRaisesRegex(ValueError, "must not contain"):
            installer.verify_bundle(self.bundle)

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
        for name in ("Launch.ps1", "PhotoCraft.cmd", "Uninstall.cmd", "Uninstall.ps1"):
            (self.bundle / name).write_text("fixture")
        request = {"installDir": str(self.destination), "photocraftDir": str(self.host), "modelsDir": str(self.models),
                   "engine": "direct", "python": "existing/python.exe"}
        sentinel = self.models / "do not copy.safetensors"
        sentinel.write_bytes(b"synthetic sentinel")
        # Use the real file-copy/install/metadata transaction, mocking only the
        # external interpreter probe and optional model discovery.
        with patch.object(installer, "check_python", return_value="existing/python.exe"), \
             patch("genstudio.models.resolve", return_value={}) as discover:
            installer.install(request, self.bundle, shortcuts=False)
            prefs_path = self.profile / "preferences.json"
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
        self.assertEqual(before["executable"], after["executable"])
        self.assertNotEqual(before["runtime"], after["runtime"])
        self.assertTrue(Path(before["executable"]).is_file())
        updated = installer.read_json(prefs_path)
        self.assertEqual(updated["general"], {"theme": "light"})
        self.assertEqual(updated["genStudio"]["directStartup"], "off")
        self.assertEqual(updated["genStudio"]["model"], "flux-3-image-klein")
        self.assertEqual(updated["genStudio"]["textEncoder"], "text_encoders/custom.safetensors")
        self.assertEqual(updated["genStudio"]["vae"], "vae/custom.safetensors")
        self.assertTrue(updated["genStudio"]["segmentation"]["enabled"])
        self.assertEqual(updated["genStudio"]["segmentation"]["detector"], "yolov8m-worldv2")
        self.assertTrue(list((self.host / ".gengate/backups").iterdir()))
        self.assertEqual(list(self.models.iterdir()), [sentinel])
        self.assertFalse(list(self.destination.rglob("*.safetensors")))
        self.assertFalse(list(self.destination.rglob("photocraft.exe")))

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

        # Updating the editor/add-on must keep the newest preset and economy
        # choice, not silently fall back to the original two preset families.
        from genstudio.models import FIBO
        updated = installer.read_json(prefs_path)
        presets[FIBO] = {**component_defaults(), "constantLora": "loras/fibo-edit.safetensors",
                         "constantLoraStrength": .75}
        updated["genStudio"].update(model=FIBO, presets=presets, reduceEditResolution=True, studioMode="workflow", studioWorkflow="C:/Work/my workflow.json")
        installer.write_json(prefs_path, updated)
        with patch.object(installer, "check_python", return_value="existing/python.exe"), \
             patch("genstudio.models.resolve_components", return_value={}) as discover:
            installer.install(request, self.bundle, shortcuts=False)
            discover.assert_called_once_with(str(self.models.resolve()), presets[FIBO], FIBO)
        kept = installer.read_json(prefs_path)["genStudio"]
        self.assertEqual(kept["model"], FIBO)
        self.assertEqual(kept["presets"], presets)
        self.assertTrue(kept["reduceEditResolution"])
        self.assertEqual(kept["studioMode"], "workflow")
        self.assertEqual(kept["studioWorkflow"], "C:/Work/my workflow.json")
        self.assertEqual(list(self.models.iterdir()), [sentinel])


if __name__ == "__main__":
    unittest.main()
