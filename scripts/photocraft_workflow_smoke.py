"""Official PhotoCraft MCP workflow UI/import regression; synthetic host, no inference."""
from __future__ import annotations
import argparse,base64,json,os,socket,subprocess,sys,threading,time
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
RUNTIME=Path(sys.argv[sys.argv.index('--runtime')+1]).resolve() if '--runtime' in sys.argv else ROOT
sys.path[:0]=[str(RUNTIME),str(ROOT/'scripts')]
from PIL import Image
from genfill.image_ops import png_bytes
from genstudio.settings import defaults
from genstudio.service import create_server
from photocraft_engine_smoke import MCP
from photocraft_remove_smoke import SyntheticHost


class Host(SyntheticHost):
    def generate_workflow(self,definition,data,cancel,progress):
        from genstudio.comfy_workflow import parse_workflow
        assert definition is None, 'Studio must request the active graph, not a workflow file'
        definition=parse_workflow(self.active_graph)
        self.calls.append({'prompt':data['prompt'],'context':data.get('context',{}),'references':len(data['references']),'hash':definition['sha256'],'negativePrompt':data.get('negativePrompt','')})
        progress('Starting the synthetic workflow transport…')
        if cancel.wait(.5):
            from genfill.comfy import Cancelled
            raise Cancelled()
        rows=[]
        for index,size in enumerate(((320,256),(256,256))):
            rows.append({'image':base64.b64encode(png_bytes(Image.new('RGBA',size,(25,100,220,255)))).decode(),
                         'mask':base64.b64encode(png_bytes(Image.new('L',size,128))).decode(),
                         'width':size[0],'height':size[1],'name':'Workflow '+str(index+1),'prompt':data.get('sourcePrompt',data['prompt']),'negativePrompt':data.get('negativePrompt',''),'seed':17+index,
                         'loras':[{'path':'style.safetensors','strength':.7}]})
        return {'images':rows,'prompt':data['prompt'],'workflow':{'path':data['workflowPath'],'sha256':definition['sha256']}}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe',type=Path,required=True);parser.add_argument('--cli',type=Path,required=True);parser.add_argument('--runtime',type=Path,default=ROOT)
    args=parser.parse_args();folder=ROOT/'.runtime'/('workflow-mcp-'+str(time.time_ns()));folder.mkdir()
    models=folder/'models';(models/'loras').mkdir(parents=True);(models/'loras/style.safetensors').write_bytes(b'synthetic fixture, never open as weights')
    graph={'in':{'class_type':'GenGateIN2','inputs':{}},
           'a':{'class_type':'EmptyImage','inputs':{'width':320,'height':256,'batch_size':1,'color':0}},
           'b':{'class_type':'EmptyImage','inputs':{'width':256,'height':256,'batch_size':1,'color':0}},
           'outA':{'class_type':'GenGateOUT2','inputs':{'images':['a',0]}},
           'outB':{'class_type':'GenGateOUT2','inputs':{'images':['b',0]}}}
    path=folder/'My workflow.json';path.write_text(json.dumps(graph))
    settings={**defaults(),'runtimeRoot':str(args.runtime.resolve()),'modelsDir':str(models),'engine':'comfy','studioMode':'preset','directStartup':'off','comfyStartup':'off'}
    host=Host(settings);host.active_graph=graph;server=create_server(settings,port=0,host=host);threading.Thread(target=server.serve_forever,daemon=True).start()
    (folder/'preferences.json').write_text(json.dumps({'genStudio':settings,'general':{'automaticallyCheckUpdates':False},'interface':{'language':'en','theme':'studio'},'dialogs':{'photoshopShortcuts':{'imported':False}}}))
    env={**os.environ,'PHOTOCRAFT_CONFIG_DIR':str(folder),'GENSTUDIO_SERVICE_URL':f'http://127.0.0.1:{server.server_port}','CUDA_VISIBLE_DEVICES':'','GENGATE_NO_INFERENCE':'1'}
    env.pop('GENSTUDIO_NO_PROCESS',None)
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    token=folder/'control.token';process=mcp=None
    report={'passed':False,'real_inference':False,'folder':str(folder),'steps':[],
            'executable':str(args.exe.resolve()),'cli':str(args.cli.resolve()),'runtime':str(args.runtime.resolve())}
    def until(check,timeout=80):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            result=check()
            if result:return result
            time.sleep(.08)
        raise TimeoutError('Workflow MCP condition did not arrive')
    def step(message):report['steps'].append(message);print(message,flush=True)
    try:
        with patch('genstudio.service.save',lambda _:None),(folder/'app.log').open('wb') as log,(folder/'mcp.log').open('wb') as bridge:
            process=subprocess.Popen([str(args.exe),'--control',str(port),'--control-token-file',str(token),'--automation-read-root',str(folder),'--automation-write-root',str(folder)],cwd=args.exe.parent,env=env,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
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
            def click(rect):control('ui.click',{'x':rect[0]+rect[2]/2,'y':rect[1]+rect[3]/2})
            def screenshot(name):
                for b in mcp.tool('ui_screenshot',{'max_side':1600})['content']:
                    if b['type']=='image':(folder/name).write_bytes(base64.b64decode(b['data']))
            def finished():
                pending=[j for j in mcp.run('jobs.list')['jobs'] if j['command']=='genstudio.studio.generate']
                if not pending or any(j['state']=='running' for j in pending):return False
                assert all(j['state']=='done' for j in pending),pending
                return pending[-1]
            control('ui.resize',{'width':1600,'height':1000});menu('genstudio.open')
            # The first 0.17 frame can still be warming the expanded font atlas.
            until(lambda:(studio().get('workflow') or {}).get('open'))
            assert studio()['workflow']['active'] and 'browse' not in studio()['workflow']
            menu('genstudio.studio.configure',{'prompt':'A quiet forest','negativePrompt':'text, watermark','batch':3})
            until(lambda:studio().get('negativeOpen') and studio()['controls'].get('negativePrompt'))
            for theme in ('pro','proMedium','proLightGray','proLight','studio','studioLight','classic','adwaita','adwaitaDark','solarizedDark','breezeLight','breezeDark','softDark','softLight'):
                control('ui.set',{'theme':theme})
                for width in (1100,1600):
                    control('ui.resize',{'width':width,'height':1000})
                    screenshot(f'negative-{theme}-{width}.png')
            control('ui.set',{'theme':'studio'})
            click(studio()['controls']['negativeToggle']);until(lambda:not studio()['negativeOpen'])
            assert studio()['negativePrompt']=='text, watermark'
            click(studio()['controls']['negativeToggle']);until(lambda:studio()['negativeOpen'])
            click(studio()['controls']['generate']);until(finished)
            info=mcp.run('genstudio.studio.layer.inspect');meta=info['generation']
            assert meta['model']=='workflow' and meta['workflow']['path']=='' and meta['prompt']=='A quiet forest' and meta['seed']==18,meta
            assert meta['negative_prompt']=='text, watermark',meta
            assert meta['width']==256 and meta['height']==256 and meta['loras']==[{'path':'style.safetensors','strength':.7}]
            generated=info['layer'];report['generated_layer']=meta
            assert len(host.calls)==1 and host.calls[0]['references']==0 and host.calls[0]['negativePrompt']=='text, watermark'
            step('Actual Neg button splits/hides the fields; active Studio workflow receives both texts and returns its own batch/dimensions')
            control('app.save',{'path':'workflow.pcraft'});control('app.open',{'path':'workflow.pcraft'})
            meta=mcp.run('genstudio.studio.layer.inspect')['generation']
            assert meta['workflow']['sha256']==host.calls[-1]['hash'] and meta['negative_prompt']=='text, watermark'
            menu('genstudio.studio.configure',{'prompt':'Changed','negativePrompt':'Changed negative'})
            menu('genstudio.studio.layer.restore',{'layer':generated})
            until(lambda:studio()['prompt']=='A quiet forest' and studio()['negativePrompt']=='text, watermark' and not studio()['loading'])
            assert studio()['negativeOpen'] and studio()['loras']['selected']==[{'path':'style.safetensors','strength':.7}]
            step('Native save/reopen and generated-layer restore preserve positive and negative prompts plus adapter recipe')
            mcp.run('select.all');menu('genstudio.studio.reference.selection');mcp.await_engine()
            until(lambda:not studio()['loading'])
            host.active_graph={'in':{'class_type':'GenGateIN2','inputs':{}},'out':{'class_type':'GenGateOUT2','inputs':{'images':['in',0],'mask':['in',1]}}}
            menu('genstudio.studio.configure',{'prompt':'Use the Studio selection reference','negativePrompt':''})
            control('ui.menu.invoke',{'id':'genstudio.studio.submit','params':{},'wait':False})
            until(lambda:len(host.calls)==2);until(finished)
            assert host.calls[-1]['references']==1 and not host.calls[-1]['context'] and not host.calls[-1]['negativePrompt']
            assert host.calls[-1]['hash']!=host.calls[0]['hash']
            step('A second Generate uses the changed active graph, latest text and Studio selection reference')
            mcp.run('prefs.set',{'path':'genStudio.engine','value':'direct'})
            until(lambda:not (studio().get('workflow') or {}).get('open'));screenshot('workflow-direct.png')
            step('Direct hides active-workflow controls')
            mcp.run('genstudio.engine.configure',{**settings,'fillWorkflow':str(path),'removeWorkflow':str(path)});mcp.await_engine()
            menu('genstudio.studio.configure',{'advancedOpen':False})
            dialog=menu('genstudio.settings.open')['dialog'];screenshot('settings-comfy-collapsed.png')
            control('ui.dialog.cancel',{'dialog':dialog})
            for count in (None,4):
                configured={**settings,'fillWorkflow':str(path),'removeWorkflow':str(path),
                            'presets':{'qwen-image-2.1-turbo':{'constantLoraSteps':count}}}
                mcp.run('genstudio.engine.configure',configured);mcp.await_engine()
                menu('genstudio.studio.configure',{'advancedOpen':True})
                dialog=menu('genstudio.settings.open')['dialog']
                screenshot('constant-steps-'+str(count)+'.png')
                control('ui.dialog.cancel',{'dialog':dialog})
            step('Native settings render separate tool workflows and blank/explicit Constant LoRA steps')
            mcp.run('prefs.set',{'path':'genStudio.engine','value':'direct'})
            menu('genstudio.studio.configure',{'advancedOpen':True})
            dialog=menu('genstudio.settings.open')['dialog'];screenshot('settings-direct-advanced.png')
            control('ui.dialog.cancel',{'dialog':dialog})
            report['passed']=True
    finally:
        if mcp:mcp.process.terminate();mcp.process.wait(5)
        if process and process.poll() is None:process.terminate();process.wait(10)
        server.shutdown();server.server_close()
        (folder/'report.json').write_text(json.dumps(report,indent=2))
        (ROOT/'.runtime/workflow-mcp-latest.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2),flush=True)


if __name__=='__main__':main()
