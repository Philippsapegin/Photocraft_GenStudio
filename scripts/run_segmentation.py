"""Windows embedded-Python-compatible, short-lived GPU segmentation entry point."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from genstudio.segmentation import worker

if __name__ == "__main__":
    worker(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
