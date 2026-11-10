"""Install for the signed-in Windows account, without patching shared editors."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid
from functools import lru_cache

from . import editor_patch as ep

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def account():
    result = subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',
                             str(ROOT/'packaging/Setup-Account.ps1')], capture_output=True,
                            text=True, timeout=30, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:
        raise ValueError('Could not identify the signed-in Windows account. Run Setup normally from your desktop. '+result.stderr[-400:])
    return json.loads(result.stdout)


def assert_account():
    if os.name == 'nt':
        value = account()
        if not value.get('currentSid') or value['currentSid'] != value.get('sessionSid'):
            raise ValueError('Setup is running as a different Windows account. Close it and run Setup.cmd normally as the signed-in user; do not use Run as administrator. PhotoCraft in Program Files will get a personal copy automatically.')


def _writable_executable(path):
    if os.name != 'nt':
        with path.open('r+b'):
            return
    # Python's CRT open() loses GetLastError and reports both ACL denial and
    # a mapped executable as errno 13. Keep the real Win32 error for routing.
    import ctypes
    from ctypes import wintypes
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    api.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    api.CreateFileW.restype=wintypes.HANDLE
    api.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=api.CreateFileW(str(path),0x40000000,7,None,3,0x80,None)
    if handle==wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    api.CloseHandle(handle)


def writable(folder):
    """Probe both directory replacement and the existing executables; never alter them."""
    try:
        with tempfile.TemporaryFile(dir=folder):
            pass
        for name in ep.FILES:
            _writable_executable(folder/name)
        return True
    except PermissionError as error:
        # A running executable is locked, not owned by another account. The
        # updater already waits for the editor to close before replacing it.
        return getattr(error, 'winerror', None) in (32, 33)


def require_destination(path):
    """Fail before staging/dependency work with an actionable C: permission error."""
    parent=path
    try:
        while not parent.exists() and parent != parent.parent:
            parent=parent.parent
        with tempfile.TemporaryFile(dir=parent):
            pass
    except PermissionError as error:
        raise ValueError('Access denied to GenGate installation folder: '+str(path)+'. Choose a folder in your own LocalAppData\\Programs (the default). Run Setup normally, without administrator rights.') from error


def personal_required(host):
    if os.name == 'nt':
        for key in ('ProgramFiles', 'ProgramFiles(x86)', 'ProgramW6432', 'ProgramData'):
            base = os.environ.get(key)
            if base and host.is_relative_to(Path(base).resolve()):
                return True
    return not writable(host)


def personal_host(source, bundle, *, progress=print, cache=None):
    """Use verified official files, not arbitrary files from another account's runtime."""
    ep.assert_closed(source)
    destination = Path(os.environ['LOCALAPPDATA'])/'Programs/ArtCraft/PhotoCraft-GenGate'
    marker = destination/'.gengate-personal.json'
    if destination.exists():
        if ep.is_link(destination):
            raise ValueError('The personal PhotoCraft folder is a link. Choose a real per-user installation folder instead: '+str(destination))
        if not marker.is_file() or ep.read(marker).get('format') != 1:
            raise ValueError('The personal PhotoCraft folder already contains other files: '+str(destination))
        host = ep.host_folder(destination)
        try:
            ep.choose_patch(host, ep.catalog_for(bundle))
        except ep.CompatibilityError as error:
            raise ValueError('Select your personal PhotoCraft folder in Setup to update it first: '+str(host)) from error
        return host
    from .editor_update import Update
    from .setup_compatibility import installed_version
    bases = ep.read(ROOT/'photocraft/editor-bases.json')
    supported = {p['hostVersion'] for p in ep.catalog_for(bundle)['patches']}
    version = installed_version(source, bases)
    if version not in supported:
        from .setup_compatibility import version_tuple
        newest = max(supported, key=version_tuple)
        if version and version_tuple(version) > version_tuple(newest):
            raise ValueError('A newer GenGate release is required for this PhotoCraft. It will not be downgraded.')
        version = newest
    destination.parent.mkdir(parents=True,exist_ok=True)
    stage = destination.with_name(destination.name+'.'+uuid.uuid4().hex+'.tmp')
    stage.mkdir()
    try:
        progress('Preparing your personal PhotoCraft copy. The shared installation will not be changed.')
        base = bases[version]
        # Prefer the already verified local official originals, including a patched host's backup.
        try:
            _, originals, _ = ep.choose_patch(source, ep.catalog_for(bundle))
            for name, spec in base['portableFiles'].items():
                if name == 'portable.txt':
                    continue
                candidate = originals[name] if name in ep.FILES else ep.safe_path(source,name)
                if candidate.stat().st_size != spec['size'] or ep.sha256(candidate) != spec['sha256']:
                    raise ValueError('Official local file differs')
                target=ep.safe_path(stage,name);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(candidate,target)
        except (OSError, ValueError):
            # Missing MSI notices/unknown older host: use the pinned signed portable archive.
            plan = Update(source,bundle,target=version,cache=cache,installation={'kind':'portable','scope':'user'},progress=progress).prepare()
            for name in base['portableFiles']:
                target=ep.safe_path(stage,name);target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(ep.safe_path(plan.portable.root,name),target)
        (stage/'portable.txt').write_text('Personal PhotoCraft installation prepared by GenGate.\n',encoding='utf-8')
        profile=ep.profile_folder(source)
        if profile.is_dir():
            shutil.copytree(profile,stage/'PhotoCraftData',ignore=shutil.ignore_patterns('cache','logs'))
        (stage/'.gengate-personal.json').write_bytes(ep.encoded({'format':1,'source':str(source),'hostVersion':version}))
        stage.rename(destination)
    except BaseException:
        if stage.exists():
            if stage.resolve().parent != destination.parent.resolve() or ep.is_link(stage):
                raise ValueError('Personal staging folder escaped its destination')
            shutil.rmtree(stage)
        raise
    return destination


def prepare(request, bundle, *, progress=print):
    assert_account()
    host=ep.host_folder(request['photocraftDir'])
    if not personal_required(host):
        return request
    personal=personal_host(host,bundle,progress=progress,cache=request.get('_updateCache'))
    progress('Personal PhotoCraft folder: '+str(personal))
    return {**request,'photocraftDir':str(personal),'_profile':str(personal/'PhotoCraftData'),
            '_editorInstallation':{'kind':'portable','scope':'user'}}
