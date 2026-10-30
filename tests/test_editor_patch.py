import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from genstudio import editor_patch as ep
from installer_fixtures import fixture


class EditorPatchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="GenGate overlay ")
        self.root = Path(self.temporary.name).resolve()
        self.host, self.bundle = self.root / "existing PhotoCraft", self.root / "add-on"
        self.originals, self.outputs, self.catalog = fixture(self.bundle, self.host)
        self.profile = self.host / "PhotoCraftData"
        self.profile.mkdir()
        self.old_preferences = {"interface": {"theme": "studio"}, "unrelated": "keep"}
        self.prefs = self.profile / "preferences.json"
        self.prefs.write_bytes(ep.encoded(self.old_preferences))
        self.settings = {"runtimeRoot": str(self.bundle / "runtime"), "engine": "direct", "directStartup": "off"}
        self.preferences = {**self.old_preferences, "genStudio": self.settings}
        self.process_guard = patch.object(ep, "assert_closed")
        self.process_guard.start()

    def tearDown(self):
        self.process_guard.stop()
        self.temporary.cleanup()

    def apply(self, bundle=None):
        return ep.apply(bundle or self.bundle, self.host, self.settings, self.preferences, self.profile, "test")

    def assert_original(self):
        for name, data in self.originals.items():
            self.assertEqual((self.host / name).read_bytes(), data)

    def test_overlay_and_restore_preserve_documents_and_native_preferences(self):
        document = self.host / "work.pcraft"
        document.write_bytes(b"do not touch")
        self.apply()
        for name, data in self.outputs.items():
            self.assertEqual((self.host / name).read_bytes(), data)
        self.assertEqual(ep.ensure_current(self.host)["state"], "applied")
        self.assertEqual(ep.read(self.prefs)["interface"], self.old_preferences["interface"])
        result = ep.restore(self.host)
        self.assertTrue(result["restored"])
        self.assert_original()
        self.assertEqual(ep.read(self.prefs), self.old_preferences)
        self.assertEqual(document.read_bytes(), b"do not touch")
        self.assertFalse((self.host / "runtime/config.photocraft.json").exists())

    def test_repeat_install_retains_true_originals_and_is_reversible(self):
        self.apply()
        self.apply()
        self.assertEqual(ep.read(ep.state_path(self.host))["previousGenStudio"], None)
        ep.restore(self.host)
        self.assert_original()
        self.assertEqual(ep.read(self.prefs), self.old_preferences)

    def test_supported_editor_refresh_reapplies_without_downgrade(self):
        self.apply()
        for name, data in self.originals.items():
            (self.host / name).write_bytes(data)
        self.assertEqual(ep.ensure_current(self.host)["state"], "reapplied")
        for name, data in self.outputs.items():
            self.assertEqual((self.host / name).read_bytes(), data)

    def test_official_editor_can_drop_addon_preferences_without_losing_runtime_settings(self):
        self.apply()
        previous=ep.read(self.prefs)['genStudio']
        prefs=ep.read(self.prefs);prefs.pop('genStudio');self.prefs.write_bytes(ep.encoded(prefs))
        for name,data in self.originals.items():(self.host/name).write_bytes(data)
        self.assertEqual(ep.previous_settings(self.host,prefs),previous)
        self.assertEqual(ep.ensure_current(self.host)['state'],'reapplied')
        self.assertEqual(ep.read(self.prefs)['genStudio'],previous)
        self.assertEqual(ep.read(self.prefs)['interface'],self.old_preferences['interface'])

    def test_preference_recovery_never_hides_malformed_existing_settings(self):
        self.apply()
        with self.assertRaisesRegex(ValueError,'invalid'):
            ep.previous_settings(self.host,{'genStudio':False})

    def test_new_host_release_uses_its_own_delta_and_restores_the_new_originals(self):
        self.apply()
        package = self.root / "updated add-on"
        original_host = self.root / "new official release"
        originals, outputs, catalog = fixture(package, original_host,
            base_suffix=b" release 0.15.1", host_version="0.15.1")
        for name, content in originals.items():
            (self.host / name).write_bytes(content)
        state = self.apply(package)
        self.assertEqual(state["hostVersion"], "0.15.1")
        self.assertEqual(state["files"], catalog["patches"][0]["files"])
        self.assertEqual(ep.ensure_current(self.host)["hostVersion"], "0.15.1")
        for name, content in outputs.items():
            self.assertEqual((self.host / name).read_bytes(), content)
        ep.restore(self.host)
        for name, content in originals.items():
            self.assertEqual((self.host / name).read_bytes(), content)
        self.assertEqual(ep.read(self.prefs), self.old_preferences)

    def test_unknown_update_is_never_patched_or_downgraded_even_on_uninstall(self):
        self.apply()
        for name in ep.FILES:
            (self.host / name).write_bytes(b"a newer official release " + name.encode())
        before = {name: (self.host / name).read_bytes() for name in ep.FILES}
        with self.assertRaisesRegex(ValueError, "no compatible.*patch"):
            ep.ensure_current(self.host)
        self.assertEqual(set(ep.restore(self.host)["updatedFilesRetained"]), set(ep.FILES))
        self.assertEqual(before, {name: (self.host / name).read_bytes() for name in ep.FILES})

    def test_corrupt_patch_and_missing_original_backup_fail_before_replacement(self):
        file = self.bundle / self.catalog["patches"][0]["files"]["photocraft.exe"]["patch"]
        original_patch = file.read_bytes()
        file.write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "verification failed"):
            self.apply()
        self.assert_original()
        file.write_bytes(original_patch)
        self.apply()
        state = ep.state_for(self.host)
        ep.backup_file(self.host, state["files"]["photocraft.exe"]["baseSha256"]).unlink()
        before = {name: ep.sha256(self.host / name) for name in ep.FILES}
        with self.assertRaisesRegex(ValueError, "backup is missing"):
            ep.restore(self.host)
        self.assertEqual(before, {name: ep.sha256(self.host / name) for name in ep.FILES})

    def test_write_failure_rolls_back_both_binaries_settings_and_metadata(self):
        atomic = ep._atomic
        fail_once = [True]
        def failing(path, data):
            if path == self.host / "photocraft-cli.exe" and fail_once[0]:
                fail_once[0] = False
                raise PermissionError("synthetic locked CLI")
            return atomic(path, data)
        with patch.object(ep, "_atomic", side_effect=failing):
            with self.assertRaisesRegex(PermissionError, "locked CLI"):
                self.apply()
        self.assert_original()
        self.assertEqual(ep.read(self.prefs), self.old_preferences)
        self.assertFalse(ep.state_path(self.host).exists())
        self.assertFalse((self.host / ".gengate/journal.json").exists())

    def test_crash_recovery_reverts_partial_transaction_before_retry(self):
        path = self.host / "photocraft.exe"
        before = ep.sha256(path)
        ep._atomic(ep.backup_file(self.host, before), path)
        path.write_bytes(self.outputs[path.name])
        ep._atomic(self.host / ".gengate/journal.json", ep.encoded([
            {"path": str(path), "before": before, "after": ep.sha256(path)}]))
        with ep.locked(self.host):
            ep.recover(self.host, self.profile)
        self.assert_original()
        self.apply()
        self.assertEqual(ep.ensure_current(self.host)["state"], "applied")

    def test_recovery_refuses_unrelated_paths_and_external_updates(self):
        alien = self.root / "unrelated.psd"
        alien.write_bytes(b"keep")
        journal = self.host / ".gengate/journal.json"
        ep._atomic(journal, ep.encoded([{"path": str(alien), "before": None, "after": ep.sha256(alien)}]))
        with self.assertRaisesRegex(ValueError, "recovery path"):
            ep.recover(self.host, self.profile)
        self.assertEqual(alien.read_bytes(), b"keep")
        path = self.host / "photocraft.exe"
        before = ep.sha256(path)
        ep._atomic(ep.backup_file(self.host, before), path)
        ep._atomic(journal, ep.encoded([{"path": str(path), "before": before, "after": "0" * 64}]))
        path.write_bytes(b"new external update")
        with self.assertRaisesRegex(ValueError, "will not overwrite"):
            ep.recover(self.host, self.profile)
        self.assertEqual(path.read_bytes(), b"new external update")

    def test_new_addon_revision_uses_original_base_and_restores_preexisting_config(self):
        config = self.host / "runtime/config.photocraft.json"
        config.parent.mkdir()
        config.write_bytes(ep.encoded({"runtimeRoot": "old custom integration"}))
        self.apply()
        new_bundle, ignored_host = self.root / "new package", self.root / "fixture host"
        _, expected, _ = fixture(new_bundle, ignored_host, suffix=b"newer GenGate")
        self.apply(new_bundle)
        for name, data in expected.items():
            self.assertEqual((self.host / name).read_bytes(), data)
        ep.restore(self.host)
        self.assert_original()
        self.assertEqual(ep.read(config), {"runtimeRoot": "old custom integration"})

    def test_invalid_sizes_and_package_traversal_are_rejected(self):
        for key, value in (("patch", "../secret"), ("targetSize", ep.MAX_BINARY + 1), ("baseSha256", "no")):
            data = copy.deepcopy(self.catalog)
            data["patches"][0]["files"]["photocraft.exe"][key] = value
            (self.bundle / "editor-patches.json").write_bytes(ep.encoded(data))
            with self.assertRaises(ValueError):
                ep.catalog_for(self.bundle)
        self.assert_original()

    def test_restore_preserves_native_preferences_changed_since_install(self):
        self.apply()
        values = ep.read(self.prefs)
        values["unrelated"] = "user changed this"
        self.prefs.write_bytes(ep.encoded(values))
        ep.restore(self.host)
        self.assertEqual(ep.read(self.prefs)["unrelated"], "user changed this")


if __name__ == "__main__":
    unittest.main()
