"""Official MCP regression for native-resolution fills; synthetic backend only."""
from __future__ import annotations
import argparse, base64, hashlib, json, os
from pathlib import Path
import socket, subprocess, sys, threading, time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
from genstudio.settings import defaults
from genstudio.service import create_server
from photocraft_engine_smoke import MCP
from photocraft_remove_smoke import SyntheticHost


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--cli", type=Path, required=True)
    args = parser.parse_args()
    folder = ROOT / ".runtime" / ("edit-resolution-smoke-" + str(time.time_ns()))
    folder.mkdir(parents=True)
    settings = {**defaults(), "runtimeRoot": str(ROOT), "engine": "direct", "directStartup": "off", "comfyStartup": "off"}
    # The service's generate method is synthetic and its spawn method fails closed.
    host = SyntheticHost(settings)
    server = create_server(settings, port=0, host=host)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    (folder / "preferences.json").write_text(json.dumps({"genStudio": settings,
        "interface": {"language": "en", "theme": "studio"},
        "dialogs": {"photoshopShortcuts": {"imported": False}}}), encoding="utf-8")
    env = os.environ.copy()
    env.update(PHOTOCRAFT_CONFIG_DIR=str(folder), GENSTUDIO_SERVICE_URL=f"http://127.0.0.1:{server.server_port}",
               GENGATE_NO_INFERENCE="1", CUDA_VISIBLE_DEVICES="")
    env.pop("GENSTUDIO_NO_PROCESS", None)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    token = folder / "control.token"
    process = mcp = None
    with args.exe.open("rb") as executable:
        executable_hash = hashlib.file_digest(executable, "sha256").hexdigest()
    report = {"real_inference": False, "synthetic_backend": True, "editorSha256": executable_hash, "steps": []}
    def until(check, timeout=50):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = check()
            if value: return value
            time.sleep(.1)
        raise TimeoutError("Resolution regression timed out")
    def step(message):
        report["steps"].append(message); print(message, flush=True)
        (folder / "report.json").write_text(json.dumps(report, indent=2))
    try:
        with patch("genstudio.service.save", lambda _: None), (folder / "app.log").open("wb") as log, (folder / "mcp.log").open("wb") as bridge:
            process = subprocess.Popen([str(args.exe), "--control", str(port), "--control-token-file", str(token),
                "--automation-read-root", str(folder), "--automation-write-root", str(folder)], env=env,
                stdout=log, stderr=log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            def listening():
                if process.poll() is not None: raise RuntimeError("Editor exited")
                if not token.exists(): return False
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=1): return True
                except OSError: return False
            until(listening)
            mcp = MCP([str(args.cli), "mcp", "--bridge", f"127.0.0.1:{port}", "--control-token-file", str(token)], bridge)
            control = lambda method, params=None: mcp.tool("control_call", {"method": method, "params": params or {}})
            menu = lambda name, params=None: control("ui.menu.invoke", {"id": name, "params": params or {}})
            queue = lambda: mcp.run("genstudio.fill.status")["fills"]
            def shot(name):
                for block in mcp.tool("ui_screenshot", {"max_side": 1600})["content"]:
                    if block["type"] == "image": (folder / name).write_bytes(base64.b64decode(block["data"]))
            control("ui.resize", {"width": 1440, "height": 1000})
            assert mcp.run("prefs.get", {"path": "genStudio.reduceEditResolution"}) is False
            preferences = menu("edit.preferences.genStudio")["dialog"]
            time.sleep(.4); shot("preferences-full-resolution.png")
            control("ui.dialog.cancel", {"dialog": preferences})
            mcp.run("file.new", {"width": 2600, "height": 1800, "background": "white"})
            selection = [1000, 700, 763, 509]
            mcp.run("select.rect", dict(zip(("x", "y", "width", "height"), selection)))
            def edit_prompt(label, tool="RectMarquee"):
                control("ui.set", {"tool": tool})
                info = mcp.tool("ui_inspect")["aiFillLayer"]["bar"]
                x, y, w, h = info["promptRect"]
                control("ui.click", {"x": x + w / 2, "y": y + h / 2})
                control("ui.key", {"key": "A", "ctrl": True})
                control("ui.type", {"text": "Remove this object — тест 👩‍💻"})
                control("ui.move", {"x": 350, "y": 250})
                time.sleep(.2)
                before = mcp.run("document.inspect")
                started = time.monotonic()
                for _ in range(80):
                    control("ui.key", {"key": "Backspace"})
                    time.sleep(.015)
                time.sleep(.2)
                after = mcp.run("document.inspect")
                current = mcp.tool("ui_inspect")["aiFillLayer"]["bar"]
                assert current["prompt"] == "", current
                assert before == after, "Editing a prompt must not edit the document"
                report.setdefault("backspace", []).append({"case": label, "seconds": round(time.monotonic() - started, 3)})
                step("Repeated Backspace stays responsive and leaves the document unchanged: " + label)
            edit_prompt("selection before generation")
            layer = mcp.run("edit.aiFill")["layer"]
            mcp.run("genstudio.ai.generate", {"layer": layer, "prompt": "Synthetic color"})
            until(lambda: not queue())
            full = host.calls[-1]["size"]
            assert full[0] > 2048 and full[1] > 768, full
            assert all(n % 32 == 0 for n in full)
            assert mcp.run("document.inspect")["selectionBounds"] == selection
            assert mcp.run("document.sampleColor", {"x": 1100, "y": 800})[1] > .65
            assert mcp.run("document.sampleColor", {"x": 20, "y": 20})[:3] == [1.0, 1.0, 1.0]
            report["full_resolution"] = list(full)
            step("Full-resolution selection fill exceeds old 768/2048 limits and preserves its mask")
            edit_prompt("generated Ai Fill layer")
            edit_prompt("generated layer, Remove Tool, pointer outside prompt", "Remove")
            control("ui.key", {"key": "Escape"})
            mcp.run("select.rect", dict(zip(("x", "y", "width", "height"), selection)))
            started = time.monotonic()
            for _ in range(12):
                control("ui.key", {"key": "Backspace"})
                mcp.tool("ui_inspect")
            report["canvas_backspace_seconds"] = round(time.monotonic() - started, 3)
            step("Repeated canvas Backspace with the floating bar visible stays responsive")
            mcp.run("genstudio.engine.configure", {**settings, "reduceEditResolution": True})
            mcp.await_engine()
            assert mcp.run("prefs.get", {"path": "genStudio.reduceEditResolution"}) is True
            preferences = menu("edit.preferences.genStudio")["dialog"]
            time.sleep(.4); shot("preferences-half-resolution.png")
            control("ui.dialog.cancel", {"dialog": preferences})
            mcp.run("genstudio.ai.generate", {"layer": layer, "prompt": "Synthetic color at half resolution"})
            until(lambda: not queue())
            half = host.calls[-1]["size"]
            assert all(n % 32 == 0 for n in half)
            assert all(full[i] / half[i] <= 2 for i in (0, 1))
            assert half[0] < full[0] and half[1] < full[1]
            assert mcp.run("document.inspect")["selectionBounds"] == selection
            report["reduced_resolution"] = list(half)
            step("Preference reaches the transport; reduction stays within 2x and preserves selection")
            mcp.run("select.deselect")
            mcp.run("paint.remove", {"points": [[600, 800], [640, 850]], "size": 31,
                                     "useQwen": True, "newLayer": True, "sampleAllLayers": True})
            until(lambda: not queue())
            assert all(n % 32 == 0 for n in host.calls[-1]["size"])
            step("Remove Tool uses the same aligned geometry and resolution preference")
            shot("native-resolution-fill.png")
            mcp.run("file.close", {"discard": True})
            mcp.run("file.new", {"width": 640, "height": 480, "background": "white"})
            mcp.run("select.rect", {"x": 280, "y": 200, "width": 40, "height": 32})
            small_layer = mcp.run("edit.aiFill")["layer"]
            mcp.run("genstudio.ai.generate", {"layer": small_layer, "prompt": "Synthetic small crop"})
            until(lambda: not queue())
            small = host.calls[-1]["size"]
            assert list(small) == [576, 480], small
            report["small_economy_resolution"] = list(small)
            step("Economy mode sends a small crop at its original resolution")
            report["passed"] = True
    finally:
        if mcp:
            try:
                mcp.run("file.close", {"discard": True})
                mcp.tool("control_call", {"method": "app.quit", "params": {}})
            except Exception: pass
            mcp.process.terminate(); mcp.process.wait(timeout=5)
        if process:
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.terminate(); process.wait(timeout=10)
        server.shutdown(); server.manager.shutdown(); server.studio.shutdown(); server.server_close(); thread.join(5)
        (folder / "report.json").write_text(json.dumps(report, indent=2))
        (ROOT / ".runtime/edit-resolution-smoke-latest.json").write_text(json.dumps({"folder": str(folder), **report}, indent=2))
        print(str(folder), flush=True)


if __name__ == "__main__": main()
