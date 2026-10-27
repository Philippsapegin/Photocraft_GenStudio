"""Opt-in CPU-only real Comfy IN/OUT check. No checkpoint or inference nodes."""
from __future__ import annotations
import argparse
import base64
from io import BytesIO
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PIL import Image
from genfill.image_ops import png_bytes
from genstudio.comfy_workflow import install_nodes, read_workflow
from genstudio.lifecycle import EngineHost, http_json
from genstudio.settings import defaults
from genstudio.studio import request


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--comfy',type=Path,required=True)
    parser.add_argument('--python',type=Path,required=True)
    args=parser.parse_args()
    installed=install_nodes(str(args.comfy))
    folder=ROOT/'.runtime'/('comfy-workflow-cpu-'+str(time.time_ns()))
    folder.mkdir()
    for name in ('input','output','temp','user','models'):(folder/name).mkdir()
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
    base='http://127.0.0.1:'+str(port)
    env={**os.environ,'CUDA_VISIBLE_DEVICES':'','PYTHONUTF8':'1'}
    for key in ('PYTHONHOME','PYTHONPATH'):env.pop(key,None)
    command=[str(args.python.resolve()),'-X','utf8',str(args.comfy.resolve()/'main.py'),'--cpu','--listen','127.0.0.1','--port',str(port),
             '--disable-auto-launch','--disable-api-nodes','--disable-all-custom-nodes','--whitelist-custom-nodes','GenGate',
             '--preview-method','none','--input-directory',str(folder/'input'),'--output-directory',str(folder/'output'),
             '--temp-directory',str(folder/'temp'),'--user-directory',str(folder/'user'),
             '--database-url','sqlite:///'+str(folder/'comfy.db').replace('\\','/')]
    report={'passed':False,'real_inference':False,'cpu_only':True,'folder':str(folder),'node_install':installed,'checks':[]}
    process=None
    def check(value,message):
        if not value:raise AssertionError(message)
        report['checks'].append(message);print(message,flush=True)
    try:
        with (folder/'comfy.log').open('w',encoding='utf-8') as log:
            process=subprocess.Popen(command,cwd=args.comfy,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            deadline=time.monotonic()+120
            while time.monotonic()<deadline:
                if process.poll() is not None:raise RuntimeError('CPU Comfy stopped: '+(folder/'comfy.log').read_text(errors='replace')[-3500:])
                try:
                    if http_json(base+'/system_stats').get('system'):break
                except OSError:pass
                time.sleep(.25)
            else:raise TimeoutError('CPU Comfy startup timed out')
            graph={'in':{'class_type':'GenGateIN2','inputs':{}},
                   'invert':{'class_type':'ImageInvert','inputs':{'image':['in',0]}},
                   'out':{'class_type':'GenGateOUT','inputs':{'images':['invert',0],'mask':['in',1],'prompt':['in',2]}}}
            path=folder/'invert-api.json';path.write_text(json.dumps(graph),encoding='utf-8')
            settings={**defaults(),'engine':'comfy','model':'fibo-2-edit-turbo','studioMode':'workflow','studioWorkflow':str(path),
                      'comfyUrl':base,'comfyDir':str(args.comfy.resolve()),'comfyPython':str(args.python.resolve()),
                      'modelsDir':str(folder/'models'),'comfyStartup':'off'}
            host=EngineHost(settings)
            try:
                host.ensure('request')
            except ValueError as error:
                check('missing' in str(error).lower(),'Quick editing still validates its native preset independently of Studio workflows')
            else:raise AssertionError('Quick editing must require the missing FIBO preset components')
            def run(color,prompt,definition=None):
                source=Image.new('RGBA',(31,19),color)
                data=request({'prompt':prompt,'references':[base64.b64encode(png_bytes(source)).decode()],'batch':3},settings['model'],workflow=True)
                data['workflowPath']=str(path)
                return host.generate_workflow(definition or read_workflow(str(path)),data,threading.Event(),lambda m:None)
            first=run((10,30,60,128),'First')
            check(len(first['images'])==1,'Graph result count overrides the Studio batch request')
            row=first['images'][0]
            with Image.open(BytesIO(base64.b64decode(row['image']))) as image:
                check(image.size==(31,19) and image.convert('RGB').getpixel((0,0))==(245,225,195),'Actual IN -> ImageInvert -> OUT preserves dimensions and changes the expected pixels')
            with Image.open(BytesIO(base64.b64decode(row['mask']))) as mask:
                check(mask.getpixel((0,0))==127,'IN alpha mask reaches OUT with white-visible mask polarity')
            second=run((255,0,0,255),'Second')
            with Image.open(BytesIO(base64.b64decode(second['images'][0]['image']))) as image:
                check(image.convert('RGB').getpixel((0,0))==(0,255,255) and second['images'][0]['prompt']=='Second','A repeated graph receives new pixels and prompt, without stale caching')
            # Text-only/multi-output/batch uses only core EmptyImage, not a model.
            graph={'in':{'class_type':'GenGateIN2','inputs':{}},
                   'blank':{'class_type':'EmptyImage','inputs':{'width':['in',3],'height':['in',4],'batch_size':['in',6],'color':123}},
                   'one':{'class_type':'GenGateOUT','inputs':{'images':['blank',0]}},
                   'two':{'class_type':'GenGateOUT','inputs':{'images':['blank',0]}}}
            assert {n['class_type'] for n in graph.values()}<={'GenGateIN2','GenGateOUT','EmptyImage'}
            path.write_text(json.dumps(graph),encoding='utf-8')
            data=request({'prompt':'','references':[],'width':256,'height':320,'batch':2},settings['model'],workflow=True);data['workflowPath']=str(path)
            result=host.generate_workflow(read_workflow(str(path)),data,threading.Event(),lambda m:None)
            check(len(result['images'])==4 and all((r['width'],r['height'])==(256,320) for r in result['images']),'Text-only input, empty prompt, graph batch and multiple OUT nodes work without preset models')
            check('WARNING: Run this workflow' not in (folder/'comfy.log').read_text(errors='replace'),'Cache probes do not emit misleading missing-context warnings')
            report['passed']=True
    finally:
        if process and process.poll() is None:
            process.terminate();process.wait(timeout=15)
        (folder/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        (ROOT/'.runtime/comfy-workflow-cpu-latest.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2),flush=True)


if __name__=='__main__':main()
