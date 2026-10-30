"""Native Studio readiness regression with a synthetic health endpoint; no inference."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path(sys.argv[sys.argv.index('--runtime') + 1]).resolve() if '--runtime' in sys.argv else ROOT
sys.path[:0] = [str(RUNTIME), str(ROOT / 'scripts')]
from genstudio.lifecycle import EngineHost
from genstudio.models import FLUX, MODEL
from genstudio.service import create_server
from genstudio.settings import active_components, defaults
from photocraft_engine_smoke import MCP


class Host(EngineHost):
    def __init__(self, settings):
        def forbidden(*args, **kwargs):
            raise AssertionError('Readiness validation must not start an AI process or generate images')
        super().__init__(settings, spawn=forbidden, request=self.health)
        self.model_state = 'unloaded'
        self.direct_url = 'http://127.0.0.1:1'
        self.generate = self.generate_studio = forbidden

    def configure(self, value):
        super().configure(value)
        self.direct_url = 'http://127.0.0.1:1'

    def health(self, url):
        if self.engine == 'comfy':
            return {'system': {}}
        return {'service': 'genstudio-direct', 'ready': True, 'owner': self.worker_id,
                'modelsDir': str(Path(self.settings['modelsDir']).resolve()),
                'model': self.settings['model'], 'components': active_components(self.settings),
                'modelState': self.model_state, 'modelLoaded': self.model_state == 'loaded',
                'modelError': 'Synthetic model error' if self.model_state == 'error' else ''}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, required=True)
    parser.add_argument('--cli', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, default=ROOT)
    args = parser.parse_args()
    folder = ROOT / '.runtime' / ('readiness-mcp-' + str(time.time_ns()))
    folder.mkdir()
    models = folder / 'models'
    models.mkdir()
    settings = {**defaults(), 'runtimeRoot': str(args.runtime.resolve()), 'modelsDir': str(models),
                'engine': 'direct', 'model': 'fibo-2-edit-turbo', 'directStartup': 'off', 'comfyStartup': 'off'}
    host = Host(settings)
    server = create_server(settings, port=0, host=host)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    (folder / 'preferences.json').write_text(json.dumps({'genStudio': settings,
        'general': {'automaticallyCheckUpdates': False}, 'interface': {'language': 'en', 'theme': 'studio'},
        'dialogs': {'photoshopShortcuts': {'imported': False}}}), encoding='utf-8')
    env = {**os.environ, 'PHOTOCRAFT_CONFIG_DIR': str(folder),
           'GENSTUDIO_SERVICE_URL': f'http://127.0.0.1:{server.server_port}',
           'CUDA_VISIBLE_DEVICES': '', 'GENGATE_NO_INFERENCE': '1'}
    env.pop('GENSTUDIO_NO_PROCESS', None)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    token = folder / 'control.token'
    process = mcp = None
    report = {'passed': False, 'real_inference': False, 'folder': str(folder), 'steps': [],
              'executable': str(args.exe.resolve()), 'cli': str(args.cli.resolve()),
              'runtime': str(args.runtime.resolve()), 'statuses': {},
              'sha256': {name: hashlib.sha256(path.read_bytes()).hexdigest()
                         for name, path in (('photocraft.exe', args.exe), ('photocraft-cli.exe', args.cli))}}

    def until(check, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = check()
            if result:
                return result
            time.sleep(.08)
        raise TimeoutError('Studio readiness condition did not arrive')

    try:
        with patch('genstudio.service.save', lambda _: None), (folder / 'app.log').open('wb') as log, (folder / 'mcp.log').open('wb') as bridge:
            process = subprocess.Popen([str(args.exe), '--control', str(port), '--control-token-file', str(token),
                '--automation-read-root', str(folder), '--automation-write-root', str(folder)],
                cwd=args.exe.parent, env=env, stdout=log, stderr=log,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

            def ready():
                assert process.poll() is None, 'Editor exited'
                if not token.exists():
                    return False
                try:
                    with socket.create_connection(('127.0.0.1', port), .2):
                        return True
                except OSError:
                    return False
            until(ready)
            mcp = MCP([str(args.cli), 'mcp', '--bridge', f'127.0.0.1:{port}', '--control-token-file', str(token)], bridge)
            control = lambda method, p=None: mcp.tool('control_call', {'method': method, 'params': p or {}})
            menu = lambda name, p=None: control('ui.menu.invoke', {'id': name, 'params': p or {}})
            studio = lambda: menu('genstudio.studio.inspect')

            def screenshot(name):
                for block in mcp.tool('ui_screenshot', {'max_side': 1600})['content']:
                    if block['type'] == 'image':
                        (folder / name).write_bytes(base64.b64decode(block['data']))

            control('ui.resize', {'width': 1100, 'height': 850})
            menu('genstudio.open')
            menu('genstudio.studio.configure', {'prompt': 'Make the sky blue'})
            reason = 'Add a source image to use FIBO 2.0 Edit Turbo'
            until(lambda: studio().get('controls', {}).get('generateDisabledReason') == reason)
            assert studio()['controls']['generateEnabled'] is False
            screenshot('fibo-needs-source.png')
            report['steps'].append('Prompt-only FIBO is disabled with an explicit source-image requirement')

            for state in ('unloaded', 'loading', 'loaded', 'error'):
                host.model_state = state
                def current_status():
                    status = mcp.run('genstudio.engine.status')
                    return status if (status.get('status') or {}).get('modelState') == state else False
                status = until(current_status)
                assert status['status']['serviceReady'] is True
                assert status['status']['modelLoaded'] is (state == 'loaded')
                report['statuses'][state] = status['status']
                screenshot('direct-' + state + '.png')
            state = host.model_state = 'unloaded'
            until(current_status)
            report['steps'].append('Native transport distinguishes a running service, loading, loaded components and a model error')

            mcp.run('file.new', {'width': 96, 'height': 64, 'background': 'white'})
            mcp.run('select.all')
            menu('genstudio.studio.reference.selection')
            mcp.await_engine()
            until(lambda: studio().get('controls', {}).get('generateEnabled') is True and not studio()['loading'])
            screenshot('fibo-with-source.png')
            menu('genstudio.studio.reference.remove', {'slot': 'main'})
            until(lambda: studio()['controls'].get('generateDisabledReason') == reason)
            report['steps'].append('Adding a synthetic source enables FIBO; removing it restores the requirement')

            for model in (MODEL, FLUX):
                configured = {**settings, 'model': model}
                mcp.run('genstudio.engine.configure', configured)
                mcp.await_engine()
                until(lambda: studio()['controls'].get('generateEnabled') is True)
                screenshot(model + '-text-only.png')
            report['steps'].append('Qwen and FLUX remain available for text-only Studio generation')
            mcp.run('genstudio.engine.configure', {**settings, 'engine': 'comfy'})
            mcp.await_engine()
            menu('genstudio.studio.configure', {'prompt': ''})
            until(lambda: studio()['controls'].get('generateEnabled') is True and (studio().get('workflow') or {}).get('active'))
            screenshot('comfy-graph.png')
            report['steps'].append('ComfyUI follows active-graph requirements even with FIBO selected and no prompt/source')
            report['passed'] = True
    finally:
        if mcp:
            mcp.process.terminate()
            mcp.process.wait(5)
        if process and process.poll() is None:
            process.terminate()
            process.wait(10)
        server.shutdown()
        server.server_close()
        (folder / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        (ROOT / '.runtime/readiness-mcp-latest.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()
