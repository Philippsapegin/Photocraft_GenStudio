"""Exercise the full Qwen workspace through official MCP, with synthetic pixels only."""
from __future__ import annotations
import argparse
import base64
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
sys.path.insert(0,str(ROOT/"scripts"))
from PIL import Image
from genfill.comfy import Cancelled
from genfill.image_ops import png_bytes
from genstudio.models import FILES
from genstudio.service import create_server
from genstudio.settings import defaults
from photocraft_engine_smoke import MCP
from photocraft_remove_smoke import SyntheticHost


class Host(SyntheticHost):
    def __init__(self,settings):
        super().__init__(settings)
        self.block=False
        self.cancelled=threading.Event()
    def generate(self,*args):
        raise AssertionError("Studio must not use the masked-edit path")
    def generate_studio(self,references,seed,prompt,width,height,cancel,progress):
        with self.lock:
            self.busy=True
            self.calls.append({"references":len(references),"seed":seed,"prompt":prompt,"size":[width,height]})
        try:
            progress("Starting the synthetic AI engine…")
            deadline=time.monotonic()+30
            while self.block:
                if cancel.wait(.03):
                    self.cancelled.set()
                    raise Cancelled()
                if time.monotonic()>deadline:raise TimeoutError("Synthetic gate")
            for step in range(1,9):
                if cancel.wait(.02):raise Cancelled()
                progress(f"Generating · {step}/8")
            return Image.new("RGBA",(width,height),(30,160,90,255))
        finally:
            with self.lock:self.busy=False


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe",type=Path,required=True)
    parser.add_argument("--cli",type=Path,required=True)
    args=parser.parse_args()
    folder=ROOT/".runtime/photocraft-studio-smoke"
    profile=folder/f"profile-{time.time_ns()}"
    profile.mkdir(parents=True)
    models=folder/"fixture-models"
    for name,folders in FILES.values():
        path=models/folders[0]/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(b"not a model; discovery fixture only")
    settings={**defaults(),"runtimeRoot":str(ROOT),"modelsDir":str(models),"engine":"direct",
              "directStartup":"off","comfyStartup":"off"}
    host=Host(settings)
    server=create_server(settings,port=0,host=host)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    (profile/"preferences.json").write_text(json.dumps({"genStudio":settings,"interface":{"language":"en"},
        "dialogs":{"photoshopShortcuts":{"imported":False}}}),encoding="utf-8")
    env=os.environ.copy()
    env.update(PHOTOCRAFT_CONFIG_DIR=str(profile),GENSTUDIO_SERVICE_URL=f"http://127.0.0.1:{server.server_port}",CUDA_VISIBLE_DEVICES="")
    env.pop("GENSTUDIO_NO_PROCESS",None)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0));port=sock.getsockname()[1]
    token=profile/"control.token"
    with args.exe.open("rb") as binary:digest=hashlib.file_digest(binary,"sha256").hexdigest()
    report={"model_inference":False,"synthetic_backend":True,"exe":str(args.exe),"sha256":digest}
    process=mcp=None
    def until(predicate,timeout=40):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            result=predicate()
            if result:return result
            time.sleep(.08)
        raise TimeoutError("Studio UI condition did not arrive")
    try:
        with patch("genstudio.service.save",lambda _:None),open(folder/"app.log","wb") as log,open(folder/"mcp.log","wb") as bridge:
            process=subprocess.Popen([str(args.exe),"--control",str(port),"--control-token-file",str(token),
                "--automation-read-root",str(folder),"--automation-write-root",str(folder)],env=env,
                stdout=log,stderr=log,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            def listening():
                if process.poll() is not None:raise RuntimeError("PhotoCraft exited; see app.log")
                if not token.exists():return False
                try:
                    with socket.create_connection(("127.0.0.1",port),timeout=1):return True
                except OSError:return False
            until(listening,60)
            mcp=MCP([str(args.cli),"mcp","--bridge",f"127.0.0.1:{port}","--control-token-file",str(token)],bridge)
            control=lambda method,params=None:mcp.tool("control_call",{"method":method,"params":params or {}})
            menu=lambda name,params=None:control("ui.menu.invoke",{"id":name,"params":params or {}})
            submit=lambda:control("ui.menu.invoke",{"id":"genstudio.studio.submit","params":{},"wait":False})
            inspect=lambda:mcp.tool("ui_inspect")
            studio=lambda:menu("genstudio.studio.inspect")
            jobs=lambda:mcp.run("jobs.list")["jobs"]
            idle=lambda:not any(job["command"]=="genstudio.studio.generate" and job["state"]=="running" for job in jobs())
            def shot(name):
                for block in mcp.tool("ui_screenshot",{"max_side":1600})["content"]:
                    if block["type"]=="image":(folder/name).write_bytes(base64.b64decode(block["data"]))
            control("ui.resize",{"width":1440,"height":900})
            menu("genstudio.open")
            until(lambda:studio().get("slots"))
            state=inspect()["genStudio"]
            assert not state["settings_open"] and not state["advanced_open"]
            assert len(studio()["slots"])==2 and not studio()["references"]
            menu("genstudio.studio.configure",{"prompt":"A quiet forest at sunrise","batch":2})
            shot("studio-text-only.png")
            job=submit()["job"]
            until(idle,60)
            finished=jobs()
            complete=next(item for item in finished if item["id"]==job)
            assert complete["state"]=="done",complete
            assert complete["result"]["count"]==2,complete
            assert len(inspect()["session"]["documents"])==2
            assert all(doc["dirty"] and doc["path"] is None for doc in inspect()["session"]["documents"])
            assert len(host.calls)==2 and all(call["references"]==0 for call in host.calls)
            report["text_only_batch"]=complete
            menu("genstudio.open",{"open":False})
            mcp.run("file.new",{"width":384,"height":256,"background":"white"})
            mcp.run("edit.fill",{"color":"#2266dd"})
            mcp.run("select.rect",{"x":80,"y":60,"width":96,"height":64})
            menu("genstudio.open")
            until(lambda:len(studio()["references"])==1 and not studio()["loading"])
            assert studio()["references"][0]["name"]=="Selection"
            assert inspect()["aiFillLayer"]["bar"] is None,"selection controls must not cover the Studio window"
            image=base64.b64encode(png_bytes(Image.new("RGBA",(96,96),(220,140,30,255)))).decode()
            for index in range(9):
                menu("genstudio.studio.reference.set",{"slot":index,"image":image,"name":f"Fixture {index+1}"})
                until(lambda:len(studio()["references"])==index+2 and not studio()["loading"])
            assert len(studio()["slots"])==10
            menu("genstudio.studio.configure",{"prompt":"Use [Image 1] for composition and [Image 10] for color.","batch":1,"monospace":False})
            shot("studio-ten-references.png")
            menu("genstudio.studio.reference.remove",{"slot":0})
            until(lambda:len(studio()["references"])==9 and "[Image 9]" in studio()["prompt"])
            assert "[Image 10]" not in studio()["prompt"]
            menu("genstudio.studio.configure",{"resolution":2048,"monospace":True})
            shot("studio-monospace.png")
            job=submit()["job"]
            until(idle,60)
            complete=next(item for item in jobs() if item["id"]==job)
            assert complete["state"]=="done",complete
            assert host.calls[-1]["references"]==9 and host.calls[-1]["size"]==[2048,1376],host.calls
            assert host.calls[-1]["prompt"]=="Use image 1 for composition and image 9 for color."
            report["selection_and_references"]=host.calls[-1]
            menu("genstudio.studio.configure",{"settingsOpen":True,"advancedOpen":True})
            until(lambda:bool(mcp.run("genstudio.engine.status")["models"].get("defaultTextEncoder")))
            report["catalog"]=mcp.run("genstudio.engine.status")["models"]
            time.sleep(.4)
            shot("studio-settings.png")
            menu("genstudio.studio.configure",{"settingsOpen":False,"advancedOpen":False})
            # A camera-size reference exercises bounded decoding and downscaling,
            # with no model invocation. The UI must answer while its worker loads.
            large=base64.b64encode(png_bytes(Image.new("RGBA",(6000,4000),(90,120,160,255)))).decode()
            began=time.monotonic()
            menu("genstudio.studio.reference.set",{"slot":8,"image":large,"name":"24 MP fixture"})
            ui_began=time.monotonic();inspect();ui_seconds=time.monotonic()-ui_began
            until(lambda:any(ref["name"]=="24 MP fixture" and ref["width"]==1024 for ref in studio()["references"]) and not studio()["loading"])
            report["reference_24mp"]={"seconds":time.monotonic()-began,"ui_response_seconds":ui_seconds,"prepared_size":[1024,683]}
            host.block=True
            job=submit()["job"]
            until(lambda:host.busy)
            mcp.run("jobs.cancel",{"job":job})
            until(lambda:host.cancelled.is_set(),20)
            until(idle)
            report["cancelled"]=True
            report["requests"]=host.calls
            report["passed"]=True
    finally:
        host.block=False
        if mcp:mcp.process.terminate()
        if process:
            try:process.wait(timeout=1)
            except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=10)
        server.shutdown();server.manager.shutdown();server.studio.shutdown();server.server_close()
        (folder/"report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps({"passed":report.get("passed",False),"report":str(folder/"report.json")}))


if __name__=="__main__":main()
