"""Verified official PhotoCraft downloads, portable transactions and MSI handoff."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
import zipfile

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from genstudio import editor_patch as ep
from genstudio.setup_compatibility import installed_version, version_tuple

ROOT = Path(__file__).resolve().parents[1]


class Cancelled(RuntimeError):
    pass


class RestartRequired(RuntimeError):
    pass


def windows(request):
    with tempfile.TemporaryDirectory(prefix='GenGate verification ') as folder:
        data = Path(folder) / 'request.json'
        data.write_bytes(ep.encoded(request))
        result = subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',
            str(ROOT/'packaging/PhotoCraft-Windows.ps1'),'-Data',str(data)], capture_output=True,
            text=True, encoding='utf-8', errors='replace', timeout=120,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:
        raise ValueError('Official PhotoCraft verification failed. '+result.stderr[-1200:])
    return json.loads(result.stdout)


def signatures(files, base, *, msi=None, version=None):
    request = {'files':list(map(str,files)), 'publisher':base['publisher']}
    if msi is not None:
        request.update(msi=str(msi),version=version,upgradeCode=base['msi']['upgradeCode'])
    windows(request)


def download(url, digest, destination, cancel, progress, *, size=None):
    if not re.fullmatch('[0-9a-f]{64}', digest):
        raise ValueError('Invalid official download checksum')
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.netloc != 'github.com' or not parsed.path.startswith('/storytold/photocraft/releases/download/'):
        raise ValueError('The official download URL is not a PhotoCraft release')
    if destination.is_file() and ep.sha256(destination) == digest:
        progress('Using the verified cached PhotoCraft download.');return destination
    partial = destination.with_suffix(destination.suffix+'.'+uuid.uuid4().hex+'.part')
    destination.parent.mkdir(parents=True,exist_ok=True)
    try:
        request = urllib.request.Request(url, headers={'User-Agent':'GenGate/EditorUpdate'})
        with urllib.request.urlopen(request,timeout=30) as response, partial.open('xb') as stream:
            total=0
            while chunk := response.read(1024*1024):
                cancel();total+=len(chunk)
                if total > (size or 2*1024**3):raise ValueError('Official download exceeds its expected size')
                stream.write(chunk)
                progress(f'Downloading PhotoCraft: {total//(1024*1024)} MB')
        cancel()
        if size is not None and total != size or ep.sha256(partial) != digest:
            raise ValueError('Official PhotoCraft download checksum mismatch')
        partial.replace(destination);return destination
    finally:
        partial.unlink(missing_ok=True)


class PortableFiles:
    def __init__(self, root, version, base, cancel=lambda:None):
        self.root,self.version,self.base=Path(root),version,base
        self.cancel=cancel

    def validate(self):
        expected=self.base['portableFiles']
        actual={p.relative_to(self.root).as_posix() for p in self.root.rglob('*') if p.is_file()}
        if actual != set(expected):raise ValueError('Unexpected files in the official PhotoCraft archive')
        for name,spec in expected.items():
            path=ep.safe_path(self.root,name)
            if path.stat().st_size != spec['size'] or ep.sha256(path) != spec['sha256']:
                raise ValueError('Official PhotoCraft file verification failed: '+name)

    def extra_changes(self, host):
        trusted=ep.official_manifest(self.version)
        if trusted != self.base['portableFiles']:raise ValueError('Official file manifest changed')
        return [(ep.safe_path(host,name),ep.safe_path(self.root,name)) for name in trusted if name not in (*ep.FILES,'portable.txt')]


class Update:
    def __init__(self, host, bundle, *, target=None, cache=None, cancel=lambda:None, progress=print, bases=None, installation=None):
        self.host,self.bundle=ep.host_folder(host),Path(bundle).resolve()
        self.cancel,self.progress=cancel,progress
        self.bases=bases if bases is not None else ep.read(ROOT/'photocraft/editor-bases.json')
        self.catalog=ep.catalog_for(self.bundle)
        versions={p['hostVersion'] for p in self.catalog['patches']}
        self.target=target or max(versions,key=version_tuple)
        if self.target not in versions or self.target not in self.bases:
            raise ValueError('A compatible GenGate update is needed before updating to this PhotoCraft version.')
        self.base=self.bases[self.target]
        current=installed_version(self.host,self.bases)
        if current and version_tuple(current) > version_tuple(self.target):raise ValueError('This update would downgrade PhotoCraft. Update GenGate first.')
        self.installation=installation or windows({'host':str(self.host)})['installation']
        self.cache=Path(cache or Path(os.environ.get('LOCALAPPDATA',Path.home()))/'GenGate/updates')
        self.work=self.cache/uuid.uuid4().hex
        self.portable=None;self.msi=None;self.msi_installed=False

    def prepare(self):
        self.cancel();self.work.mkdir(parents=True)
        if self.installation['kind']=='msi':
            spec=self.base['msi'];self.msi=download(spec['url'],spec['sha256'],self.cache/(spec['sha256']+'.msi'),self.cancel,self.progress,size=spec['size'])
            signatures([self.msi],self.base,msi=self.msi,version=self.target)
        else:
            archive=download(self.base['archive'],self.base['archiveSha256'],self.cache/(self.base['archiveSha256']+'.zip'),self.cancel,self.progress)
            stage=self.work/'official';stage.mkdir()
            expected=self.base['portableFiles']
            with zipfile.ZipFile(archive) as source:
                entries=[i for i in source.infolist() if not i.is_dir()]
                if len(entries)!=len(expected) or {i.filename for i in entries}!=set(expected):raise ValueError('Unexpected official archive entries')
                for item in entries:
                    self.cancel()
                    if (item.external_attr>>16)&0o170000==0o120000 or item.file_size!=expected[item.filename]['size']:raise ValueError('Unsafe official archive entry')
                    path=ep.safe_path(stage,item.filename);path.parent.mkdir(parents=True,exist_ok=True)
                    with source.open(item) as src,path.open('xb') as dst:shutil.copyfileobj(src,dst,1024*1024)
            self.portable=PortableFiles(stage,self.target,self.base,self.cancel);self.portable.validate()
            executables=[]
            for path in stage.rglob('*'):
                if path.is_file():
                    with path.open('rb') as stream:
                        if stream.read(2)==b'MZ':executables.append(path)
            signatures(executables,self.base)
            ep.choose_patch(stage,self.catalog)
        self.cancel();self.progress('Official PhotoCraft package and publisher verified.')
        return self

    def validate_host(self, host, bundle):
        if host!=self.host or Path(bundle).resolve()!=self.bundle or not (self.portable or self.msi):raise ValueError('Update preparation does not match this setup')
        current=installed_version(host,self.bases)
        if current and version_tuple(current)>version_tuple(self.target):raise ValueError('PhotoCraft was updated to a newer version. This update will not downgrade it.')
        self.cancel()

    def before_apply(self):
        self.cancel();ep.assert_closed(self.host)
        if self.portable is not None:return self.portable
        self.progress('GENGATE_SETUP_PHASE '+json.dumps({'message':'Windows Installer is updating PhotoCraft. This step must finish before setup can close.'}))
        system=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/msiexec.exe'
        # PhotoCraft's upgrade detection requires an empty ALLUSERS for a
        # per-user install; ALLUSERS=2 can register a second product beside it.
        user=self.installation['scope']=='user'
        command=[str(system),'/i',str(self.msi),'/passive','/norestart','/L*v',str(self.work/'msi.log'),
                 'ALLUSERS='+('' if user else '1'),'MSIINSTALLPERUSER='+('1' if user else ''),
                 'INSTALLFOLDER='+str(self.host)+os.sep,'WIXUI_EXITDIALOGOPTIONALCHECKBOX=0']
        code=subprocess.run(command,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).returncode
        if code==1602:raise Cancelled('The Windows Installer update was cancelled.')
        if code in (1641,3010):raise RestartRequired('PhotoCraft requires a Windows restart. Restart Windows, then run GenGate Setup again; prepared runtimes are retained.')
        if code:raise RuntimeError(f'Windows Installer could not update PhotoCraft (exit {code}). See {self.work / "msi.log"}.')
        self.msi_installed=True
        for name,digest in self.base['files'].items():
            if ep.sha256(self.host/name)!=digest:raise ValueError('Windows Installer updated a different PhotoCraft folder; GenGate was not applied.')
        signatures([self.host/name for name in ep.FILES],self.base)
        return None


def installer_module():
    spec=importlib.util.spec_from_file_location('gengate_update_installer',ROOT/'packaging/installer.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def run(request, bundle, *, target=None, cancel=lambda:None, progress=print, cache=None, shortcuts=True, warnings=None):
    installer=installer_module();installer.verify_bundle(Path(bundle))
    plan=Update(request['photocraftDir'],bundle,target=target,cache=cache,cancel=cancel,progress=progress,
                installation=request.get('_editorInstallation')).prepare()
    progress('GENGATE_SETUP_PHASE '+json.dumps({'message':'Save your work and close PhotoCraft to continue. The update will wait here.'}))
    while True:
        cancel()
        try:ep.assert_closed(plan.host);break
        except ValueError as error:
            if not str(error).startswith('Save your work and close'):raise
            time.sleep(1)
    warnings=[] if warnings is None else warnings
    installer.report=progress
    try:
        return installer.install(request,Path(bundle),shortcuts=shortcuts,warnings=warnings,editor_update=plan)
    except Exception as error:
        if plan.msi_installed:raise RuntimeError('PhotoCraft was updated, but GenGate setup needs to be retried. Your settings and prepared runtimes are retained. '+str(error)) from error
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request',required=True,type=Path)
    parser.add_argument('--bundle',required=True,type=Path)
    parser.add_argument('--target')
    parser.add_argument('--cancel-file',type=Path)
    parser.add_argument('--no-shortcuts',action='store_true')
    args=parser.parse_args()
    def cancel():
        if args.cancel_file is not None and args.cancel_file.exists():raise Cancelled('Update cancelled. Your previous installation is retained.')
    def progress(message):print(message,flush=True)
    try:
        request=ep.read(args.request)
        if request.get('_profile'):os.environ['PHOTOCRAFT_CONFIG_DIR']=request['_profile']
        warnings=[]
        destination=run(request,args.bundle.resolve(),target=args.target,cancel=cancel,progress=progress,cache=request.get('_updateCache'),shortcuts=not args.no_shortcuts,warnings=warnings)
        value={'status':'installedWithWarnings' if warnings else 'installed','installDir':str(destination),'warnings':warnings};code=0
    except Cancelled as error:value={'status':'cancelled','message':str(error)};code=4
    except RestartRequired as error:value={'status':'restartRequired','message':str(error)};code=3
    except Exception as error:value={'status':'failed','message':str(error)};code=1
    print('GENGATE_SETUP_RESULT '+json.dumps(value,ensure_ascii=True),flush=True)
    return code


if __name__=='__main__':
    raise SystemExit(main())
