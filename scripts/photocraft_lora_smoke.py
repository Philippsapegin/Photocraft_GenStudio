"""Official MCP regression for FIBO and LoRA UI/metadata; synthetic images only."""
from __future__ import annotations
import argparse,base64,hashlib,json,os,socket,subprocess,sys,threading,time
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
RUNTIME=Path(sys.argv[sys.argv.index('--runtime')+1]).resolve() if '--runtime' in sys.argv else ROOT
sys.path[:0]=[str(RUNTIME),str(ROOT/'scripts')]
from PIL import Image
from genstudio.models import FIBO,MODEL,MODELS
from genstudio.settings import defaults
from genstudio.service import create_server
from genfill.image_ops import png_bytes
from photocraft_engine_smoke import MCP
from photocraft_studio_smoke import Host as BaseHost


class Host(BaseHost):
    def generate_studio(self,references,seed,prompt,width,height,cancel,progress,*,loras=None):
        result=super().generate_studio(references,seed,prompt,width,height,cancel,progress)
        self.calls[-1]['loras']=loras or []
        return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe',type=Path,required=True)
    parser.add_argument('--cli',type=Path,required=True)
    parser.add_argument('--runtime',type=Path,default=ROOT)
    args=parser.parse_args()
    folder=ROOT/'.runtime'/('lora-smoke-'+str(time.time_ns()));folder.mkdir()
    models=folder/'models'
    for model in MODELS.values():
        for name,dirs in model['files'].values():
            file=models/dirs[0]/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(b'synthetic component')
    lora=models/'loras/style/portrait.safetensors';lora.parent.mkdir(parents=True);lora.write_bytes(b'not weights')
    (models/'loras/ignore.txt').write_text('not an adapter')
    settings={**defaults(),'model':MODEL,'runtimeRoot':str(args.runtime.resolve()),'modelsDir':str(models),'engine':'direct','directStartup':'off','comfyStartup':'off'}
    host=Host(settings);server=create_server(settings,port=0,host=host)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    (folder/'preferences.json').write_text(json.dumps({'genStudio':settings,'interface':{'language':'en','theme':'studio'},'dialogs':{'photoshopShortcuts':{'imported':False}}}))
    env={**os.environ,'PHOTOCRAFT_CONFIG_DIR':str(folder),'GENSTUDIO_SERVICE_URL':f'http://127.0.0.1:{server.server_port}','CUDA_VISIBLE_DEVICES':'','GENGATE_NO_INFERENCE':'1'}
    env.pop('GENSTUDIO_NO_PROCESS',None)
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    token=folder/'control.token';process=mcp=None
    report={'passed':False,'real_inference':False,'folder':str(folder),'steps':[]}
    def until(check,timeout=60):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            value=check()
            if value:return value
            time.sleep(.08)
        raise TimeoutError('LoRA regression condition did not arrive')
    def step(text):
        report['steps'].append(text);print(text,flush=True)
        (folder/'report.json').write_text(json.dumps(report,indent=2))
    try:
        with patch('genstudio.service.save',lambda _:None),(folder/'app.log').open('wb') as log,(folder/'mcp.log').open('wb') as bridge:
            process=subprocess.Popen([str(args.exe),'--control',str(port),'--control-token-file',str(token),'--automation-read-root',str(folder),'--automation-write-root',str(folder)],cwd=args.exe.parent,env=env,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
            def ready():
                assert process.poll() is None,'Editor exited'
                if not token.exists():return False
                try:
                    with socket.create_connection(('127.0.0.1',port),.2):return True
                except OSError:return False
            until(ready)
            mcp=MCP([str(args.cli),'mcp','--bridge',f'127.0.0.1:{port}','--control-token-file',str(token)],bridge)
            control=lambda method,p=None:mcp.tool('control_call',{'method':method,'params':p or {}})
            menu=lambda name,p=None:control('ui.menu.invoke',{'id':name,'params':p or {}})
            studio=lambda:menu('genstudio.studio.inspect')
            browser=lambda:studio().get('loras',{}).get('browser') or {}
            def click(rect):control('ui.click',{'x':rect[0]+rect[2]/2,'y':rect[1]+rect[3]/2})
            def screenshot(name):
                for b in mcp.tool('ui_screenshot',{'max_side':1600})['content']:
                    if b['type']=='image':(folder/name).write_bytes(base64.b64decode(b['data']))
            control('ui.resize',{'width':1600,'height':1000});menu('genstudio.open')
            until(lambda:studio().get('controls',{}).get('loras'))
            click(studio()['controls']['loras'])
            until(lambda:browser().get('rows'))
            assert abs(browser()['window']['headerRect'][3]-studio()['window']['headerRect'][3])<1
            rows=browser()['rows'];assert len(rows)==1 and rows[0]['directory']
            click(rows[0]['nameRect']);until(lambda:browser().get('folder')=='style' and browser().get('rows'))
            row=browser()['rows'][0];assert row['path']=='style/portrait.safetensors'
            click(row['checkRect']);click(row['settingsRect'])
            until(lambda:browser().get('settings'))
            assert abs(browser()['settings']['window']['headerRect'][3]-studio()['window']['headerRect'][3])<1
            assert not browser()['settings']['dirty']
            assert all(rect[2]>=300 for rect in browser()['settings']['fields']), 'Metadata fields collapsed'
            def type_field(rect,value):
                click(rect);control('ui.key',{'key':'A','ctrl':True});control('ui.type',{'text':value})
            type_field(browser()['settings']['fields'][0],'Portrait helper')
            type_field(browser()['settings']['fields'][1],'portrait, soft lighting')
            until(lambda:browser()['settings']['dirty']);click(browser()['settings']['saveRect'])
            until(lambda:not browser()['settings']['dirty'])
            assert lora.name=='portrait.safetensors' and lora.read_bytes()==b'not weights'
            menu('genstudio.loras.configure',{'path':'style/portrait.safetensors','strength':.65})
            menu('genstudio.loras.append',{'path':'style/portrait.safetensors'})
            until(lambda:studio()['prompt']=='portrait, soft lighting')
            screenshot('lora-browser-settings.png')
            widths=[]
            for _ in range(4):
                time.sleep(.12)
                widths.append(browser()['window']['rect'][2])
            assert max(widths)-min(widths)<1, ('Browser width keeps growing',widths,browser()['rows'][0])
            step('Folder navigation, alias/triggers, Save state and enabled adapter strength work without renaming files')
            try:menu('genstudio.loras.configure',{'folder':'..'})
            except RuntimeError:pass
            else:raise AssertionError('Folder escape accepted')
            assert browser()['folder']=='style'
            click(browser()['settings']['window']['closeRect'])
            click(browser()['window']['closeRect'])
            until(lambda:not studio()['loras']['open'])
            image=base64.b64encode(png_bytes(Image.new('RGBA',(512,384),(60,80,120,255)))).decode()
            menu('genstudio.studio.reference.set',{'slot':'main','image':image,'name':'Source'})
            until(lambda:not studio()['loading'] and studio()['references'])
            menu('genstudio.studio.configure',{'prompt':'A portrait, soft lighting','resolution':1024})
            control('ui.menu.invoke',{'id':'genstudio.studio.submit','params':{},'wait':False})
            until(lambda:not any(j['command']=='genstudio.studio.generate' and j['state']=='running' for j in mcp.run('jobs.list')['jobs']))
            layer=mcp.run('genstudio.studio.layer.inspect')
            expected=[{'path':'style/portrait.safetensors','strength':.65}]
            assert layer['generation']['loras']==expected and host.calls[-1]['loras']==expected
            generated=layer['layer'];original_prompt=layer['generation']['prompt']
            control('app.save',{'path':'adapters.pcraft'});control('app.open',{'path':'adapters.pcraft'})
            assert mcp.run('genstudio.studio.layer.inspect')['generation']['loras']==expected
            step('Studio applies selected adapters and stores prompt/strengths in a layer across native save/reopen')
            menu('genstudio.studio.configure',{'prompt':'Changed'})
            menu('genstudio.loras.configure',{'path':'style/portrait.safetensors','enabled':False})
            menu('genstudio.studio.layer.restore',{'layer':generated})
            until(lambda:studio()['prompt']==original_prompt and not studio()['loading'])
            assert studio()['loras']['selected']==expected
            assert len(studio()['references'])==1
            menu('genstudio.studio.configure',{'prompt':'Keep this prompt'})
            menu('genstudio.studio.layer.restore',{'layer':generated,'slot':0,'restore':False})
            until(lambda:len(studio()['references'])==2 and not studio()['loading'])
            assert studio()['prompt']=='Keep this prompt' and studio()['loras']['selected']==expected
            step('Generated layer restore reinstates its recipe; reference-slot drops preserve the active recipe')
            (models/'loras').rename(models/'loras-hidden')
            try:
                until(lambda:not studio()['loras']['available'])
                try:menu('genstudio.loras.open')
                except RuntimeError:pass
                else:raise AssertionError('Missing loras folder was accepted')
            finally:(models/'loras-hidden').rename(models/'loras')
            ordinary=mcp.run('layer.new.layer')['layer'];mcp.run('edit.fill',{'color':'#cc5533'})
            menu('genstudio.studio.layer.restore',{'layer':ordinary})
            until(lambda:studio()['prompt']=='' and not studio()['loading'])
            assert studio()['loras']['selected']==[] and len(studio()['references'])==1
            step('Ordinary layer restore sets its image and clears prompt/adapters')
            fibo={**settings,'model':FIBO}
            mcp.run('genstudio.engine.configure',fibo);mcp.await_engine()
            until(lambda:studio()['model']==FIBO)
            assert studio()['referenceLimit']==4 and studio()['referenceSyntax']=='<image_1>'
            menu('genstudio.studio.configure',{'prompt':'Change the color in <image_1>'})
            control('ui.menu.invoke',{'id':'genstudio.studio.submit','params':{},'wait':False})
            until(lambda:not any(j['command']=='genstudio.studio.generate' and j['state']=='running' for j in mcp.run('jobs.list')['jobs']))
            assert mcp.run('genstudio.studio.layer.inspect')['generation']['model']==FIBO
            screenshot('fibo-workspace.png')
            step('FIBO preset switches chips/reference limit and completes the synthetic transport only')
            mcp.run('file.new',{'width':6000,'height':4000,'background':'white'})
            began=time.monotonic()
            job=control('engine.execute',{'command':'genstudio.studio.layer.source','params':{},'wait':False})['job']
            ui_began=time.monotonic();control('ui.inspect');ui_seconds=time.monotonic()-ui_began
            def captured():
                return next((j for j in mcp.run('jobs.list')['jobs'] if j['id']==job and j['state']!='running'),None)
            result=until(captured,120)
            assert result['state']=='done',result.get('error')
            from io import BytesIO
            with Image.open(BytesIO(base64.b64decode(result['result']['image']))) as preview:
                assert preview.size==(1024,683),preview.size
            report['layer_24mp']={'seconds':time.monotonic()-began,'ui_response_seconds':ui_seconds,'prepared_size':[1024,683]}
            step('A 24 MP layer is captured on a worker while the interface remains responsive')
            click(studio()['window']['closeRect'])
            until(lambda:not control('ui.inspect')['genStudio']['open'])
            menu('genstudio.open')
            report['passed']=True
            with args.exe.open('rb') as f:report['editorSha256']=hashlib.file_digest(f,'sha256').hexdigest()
    finally:
        if mcp:mcp.process.terminate();mcp.process.wait(5)
        if process and process.poll() is None:process.terminate();process.wait(10)
        server.shutdown();server.server_close()
        (folder/'report.json').write_text(json.dumps(report,indent=2))
        (ROOT/'.runtime/lora-smoke-latest.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2),flush=True)


if __name__=='__main__':main()
