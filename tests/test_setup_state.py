"""Setup discovery reads fixture settings without executing any interpreter."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from genstudio import setup_state as setup


class SetupStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='Setup saved paths ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'папки с пробелами'
        self.root.mkdir()
        self.host = self.root / 'nonstandard editor'
        self.host.mkdir()
        for name in setup.ep.FILES:
            (self.host / name).write_bytes(b'fixture, never execute')
        (self.host / 'portable.txt').write_text('')
        self.profile = self.host / 'PhotoCraftData'
        self.profile.mkdir()
        self.install = self.root / 'separate addon'
        self.runtime = self.install / 'versions/old/runtime'
        self.runtime.mkdir(parents=True)
        self.direct = self.runtime / '.runtime/qwen-direct/python.exe'
        self.direct.parent.mkdir(parents=True)
        self.direct.write_bytes(b'never execute')
        self.cached = {'runtimeRoot':str(self.runtime), 'modelsDir':'old models', 'comfyDir':'old comfy',
                       'comfyPython':'old python', 'directPython':str(self.direct)}
        self.put(self.runtime / 'config.photocraft.json', self.cached)
        self.put(self.install / 'genstudio-install.json', {'installMode':'overlay', 'host':str(self.host), 'runtime':str(self.runtime), 'directInstalled':True})
        self.put(self.host / '.gengate/state.json', {'host':str(self.host), 'runtime':str(self.runtime), 'package':str(self.runtime.parent)})
        self.location = self.root / 'last-setup.json'
        # Respect the native resolver while keeping a caller's dev profile out.
        self.override = patch.dict('os.environ', {'PHOTOCRAFT_CONFIG_DIR':str(self.profile)})
        self.override.start()
        self.addCleanup(self.override.stop)

    def put(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def discover(self, **kwargs):
        return setup.discover(location=self.location, profile=self.profile, hosts=[], installs=[], **kwargs)

    def test_actual_profile_wins_over_old_runtime_snapshot(self):
        latest = {**self.cached, 'modelsDir':'new models', 'comfyDir':'new ComfyUI', 'comfyPython':'new python', 'fillWorkflow':'keep.json'}
        self.put(self.profile / 'preferences.json', {'genStudio':latest})
        value = self.discover(host=str(self.host))
        self.assertEqual(value['settings'], latest)
        self.assertEqual(value['installDir'], str(self.install))
        self.assertTrue(value['directReusable'])

    def test_remembered_nonstandard_installation_is_found_next_time(self):
        setup.remember(self.host, self.install, location=self.location)
        value = self.discover()
        self.assertEqual(value['host'], str(self.host))
        self.assertEqual(value['settings'], self.cached)

    def test_global_native_profile_finds_older_install_without_locator(self):
        self.put(self.profile / 'preferences.json', {'genStudio':self.cached})
        self.assertEqual(self.discover()['installDir'], str(self.install))

    def test_damaged_locator_does_not_hide_native_profile_settings(self):
        self.location.write_text('{broken')
        self.put(self.profile / 'preferences.json', {'genStudio':self.cached})
        self.assertEqual(self.discover()['host'], str(self.host))

    def test_explicit_unknown_host_does_not_load_another_installation(self):
        setup.remember(self.host, self.install, location=self.location)
        self.assertFalse(self.discover(host=str(self.root / 'missing'))['found'])

    def test_metadata_recovers_runtime_when_native_state_and_section_are_missing(self):
        (self.host / '.gengate/state.json').unlink()
        self.put(self.profile / 'preferences.json', {'unrelated':'keep'})
        value = self.discover(install=str(self.install))
        self.assertEqual(value['settings'], self.cached)
        self.assertTrue(value['directReusable'])

    def test_explicitly_disabled_direct_remains_disabled(self):
        self.put(self.profile / 'preferences.json', {'genStudio':{**self.cached, 'directPython':''}})
        value = self.discover(host=str(self.host))
        self.assertFalse(value['installDirect'])
        self.assertFalse(value['directReusable'])

    def test_location_is_not_written_for_an_incomplete_install(self):
        with self.assertRaises(ValueError):
            setup.remember(self.host, self.root / 'incomplete', location=self.location)
        self.assertFalse(self.location.exists())
