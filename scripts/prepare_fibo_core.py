"""Export the pinned FIBO 2-compatible library; never load weights or a server."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare_qwen_core import prepare

REVISION = "d07dff4565ea9804277fff8cdfa0c817e08c3d44"

if __name__ == '__main__':
    print(prepare(revision=REVISION, directory='fibo-core'))
