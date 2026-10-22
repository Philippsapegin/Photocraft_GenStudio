"""Fetch PhotoCraft 0.12's pinned release fonts and their original licenses."""
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import json
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REVISION = "abb83316d96aa59c1cf64784289e378fe9fa5695"
BASE = "https://raw.githubusercontent.com/storytold/craft-fonts/" + REVISION + "/"
FONTS = {
    "fonts/shippori-mincho/ShipporiMincho-Regular.ttf": "769b5269f0f9bc6534b352c0e6bd856a566e03ff788f107191c2d835863570b2",
    "fonts/biz-ud-pgothic/BIZUDPGothic-Regular.ttf": "258d7156c165f2ff774b6efee637c22c3b950de0d8a10e501137061bc8085d01",
    "fonts/biz-ud-pgothic/BIZUDPGothic-Bold.ttf": "30eba52fc837e8b62c97d4b82e6706583149fb7294e3712dd71a655eaea80a90",
    "fonts/biz-ud-mincho/BIZUDMincho-Regular.ttf": "468ee6d9b149ca144809e03841bf18740ecf014e055a00da6ecaf1aaf4165af2",
}


def prepare():
    destination = ROOT / ".runtime" / ("craft-fonts-" + REVISION[:12])

    def fetch(relative, expected=None):
        if ":" in relative or "\\" in relative or PurePosixPath(relative).is_absolute() or ".." in PurePosixPath(relative).parts:
            raise ValueError("Invalid font path")
        target = destination / relative
        if target.is_file() and (expected is None or hashlib.sha256(target.read_bytes()).hexdigest() == expected):
            return target
        request = urllib.request.Request(BASE + relative, headers={"User-Agent": "PhotoCraft-dev"})
        with urllib.request.urlopen(request, timeout=90) as response:
            data = response.read(32 * 1024 * 1024 + 1)
        if len(data) > 32 * 1024 * 1024 or expected and hashlib.sha256(data).hexdigest() != expected:
            raise ValueError("Invalid pinned font: " + relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")
        partial.write_bytes(data)
        partial.replace(target)
        return target

    manifest = fetch("fonts/manifest.txt")
    seen = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        fields = [part.strip() for part in line.split(" | ")]
        if len(fields) != 8 or FONTS.get(fields[2]) != fields[6]:
            raise ValueError("Unexpected release font manifest")
        seen[fields[2]] = fields[6]
        fetch(fields[2], fields[6])
        fetch(fields[5])
    if seen != FONTS:
        raise ValueError("Incomplete release font manifest")
    return destination.resolve()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="ASCII JSON for PowerShell 5 path transport")
    args = parser.parse_args()
    path = str(prepare())
    print(json.dumps(path) if args.json else path, flush=True)
