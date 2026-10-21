"""Download the default YOLO-World and SAM3 weights without running either model."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from genstudio.segmentation_models import ensure_models, ensure_modules


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", type=Path, required=True)
    args = parser.parse_args()
    report = ROOT / ".runtime/segmentation-download.json"
    report.parent.mkdir(exist_ok=True)
    def emit(event):
        temporary = report.with_suffix(".tmp")
        temporary.write_text(json.dumps(dict(state="preparing", **event)), encoding="utf-8")
        temporary.replace(report)
    try:
        ensure_modules(emit)
        paths = ensure_models(args.models, emit)
        emit({"message": "Segmentation models downloaded", "progress": 1})
        report.write_text(json.dumps({"state": "ready", "models": paths}), encoding="utf-8")
    except Exception as error:
        report.write_text(json.dumps({"state": "error", "message": str(error)}), encoding="utf-8")
        raise


if __name__ == "__main__":
    main()
