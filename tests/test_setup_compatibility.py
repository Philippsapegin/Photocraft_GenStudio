"""Early setup decisions for the external verified updater; no downloads."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from installer_fixtures import fixture
from genstudio import setup_compatibility as setup


class SetupCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='Setup compatibility ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.host, self.bundle = self.root / 'PhotoCraft', self.root / 'bundle'
        self.originals, _, _ = fixture(self.bundle, self.host, host_version='0.21.0')

    def mismatch(self, version):
        for name in setup.ep.FILES:
            (self.host / name).write_bytes(('different '+version+name).encode())
        return {version:{'files':{name:setup.ep.sha256(self.host/name) for name in setup.ep.FILES}}}

    def test_exact_supported_editor_is_ready(self):
        value = setup.preflight(self.host, self.bundle, known={}, check_open=False)
        self.assertTrue(value['compatible'])
        self.assertEqual(value['state'], 'ready')
        self.assertEqual(value['installed'], '0.21.0')

    def test_old_editor_requests_update_without_changing_files(self):
        known = self.mismatch('0.20.2')
        before = {p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        value = setup.preflight(self.host, self.bundle, known=known, check_open=False)
        self.assertEqual(value['state'], 'updatePhotoCraft')
        self.assertEqual(value['installed'], '0.20.2')
        self.assertEqual({p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, before)

    def test_unsupported_requested_update_requires_new_gengate_first(self):
        value = setup.preflight(self.host, self.bundle, known={}, target='0.22.0', check_open=False)
        self.assertFalse(value['compatible'])
        self.assertEqual(value['state'], 'updateGenGate')
        self.assertIn('0.22.0', value['message'])

    def test_newer_editor_requests_gengate_update_instead_of_downgrade(self):
        value = setup.preflight(self.host, self.bundle, known=self.mismatch('0.22.0'), check_open=False)
        self.assertEqual(value['state'], 'updateGenGate')
        self.assertIn('Photocraft_GenStudio/releases/latest', value['url'])

    def test_version_string_alone_does_not_approve_an_unknown_build(self):
        self.mismatch('custom')
        with patch.object(setup, 'installed_version', return_value='0.21.0'):
            value = setup.preflight(self.host, self.bundle, known={}, check_open=False)
        self.assertFalse(value['compatible'])
        self.assertEqual(value['state'], 'getPhotoCraft')

    def test_compatible_open_editor_requests_save_and_close(self):
        with patch.object(setup.ep, 'assert_closed', side_effect=ValueError('open')):
            value = setup.preflight(self.host, self.bundle, known={})
        self.assertTrue(value['compatible'])
        self.assertTrue(value['running'])
        self.assertIn('Save', value['message'])
