"""Entrypoint that also works with Windows embedded Python's isolated sys.path."""
from pathlib import Path
import runpy
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if '--model' in sys.argv:
    selected = sys.argv.index('--model') + 1
    if selected < len(sys.argv):
        core = {'flux-3-image-klein': 'flux-core', 'fibo-2-edit-turbo': 'fibo-core'}.get(sys.argv[selected])
        if core:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '.runtime' / core / 'deps'))
runpy.run_module("genstudio.direct_worker", run_name="__main__")
