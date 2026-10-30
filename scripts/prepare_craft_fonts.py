"""Fetch each PhotoCraft release's pinned fonts and their original licenses."""
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import json
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REVISION = "b076417b5880bd4154bd22f00695b1d28c6bea83"
LEGACY_REVISION = "abb83316d96aa59c1cf64784289e378fe9fa5695"
LEGACY_FONTS = {
    "fonts/shippori-mincho/ShipporiMincho-Regular.ttf": "769b5269f0f9bc6534b352c0e6bd856a566e03ff788f107191c2d835863570b2",
    "fonts/biz-ud-pgothic/BIZUDPGothic-Regular.ttf": "258d7156c165f2ff774b6efee637c22c3b950de0d8a10e501137061bc8085d01",
    "fonts/biz-ud-pgothic/BIZUDPGothic-Bold.ttf": "30eba52fc837e8b62c97d4b82e6706583149fb7294e3712dd71a655eaea80a90",
    "fonts/biz-ud-mincho/BIZUDMincho-Regular.ttf": "468ee6d9b149ca144809e03841bf18740ecf014e055a00da6ecaf1aaf4165af2",
}
FONTS = {
    **LEGACY_FONTS,
    "fonts/noto-sans-cjk-sc/NotoSansCJKsc-Regular.otf": "2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b",
    "fonts/noto-sans-arabic/NotoSansArabic.ttf": "63111b5b2e074dd48cc67692e0a2726d86ee94c1c37fe8598257b7b4e87e869e",
    "fonts/noto-sans-thai/NotoSansThai.ttf": "5a1c559bb539583c8a1fd99d1c5b9491e5e14478c9cd2bd0970d5c3096cc9ef8",
    "fonts/noto-sans-georgian/NotoSansGeorgian.ttf": "dc591156f36842d38996c4a7a17fee9bb58e45da3e2cac7a31b7d33de700adb9",
    "fonts/nanum-gothic/NanumGothic-Regular.ttf": "76f45ef4a6bcff344c837c95a7dcc26e017e38b5846d5ae0cdcb5b86be2e2d31",
    "fonts/noto-sans-symbols/NotoSansSymbols.ttf": "f7e7e04b4a24b6c78893d50cbfd2b2f6cae49617ab047bfef668d252adb128f7",
    "fonts/noto-sans-symbols-2/NotoSansSymbols2-Regular.ttf": "7d5fb73b7ca67a6798101741f5d280a3d016a56a197afcd4199dbb57b4b82a21",
    "fonts/noto-sans-hebrew/NotoSansHebrew-Regular.ttf": "671951828bd5c95db818e5bb12dcea2d0c0dda00311888522be061ee6835125e",
    "fonts/noto-sans-devanagari/NotoSansDevanagari-Regular.ttf": "573e525d9f4923d34645fe752475419f45f8dec5a8e43f3e83c5c5f43d1f2ff9",
}


def prepare(host_version="0.17.0"):
    revision = REVISION if host_version == "0.17.0" else LEGACY_REVISION
    fonts = FONTS if host_version == "0.17.0" else LEGACY_FONTS
    base = "https://raw.githubusercontent.com/storytold/craft-fonts/" + revision + "/"
    destination = ROOT / ".runtime" / ("craft-fonts-" + revision[:12])

    def fetch(relative, expected=None):
        if ":" in relative or "\\" in relative or PurePosixPath(relative).is_absolute() or ".." in PurePosixPath(relative).parts:
            raise ValueError("Invalid font path")
        target = destination / relative
        if target.is_file() and (expected is None or hashlib.sha256(target.read_bytes()).hexdigest() == expected):
            return target
        request = urllib.request.Request(base + relative, headers={"User-Agent": "PhotoCraft-dev"})
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
        if len(fields) != 8 or fonts.get(fields[2]) != fields[6]:
            raise ValueError("Unexpected release font manifest")
        seen[fields[2]] = fields[6]
        fetch(fields[2], fields[6])
        fetch(fields[5])
    if seen != fonts:
        raise ValueError("Incomplete release font manifest")
    return destination.resolve()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="ASCII JSON for PowerShell 5 path transport")
    parser.add_argument("--host-version", default="0.17.0", choices=("0.12.0","0.15.1","0.16.0","0.17.0"))
    args = parser.parse_args()
    path = str(prepare(args.host_version))
    print(json.dumps(path) if args.json else path, flush=True)
