"""Drive an isolated PhotoCraft through its official MCP server; no inference."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from genstudio.settings import defaults


class MCP:
    def __init__(self, args, stderr):
        self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=stderr, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.inbox = queue.Queue()
        self.sequence = 0
        def reader():
            for line in self.process.stdout:
                self.inbox.put(json.loads(line))
        threading.Thread(target=reader, daemon=True).start()
        self.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "GenStudio-validation", "version": "0.3.0"}})
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def send(self, message):
        self.process.stdin.write((json.dumps(message) + "\n").encode())
        self.process.stdin.flush()

    def call(self, method, params):
        self.sequence += 1
        self.send({"jsonrpc": "2.0", "id": self.sequence, "method": method, "params": params})
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            reply = self.inbox.get(timeout=max(0.1, deadline - time.monotonic()))
            if reply.get("id") == self.sequence:
                if "error" in reply:
                    raise RuntimeError(reply["error"])
                return reply["result"]
        raise TimeoutError(method)

    def tool(self, name, args=None):
        result = self.call("tools/call", {"name": name, "arguments": args or {}})
        if result.get("isError"):
            raise RuntimeError(str(result.get("content")))
        if any(c["type"] == "image" for c in result.get("content", [])):
            return result
        text = next((c["text"] for c in result.get("content", []) if c["type"] == "text"), "null")
        return json.loads(text)

    def run(self, name, params=None):
        return self.tool("command_run", {"id": name, "params": params or {}})

    def await_engine(self):
        deadline = time.monotonic() + 360
        while time.monotonic() < deadline:
            status = self.run("genstudio.engine.status")
            if not status.get("pending"):
                return status
            time.sleep(0.2)
        raise TimeoutError("engine control")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, default=ROOT / ".runtime/photocraft-source/target/release/photocraft.exe")
    parser.add_argument("--start-comfy", action="store_true")
    parser.add_argument("--start-direct", action="store_true")
    parser.add_argument("--check-settings", action="store_true")
    parser.add_argument("--check-canvas", action="store_true")
    parser.add_argument("--check-app-start", action="store_true")
    args = parser.parse_args()
    folder = ROOT / ".runtime/photocraft-engine-smoke"
    folder.mkdir(parents=True, exist_ok=True)
    settings = defaults()
    # An unshared test port isolates the service lifecycle from any user's ComfyUI.
    settings["comfyUrl"] = "http://127.0.0.1:18190"
    if args.check_app_start:
        settings.update(engine="direct", directStartup="appStart")
    (folder / "preferences.json").write_text(json.dumps({"genStudio": settings, "interface": {"language": "en"},
                                                       "dialogs": {"photoshopShortcuts": {"imported": False}}}), encoding="utf-8")
    token = folder / "control.token"
    env = os.environ.copy()
    env["PHOTOCRAFT_CONFIG_DIR"] = str(folder)
    cli = Path(os.environ.get("PHOTOCRAFT_CLI", ROOT / ".runtime/photocraft-source/target/release/photocraft-cli.exe"))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    mcp = None
    report = {"model_inference": False}
    with open(folder / "app.log", "wb") as app_log, open(folder / "mcp.log", "wb") as mcp_log:
        process = subprocess.Popen([str(args.exe), "--control", str(port), "--control-token-file", str(token),
                                    "--automation-read-root", str(folder), "--automation-write-root", str(folder)],
                                   env=env, stdout=app_log, stderr=app_log,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("PhotoCraft exited. See app.log.")
                if token.exists():
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=1):
                            break
                    except OSError:
                        pass
                time.sleep(0.2)
            mcp = MCP([str(cli), "mcp", "--bridge", f"127.0.0.1:{port}", "--control-token-file", str(token)], mcp_log)
            tools = mcp.call("tools/list", {})
            report["official_mcp"] = len(tools["tools"])
            commands = mcp.tool("command_list", {"filter": "genstudio"})
            report["commands"] = commands
            items = mcp.tool("control_call", {"method": "ui.menu.list", "params": {}})
            suite = next(item for item in items if item["id"] == "genstudio.open")
            assert suite["enabled"] and suite["path"] == ["Filter", "Plug-ins"], suite
            report["suite_menu"] = suite
            mcp.tool("ui_menu_invoke", {"id": "genstudio.open"})
            ui = mcp.tool("ui_inspect")
            report["window"] = ui.get("genStudio")
            assert report["window"] and report["window"]["open"], ui.keys()
            status = mcp.await_engine() if args.check_app_start else mcp.run("genstudio.engine.status")
            assert not status["pending"]
            assert status["settings"]["comfyStartup"] == "firstRequest"
            report["initial_status"] = status
            if args.check_app_start:
                assert status["status"]["ready"] and status["settings"]["engine"] == "direct", status
                report["real_app_start"] = status
            if args.check_settings:
                disabled = {**settings, "comfyStartup": "off", "directStartup": "appStart"}
                mcp.run("genstudio.engine.configure", disabled)
                assert mcp.await_engine()["status"]["state"] == "stopped"
                mcp.run("genstudio.engine.ensure", {"event": "request"})
                error = mcp.await_engine()
                assert error["status"]["state"] == "error" and "Automatic start is off" in error["status"]["message"], error
                report["automatic_start_off"] = error
                direct = {**disabled, "engine": "direct", "modelsDir": str(folder / "missing models")}
                mcp.run("genstudio.engine.configure", direct)
                assert mcp.await_engine()["settings"]["engine"] == "direct"
                prefs = mcp.run("prefs.get", {"path": "genStudio"})
                assert prefs["comfyStartup"] == "off" and prefs["directStartup"] == "appStart", prefs
                report["separate_startup_preferences"] = prefs
                mcp.run("genstudio.engine.start")
                error = mcp.await_engine()
                assert error["status"]["state"] == "error" and "Models folder" in error["status"]["message"], error
                report["direct_missing_model"] = error
                mcp.run("genstudio.engine.configure", settings)
                mcp.await_engine()
                mcp.tool("ui_menu_invoke", {"id": "genstudio.open"})
            if args.start_comfy:
                mcp.run("genstudio.engine.ensure", {"event": "request"})
                status = mcp.await_engine()
                assert status["status"]["ready"], status
                report["real_comfy_start"] = status
                from genstudio.lifecycle import http_json
                report["queue"] = http_json(settings["comfyUrl"] + "/queue")
                assert report["queue"] == {"queue_running": [], "queue_pending": []}
                mcp.run("genstudio.engine.stop")
                report["real_comfy_stop"] = mcp.await_engine()
                assert not report["real_comfy_stop"]["status"]["ready"]
            if args.start_direct:
                direct = {**settings, "engine": "direct"}
                mcp.run("genstudio.engine.configure", direct)
                mcp.await_engine()
                mcp.run("genstudio.engine.ensure", {"event": "request"})
                status = mcp.await_engine()
                assert status["status"]["ready"], status
                report["real_direct_start"] = status
                from genstudio.lifecycle import http_json
                health = http_json(status["status"]["workerUrl"] + "/health")
                assert Path(health["modelsDir"]).resolve() == Path(settings["modelsDir"]).resolve(), health
                report["direct_health"] = health
                mcp.run("genstudio.engine.stop")
                assert not mcp.await_engine()["status"]["ready"]
                mcp.run("genstudio.engine.configure", settings)
                mcp.await_engine()
                mcp.tool("ui_menu_invoke", {"id": "genstudio.open"})
            if args.check_canvas:
                mcp.run("file.new", {"width": 512, "height": 512, "background": "white"})
                mcp.run("edit.fill", {"color": "#2266dd"})
                time.sleep(1)
                canvas = mcp.tool("ui_inspect")
                report["real_canvas"] = canvas.get("perf")
                assert canvas["perf"]["timings"]["gpu"], canvas.get("perf")
            result = mcp.tool("ui_screenshot", {"max_side": 1600})
            for block in result["content"]:
                if block["type"] == "image":
                    name = ("app-start-check.png" if args.check_app_start else "canvas-check.png" if args.check_canvas
                            else "settings-check.png" if args.check_settings else "genstudio.png")
                    (folder / name).write_bytes(base64.b64decode(block["data"]))
            if args.check_canvas:
                mcp.run("file.close", {"discard": True})
            report["passed"] = True
        finally:
            if mcp:
                try:
                    mcp.run("genstudio.engine.stop")
                    mcp.await_engine()
                    mcp.tool("ui_menu_invoke", {"id": "file.exit"})
                except Exception:
                    pass
                mcp.process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=10)
            (folder / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report.get("passed", False), "report": str(folder / "report.json")}))


if __name__ == "__main__":
    main()
