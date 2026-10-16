"""AI feedback tests: synthetic adapters only, no weights or inference."""
import base64
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from genfill.comfy_progress import ComfyProgress
from genfill.image_ops import png_bytes
from genfill.progress import sampling_fraction
from genfill.server import Job, JobManager
from genstudio.edit import edit


class FeedbackTests(unittest.TestCase):
    def test_selection_prompt_is_forwarded_as_fill_without_the_removal_instruction(self):
        from genstudio.edit import REMOVE_PROMPT
        for prompt in ("A red bicycle", "Красный велосипед", ""):
            posted=[]
            encoded=lambda image:base64.b64encode(png_bytes(image)).decode()
            def request(url,body=None,**kwargs):
                if url.endswith("/jobs"):
                    posted.append(body)
                    return {"id":"synthetic"}
                if url.endswith("/result"):
                    return {"x":0,"y":0,"image":encoded(Image.new("RGBA",(32,32),"red")),
                            "mask":encoded(Image.new("L",(32,32),255))}
                return {"state":"done","message":"Done","progress":1.0}
            with tempfile.TemporaryDirectory() as folder:
                folder=Path(folder)
                for name,mode,color in (("input.png","RGBA","blue"),("mask.png","L",255)):
                    (folder/name).write_bytes(png_bytes(Image.new(mode,(32,32),color)))
                edit(folder,{},lambda _:None,prompt=prompt,request=request,configure=lambda *args:None,interval=0)
            self.assertEqual(posted[0]["mode"],"fill")
            self.assertEqual(posted[0]["prompt"],prompt)
            self.assertNotEqual(posted[0]["prompt"],REMOVE_PROMPT)

    def test_invalid_selection_prompts_never_start_a_host_or_submit_a_job(self):
        for prompt in (False, "é"*3001, "bad\0prompt"):
            request=Mock();configure=Mock()
            with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
                edit(folder,{},lambda _:None,prompt=prompt,request=request,configure=configure)
            request.assert_not_called();configure.assert_not_called()

    def test_only_real_step_counts_produce_sampling_progress(self):
        self.assertEqual(sampling_fraction("Qwen 2.1 Turbo · 2/8"),0.25)
        self.assertEqual(sampling_fraction("Generating · 8/8"),1.0)
        self.assertEqual(sampling_fraction("Step 0/8"),0.0)
        for value in (None,"loading the model / generating 8 steps", "Generating · 4/0", "Generating · 9/8"):
            self.assertIsNone(sampling_fraction(value))

    def test_websocket_progress_ignores_other_prompts_and_nodes_and_invalid_packets(self):
        def event(prompt="ours",node="12",value=2,total=8):
            return json.dumps({"type":"progress","data":{"prompt_id":prompt,"node":node,"value":value,"max":total}})
        messages=iter([b"preview","bad json", event("somebody-else"), event(node="8"),
                       event(value=True),event(total=0),event(value=10),event(),event(value=5)])
        class Socket:
            def recv(self):
                try: return next(messages)
                except StopIteration: raise TimeoutError()
            settimeout=Mock()
            close=Mock()
        socket=Socket()
        connect=Mock(return_value=socket)
        progress=[]
        with patch.dict("sys.modules",{"websocket":SimpleNamespace(create_connection=connect)}):
            with ComfyProgress("http://127.0.0.1:8190","our-client",progress.append) as stream:
                stream.poll("ours")
        self.assertEqual(progress,["Generating · 2/8","Generating · 5/8"])
        self.assertIn("ws://127.0.0.1:8190/ws?clientId=our-client",connect.call_args.args)
        socket.close.assert_called_once()

    def test_missing_websocket_does_not_break_the_http_generation_path(self):
        progress=[]
        with patch.dict("sys.modules",{"websocket":SimpleNamespace(create_connection=Mock(side_effect=OSError("offline")))}):
            with ComfyProgress("http://127.0.0.1:8190","test",progress.append) as stream:
                stream.poll("ours")
                self.assertIsNone(stream.socket)
        self.assertEqual(progress,[])

    def test_native_transport_forwards_fraction_changes_with_identical_status_text(self):
        encoded=lambda image:base64.b64encode(png_bytes(image)).decode()
        states=iter([{"state":"running","message":"Generating","progress":0.25},
                     {"state":"running","message":"Generating","progress":0.75},
                     {"state":"done","message":"Done","progress":1.0}])
        def request(url,body=None,**kwargs):
            if url.endswith("/jobs"): return {"id":"our-job"}
            if url.endswith("/result"):
                return {"x":0,"y":0,"image":encoded(Image.new("RGBA",(32,32),"green")),
                        "mask":encoded(Image.new("L",(32,32),255))}
            return next(states)
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder)
            for name,mode,color in (("input.png","RGBA","blue"),("mask.png","L",255)):
                (folder/name).write_bytes(png_bytes(Image.new(mode,(32,32),color)))
            events=[]
            edit(folder,{},events.append,request=request,configure=lambda *args:None,interval=0)
        self.assertEqual([event["progress"] for event in events if "progress" in event],[0.25,0.75,1.0])

    def test_sampling_fraction_does_not_regress_when_status_messages_change(self):
        class Model:
            def generate(self,image,mask,seed,prompt,cancel,progress):
                progress("Generating · 5/8")
                progress("Generating · 1/8")
                progress("Decoding the image")
                return image
        manager=JobManager({"diffusion_model":"synthetic"},Model())
        try:
            from genfill.image_ops import prepare_edit
            image=Image.new("RGBA",(32,32),"blue")
            edit_input=prepare_edit(image,Image.new("L",image.size,255))
            job=Job("synthetic")
            manager._run(job,edit_input,0,"remove",0)
            self.assertEqual(job.state,"done")
            self.assertEqual(job.public()["progress"],5/8)
        finally: manager.shutdown()
