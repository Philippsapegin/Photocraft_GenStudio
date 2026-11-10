"""Check, repair or detach GenGate without installing another PhotoCraft."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from genstudio import editor_patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--metadata", type=Path)
    source.add_argument("--host", type=Path, help="Restore an old shared host using the extracted package's Python.")
    parser.add_argument("--restore", action="store_true")
    parser.add_argument("--cleanup-recovery", action="store_true")
    args = parser.parse_args()
    try:
        if args.host:
            if not args.restore:
                raise ValueError('A host-only operation supports removal only.')
            state=editor_patch.state_for(args.host)
            result=editor_patch.restore(args.host)
            if args.cleanup_recovery:
                result.update(editor_patch.cleanup_recovery(args.host,state.get('files')))
            print(json.dumps(result), flush=True)
            return 0
        metadata = editor_patch.read(args.metadata)
        if metadata.get("installMode") != "overlay":
            raise ValueError("This is not a GenGate add-on installation.")
        host = Path(metadata["host"])
        state = editor_patch.state_for(host)
        if (host / ".gengate/journal.json").exists():
            # Root metadata can already point at the new runtime while an interrupted
            # transaction still owns the old native state. Recover before ownership checks.
            profile = Path(state.get("profile", metadata["profile"])).resolve()
            with editor_patch.locked(host):
                editor_patch.assert_closed(host)
                editor_patch.recover(host, profile)
            if not args.metadata.exists():
                print(json.dumps({"restored": True, "reason": "Interrupted first installation rolled back."}), flush=True)
                return 0
            metadata = editor_patch.read(args.metadata)
            state = editor_patch.state_for(host)
        if args.restore and state and state.get("runtime") != metadata.get("runtime"):
            raise ValueError("A newer GenGate installation owns this PhotoCraft. Use its Uninstall.cmd.")
        result = editor_patch.restore(host) if args.restore else editor_patch.ensure_current(host)
        if args.restore and metadata.get("shortcutsInstalled", True):
            from genstudio.windows_shortcuts import repair
            repair(args.metadata.parent, host, desktop=metadata.get("desktopShortcut", True), installed=False)
        if args.cleanup_recovery:
            if not args.restore:raise ValueError('Recovery cleanup requires uninstalling first.')
            result.update(editor_patch.cleanup_recovery(host,state.get('files')))
        print(json.dumps(result), flush=True)
        return 0 if args.restore or result["state"] != "notInstalled" else 2
    except Exception as error:
        print(str(error), flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
