"""FIBO dispatch and Studio adapters; tiny files/fake modules, no neural inference."""
import json
from pathlib import Path
import tempfile
import threading
import types
import sys
import unittest
from unittest.mock import Mock, patch
from PIL import Image

from genstudio.models import FIBO, MODEL, MODELS, catalog, resolve_components
from genstudio.settings import defaults, active_components, validate
from genstudio.lora import validate_studio, apply_studio, studio_graph
from genstudio.fibo_pipeline import FiboPipeline
from genstudio.fibo_workflow import workflow
from genstudio.studio import request
from genfill.comfy import Cancelled
from genfill.image_ops import png_bytes
import base64


class FiboTests(unittest.TestCase):
    def test_turbo_discovery_reuses_shared_encoder_and_vae_and_excludes_dev(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            for name in ('diffusion_models/fibo2_dev_bf16.safetensors', 'diffusion_models/fibo2_edit_turbo_int8_convrot.safetensors',
                         'text_encoders/qwen3vl_4b_int8_convrot.safetensors', 'vae/flux2-vae.safetensors'):
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'synthetic')
            found=catalog(root,FIBO)
            self.assertEqual(found['referenceLimit'],4)
            self.assertEqual(found['defaultModel'],'diffusion_models/fibo2_edit_turbo_int8_convrot.safetensors')
            self.assertFalse(next(v for v in found['diffusionModels'] if 'dev' in v['value'])['compatible'])
            files=resolve_components(root,{},FIBO)
            self.assertEqual(Path(files['vae']['path']),root/'vae/flux2-vae.safetensors')
            self.assertNotIn('promptUpsamplers',found)

    def test_each_preset_keeps_its_own_advanced_components(self):
        settings=validate({**defaults(),'model':FIBO,'presets':{
            FIBO:{'modelFile':'fibo-custom.safetensors','textEncoder':'fibo-encoder.safetensors'},
            MODEL:{'modelFile':'qwen-custom.safetensors'}}})
        self.assertEqual(active_components(settings)['modelFile'],'fibo-custom.safetensors')
        settings['model']=MODEL
        self.assertEqual(active_components(settings)['modelFile'],'qwen-custom.safetensors')

    def test_edit_reference_limits_and_plain_or_structured_prompts(self):
        image=base64.b64encode(png_bytes(Image.new('RGB',(256,256)))).decode()
        for text in ('Change the background.', '{"edit_instruction":"Change the background."}'):
            value=request({'prompt':text,'references':[image]*4},FIBO)
            self.assertEqual(len(value['references']),4)
        for count in (0,5):
            with self.assertRaises(ValueError):request({'prompt':'Edit','references':[image]*count},FIBO)

    def test_comfy_graph_uses_four_steps_and_patches_only_the_edit_encoder(self):
        config={key:value[0] for key,value in MODELS[FIBO]['files'].items()}
        graph=workflow(config,['a.png','b.png'],7,'Use <image_2>',1024,768,
                       loras=[{'path':'style.safetensors','strength':.75}])
        self.assertEqual(graph['12']['inputs']['steps'],4)
        self.assertEqual(graph['12']['inputs']['cfg'],1)
        self.assertEqual(graph['4']['inputs']['images.image_2'],['21',0])
        self.assertEqual(graph['12']['inputs']['model'],['500',0])
        self.assertEqual(graph['4']['inputs']['clip'],['500',1])
        self.assertEqual(graph['2']['inputs']['type'],'fibo2')

    def test_worker_construction_and_cancel_do_not_import_torch_or_open_weights(self):
        before='torch' in sys.modules
        from genstudio.direct_worker import Worker
        worker=Worker('missing',model_id=FIBO)
        self.assertIsInstance(worker.pipeline,FiboPipeline)
        event=threading.Event();event.set()
        with patch.object(worker.pipeline,'load',side_effect=AssertionError('No weights')):
            with self.assertRaises(Cancelled):
                worker.pipeline.generate_studio([Image.new('RGB',(256,256))],0,'Edit',256,256,event,lambda _:None)
        worker.pool.shutdown()
        self.assertEqual('torch' in sys.modules,before)


class StudioLoraTests(unittest.TestCase):
    def test_portable_paths_strengths_and_duplicates_are_validated(self):
        for path in ('../secret.safetensors','C:/secret.safetensors','/secret.safetensors','a\\b.safetensors','a//b.safetensors','a.pt'):
            with self.assertRaises(ValueError):validate_studio([{'path':path,'strength':1}])
        for strength in (True,float('nan'),float('inf'),21):
            with self.assertRaises(ValueError):validate_studio([{'path':'a.safetensors','strength':strength}])
        with self.assertRaises(ValueError):validate_studio([{'path':'a.safetensors'},{'path':'A.safetensors'}])
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'loras/sub').mkdir(parents=True)
            (root/'loras/sub/a.safetensors').write_bytes(b'fake')
            self.assertEqual(validate_studio([{'path':'sub/a.safetensors','strength':.4}],root),[{'path':'sub/a.safetensors','strength':.4}])
            with self.assertRaises(ValueError):validate_studio([{'path':'missing.safetensors'}],root)

    def test_direct_stack_patches_clones_in_order_and_keeps_base_and_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'loras').mkdir()
            for name in ('a','b'):(root/'loras'/f'{name}.safetensors').write_bytes(b'fake')
            sd=types.ModuleType('comfy.sd');utils=types.ModuleType('comfy.utils');comfy=types.ModuleType('comfy')
            comfy.sd=sd;comfy.utils=utils
            utils.load_torch_file=Mock(side_effect=lambda path,**_:Path(path).name)
            sd.load_lora_for_models=Mock(side_effect=lambda m,c,w,sm,sc:(m+'+'+w,c+'+'+w))
            selected=[{'path':'a.safetensors','strength':.3},{'path':'b.safetensors','strength':.7}]
            with patch.dict(sys.modules,{'comfy':comfy,'comfy.sd':sd,'comfy.utils':utils}):
                result=apply_studio('base','encoder',root,selected,lambda _:None)
            self.assertEqual(result,('base+a.safetensors+b.safetensors','encoder+a.safetensors+b.safetensors'))
            self.assertEqual(sd.load_lora_for_models.call_args.args[-2:],(.7,.7))
            self.assertEqual((root/'loras/a.safetensors').read_bytes(),b'fake')

    def test_comfy_stack_is_ordered_and_zero_strength_does_not_add_a_node(self):
        graph={};model,clip=studio_graph(graph,['1',0],['2',0],[{'path':'a.safetensors','strength':.5},{'path':'b.safetensors','strength':0}])
        self.assertEqual(model,['500',0]);self.assertEqual(clip,['500',1]);self.assertNotIn('501',graph)
        self.assertEqual(graph['500']['inputs']['strength_model'],.5)
