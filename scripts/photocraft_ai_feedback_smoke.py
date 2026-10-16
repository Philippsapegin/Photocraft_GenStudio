"""Official MCP + isolated synthetic server: startup, persistent masks and AI queue UX."""
from __future__ import annotations
import base64
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PIL import Image
from genstudio.service import create_server
from genstudio.settings import defaults
from genfill.comfy import Cancelled
from photocraft_engine_smoke import MCP
from photocraft_remove_smoke import SyntheticHost


class Host(SyntheticHost):
    def __init__(self,settings):
        super().__init__(settings)
        self.control_started=threading.Event()
        self.control_release=threading.Event()
        self.generation_started=threading.Event()
        self.begin_steps=threading.Event()
        self.step_two=threading.Event()
        self.finish_first=threading.Event()

    def ensure(self,*args,**kwargs):
        self.control_started.set()
        if not self.control_release.wait(15): raise RuntimeError("synthetic control gate timed out")
        return self.status()

    def generate(self,image,mask,seed,prompt,cancel,progress):
        with self.lock:
            self.busy=True
            self.calls.append({"size":image.size,"mask":mask.getbbox(),"prompt":prompt,
                               "corner_alpha":image.getpixel((0,0))[3]})
            number=len(self.calls)
        def wait(event):
            deadline=time.monotonic()+30
            while not event.wait(0.04):
                if cancel.is_set(): raise Cancelled()
                if time.monotonic()>deadline: raise RuntimeError("synthetic generation gate timed out")
        try:
            if number==1:
                progress("Starting the synthetic AI engine…")
                self.generation_started.set()
                wait(self.begin_steps)
            for step in range(1,9):
                if cancel.is_set(): raise Cancelled()
                progress(f"Generating · {step}/8")
                if number==1 and step==2:
                    self.step_two.set()
                    wait(self.finish_first)
                cancel.wait(0.06)
            return Image.new("RGBA",image.size,(20,180,40,255))
        finally:
            with self.lock: self.busy=False


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe",type=Path,default=ROOT/".runtime/photocraft-source/target/release/photocraft.exe")
    args=parser.parse_args()
    metadata={"executable":str(args.exe),"sha256":hashlib.sha256(args.exe.read_bytes()).hexdigest()}
    folder=ROOT/".runtime/photocraft-ai-feedback-smoke"
    folder.mkdir(parents=True,exist_ok=True)
    profile=folder/f"profile-{time.time_ns()}"
    profile.mkdir()
    settings={**defaults(),"engine":"direct","comfyStartup":"off","directStartup":"off"}
    host=Host(settings)
    server=create_server(settings,port=0,host=host)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    (profile/"preferences.json").write_text(json.dumps({"genStudio":settings,"interface":{"language":"en"},
        "dialogs":{"photoshopShortcuts":{"imported":False}}}),encoding="utf-8")
    token=folder/"control.token"
    env=os.environ.copy()
    env["PHOTOCRAFT_CONFIG_DIR"]=str(profile)
    env["GENSTUDIO_SERVICE_URL"]=f"http://127.0.0.1:{server.server_port}"
    env.pop("GENSTUDIO_NO_PROCESS",None)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0)); port=sock.getsockname()[1]
    process=mcp=None
    report={"model_inference":False,"synthetic_backend":True,"installed":metadata["executable"],"sha256":metadata["sha256"]}
    def until(predicate,timeout=15):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if predicate(): return
            time.sleep(0.08)
        raise TimeoutError("synthetic UI condition did not arrive")
    try:
        with patch("genstudio.service.save",lambda _:None),open(folder/"app.log","wb") as log,open(folder/"mcp.log","wb") as bridge:
            process=subprocess.Popen([metadata["executable"],"--control",str(port),"--control-token-file",str(token),
                "--automation-read-root",str(folder),"--automation-write-root",str(folder)],env=env,
                stdout=log,stderr=log,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            def listening():
                if process.poll() is not None: raise RuntimeError("PhotoCraft exited; see app.log")
                if not token.exists(): return False
                try:
                    with socket.create_connection(("127.0.0.1",port),timeout=1): return True
                except OSError: return False
            until(listening,60)
            mcp=MCP([str(Path(os.environ.get("PHOTOCRAFT_CLI", ROOT / ".runtime/photocraft-source/target/release/photocraft-cli.exe"))),"mcp","--bridge",f"127.0.0.1:{port}","--control-token-file",str(token)],bridge)
            control=lambda method,params=None:mcp.tool("control_call",{"method":method,"params":params or {}})
            inspect=lambda:mcp.tool("ui_inspect")
            menu=lambda command,params=None:control("ui.menu.invoke",{"id":command,"params":params or {}})
            queue=lambda:mcp.run("genstudio.fill.status")["fills"]
            def screenshot(name):
                for block in mcp.tool("ui_screenshot",{"max_side":1600})["content"]:
                    if block["type"]=="image": (folder/name).write_bytes(base64.b64decode(block["data"]))
            def click(x,y):
                control("ui.click",{"x":x,"y":y}); time.sleep(0.18)
            def stroke(x,y=100):
                control("ui.pointer",{"events":[{"kind":"down","x":x,"y":y},
                    {"kind":"move","x":x,"y":y+30},{"kind":"up","x":x,"y":y+30}]})
            control("ui.resize",{"width":1440,"height":900})
            menu("genstudio.open")
            mcp.run("genstudio.engine.start")
            assert host.control_started.wait(10)
            until(lambda: mcp.run("genstudio.engine.status")["pending"])
            screenshot("studio-starting.png")
            report["startup_pending"]=mcp.run("genstudio.engine.status")
            host.control_release.set()
            until(lambda:not mcp.run("genstudio.engine.status")["pending"])
            menu("genstudio.open",{"open":False})
            assert not inspect()["genStudio"]["open"]
            mcp.run("file.new",{"width":384,"height":256,"background":"white"})
            mcp.run("edit.fill",{"color":"#2266dd"})
            control("ui.set",{"tool":"Remove","brushSize":16,"fit":True})
            screenshot("toolbar-before.png")
            # Native toolbar order is also checked by the egui accessibility test.
            click(668,50)
            assert inspect()["toolOptions"]["remove_ai_fill"],"Ai Fill must be the final toolbar checkbox"
            sample=lambda x,y:mcp.run("document.sampleColor",{"x":x,"y":y})
            original=sample(210,110)
            stroke(150)
            assert host.generation_started.wait(10)
            stroke(210);stroke(270)
            until(lambda:len(queue())==3)
            fills=queue()
            assert [fill["state"] for fill in fills]==["running","queued","queued"]
            assert len(host.calls)==1
            report["queued"]=fills
            until(lambda:len(inspect()["aiFillOverlays"])==3)
            overlays=inspect()["aiFillOverlays"]
            assert all(item["progressBar"][1]>=item["maskBounds"][1]+item["maskBounds"][3] for item in overlays),"nearby masks keep their bars underneath"
            report["queued_overlays"]=overlays
            screenshot("queue-starting-synthetic.png")
            second=fills[1]["id"]
            overlay=next(item for item in inspect()["aiFillOverlays"] if item["id"]==second)
            x,y,w,h=overlay["cancelButton"]
            click(x+w/2,y+h/2)
            until(lambda:len(queue())==2)
            assert all(fill["id"]!=second for fill in queue()) and len(host.calls)==1
            report["cancelled_pending"]=second
            menu("genstudio.progress.set",{"enabled":False})
            until(lambda:all(item["progressBar"] is None for item in inspect()["aiFillOverlays"]))
            overlays=inspect()["aiFillOverlays"]
            assert len(overlays)==2 and any(item["cancelButton"] is not None for item in overlays)
            report["progress_hidden_overlays"]=overlays
            screenshot("progress-hidden-synthetic.png")
            menu("genstudio.progress.set",{"enabled":True})
            host.begin_steps.set()
            assert host.step_two.wait(5)
            until(lambda:any(0.3<fill["progress"]<0.5 for fill in queue() if fill["state"]=="running"))
            report["sampling"]=queue()
            screenshot("sampling-progress-synthetic.png")
            host.finish_first.set()
            until(lambda:not queue())
            assert len(host.calls)==2
            assert sample(150,110)[1]>0.65 and sample(270,110)[1]>0.65
            assert sample(210,110)==original,"a cancelled queued request must never paint"
            assert not inspect()["aiFillOverlays"]
            mcp.run("edit.undo"); assert sample(270,110)==original
            mcp.run("edit.undo"); assert sample(150,110)==original
            # All existing checkboxes keep their native semantics in the queue.
            click(238,50);click(367,50)
            options=inspect()["toolOptions"]
            assert options["sample_all_layers"] and options["remove_new_layer"] and options["remove_after_each_stroke"]
            stroke(150);stroke(270)
            until(lambda:len(host.calls)==4 and not queue())
            layers=mcp.run("document.inspect")["layers"]
            assert len(layers)==3 and layers[0]["hasMask"] and layers[1]["hasMask"]
            assert layers[0]["id"]>layers[1]["id"]
            assert host.calls[-1]["corner_alpha"]==255
            click(501,50)
            assert not inspect()["toolOptions"]["remove_after_each_stroke"]
            stroke(180,180);stroke(240,180)
            assert not queue() and len(host.calls)==4
            control("ui.key",{"key":"Enter"})
            until(lambda:len(host.calls)==5 and not queue())
            assert sample(180,190)[1]>0.65 and sample(240,190)[1]>0.65
            assert len(mcp.run("document.inspect")["layers"])==4
            mcp.run("edit.undo")
            assert len(mcp.run("document.inspect")["layers"])==3
            report["all_checkboxes"]=True
            report["requests"]=host.calls
            assert all("same scene without the masked object" in item["prompt"].lower() for item in host.calls)
            assert all("similar version" in item["prompt"] for item in host.calls)
            screenshot("complete-synthetic.png")
            mcp.run("file.close",{"discard":True})
            mcp.run("file.new",{"width":6000,"height":4000,"background":"white"})
            started=time.monotonic()
            mcp.run("genstudio.fill.enqueue",{"command":"paint.remove","params":{
                "points":[[3000,2000],[3000,2030]],"size":20,"newLayer":False}})
            until(lambda:len(host.calls)==6 and not queue())
            elapsed=time.monotonic()-started
            pixel=sample(3000,2010)
            assert pixel[0]<0.2 and pixel[1]>0.65 and pixel[2]<0.3
            assert max(host.calls[-1]["size"])<=768
            report["24mp_synthetic"]={"seconds":elapsed,"model_input":host.calls[-1]["size"],
                "note":"Queue, crop, transport and synthetic steps; no model inference."}
            report["passed"]=True
            mcp.run("file.close",{"discard":True})
            control("app.quit")
    finally:
        host.control_release.set();host.begin_steps.set();host.finish_first.set()
        if mcp: mcp.process.terminate()
        if process:
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.terminate();process.wait(timeout=10)
        server.shutdown();server.manager.shutdown();server.server_close()
        (folder/"report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps({"passed":report.get("passed",False),"report":str(folder/"report.json")}))


if __name__=="__main__": main()
