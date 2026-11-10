"""Official update transactions with synthetic PE/ZIP/MSI fixtures only."""
import hashlib
import contextlib
import io
import importlib.util
import json
import runpy
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from installer_fixtures import fixture
from genstudio import editor_patch as ep, editor_update as update

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('update_test_installer',ROOT/'packaging/installer.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)


class EditorUpdateTests(unittest.TestCase):
    def setUp(self):
        account_guard=patch('genstudio.user_install.assert_account');account_guard.start();self.addCleanup(account_guard.stop)
        self.temp=tempfile.TemporaryDirectory(prefix='Editor update ');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.bundle=self.root/'bundle';self.host=self.root/'PhotoCraft'
        originals,self.outputs,_=fixture(self.bundle,self.host,host_version='0.21.0')
        self.official={**originals,'NOTICE':b'new official notices','portable.txt':b'new marker'}
        self.files={name:{'sha256':hashlib.sha256(data).hexdigest(),'size':len(data)} for name,data in self.official.items()}
        self.archive=self.root/'official.zip'
        with zipfile.ZipFile(self.archive,'w') as archive:
            for name,data in self.official.items():archive.writestr(name,data)
        self.base={'files':{name:self.files[name]['sha256'] for name in ep.FILES},'portableFiles':self.files,
                   'archive':'https://github.com/storytold/photocraft/releases/download/v0.21.0/official.zip',
                   'archiveSha256':ep.sha256(self.archive),'publisher':'Fixture publisher',
                   'msi':{'url':'https://github.com/storytold/photocraft/releases/download/v0.21.0/official.msi',
                          'sha256':'a'*64,'size':3,'upgradeCode':'{D9660F32-2D0A-4ABE-B73D-17BE25F1542C}'}}
        for name in ep.FILES:(self.host/name).write_bytes(b'MZ old editor '+name.encode())
        (self.host/'NOTICE').write_bytes(b'old notices')
        self.before={name:(self.host/name).read_bytes() for name in (*ep.FILES,'NOTICE','portable.txt')}
        self.profile=self.host/'PhotoCraftData';self.profile.mkdir()
        self.models=self.root/'shared models';self.models.mkdir();(self.models/'keep.safetensors').write_bytes(b'fixture, never load')
        self.preferences={'unrelated':'keep','genStudio':{'directStartup':'off','fillWorkflow':'keep.json'}}
        (self.profile/'preferences.json').write_bytes(ep.encoded(self.preferences))
        self.dest=self.root/'GenGate'
        for name in ('Launch.ps1','PhotoCraft.cmd','Uninstall.cmd','Uninstall.ps1'):(self.bundle/name).write_bytes(b'fixture')
        for name in ('runtime/.venv/Scripts/python.exe','runtime/genstudio/control.py'):
            p=self.bundle/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'fixture, never execute')
        manifest={'version':installer.VERSION,'installMode':'overlay','files':{p.relative_to(self.bundle).as_posix():ep.sha256(p) for p in self.bundle.rglob('*') if p.is_file()}}
        (self.bundle/'manifest.json').write_bytes(ep.encoded(manifest))
        self.bases={'0.21.0':self.base,'0.20.2':{'files':{name:ep.sha256(self.host/name) for name in ep.FILES}}}
        self.request={'installDir':str(self.dest),'photocraftDir':str(self.host),'modelsDir':str(self.models),'engine':'direct','python':'existing/python.exe'}
        for guard in (patch.object(ep,'assert_closed'),patch.object(ep,'official_manifest',return_value=self.files),
                      patch.object(installer,'check_python',return_value='existing/python.exe'),patch('genstudio.models.resolve')):
            guard.start();self.addCleanup(guard.stop)

    def plan(self, *, kind='portable', cancel=lambda:None):
        value=update.Update(self.host,self.bundle,bases=self.bases,installation={'kind':kind,'scope':'user'},cache=self.root/'cache',cancel=cancel,progress=lambda _:None)
        with patch.object(update,'download',return_value=self.archive),patch.object(update,'signatures') as signatures:
            value.prepare();signatures.assert_called_once()
        return value

    def assert_old(self):
        for name,data in self.before.items():self.assertEqual((self.host/name).read_bytes(),data,name)
        self.assertEqual(ep.read(self.profile/'preferences.json'),self.preferences)
        self.assertEqual((self.models/'keep.safetensors').read_bytes(),b'fixture, never load')
        self.assertFalse((self.dest/'genstudio-install.json').exists())

    def test_portable_update_and_overlay_commit_together(self):
        installer.install(self.request,self.bundle,shortcuts=False,editor_update=self.plan())
        for name,data in self.outputs.items():self.assertEqual((self.host/name).read_bytes(),data)
        self.assertEqual((self.host/'NOTICE').read_bytes(),b'new official notices')
        self.assertEqual((self.host/'portable.txt').read_bytes(),self.before['portable.txt'])
        self.assertEqual(ep.read(self.profile/'preferences.json')['genStudio']['fillWorkflow'],'keep.json')
        self.assertEqual((self.models/'keep.safetensors').read_bytes(),b'fixture, never load')
        installed=ep.read(self.dest/'genstudio-install.json')
        installer.verify_bundle(Path(installed['package']))
        ep.restore(self.host)
        for name in ep.FILES:self.assertEqual((self.host/name).read_bytes(),self.official[name])

    def test_standalone_script_can_prepare_the_signed_in_user_update(self):
        entry = runpy.run_path(update.__file__)['run']
        fake_installer = Mock()
        fake_installer.valid_destination.return_value = self.dest
        fake_installer.install.return_value = self.dest
        plan = Mock(host=self.host)
        with patch.dict(entry.__globals__, {'installer_module': lambda: fake_installer,
                                           'Update': Mock(return_value=Mock(prepare=Mock(return_value=plan)))}), \
                patch('genstudio.user_install.prepare', return_value=self.request):
            self.assertEqual(entry(self.request, self.bundle, shortcuts=False), self.dest)
        fake_installer.install.assert_called_once()

    def test_portable_failure_restores_editor_preferences_and_root_metadata(self):
        plan=self.plan();atomic=ep._atomic
        def fail(path,data):
            if path==self.host/'NOTICE':raise OSError('synthetic disk failure')
            return atomic(path,data)
        with patch.object(ep,'_atomic',side_effect=fail),self.assertRaisesRegex(OSError,'disk failure'):
            installer.install(self.request,self.bundle,shortcuts=False,editor_update=plan)
        self.assert_old()

    def test_safe_cancel_during_commit_rolls_back_instead_of_killing_helper(self):
        def cancel():
            if (self.host/'photocraft.exe').read_bytes()==self.outputs['photocraft.exe']:raise update.Cancelled('cancel')
        plan=self.plan(cancel=cancel)
        with self.assertRaises(update.Cancelled):installer.install(self.request,self.bundle,shortcuts=False,editor_update=plan)
        self.assert_old()

    def test_interrupted_official_file_transaction_recovers_on_next_run(self):
        plan=self.plan();atomic=ep._atomic;recover=ep.recover
        def fail(path,data):
            if path==self.host/'NOTICE':raise OSError('simulated interruption')
            return atomic(path,data)
        calls=0
        def interrupted(host,profile):
            nonlocal calls
            calls+=1
            if (host/'.gengate/journal.json').exists():raise RuntimeError('process stopped')
            return recover(host,profile)
        with patch.object(ep,'_atomic',side_effect=fail),patch.object(ep,'recover',side_effect=interrupted),self.assertRaisesRegex(RuntimeError,'process stopped'):
            installer.install(self.request,self.bundle,shortcuts=False,editor_update=plan)
        self.assertTrue((self.host/'.gengate/journal.json').exists())
        ep.recover(self.host,self.profile);self.assert_old()

    def test_official_archive_content_is_checked_before_live_files_change(self):
        with zipfile.ZipFile(self.archive,'a') as archive:archive.writestr('../outside',b'bad')
        with self.assertRaisesRegex(ValueError,'Unexpected official archive'):self.plan()
        self.assert_old()

    def test_publisher_rejection_never_reaches_installation(self):
        plan=update.Update(self.host,self.bundle,bases=self.bases,installation={'kind':'portable','scope':'user'},cache=self.root/'cache',progress=lambda _:None)
        with patch.object(update,'download',return_value=self.archive),patch.object(update,'signatures',side_effect=ValueError('wrong publisher')),self.assertRaisesRegex(ValueError,'publisher'):
            plan.prepare()
        self.assert_old()

    def test_modified_official_restore_backup_is_rejected_before_update(self):
        plan=self.plan()
        backup=ep.backup_file(self.host,self.base['files']['photocraft.exe'])
        backup.parent.mkdir(parents=True,exist_ok=True);backup.write_bytes(b'corrupted backup')
        with self.assertRaisesRegex(ValueError,'backup was modified'):
            installer.install(self.request,self.bundle,shortcuts=False,editor_update=plan)
        self.assert_old()

    def test_update_waits_for_saved_and_closed_editor_before_installing(self):
        plan=Mock(host=self.host);plan.prepare.return_value=plan
        module=Mock();module.valid_destination.return_value=self.dest;messages=[]
        def installed(*args,**kwargs):
            self.assertEqual(closed.call_count,2)
            return self.dest
        module.install.side_effect=installed
        with patch.object(update,'installer_module',return_value=module),patch.object(update,'Update',return_value=plan),patch.object(ep,'assert_closed',side_effect=[ValueError('Save your work and close this PhotoCraft'),None]) as closed,patch.object(update.time,'sleep'):
            self.assertEqual(update.run(self.request,self.bundle,progress=messages.append),self.dest)
        self.assertTrue(any('Save your work and close' in message for message in messages))

    def test_cancel_while_waiting_never_starts_installation(self):
        plan=Mock(host=self.host);plan.prepare.return_value=plan;module=Mock();module.valid_destination.return_value=self.dest
        with patch.object(update,'installer_module',return_value=module),patch.object(update,'Update',return_value=plan),patch.object(ep,'assert_closed',side_effect=ValueError('Save your work and close this PhotoCraft')),patch.object(update.time,'sleep',side_effect=update.Cancelled('cancel')),self.assertRaises(update.Cancelled):
            update.run(self.request,self.bundle,progress=lambda _:None)
        module.install.assert_not_called();self.assert_old()

    def test_msi_uses_windows_installer_and_retains_install_scope(self):
        plan=self.plan(kind='msi')
        def install(command,**kwargs):
            for name in ep.FILES:(self.host/name).write_bytes(self.official[name])
            self.assertIn('/norestart',command);self.assertIn('ALLUSERS=',command);self.assertIn('MSIINSTALLPERUSER=1',command)
            self.assertIn('INSTALLFOLDER='+str(self.host)+__import__('os').sep,command)
            self.assertIn('WIXUI_EXITDIALOGOPTIONALCHECKBOX=0',command)
            return Mock(returncode=0)
        with patch.object(update.subprocess,'run',side_effect=install),patch.object(update,'signatures'):
            installer.install(self.request,self.bundle,shortcuts=False,editor_update=plan)
        for name,data in self.outputs.items():self.assertEqual((self.host/name).read_bytes(),data)

    def test_machine_msi_keeps_machine_scope(self):
        plan=self.plan(kind='msi');plan.installation['scope']='machine'
        def install(command,**kwargs):
            self.assertIn('ALLUSERS=1',command);self.assertIn('MSIINSTALLPERUSER=',command)
            for name in ep.FILES:(self.host/name).write_bytes(self.official[name])
            return Mock(returncode=0)
        with patch.object(update.subprocess,'run',side_effect=install),patch.object(update,'signatures'):
            installer.install(self.request,self.bundle,shortcuts=False,editor_update=plan)

    def test_success_with_optional_notes_is_reported_as_installed(self):
        request=self.root/'request.json';request.write_bytes(ep.encoded(self.request))
        def installed(*args,**kwargs):
            kwargs['warnings'].append('Shortcut setup needs attention')
            return self.dest
        output=io.StringIO()
        with patch.object(update.sys,'argv',['update','--request',str(request),'--bundle',str(self.bundle)]),patch.object(update,'run',side_effect=installed),contextlib.redirect_stdout(output):
            self.assertEqual(update.main(),0)
        result=json.loads(output.getvalue().strip().removeprefix('GENGATE_SETUP_RESULT '))
        self.assertEqual(result['status'],'installedWithWarnings')
        self.assertEqual(result['warnings'],['Shortcut setup needs attention'])

    def test_msi_failure_keeps_gengate_unmodified(self):
        plan=self.plan(kind='msi')
        with patch.object(update.subprocess,'run',return_value=Mock(returncode=1603)),self.assertRaisesRegex(RuntimeError,'1603'):
            installer.install(self.request,self.bundle,shortcuts=False,editor_update=plan)
        self.assert_old()

    def test_msi_reboot_does_not_force_restart_or_patch_old_files(self):
        plan=self.plan(kind='msi')
        with patch.object(update.subprocess,'run',return_value=Mock(returncode=3010)),self.assertRaises(update.RestartRequired):
            installer.install(self.request,self.bundle,shortcuts=False,editor_update=plan)
        self.assert_old()

    def test_newer_editor_is_never_downgraded(self):
        self.bases['0.22.0']=self.bases.pop('0.20.2')
        with self.assertRaisesRegex(ValueError,'downgrade'):
            update.Update(self.host,self.bundle,bases=self.bases,installation={'kind':'portable','scope':'user'})

    def test_editor_updated_while_download_was_running_is_not_downgraded(self):
        plan=self.plan()
        for name in ep.FILES:(self.host/name).write_bytes(b'MZ newer '+name.encode())
        self.bases['0.22.0']={'files':{name:ep.sha256(self.host/name) for name in ep.FILES}}
        with self.assertRaisesRegex(ValueError,'downgrade'):
            installer.install(self.request,self.bundle,shortcuts=False,editor_update=plan)
        self.assertFalse(self.dest.exists())

    def test_download_hash_and_origin_are_enforced(self):
        import io
        with self.assertRaisesRegex(ValueError,'URL'):
            update.download('https://example.com/editor.zip','a'*64,self.root/'bad.zip',lambda:None,lambda _:None)
        with patch.object(update.urllib.request,'urlopen',return_value=io.BytesIO(b'wrong')),self.assertRaisesRegex(ValueError,'checksum'):
            update.download(self.base['archive'],'a'*64,self.root/'bad.zip',lambda:None,lambda _:None)
        self.assertFalse((self.root/'bad.zip').exists())
        self.assertFalse(list(self.root.glob('*.part')))
