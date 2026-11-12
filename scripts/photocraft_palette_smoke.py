"""Exercise only an owned test editor/profile, with synthetic generation and MCP."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
from PIL import Image
from genstudio.service import create_server
from genstudio.settings import defaults
from photocraft_engine_smoke import MCP
from photocraft_remove_smoke import SyntheticHost


class Host(SyntheticHost):
    def generate_studio(self, references, seed, prompt, width, height, cancel, progress, **kw):
        self.calls.append({"references": len(references), "seed": seed, "prompt": prompt, "size": [width, height]})
        progress("Synthetic palette test")
        return Image.new("RGBA", (width, height), (35, 80, 135, 255))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--cli", type=Path, required=True)
    parser.add_argument("--interactive", action="store_true", help="Leave an isolated editor open for human review; generation remains synthetic")
    parser.add_argument("--aux-probe", action="store_true", help="Check collapsing while the LoRA browser is open")
    args = parser.parse_args()
    folder = ROOT / ".runtime" / ("palette-smoke-" + str(time.time_ns()))
    folder.mkdir()
    if args.interactive:
        sys.stdout = sys.stderr = (folder / "preview.log").open("w", encoding="utf-8", buffering=1)
    (folder / "loras/styles").mkdir(parents=True)
    (folder / "loras/styles/fixture.safetensors").write_bytes(b"discovery fixture; never a checkpoint")
    settings = {**defaults(), "engine": "direct", "model": "qwen-image-2.1-turbo", "modelsDir": str(folder),
                "runtimeRoot": str(ROOT), "directStartup": "off", "comfyStartup": "off"}
    host = Host(settings)
    server = create_server(settings, port=0, host=host)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    (folder / "preferences.json").write_text(json.dumps({"genStudio": settings, "general": {"automaticallyCheckUpdates": False},
        "interface": {"language": "en", "theme": "studio"}, "dialogs": {"photoshopShortcuts": {"imported": False}}}), encoding="utf-8")
    env = {**os.environ, "PHOTOCRAFT_CONFIG_DIR": str(folder), "GENSTUDIO_SERVICE_URL": f"http://127.0.0.1:{server.server_port}",
           "CUDA_VISIBLE_DEVICES": "", "GENGATE_NO_INFERENCE": "1"}
    env.pop("GENSTUDIO_NO_PROCESS", None)
    token = folder / "control.token"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    report = {"completed": False, "real_inference": False, "folder": str(folder)}
    process = mcp = None

    def until(fn, timeout=50):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = fn()
            if value:
                return value
            if process is not None and process.poll() is not None:
                raise RuntimeError("Owned test editor exited; see app.log")
            time.sleep(.08)
        raise TimeoutError("Palette test condition did not arrive")

    try:
        with patch("genstudio.service.save", lambda _: None), (folder / "app.log").open("wb") as log, (folder / "mcp.log").open("wb") as bridge:
            process = subprocess.Popen([str(args.exe.resolve()), "--control", str(port), "--control-token-file", str(token),
                "--automation-read-root", str(folder), "--automation-write-root", str(folder)], cwd=args.exe.resolve().parent,
                env=env, stdout=log, stderr=log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

            def ready():
                if not token.exists():
                    return False
                try:
                    with socket.create_connection(("127.0.0.1", port), .2):
                        return True
                except OSError:
                    return False
            until(ready, 70)
            mcp = MCP([str(args.cli.resolve()), "mcp", "--bridge", f"127.0.0.1:{port}", "--control-token-file", str(token)], bridge)
            mcp.await_ui()
            def control(method, params=None):
                return mcp.tool("control_call", {"method": method, "params": params or {}})
            def menu(name, params=None):
                return control("ui.menu.invoke", {"id": name, "params": params or {}})
            def studio():
                return menu("genstudio.studio.inspect") or {}
            def configure(**values):
                return menu("genstudio.studio.configure", values)
            def inputs(*events):
                configure(palette={"input": list(events)})
                until(lambda: studio().get("palette", {}).get("pendingInput") == 0)
                time.sleep(.08)
            def click(rect):
                point={"x": rect[0] + rect[2] / 2, "y": rect[1] + rect[3] / 2}
                inputs({"kind": "down", **point}, {"kind": "up", **point})
            def shot(name):
                configure(palette={"capture": True})
                result = until(lambda: (v if not v["pending"] else None) if (v := menu("genstudio.studio.inspect", {"paletteCapture": True})) else None)
                image = result["capture"]
                assert image.get("ok"), image
                (folder / name).write_bytes(base64.b64decode(image["result"]["base64"]))
                return [image["result"]["width"], image["result"]["height"]]
            def done():
                jobs = [j for j in mcp.run("jobs.list")["jobs"] if j["command"] == "genstudio.studio.generate"]
                if not jobs or any(j["state"] == "running" for j in jobs):
                    return False
                assert all(j["state"] == "done" for j in jobs), jobs
                return jobs

            control("ui.resize", {"width": 1050, "height": 800})
            menu("genstudio.open")
            until(lambda: studio().get("palette", {}).get("native"))
            if args.interactive:
                configure(prompt="Palette review — synthetic generation")
                report["exitCode"] = process.wait()
                report["completed"] = report["exitCode"] == 0
                return
            configure(prompt="Synthetic palette", palette={"positionPixels": [1200, 120], "size": [940, 650]})
            until(lambda: abs(studio()["palette"]["positionPixels"][0] - 1200) < 3)
            report["palette"] = studio()["palette"]
            report["native_capture_size"] = shot("palette-dark.png")
            assert report["native_capture_size"][0] > 900
            click(studio()["controls"]["generate"])
            until(done)
            until(lambda: not studio()["loading"])
            assert len(host.calls) == 1 and not studio()["references"]
            layers = len(mcp.run("document.inspect")["layers"])
            click(studio()["promptRect"])
            inputs({"kind": "key", "key": "A", "ctrl": True}, {"kind": "key", "key": "Delete"})
            until(lambda: studio()["prompt"] == "")
            assert len(mcp.run("document.inspect")["layers"]) == layers
            inputs({"kind": "text", "text": "Palette typing: Привет"})
            until(lambda: studio()["prompt"] == "Palette typing: Привет")
            report["typing_does_not_delete_document_layer"] = True
            click(studio()["controls"]["negativeToggle"])
            until(lambda: studio()["negativeOpen"])
            assert "seed" not in studio()["controls"]
            assert not studio().get("numericDialog")
            report["seed_controls_removed"] = True
            configure(customSize={"width": 768, "height": 512})
            until(lambda: studio()["sizeLabel"] == "768px:512px")
            shot("palette-custom-size.png")
            report["custom_size_in_native_palette"] = studio()["customSize"]
            # Save/open state and geometry across an actual palette close/recreate.
            before = studio()
            click(before["window"]["closeRect"])
            until(lambda: not mcp.tool("ui_inspect")["genStudio"]["open"])
            until(lambda: studio().get("palette", {}).get("native") is False)
            assert process.poll() is None
            menu("genstudio.open")
            until(lambda: studio().get("palette", {}).get("native"))
            assert studio()["prompt"] == before["prompt"] and studio()["seed"] == before["seed"]
            assert abs(studio()["palette"]["positionPixels"][0] - 1200) < 3
            report["close_reopen_preserves_session_and_position"] = True
            # Collapse using the header's left triangle, then restore the same expanded size.
            configure(palette={"size": [1000, 700]})
            until(lambda: abs(studio()["palette"]["size"][1] - 700) < 3)
            # Native geometry can arrive before the recreated viewport has painted
            # its new layout. Capture waits for settled frames before hit testing.
            shot("palette-resized.png")
            header = studio()["window"]["headerRect"]
            inputs({"kind": "click", "x": header[0] + 17, "y": header[1] + header[3] / 2})
            until(lambda: studio()["window"]["collapsed"])
            report["collapsed_capture_size"] = shot("palette-collapsed.png")
            assert report["collapsed_capture_size"][1] < 80
            header = studio()["window"]["headerRect"]
            inputs({"kind": "click", "x": header[0] + 17, "y": header[1] + header[3] / 2})
            until(lambda: not studio()["window"]["collapsed"])
            report["expanded_capture_size"] = shot("palette-expanded.png")
            assert report["expanded_capture_size"] == [1000, 700]
            click(studio()["controls"]["loras"])
            until(lambda: studio().get("loras", {}).get("open"))
            shot("palette-loras.png")
            header=studio()["window"]["headerRect"]
            click([header[0],header[1],34,header[3]])
            until(lambda: studio()["window"]["collapsed"])
            shot("palette-collapsed-with-browser.png")
            header=studio()["window"]["headerRect"]
            click([header[0],header[1],34,header[3]])
            until(lambda: not studio()["window"]["collapsed"],3)
            assert studio()["loras"]["open"]
            shot("palette-browser-after-expanding.png")
            report["collapse_preserves_browser_and_keeps_header_usable"]=True
            if args.aux_probe:
                report["completed"]=True
                return
            menu("genstudio.loras.open", {"open": False})
            mcp.run("prefs.set", {"path": "interface.theme", "value": "studioLight"})
            shot("palette-light.png")
            # A saved monitor can disappear. Recreating at an unreachable desktop
            # position must recover the title to a connected display.
            configure(palette={"positionPixels": [-12000, -12000]})
            until(lambda: studio()["palette"]["positionPixels"][0] < -10000)
            menu("genstudio.open", {"open": False})
            until(lambda: not mcp.tool("ui_inspect")["genStudio"]["open"])
            until(lambda: studio().get("palette", {}).get("native") is False)
            menu("genstudio.open")
            until(lambda: studio()["palette"]["positionPixels"][0] > -10000)
            report["unreachable_saved_position_recovers"] = studio()["palette"]["positionPixels"]
            configure(palette={"positionPixels": [1200, 120], "size": [960, 680]})
            until(lambda: abs(studio()["palette"]["positionPixels"][0] - 1200) < 3 and abs(studio()["palette"]["size"][1] - 680) < 3)
            stable=[]
            for _ in range(6):
                time.sleep(.06)
                observed=studio()["palette"]["size"]
                stable.append(observed)
                assert abs(observed[0]-960)<3 and abs(observed[1]-680)<3,stable
            report["resize_stays_at_requested_size"] = stable
            control("app.quit")
            process.wait(15)
            mcp.process.terminate()
            mcp.process.wait(5)
            token.unlink(missing_ok=True)
            process = subprocess.Popen([str(args.exe.resolve()), "--control", str(port), "--control-token-file", str(token),
                "--automation-read-root", str(folder), "--automation-write-root", str(folder)], cwd=args.exe.resolve().parent,
                env=env, stdout=log, stderr=log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            until(ready, 70)
            mcp = MCP([str(args.cli.resolve()), "mcp", "--bridge", f"127.0.0.1:{port}", "--control-token-file", str(token)], bridge)
            mcp.await_ui()
            menu("genstudio.open")
            until(lambda: studio().get("palette", {}).get("native"))
            restored = studio()["palette"]
            assert abs(restored["positionPixels"][0] - 1200) < 3 and abs(restored["size"][1] - 680) < 3, restored
            report["geometry_survives_editor_restart"] = restored
            report["calls"] = host.calls
            report["completed"] = True
    except Exception as error:
        report["error"] = str(error)
        if mcp and process and process.poll() is None:
            try:
                report["failure_studio"] = studio()
                shot("palette-failure.png")
            except Exception as diagnostic_error:
                report["diagnostic_error"] = str(diagnostic_error)
        raise
    finally:
        if mcp:
            mcp.process.terminate()
            mcp.process.wait(5)
        if process and process.poll() is None:
            process.terminate()
            process.wait(10)
        server.shutdown()
        server.server_close()
        report_path = ROOT / (".runtime/palette-preview.json" if args.interactive else ".runtime/palette-aux.json" if args.aux_probe else ".runtime/palette-smoke.json")
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
