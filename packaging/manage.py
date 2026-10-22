"""Check, repair or detach GenGate without installing another PhotoCraft."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from genstudio import editor_patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--restore", action="store_true")
    args = parser.parse_args()
    try:
        metadata = editor_patch.read(args.metadata)
        if metadata.get("installMode") != "overlay":
            raise ValueError("This is not a GenGate add-on installation.")
        host = Path(metadata["host"])
        state = editor_patch.state_for(host)
        if args.restore and state and state.get("runtime") != metadata.get("runtime"):
            raise ValueError("A newer GenGate installation owns this PhotoCraft. Use its Uninstall.cmd.")
        result = editor_patch.restore(host) if args.restore else editor_patch.ensure_current(host)
        if args.restore and metadata.get("shortcutsInstalled", True):
            from genstudio.windows_shortcuts import repair
            repair(args.metadata.parent, host, desktop=metadata.get("desktopShortcut", True), installed=False)
        print(json.dumps(result), flush=True)
        return 0 if args.restore or result["state"] != "notInstalled" else 2
    except Exception as error:
        print(str(error), flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
