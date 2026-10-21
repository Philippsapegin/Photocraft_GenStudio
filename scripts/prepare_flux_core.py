"""Export the isolated FLUX3-compatible GPL model library, without weights."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare_qwen_core import prepare

REVISION = "8713616fcc321624e306bbd36be5906203dcb0dc"  # ComfyUI v0.41.3

if __name__ == "__main__":
    print(prepare(revision=REVISION, directory="flux-core"))
