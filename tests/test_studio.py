"""Full generation HTTP and component discovery, using no neural model."""
import base64
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError

from PIL import Image
from genfill.comfy import Cancelled
from genfill.image_ops import png_bytes
from genfill.server import load_config
from genfill.workflow import studio_workflow
from genstudio.models import catalog,resolve,FILES
from genstudio.settings import defaults,validate
from genstudio.service import create_server
from genstudio.studio import request
from genstudio.studio_edit import generate as transport


class Host:
    def __init__(self):
        self.settings=defaults();self.lock=threading.RLock();self.busy=False;self.calls=[]
    def config(self):return load_config()
    def generate_studio(self,references,seed,prompt,width,height,cancel,progress):
        self.calls.append((len(references),seed,prompt,width,height))
        progress("Starting the local AI engine…")
        progress("Qwen 2.1 Turbo · 4/8")
        return Image.new("RGBA",(width,height),(20,180,60,255))
    def generate(self,*args):raise AssertionError("Full generation must not use inpainting")


@contextmanager
def server():
    host=Host();http=create_server(host=host,port=0)
    thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    try:yield http,host,f"http://127.0.0.1:{http.server_port}"
    finally:
        http.shutdown();http.manager.shutdown();http.studio.shutdown();http.server_close();thread.join(3)


def http(url,body=None,timeout=10):
    data=json.dumps(body).encode() if body is not None else None
    with urlopen(Request(url,data=data,headers={"Content-Type":"application/json"}),timeout=timeout) as response:
        return json.load(response)


class StudioTests(unittest.TestCase):
    def test_cancelled_and_queued_studio_jobs_block_engine_changes_without_leaking_results(self):
        with server() as (service,host,base):
            started=threading.Event()
            def blocking(references,seed,prompt,width,height,cancel,progress):
                started.set()
                if not cancel.wait(10):raise TimeoutError("Cancellation did not arrive")
                raise Cancelled()
            host.generate_studio=blocking
            body={"prompt":"A landscape","width":256,"height":256}
            first=http(base+"/studio/jobs",body)["id"]
            self.assertTrue(started.wait(3))
            second=http(base+"/studio/jobs",body)["id"]
            with self.assertRaises(HTTPError) as caught:http(base+"/engine",{"action":"stop"})
            self.assertEqual(caught.exception.code,400)
            http(base+f"/studio/jobs/{second}/cancel",{})
            http(base+f"/studio/jobs/{first}/cancel",{})
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                if all(http(base+f"/studio/jobs/{job}")["state"]=="cancelled" for job in (first,second)):break
                time.sleep(.02)
            else:self.fail("Cancelled jobs did not finish")
            for job in (first,second):
                with self.assertRaises(HTTPError):http(base+f"/studio/jobs/{job}/result")

    def test_full_generation_batch_http_round_trip_and_native_file_transport(self):
        with server() as (service,host,base),tempfile.TemporaryDirectory() as directory:
            events=[];body={"prompt":"An empty garden, use image 1 for the palette", "width":256,"height":320,"batch":2,"seed":15,
                "references":[base64.b64encode(png_bytes(Image.new("RGBA",(44,88),(200,20,10,80)))).decode()]}
            def request_local(url,body=None,timeout=10):
                return http(url.replace("http://127.0.0.1:8766",base),body,timeout)
            configured=[]
            transport(directory,defaults(),body,events.append,configure=lambda *args:configured.append(args),request=request_local,interval=.01)
            self.assertEqual(len(configured),1)
            self.assertEqual([c[1] for c in host.calls],[15,16])
            self.assertTrue(all(c[0]==1 and c[3:]==(256,320) for c in host.calls))
            self.assertEqual(events[-1],{"done":True})
            self.assertFalse((Path(directory)/"result-mask.png").exists())
            for index in range(2):
                with Image.open(Path(directory)/f"result-{index}.png") as image:self.assertEqual(image.size,(256,320))

    def test_text_to_image_is_valid_without_any_reference(self):
        body=request({"prompt":"A landscape","references":[],"width":1024,"height":1024,"batch":1})
        self.assertEqual(body["references"],[])
        for invalid in ({"prompt":""},{"prompt":"x","width":1000},{"prompt":"x","batch":9},{"prompt":"x","references":[""]*11}):
            with self.assertRaises(ValueError):request(invalid)

    def test_browser_origin_cannot_submit_studio_generation(self):
        with server() as (_,host,base):
            req=Request(base+"/studio/jobs",data=b'{}',headers={"Origin":"https://example.com","Content-Type":"application/json"})
            with self.assertRaises(HTTPError) as error:urlopen(req)
            self.assertEqual(error.exception.code,403);self.assertEqual(host.calls,[])

    def test_comfy_workflow_keeps_ten_reference_slots_and_uses_requested_empty_latent_grid(self):
        config=load_config()
        for flat in (True,False):
            info={"TextEncodeQwenImage21":{"input":{"optional":{"image_1":[]} if flat else {}}}}
            graph=studio_workflow(config,[f"ref-{i}.png" for i in range(10)],0,"Use image 10",2048,1024,info)
            inputs=graph["8"]["inputs"]
            self.assertEqual(inputs[("" if flat else "images.")+"image_10"],["39",0])
            self.assertEqual((graph["9"]["inputs"]["width"],graph["9"]["inputs"]["height"]),(1024,512))
            self.assertEqual(graph["12"]["class_type"],"SamplerCustom")
            self.assertFalse(any("LanPaint" in node["class_type"] for node in graph.values()))


class ComponentTests(unittest.TestCase):
    def test_automatic_components_and_explicit_nested_choices_are_independent_of_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name,folders in FILES.values():
                p=root/folders[0]/name;p.parent.mkdir(exist_ok=True);p.write_bytes(b"sentinel")
            custom=root/"text_encoders/custom/alternate.safetensors";custom.parent.mkdir();custom.write_bytes(b"sentinel")
            vae=root/"vae/alternate.safetensors";vae.write_bytes(b"sentinel")
            choices=catalog(root)
            self.assertTrue(choices["defaultTextEncoder"].endswith("qwen3vl_8b_w4a8.safetensors"))
            self.assertEqual(choices["defaultVae"],"vae/qwen_image_2.1_vae_bf16.safetensors")
            chosen=resolve(root,"text_encoders/custom/alternate.safetensors","vae/alternate.safetensors")
            self.assertEqual(chosen["text_encoder"]["name"],"custom/alternate.safetensors")
            for engine in ("direct","comfy"):
                settings=validate({**defaults(),"engine":engine,"textEncoder":"text_encoders/custom/alternate.safetensors","vae":"vae/alternate.safetensors"})
                self.assertEqual(settings["model"],"qwen-image-2.1-turbo")
            for path in ("../bad.safetensors","C:/bad.safetensors"):
                with self.assertRaises(ValueError):resolve(root,path)
            self.assertEqual(custom.read_bytes(),b"sentinel")


if __name__=="__main__":unittest.main()
