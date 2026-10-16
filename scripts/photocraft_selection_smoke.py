"""Selection AI UX through official MCP and a synthetic backend; never loads a model."""
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
sys.path.insert(0,str(ROOT / "scripts"))
from PIL import Image
from genfill.comfy import Cancelled
from genstudio.service import create_server
from genstudio.settings import defaults
from photocraft_engine_smoke import MCP
from photocraft_remove_smoke import SyntheticHost


class Host(SyntheticHost):
    def __init__(self,settings):
        super().__init__(settings)
        self.started=threading.Event();self.release=threading.Event()
    def generate(self,image,mask,seed,prompt,cancel,progress):
        with self.lock:
            self.busy=True
            self.calls.append({"size":image.size,"prompt":prompt,"mask":mask.getbbox()})
            number=len(self.calls)
        try:
            if number==1:
                progress("Starting the synthetic AI engine…");self.started.set()
                deadline=time.monotonic()+30
                while not self.release.wait(0.04):
                    if cancel.is_set():raise Cancelled()
                    if time.monotonic()>deadline:raise RuntimeError("synthetic gate timed out")
            for step in range(1,9):
                if cancel.is_set():raise Cancelled()
                progress(f"Generating · {step}/8");cancel.wait(0.04)
            color=(230,190,20,255) if "yellow" in prompt.lower() else (230,30,40,255) if "red" in prompt.lower() else (20,200,40,255)
            return Image.new("RGBA",image.size,color)
        finally:
            with self.lock:self.busy=False


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe",type=Path,default=ROOT/".runtime/photocraft-source/target/release/photocraft.exe")
    parser.add_argument("--runtime-root",type=Path,default=ROOT)
    args=parser.parse_args()
    metadata={"executable":str(args.exe),"sha256":hashlib.sha256(args.exe.read_bytes()).hexdigest()}
    folder=ROOT/".runtime/photocraft-selection-smoke";folder.mkdir(parents=True,exist_ok=True)
    profile=folder/f"profile-{time.time_ns()}";profile.mkdir()
    settings={**defaults(),"engine":"direct","comfyStartup":"off","directStartup":"off","runtimeRoot":str(args.runtime_root.resolve())}
    host=Host(settings);server=create_server(settings,port=0,host=host)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    (profile/"preferences.json").write_text(json.dumps({"genStudio":settings,"interface":{"language":"en"},"dialogs":{"photoshopShortcuts":{"imported":False}}}),encoding="utf-8")
    token=profile/"control.token"
    env=os.environ.copy();env["PHOTOCRAFT_CONFIG_DIR"]=str(profile)
    env["GENSTUDIO_SERVICE_URL"]=f"http://127.0.0.1:{server.server_port}";env.pop("GENSTUDIO_NO_PROCESS",None)
    with socket.socket() as sock:sock.bind(("127.0.0.1",0));port=sock.getsockname()[1]
    process=mcp=None
    report={"model_inference":False,"synthetic_backend":True,"installed":metadata["executable"],"sha256":metadata["sha256"]}
    def until(predicate,timeout=15):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if predicate():return
            time.sleep(0.08)
        raise TimeoutError("synthetic UI condition did not arrive")
    try:
        with patch("genstudio.service.save",lambda _:None),open(folder/"app.log","wb") as log,open(folder/"mcp.log","wb") as bridge:
            process=subprocess.Popen([metadata["executable"],"--control",str(port),"--control-token-file",str(token),
                "--automation-read-root",str(folder),"--automation-write-root",str(folder)],env=env,stdout=log,stderr=log,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            def listening():
                if process.poll() is not None:raise RuntimeError("PhotoCraft exited; see app.log")
                if not token.exists():return False
                try:
                    with socket.create_connection(("127.0.0.1",port),timeout=1):return True
                except OSError:return False
            until(listening,60)
            mcp=MCP([str(Path(os.environ.get("PHOTOCRAFT_CLI", ROOT / ".runtime/photocraft-source/target/release/photocraft-cli.exe"))),"mcp","--bridge",f"127.0.0.1:{port}","--control-token-file",str(token)],bridge)
            control=lambda method,params=None:mcp.tool("control_call",{"method":method,"params":params or {}})
            inspect=lambda:mcp.tool("ui_inspect")
            menu=lambda command,params=None:control("ui.menu.invoke",{"id":command,"params":params or {}})
            queue=lambda:mcp.run("genstudio.fill.status")["fills"]
            sample=lambda x,y:mcp.run("document.sampleColor",{"x":x,"y":y})
            gallery=lambda layer:mcp.run("genstudio.ai.inspect",{"layer":layer})["gallery"]
            def screenshot(name):
                for block in mcp.tool("ui_screenshot",{"max_side":1600})["content"]:
                    if block["type"]=="image":(folder/name).write_bytes(base64.b64decode(block["data"]))
            def click(rect):
                x,y,w,h=rect;control("ui.click",{"x":x+w/2,"y":y+h/2});time.sleep(0.15)
            def type_in(rect,text):
                click(rect);control("ui.key",{"key":"A","ctrl":True});control("ui.type",{"text":text});time.sleep(0.15)
            control("ui.resize",{"width":1440,"height":900})
            menu("genstudio.open")
            until(lambda:(mcp.run("genstudio.engine.status")["status"] or {}).get("ready") is True)
            screenshot("engine-ready.png")
            report["idle_status"]=mcp.run("genstudio.engine.status")
            menu("genstudio.open",{"open":False})
            mcp.run("file.new",{"width":384,"height":256,"background":"white"})
            mcp.run("edit.fill",{"color":"#2266dd"})
            control("ui.set",{"tool":"RectMarquee","fit":True})
            try:menu("edit.aiFill")
            except RuntimeError:pass
            else:raise AssertionError("Ai Fill is disabled without a selection")
            mcp.run("select.rect",{"x":120,"y":80,"width":40,"height":32})
            until(lambda:inspect()["aiFillLayer"]["bar"] is not None)
            screenshot("selection-bar.png")
            assert not inspect()["genStudio"]["hide_ai_bar"]
            menu("genstudio.ai.bar.set",{"enabled":False})
            until(lambda:inspect()["aiFillLayer"]["bar"] is None)
            created=menu("edit.aiFill");layer=created["layer"]
            assert not host.calls and not gallery(layer)["variants"],"menu creates an empty masked layer without inference"
            props=lambda:inspect()["aiFillLayer"]["properties"] or {}
            until(lambda:props().get("layer")==layer)
            type_in(props()["promptRect"],"A red boat")
            until(lambda:props()["prompt"]=="A red boat")
            click(props()["generateRect"])
            assert host.started.wait(10)
            until(lambda:len(inspect()["aiFillOverlays"])==1)
            screenshot("selection-generating.png")
            type_in(props()["promptRect"],"A green boat")
            host.release.set();until(lambda:not queue())
            assert props()["prompt"]=="A green boat","completion preserves the next prompt being typed"
            assert gallery(layer)["variants"][0]["prompt"]=="A red boat"
            assert sample(130,90)[0]>0.8 and sample(10,10)[2]>0.8
            click(props()["generateRect"]);until(lambda:len(gallery(layer)["variants"])==2 and not queue())
            assert sample(130,90)[1]>0.7
            # Properties is an ordinary scrollable dock. Enlarge its native group
            # as a user can with the splitter before clicking gallery thumbnails.
            control("ui.set",{"dock":{"heights":{"properties":320}}})
            time.sleep(0.3)
            screenshot("properties-before-select.png")
            click(props()["variants"][0]["rect"])
            until(lambda:props()["prompt"]=="A red boat")
            assert gallery(layer)["active"]==0 and sample(130,90)[0]>0.8
            screenshot("properties-variants.png")
            mcp.run("edit.undo");until(lambda:props()["prompt"]=="A green boat")
            assert gallery(layer)["active"]==1 and sample(130,90)[1]>0.7
            mcp.run("edit.redo");until(lambda:props()["prompt"]=="A red boat")
            report["gallery"]=gallery(layer)
            control("app.save",{"path":"gallery.pcraft"})
            mcp.run("file.close",{"discard":True});control("app.open",{"path":"gallery.pcraft"})
            layers=mcp.run("document.inspect")["layers"]
            layer=next(item["id"] for item in layers if item["name"].startswith("Ai Fill"))
            mcp.run("layer.select",{"layer":layer})
            until(lambda:props().get("layer")==layer and len(props()["variants"])==2)
            assert gallery(layer)["active"]==0 and props()["prompt"]=="A red boat"
            report["native_roundtrip"]=True
            background=next(item["id"] for item in layers if not item["name"].startswith("Ai Fill"))
            mcp.run("layer.select",{"layer":background});menu("genstudio.ai.bar.set",{"enabled":True})
            mcp.run("select.rect",{"x":240,"y":170,"width":40,"height":30})
            until(lambda:inspect()["aiFillLayer"]["bar"] is not None)
            bar=lambda:inspect()["aiFillLayer"]["bar"]
            type_in(bar()["promptRect"],"A yellow tree")
            click(bar()["generateRect"]);until(lambda:len(host.calls)==3 and not queue())
            assert len(mcp.run("document.inspect")["layers"])==3
            pixel=sample(250,180);assert pixel[0]>0.8 and pixel[1]>0.65 and pixel[2]<0.2
            screenshot("floating-result.png")
            mcp.run("file.close",{"discard":True})
            mcp.run("file.new",{"width":6000,"height":4000,"background":"white"})
            mcp.run("select.rect",{"x":3000,"y":2000,"width":20,"height":30})
            started=time.monotonic();layer=menu("edit.aiFill")["layer"]
            mcp.run("genstudio.ai.generate",{"layer":layer,"prompt":"A yellow tree"})
            until(lambda:len(host.calls)==4 and not queue())
            report["24mp_synthetic"]={"seconds":time.monotonic()-started,"model_input":host.calls[-1]["size"]}
            assert min(host.calls[-1]["size"])>500 and max(host.calls[-1]["size"])<=768
            report["requests"]=host.calls
            assert all("Do not replace, redraw or recreate" not in item["prompt"] for item in host.calls)
            report["passed"]=True
            mcp.run("file.close",{"discard":True});control("app.quit")
    finally:
        host.release.set()
        if mcp:mcp.process.terminate()
        if process:
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=10)
        server.shutdown();server.manager.shutdown();server.server_close()
        (folder/"report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps({"passed":report.get("passed",False),"report":str(folder/"report.json")}))

if __name__=="__main__":main()
