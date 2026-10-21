"""Official-MCP regression with a synthetic Direct worker. No torch, CUDA or model weights."""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/"scripts"))
from genstudio.settings import defaults
from genstudio.segmentation_models import WHEEL,CLIP
from photocraft_engine_smoke import MCP


def fixture(folder):
    runtime=folder/"runtime"
    runtime.mkdir()
    for name in ("genstudio","genfill"):
        shutil.copytree(ROOT/name,runtime/name,ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copyfile(ROOT/"config.example.json",runtime/"config.example.json")
    # A lightweight host interpreter only; this environment has no torch.
    shutil.copytree(ROOT/".venv",runtime/".venv",ignore=shutil.ignore_patterns("__pycache__"))
    (runtime/"scripts").mkdir()
    shutil.copyfile(ROOT/"scripts/download_flux3.py",runtime/"scripts/download_flux3.py")
    worker='''import json,sys,time
from pathlib import Path
from PIL import Image,ImageDraw
assert __import__('importlib.util',fromlist=['find_spec']).find_spec('torch') is None
p=json.loads(Path(sys.argv[1]).read_text());folder=Path(p['folder']);runtime=Path(p['modules']).parents[2]
if (runtime/'fail-worker').exists():raise RuntimeError('Synthetic worker failure')
if (runtime/'block-worker').exists():
 (runtime/'worker-started').touch()
 while True:time.sleep(.1)
image=Image.open(folder/'input.png');w,h=image.size
mask=Image.new('L',(w,h));draw=ImageDraw.Draw(mask)
if p['kind']=='sky':draw.rectangle((0,0,w-1,max(1,h//3)-1),fill=255)
else:draw.rectangle((w//4,h//4,w*3//4-1,h*3//4-1),fill=255)
mask.save(folder/'result-mask.png')
'''
    (runtime/"scripts/run_segmentation.py").write_text(worker,encoding="utf-8")
    overlay=runtime/".runtime/segmentation/deps"
    overlay.mkdir(parents=True)
    (overlay/"gengate-deps.json").write_text(json.dumps([WHEEL[1],CLIP[1]]))
    manifest={"files":[{"id":name,"path":"segmentation/"+name+".pt","bytes":1} for name in ("yolov8s-worldv2","sam3")]}
    (runtime/"photocraft").mkdir()
    (runtime/"photocraft/segmentation-models.json").write_text(json.dumps(manifest))
    for entry in manifest["files"]:
        path=folder/"models"/entry["path"]
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(b"x")
    return runtime


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe",type=Path,required=True)
    parser.add_argument("--cli",type=Path,required=True)
    args=parser.parse_args()
    folder=ROOT/".runtime"/("segmentation-smoke-"+str(time.time_ns()))
    folder.mkdir(parents=True)
    runtime=fixture(folder)
    profile=folder/"Profile";profile.mkdir()
    settings=dict(defaults(),engine="comfy",runtimeRoot=str(runtime),modelsDir=str(folder/"models"),
                  directStartup="off",comfyStartup="off",segmentation={"enabled":True,"detector":"yolov8s-worldv2","segmenter":"sam3","python":str(runtime/".venv/Scripts/python.exe")})
    (profile/"preferences.json").write_text(json.dumps({"genStudio":settings,"interface":{"language":"en"},"dialogs":{"photoshopShortcuts":{"imported":False}}}),encoding="utf-8")
    env=os.environ.copy();env.update(PHOTOCRAFT_CONFIG_DIR=str(profile),CUDA_VISIBLE_DEVICES="")
    for key in ("GENSTUDIO_NO_PROCESS","GENGATE_NO_INFERENCE"):env.pop(key,None)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0));port=sock.getsockname()[1]
    token=profile/"control.token"
    report={"model_inference":False,"synthetic_worker":True,"exe":str(args.exe),"sha256":hashlib.sha256(args.exe.read_bytes()).hexdigest()}
    process=mcp=None
    def until(predicate,timeout=60):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            result=predicate()
            if result:return result
            time.sleep(.08)
        raise TimeoutError("Segmentation smoke condition timed out")
    try:
        with (folder/"app.log").open("wb") as log,(folder/"mcp.log").open("wb") as bridge:
            process=subprocess.Popen([str(args.exe),"--control",str(port),"--control-token-file",str(token),"--automation-read-root",str(folder),"--automation-write-root",str(folder)],env=env,stdout=log,stderr=log,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            def listening():
                if process.poll() is not None:raise RuntimeError("Editor exited; see app.log")
                if not token.exists():return False
                try:
                    with socket.create_connection(("127.0.0.1",port),timeout=1):return True
                except OSError:return False
            until(listening)
            mcp=MCP([str(args.cli),"mcp","--bridge",f"127.0.0.1:{port}","--control-token-file",str(token)],bridge)
            control=lambda method,params=None:mcp.tool("control_call",{"method":method,"params":params or {}})
            inspect=lambda:mcp.run("document.inspect")
            control("ui.resize",{"width":1440,"height":900})
            # Preparing assets must work independently of the Comfy generation engine.
            assert mcp.run("gengate.segmentation.prepare")["ready"]
            for depth in (8,16,32):
                mcp.run("file.new",{"width":120,"height":80,"depth":depth,"background":"white"})
                mcp.run("select.subject")
                assert inspect()["selectionBounds"]==[30,20,60,40],inspect()
                mcp.run("edit.undo");assert not inspect()["hasSelection"]
                mcp.run("edit.redo");assert inspect()["selectionBounds"]==[30,20,60,40]
                mcp.run("select.sky");assert inspect()["selectionBounds"]==[0,0,120,26]
                mcp.run("select.object",{"rect":[10,10,40,40],"aiSelect":True})
                assert inspect()["selectionBounds"]==[20,20,20,20]
                mcp.run("select.object",{"rect":[30,10,40,40],"aiSelect":True,"mode":"add"})
                assert inspect()["selectionBounds"]==[20,20,40,20]
                mcp.run("select.object",{"rect":[10,10,40,40],"aiSelect":True,"mode":"subtract"})
                assert inspect()["selectionBounds"]==[40,20,20,20]
                mcp.run("select.object",{"rect":[30,10,40,40],"aiSelect":True,"mode":"intersect"})
                assert inspect()["selectionBounds"]==[40,20,20,20]
                control("ui.set",{"tool":"ObjectSelection","objectAiSelect":True})
                before=inspect()["selectionBounds"]
                control("ui.pointer",{"events":[{"kind":"down","x":15,"y":15},{"kind":"up","x":15,"y":15}]})
                assert inspect()["selectionBounds"]==before
                control("ui.pointer",{"events":[{"kind":"down","x":10,"y":10},{"kind":"move","x":50,"y":50},{"kind":"up","x":50,"y":50}]})
                until(lambda:not any(j["state"]=="running" for j in mcp.run("jobs.list")["jobs"]))
                assert inspect()["selectionBounds"]==[20,20,20,20]
                mcp.run("layer.removeBackground")
                assert inspect()["layers"][0]["hasMask"] and not inspect()["hasSelection"]
                mcp.run("edit.undo");assert not inspect()["layers"][0]["hasMask"]
                mcp.run("edit.redo");assert inspect()["layers"][0]["hasMask"]
            report["depths"]=[8,16,32]
            report["subject_sky_object_background_modifiers_undo"] = True
            mcp.run("select.rect",{"x":2,"y":2,"width":6,"height":6})
            before=inspect()["selectionBounds"]
            (runtime/"fail-worker").touch()
            try:
                mcp.run("select.subject")
                raise AssertionError("A failed worker must return an error")
            except RuntimeError as error:
                assert "Synthetic worker failure" in str(error),error
            assert inspect()["selectionBounds"]==before
            (runtime/"fail-worker").unlink()
            (runtime/"block-worker").touch()
            job=control("engine.execute",{"command":"select.subject","params":{},"wait":False})["job"]
            until(lambda:(runtime/"worker-started").exists())
            mcp.run("jobs.cancel",{"job":job})
            until(lambda:not any(j["state"]=="running" for j in mcp.run("jobs.list")["jobs"]))
            assert inspect()["selectionBounds"]==before
            report["cancel_and_failure_preserve_selection"]=True
            (runtime/"block-worker").unlink()
            mcp.run("file.new",{"width":6000,"height":4000,"background":"white"})
            began=time.monotonic()
            large_job=control("engine.execute",{"command":"select.subject","params":{},"wait":False})["job"]
            ui_began=time.monotonic();control("ui.inspect");ui_seconds=time.monotonic()-ui_began
            until(lambda:not any(j["id"]==large_job and j["state"]=="running" for j in mcp.run("jobs.list")["jobs"]),120)
            assert inspect()["selectionBounds"] is not None
            report["subject_24mp"]={"seconds":time.monotonic()-began,"ui_response_seconds":ui_seconds}
            for block in mcp.tool("ui_screenshot",{"max_side":1600})["content"]:
                if block["type"]=="image":(folder/"ai-select.png").write_bytes(base64.b64decode(block["data"]))
            report["passed"]=True
    finally:
        if mcp:mcp.process.terminate()
        if process:
            try:process.wait(timeout=1)
            except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=10)
        (folder/"report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
        (ROOT/".runtime/segmentation-smoke-latest.json").write_text(json.dumps({"folder":str(folder),**report},indent=2),encoding="utf-8")
    print(json.dumps({"passed":report.get("passed",False),"folder":str(folder)}))


if __name__=="__main__":main()
