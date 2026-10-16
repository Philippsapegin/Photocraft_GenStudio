"""Entrypoint that also works with Windows embedded Python's isolated sys.path."""
from pathlib import Path
import runpy
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
runpy.run_module("genstudio.direct_worker", run_name="__main__")
