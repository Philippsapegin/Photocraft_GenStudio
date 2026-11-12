"""Prepare PhotoCraft's pinned external catalogs in a private build overlay."""
from pathlib import Path
import json
import shutil
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[1]
PREFIX = 'crates/ui-egui/src/i18n'


def catalogs(source):
    folder = Path(source) / '.gengate-build/craft-i18n/locales'
    return sorted(folder.glob('*/photocraft.tsv'))


def catalog(source, code):
    code = 'sr-latn' if code.lower() == 'sr' else code.lower()
    return next((p for p in catalogs(source) if p.parent.name.lower() == code), None)


def prepare(source):
    source = Path(source)
    pin = source / 'craft-i18n.rev'
    if not pin.is_file():
        return None
    revision = pin.read_text('utf-8').strip()
    if len(revision) != 40 or any(c not in '0123456789abcdef' for c in revision):
        raise ValueError('Invalid pinned craft-i18n revision')
    cache = ROOT / '.runtime' / ('craft-i18n-' + revision[:12])
    if not (cache / '.git').is_dir():
        subprocess.run(['git', 'clone', '--depth', '1', 'https://github.com/storytold/craft-i18n.git', str(cache)], check=True)
    current = subprocess.check_output(['git', '-C', str(cache), 'rev-parse', 'HEAD'], text=True).strip()
    if current != revision:
        subprocess.run(['git', '-C', str(cache), 'fetch', '--depth', '1', 'origin', revision], check=True)
        subprocess.run(['git', '-C', str(cache), 'checkout', '--detach', revision], check=True)
    if subprocess.check_output(['git', '-C', str(cache), 'status', '--porcelain'], text=True).strip():
        raise ValueError('Pinned craft-i18n cache has local changes')
    overlay = source / '.gengate-build/craft-i18n'
    marker = overlay / 'gengate-source.json'
    if marker.exists():
        if json.loads(marker.read_text('utf-8'))['revision'] != revision:
            raise ValueError('Translation overlay belongs to another source revision')
    else:
        shutil.copytree(cache / 'locales', overlay / 'locales')
        for name in ('LICENSE-MIT', 'LICENSE-APACHE'):
            shutil.copyfile(cache / name, overlay / name)
        marker.write_text(json.dumps({'revision': revision}), encoding='utf-8')
    if not catalogs(source):
        raise ValueError('PhotoCraft translation catalogs are missing')
    # Direct cargo test/check invocations must embed the same catalogs as Build-PhotoCraft.
    config = source / '.cargo/config.toml'
    text = config.read_text('utf-8') if config.exists() else ''
    expected = {'CRAFT_I18N_DIR': {'value': '.gengate-build/craft-i18n', 'relative': True, 'force': True},
                'CRAFT_I18N_REQUIRED': {'value': '1', 'force': True}}
    current = tomllib.loads(text).get('env')
    if current is None:
        config.parent.mkdir(exist_ok=True)
        config.write_text(text + '\n[env]\nCRAFT_I18N_DIR = { value = ".gengate-build/craft-i18n", relative = true, force = true }\nCRAFT_I18N_REQUIRED = { value = "1", force = true }\n', encoding='utf-8')
    elif any(current.get(key) != value for key, value in expected.items()):
        raise ValueError('Cargo translation environment differs from the pinned overlay')
    return overlay.resolve()
