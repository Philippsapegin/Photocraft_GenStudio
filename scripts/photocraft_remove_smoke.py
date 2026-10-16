"""Installed Remove tool + official MCP + synthetic HTTP backend. Never loads Qwen."""
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
sys.path.insert(0, str(ROOT))
from genstudio.settings import defaults
from genstudio.lifecycle import EngineHost
from genstudio.service import create_server
from genfill.comfy import Cancelled
from PIL import Image
from photocraft_engine_smoke import MCP


class SyntheticHost(EngineHost):
    def __init__(self, settings):
        def no_process(*args, **kwargs):
            raise AssertionError("Synthetic validation must never start an AI process")
        super().__init__(settings, spawn=no_process)
        self.calls = []
        self.block = False
        self.cancelled = threading.Event()
    def status(self):
        return {"state":"ready", "ready":True, "owned":False, "busy":self.busy,
                "message":"Synthetic validation backend", "engine":self.engine}
    def ensure(self, *args, **kwargs):
        return self.status()
    def generate(self, image, mask, seed, prompt, cancel, progress):
        with self.lock:
            self.busy = True
        try:
            self.calls.append({"size":image.size, "mask":mask.getbbox(), "prompt":prompt,
                               "alpha_extrema":image.getchannel("A").getextrema(),
                               "corner_alpha":image.getpixel((0,0))[3]})
            progress("Starting the synthetic validation backend…" if len(self.calls)==1 else "Generating synthetic validation pixels…")
            deadline=time.monotonic()+30 if self.block else time.monotonic()+0.7
            while time.monotonic()<deadline:
                if cancel.wait(0.05):
                    self.cancelled.set()
                    raise Cancelled()
            return Image.new("RGBA",image.size,(20,180,40,255))
        finally:
            with self.lock:
                self.busy=False
    def stop(self):
        return {"stopped":False, "message":"Synthetic backend stopped"}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    metadata=ROOT/".runtime/photocraft-genstudio-install.json"
    installed=json.loads(metadata.read_text(encoding="utf-8-sig"))["executable"] if metadata.exists() else str(ROOT / ".runtime/photocraft-source/target/release/photocraft.exe")
    parser.add_argument("--exe",type=Path,default=Path(installed))
    parser.add_argument("--toggle-x",type=float)
    parser.add_argument("--toggle-y",type=float,default=43)
    args=parser.parse_args()
    folder=ROOT/".runtime/photocraft-remove-smoke"
    folder.mkdir(parents=True,exist_ok=True)
    settings={**defaults(),"comfyStartup":"off","directStartup":"off"}
    host=SyntheticHost(settings)
    server=create_server(settings,port=0,host=host)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    token=folder/"control.token"
    (folder/"preferences.json").write_text(json.dumps({"genStudio":settings,"interface":{"language":"en"},
        "dialogs":{"photoshopShortcuts":{"imported":False}}}),encoding="utf-8")
    env=os.environ.copy()
    env["PHOTOCRAFT_CONFIG_DIR"]=str(folder)
    env["GENSTUDIO_SERVICE_URL"]=f"http://127.0.0.1:{server.server_port}"
    env.pop("GENSTUDIO_NO_PROCESS",None)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0))
        port=sock.getsockname()[1]
    process=mcp=None
    report={"model_inference":False,"synthetic_backend":True}
    try:
        with patch("genstudio.service.save",lambda _:None),open(folder/"app.log","wb") as log,open(folder/"mcp.log","wb") as bridge:
            process=subprocess.Popen([str(args.exe),"--control",str(port),"--control-token-file",str(token),
                "--automation-read-root",str(folder),"--automation-write-root",str(folder)],env=env,
                stdout=log,stderr=log,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            deadline=time.monotonic()+60
            while time.monotonic()<deadline:
                if process.poll() is not None: raise RuntimeError("PhotoCraft exited; see app.log")
                if token.exists():
                    try:
                        with socket.create_connection(("127.0.0.1",port),timeout=1): break
                    except OSError: pass
                time.sleep(0.2)
            mcp=MCP([str(Path(os.environ.get("PHOTOCRAFT_CLI", ROOT / ".runtime/photocraft-source/target/release/photocraft-cli.exe"))),"mcp","--bridge",f"127.0.0.1:{port}","--control-token-file",str(token)],bridge)
            control=lambda method,params=None:mcp.tool("control_call",{"method":method,"params":params or {}})
            control("ui.resize",{"width":1440,"height":900})
            mcp.run("file.new",{"width":384,"height":256,"background":"white"})
            mcp.run("edit.fill",{"color":"#2266dd"})
            control("ui.set",{"tool":"Remove","brushSize":16,"fit":True})
            if args.toggle_x:
                control("ui.click",{"x":args.toggle_x,"y":args.toggle_y})
                time.sleep(0.2)  # queued egui input is applied on the next frames
                assert mcp.tool("ui_inspect")["toolOptions"]["remove_ai_fill"]
            sample=lambda x,y:mcp.run("document.sampleColor",{"x":x,"y":y})
            original=sample(180,100)
            outside=sample(20,20)
            before=mcp.run("document.inspect")
            p={"useQwen":True,"points":[[180,100],[180,140]],"size":16}
            report["flatten_result"]=mcp.run("paint.remove",p)
            assert sample(180,110)[1]>0.65
            assert sample(20,20)==outside
            assert len(mcp.run("document.inspect")["layers"])==len(before["layers"])
            mcp.run("edit.undo")
            assert sample(180,100)==original
            if args.toggle_x:
                count=len(host.calls)
                control("ui.pointer",{"events":[{"kind":"down","x":180,"y":100},
                    {"kind":"move","x":180,"y":140}]})
                assert len(host.calls)==count,"drawing must not send a request"
                control("ui.pointer",{"events":[{"kind":"up","x":180,"y":140}]})
                deadline=time.monotonic()+15
                while time.monotonic()<deadline:
                    jobs=control("jobs.list")["jobs"]
                    if len(host.calls)>count and not any(j["state"]=="running" for j in jobs): break
                    time.sleep(0.1)
                assert len(host.calls)==count+1 and sample(180,110)[1]>0.65
                report["brush_release_via_ui"]=True
                mcp.run("edit.undo")
            report["new_layer_result"]=mcp.run("paint.remove",{**p,"newLayer":True})
            doc=mcp.run("document.inspect")
            active=next(l for l in doc["layers"] if l["id"]==doc["activeLayer"])
            assert active["hasMask"] and active["bounds"]==report["new_layer_result"]["damage"],active
            assert len(doc["layers"])==len(before["layers"])+1
            assert sample(20,20)==outside
            report["new_layer"]=active
            # The screenshot contains a green synthetic test patch, never a Qwen result.
            for block in mcp.tool("ui_screenshot",{"max_side":1600})["content"]:
                if block["type"]=="image": (folder/"remove-tool-synthetic.png").write_bytes(base64.b64decode(block["data"]))
            mcp.run("edit.undo")
            mcp.run("layer.new.layer")
            report["sample_all_result"]=mcp.run("paint.remove",{**p,"sampleAllLayers":True})
            assert host.calls[-1]["corner_alpha"]==255
            mcp.run("edit.undo")
            mcp.run("edit.undo")
            report["batch_result"]=mcp.run("paint.remove",{"useQwen":True,"size":12,"strokes":[[[180,100]],[[230,100]]]})
            assert sample(180,100)[1]>0.65 and sample(230,100)[1]>0.65
            mcp.run("edit.undo")
            host.block=True
            count=len(host.calls)
            running=control("engine.execute",{"command":"paint.remove","params":p,"wait":False})
            deadline=time.monotonic()+10
            while len(host.calls)==count and time.monotonic()<deadline: time.sleep(0.1)
            assert len(host.calls)>count
            control("jobs.cancel",{"job":running["job"]})
            assert host.cancelled.wait(10),"cancel must reach the host job"
            assert sample(180,100)==original
            report["cancellation"]=True
            report["ui"]=mcp.tool("ui_inspect")["toolOptions"]
            host.block=False
            mcp.run("file.new",{"width":6000,"height":4000,"background":"transparent"})
            started=time.monotonic()
            report["24mp_result"]=mcp.run("paint.remove",{"useQwen":True,"size":24,
                "points":[[3000,2000],[3020,2040]]})
            report["24mp_roundtrip_seconds"]=time.monotonic()-started
            report["24mp_model_input_size"]=host.calls[-1]["size"]
            assert max(host.calls[-1]["size"])<512,"send the context ROI, never the full 24MP canvas"
            mcp.run("file.close",{"discard":True})
            report["requests"]=host.calls
            assert all(call["alpha_extrema"][0]==0 for call in host.calls)
            assert all("Do not add any new objects" in call["prompt"] for call in host.calls)
            report["passed"]=True
            mcp.run("file.close",{"discard":True})
            control("app.quit")
    finally:
        if mcp: mcp.process.terminate()
        if process:
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.terminate(); process.wait(timeout=10)
        server.shutdown()
        server.manager.shutdown()
        server.server_close()
        (folder/"report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps({"passed":report.get("passed",False),"report":str(folder/"report.json")}))


if __name__=="__main__": main()
