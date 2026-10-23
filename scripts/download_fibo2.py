"""Explicit resumable FIBO 2 Edit Turbo download; reuse verified shared components."""
import argparse
import json
from pathlib import Path
import shutil
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from download_flux3 import download

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models', type=Path, required=True)
    args = parser.parse_args()
    models = args.models.expanduser().resolve()
    if not models.is_dir():
        raise ValueError('Choose an existing Models folder')
    manifest = json.loads((ROOT / 'photocraft/fibo2-models.json').read_text())
    missing = sum(entry['bytes'] for entry in manifest['files'] if not (models / entry['path']).exists())
    if shutil.disk_usage(models).free < missing + (256 << 20):
        raise ValueError('Not enough free space in Models')
    report = ROOT / '.runtime/fibo2-download.json'
    report.parent.mkdir(exist_ok=True)
    status = {'model': manifest['model'], 'modelsDir': str(models), 'state': 'downloading', 'files': []}
    def save():
        temporary = report.with_suffix('.tmp')
        temporary.write_text(json.dumps(status, indent=2))
        temporary.replace(report)
    try:
        for entry in manifest['files']:
            item = {'path': entry['path'], 'total': entry['bytes'], 'bytes': 0, 'state': 'queued'}
            status['files'].append(item)
            def progress(state, count):
                item.update(state=state, bytes=count); save()
            print(entry['path'], flush=True)
            download(entry, models, manifest, progress)
        status['state'] = 'ready'; save()
    except Exception as error:
        status.update(state='error', message=str(error)); save(); raise


if __name__ == '__main__': main()
