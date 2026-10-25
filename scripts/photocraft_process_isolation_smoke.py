"""Official MCP: separate generation/selection workers, synthetic pixels only."""
from __future__ import annotations
import argparse
import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
from genstudio.lifecycle import http_json
from genstudio.models import MODELS, MODEL, FLUX
from genstudio.settings import defaults
from photocraft_engine_smoke import MCP
from photocraft_segmentation_smoke import fixture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--cli", type=Path, required=True)
    args = parser.parse_args()
    folder = ROOT / ".runtime" / ("process-isolation-" + str(time.time_ns()))
    folder.mkdir(parents=True)
    runtime = fixture(folder)
    # This interpreter cannot import torch. Both worker entry points are
    # replaced by explicit synthetic fixtures, never the production inference.
    (runtime / "scripts/run_direct.py").write_text('''
import argparse, importlib.util, sys, time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
assert importlib.util.find_spec('torch') is None
from genstudio.direct_worker import Worker, Handler
from http.server import ThreadingHTTPServer
from PIL import Image
p=argparse.ArgumentParser()
for name in ('models','text-encoder','vae','model','components','owner','port'):p.add_argument('--'+name)
a=p.parse_args();root=Path(__file__).resolve().parents[1]
w=Worker(a.models,a.text_encoder,a.vae,a.model,components=__import__('json').loads(a.components),owner=a.owner)
w.pipeline.check=lambda:[]
def generated(refs,seed,prompt,width,height,cancel,progress,*,loras=None):
 assert not loras
 (root/'generation-started').touch()
 while (root/'block-generation').exists():
  if cancel.wait(.05):raise __import__('genfill.comfy',fromlist=['Cancelled']).Cancelled()
 return Image.new('RGBA',(width,height),(44,150,95,255))
w.pipeline.generate_studio=generated
s=ThreadingHTTPServer(('127.0.0.1',int(a.port)),Handler);s.worker=w;s.serve_forever()
''', encoding="utf-8")
    # Use an irregular SAM-like mask for the selection/capture path.
    source = runtime / "scripts/run_segmentation.py"
    text = source.read_text(encoding="utf-8").replace(
        "else:draw.rectangle((w//4,h//4,w*3//4-1,h*3//4-1),fill=255)",
        "else:draw.polygon([(w//8,h//3),(w//2,h//8),(w*7//8,h//2),(w*3//4,h*7//8),(w//3,h*3//4)],fill=255)")
    text = text.replace("p=json.loads(Path(sys.argv[1]).read_text());", """sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from genstudio.processes import gpu_lease
def waiting():
 f=Path(json.loads(Path(sys.argv[1]).read_text())['folder'])/'segmentation-progress.json'
 f.write_text(json.dumps({'message':'Waiting for generation to release the GPU','progress':.4}))
lease=gpu_lease(waiting=waiting);lease.__enter__()
p=json.loads(Path(sys.argv[1]).read_text());""")
    source.write_text(text, encoding="utf-8")
    for spec in MODELS.values():
        for name, folders in spec["files"].values():
            path = folder / "models" / folders[0] / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic metadata fixture, no weights")
    profile = folder / "profile"; profile.mkdir()
    settings = {**defaults(), "engine": "direct", "model": MODEL, "runtimeRoot": str(runtime),
                "modelsDir": str(folder / "models"), "directPython": str(runtime / ".venv/Scripts/python.exe"),
                "directStartup": "firstRequest", "comfyStartup": "off",
                "segmentation": {"enabled": True, "detector": "yolov8s-worldv2", "segmenter": "sam3", "python": ""}}
    (profile / "preferences.json").write_text(json.dumps({"genStudio": settings, "interface": {"language": "en"},
        "dialogs": {"photoshopShortcuts": {"imported": False}}}), encoding="utf-8")
    env = os.environ.copy()
    env.update(PHOTOCRAFT_CONFIG_DIR=str(profile), CUDA_VISIBLE_DEVICES="")
    for key in ("GENGATE_NO_INFERENCE", "GENSTUDIO_NO_PROCESS", "GENSTUDIO_SERVICE_URL"):
        env.pop(key, None)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    token = profile / "control.token"
    report = {"model_inference": False, "synthetic_workers": True, "steps": []}
    process = mcp = None
    def until(check, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = check()
            if value: return value
            time.sleep(.1)
        raise TimeoutError("Isolation regression timed out")
    def step(message):
        report["steps"].append(message)
        (folder / "report.json").write_text(json.dumps(report, indent=2))
        print(message, flush=True)
    try:
        with (folder / "app.log").open("wb") as log, (folder / "mcp.log").open("wb") as bridge:
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
            studio = lambda: menu("genstudio.studio.inspect")
            jobs = lambda: mcp.run("jobs.list")["jobs"]
            done = lambda job: next((j for j in jobs() if j["id"] == job and j["state"] not in ("running", "queued")), None)
            control("ui.resize", {"width": 1440, "height": 900})
            menu("genstudio.open")
            mcp.run("genstudio.engine.start")
            status = mcp.await_engine(); assert status["status"]["ready"], status
            first = status["status"]["workerUrl"]
            assert not first.endswith(":8767")
            step("Private generation host and owned Direct worker ready")
            mcp.run("file.new", {"width": 6000, "height": 4000, "background": "white"})
            mcp.run("select.subject")
            menu("genstudio.studio.reference.selection")
            until(lambda: studio().get("references") and not studio().get("loading"))
            for _ in range(10):
                before = time.monotonic(); control("ui.inspect")
                assert time.monotonic() - before < 3
            step("Irregular AI selection on 24MP document captured; UI responsive without typing")
            # Model switching must replace only the generation worker.
            mcp.run("genstudio.engine.configure", {**settings, "model": FLUX}); mcp.await_engine()
            mcp.run("genstudio.engine.start"); status = mcp.await_engine()
            assert status["status"]["ready"], status
            second = status["status"]["workerUrl"]
            assert second != first and http_json(second + "/health")["model"] == FLUX
            step("Qwen to FLUX switches the owned worker and port")
            (runtime / "block-generation").touch()
            menu("genstudio.studio.configure", {"prompt": "Synthetic image, no inference", "resolution": 1024, "batch": 1})
            gen = control("ui.menu.invoke", {"id": "genstudio.studio.submit", "params": {}, "wait": False})["job"]
            until(lambda: (runtime / "generation-started").exists())
            # Use another document: concurrent writes to the same document are
            # correctly rejected by PhotoCraft's own history consistency guard.
            mcp.run("file.new", {"width": 800, "height": 600, "background": "white"})
            select = control("engine.execute", {"command": "select.subject", "params": {}, "wait": False})["job"]
            until(lambda: any(j["id"] == select and "Waiting for generation" in j.get("message", "") for j in jobs()))
            control("ui.inspect")
            (runtime / "block-generation").unlink()
            assert until(lambda: done(gen))["state"] == "done"
            result = until(lambda: done(select)); assert result["state"] == "done", result
            step("Selection waits for generation GPU lease, then completes independently")
            menu("genstudio.studio.reference.selection")
            until(lambda: studio().get("references") and not studio().get("loading"))
            x, y, w, h = studio()["promptRect"]
            control("ui.click", {"x": x + w / 2, "y": y + 20})
            control("ui.type", {"text": "A simple English prompt"})
            assert "English prompt" in studio()["prompt"]
            for block in mcp.tool("ui_screenshot", {"max_side": 1600})["content"]:
                if block["type"] == "image": (folder / "responsive-studio.png").write_bytes(base64.b64decode(block["data"]))
            step("Use selection and prompt editing remain responsive after queued jobs")
            report["passed"] = True
    finally:
        (runtime / "block-generation").unlink(missing_ok=True)
        if mcp:
            try:
                mcp.run("genstudio.engine.stop"); mcp.await_engine()
                for _ in range(2): mcp.run("file.close", {"discard": True})
                mcp.tool("control_call", {"method": "app.quit", "params": {}})
            except Exception: pass
            mcp.process.terminate(); mcp.process.wait(timeout=5)
        if process:
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.terminate(); process.wait(timeout=10)
        if os.name == "nt":
            # Verify process paths rather than trusting a saved PID, which could
            # have been reused after a failed test. Includes orphan fixture workers.
            cleanup_env = os.environ.copy()
            cleanup_env["GENGATE_TEST_RUNTIME"] = str(runtime.resolve())
            cleanup = "Get-CimInstance Win32_Process -Filter \"Name LIKE 'python%'\" | " \
                      "Where-Object { ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($env:GENGATE_TEST_RUNTIME + '\\')) -or " \
                      "($_.CommandLine -and $_.CommandLine.Contains($env:GENGATE_TEST_RUNTIME + '\\')) } | " \
                      "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
            subprocess.run(["powershell.exe", "-NoProfile", "-Command", cleanup], env=cleanup_env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
        (folder / "report.json").write_text(json.dumps(report, indent=2))
        print(str(folder), flush=True)


if __name__ == "__main__": main()
