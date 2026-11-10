"""Personal/shared installation boundaries; tiny files only, no downloads or GPU."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from installer_fixtures import fixture
from genstudio import editor_patch as ep, user_install as user
from genstudio.lifecycle import hidden_process
ASSERT_ACCOUNT=user.assert_account


class PersonalInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.host=self.root/'Program Files/PhotoCraft';self.bundle=self.root/'bundle'
        self.originals,self.outputs,self.catalog=fixture(self.bundle,self.host,host_version='0.21.0')
        self.profile=self.root/'User profile';self.profile.mkdir()
        self.prefs={'nativePresets':'x'*350000,'genStudio':{'modelsDir':'keep models','runtimeRoot':'C:/Users/Admin/versions/bad/runtime','directPython':'C:/Users/Admin/versions/bad/runtime/python.exe'}}
        (self.profile/'preferences.json').write_bytes(ep.encoded(self.prefs))
        self.base={'files':{n:ep.sha256(self.host/n) for n in ep.FILES},
                   'portableFiles':{n:{'sha256':ep.sha256(self.host/n),'size':(self.host/n).stat().st_size} for n in ep.FILES}}
        data=self.root/'photocraft';data.mkdir();(data/'editor-bases.json').write_bytes(ep.encoded({'0.21.0':self.base}))
        for guard in (patch.object(user,'ROOT',self.root),patch.object(user,'assert_account'),
                      patch.dict(os.environ,{'LOCALAPPDATA':str(self.root/'User local'),'PHOTOCRAFT_CONFIG_DIR':str(self.profile)})):
            guard.start();self.addCleanup(guard.stop)

    def test_shared_patched_host_gets_a_verified_personal_copy_and_preserves_profile(self):
        entry=self.catalog['patches'][0]
        for name,data in self.originals.items():
            backup=ep.backup_file(self.host,entry['files'][name]['baseSha256']);backup.parent.mkdir(parents=True,exist_ok=True);backup.write_bytes(data)
            (self.host/name).write_bytes(self.outputs[name])
        state={'host':str(self.host),'hostVersion':'0.21.0','files':entry['files'],'profile':'C:/Users/Admin/profile'}
        ep.state_path(self.host).write_bytes(ep.encoded(state))
        before={str(p.relative_to(self.host)):p.read_bytes() for p in self.host.rglob('*') if p.is_file()}
        with patch.object(user,'personal_required',return_value=True),patch('genstudio.editor_update.Update',side_effect=AssertionError('No download needed')):
            request=user.prepare({'photocraftDir':str(self.host)},self.bundle,progress=lambda _:None)
        personal=Path(request['photocraftDir'])
        self.assertTrue(personal.is_relative_to(self.root/'User local'))
        self.assertTrue((personal/'portable.txt').is_file())
        self.assertEqual(ep.read(personal/'PhotoCraftData/preferences.json'),self.prefs)
        for name,data in self.originals.items():self.assertEqual((personal/name).read_bytes(),data)
        self.assertEqual({str(p.relative_to(self.host)):p.read_bytes() for p in self.host.rglob('*') if p.is_file()},before)
        self.assertEqual(request['_profile'],str(personal/'PhotoCraftData'))
        # Reusing a personal install neither copies again nor overwrites new preferences.
        (personal/'PhotoCraftData/preferences.json').write_text('{"unrelated":"new"}')
        self.assertEqual(user.personal_host(self.host,self.bundle),personal)
        self.assertEqual(ep.read(personal/'PhotoCraftData/preferences.json'),{'unrelated':'new'})

    def test_existing_unowned_destination_is_not_overwritten(self):
        dest=Path(os.environ['LOCALAPPDATA'])/'Programs/ArtCraft/PhotoCraft-GenGate'
        dest.mkdir(parents=True);(dest/'keep.txt').write_text('keep')
        with self.assertRaisesRegex(ValueError,'other files'):user.personal_host(self.host,self.bundle)
        self.assertEqual((dest/'keep.txt').read_text(),'keep')

    def test_program_files_requires_personal_copy_even_for_an_administrator(self):
        if os.name!='nt':self.skipTest('Windows folder policy')
        with patch.dict(os.environ,{'ProgramFiles':str(self.host.parent)}),patch.object(user,'writable',return_value=True):
            self.assertTrue(user.personal_required(self.host))

    def test_other_account_is_rejected_before_preparing_any_files(self):
        # Exercise the original checker with a synthetic over-the-shoulder token.
        if os.name!='nt':self.skipTest('Windows account policy')
        with patch.object(user,'account',return_value={'currentSid':'Admin','sessionSid':'User'}):
            with self.assertRaisesRegex(ValueError,'different Windows account'):ASSERT_ACCOUNT()

    def test_permission_denied_launch_names_the_python_and_account_problem(self):
        with patch('genstudio.lifecycle.subprocess.Popen',side_effect=PermissionError('denied')):
            with self.assertRaisesRegex(ValueError,'C:/Users/Admin/python.exe.*another Windows account'):
                hidden_process(['C:/Users/Admin/python.exe','-m','never_execute'])

    def test_destination_access_failure_does_not_suggest_elevation(self):
        with patch('genstudio.user_install.tempfile.TemporaryFile',side_effect=PermissionError('denied')):
            with self.assertRaisesRegex(ValueError,'own LocalAppData.*without administrator'):
                user.require_destination(self.root/'GenGate')

    def test_running_editor_lock_is_not_treated_as_an_acl_denial(self):
        error=PermissionError('sharing violation');error.winerror=32
        with patch('genstudio.user_install.tempfile.TemporaryFile',side_effect=error):
            self.assertTrue(user.writable(self.host))
        error.winerror=5
        with patch('genstudio.user_install.tempfile.TemporaryFile',side_effect=error):
            self.assertFalse(user.writable(self.host))
