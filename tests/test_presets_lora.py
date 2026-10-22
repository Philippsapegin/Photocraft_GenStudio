"""Preset and adapter contracts; synthetic files and fake modules only."""
import contextlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch
from PIL import Image
from genstudio.models import MODEL, FLUX, MODELS, catalog, resolve_components, extra_paths
from genstudio.settings import defaults, validate, active_components, component_defaults
from genstudio.core_pipeline import DirectPipeline
from genstudio.flux_pipeline import FluxPipeline
from genstudio.lora import apply_constant
from genstudio.lifecycle import EngineHost
from genfill.workflow import build_workflow, studio_workflow
from genstudio.flux_workflow import workflow


class PresetTests(unittest.TestCase):
    def test_profiles_roundtrip_and_legacy_components_never_leak_to_another_preset(self):
        old = validate({**defaults(), "textEncoder": "text_encoders/old.safetensors", "vae": "vae/old.safetensors"})
        self.assertEqual(active_components(old)["textEncoder"], "text_encoders/old.safetensors")
        saved = validate({**old, "presets": {MODEL: {**active_components(old), "constantLora": "loras/edit.safetensors", "constantLoraStrength": .65}}})
        saved = validate(json.loads(json.dumps(saved)))
        flux = validate({**saved, "model": FLUX})
        self.assertEqual(active_components(flux), component_defaults())
        self.assertEqual(active_components(saved)["constantLoraStrength"], .65)
        for strength in (True, "1", float("nan"), float("inf"), 21, -21):
            with self.assertRaises(ValueError):validate({**saved, "presets": {MODEL: {"constantLoraStrength": strength}}})

    def test_auto_discovers_all_components_but_never_a_lora_and_manual_files_win(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for spec in MODELS.values():
                for filename, folders in spec["files"].values():
                    p = root / folders[0] / filename;p.parent.mkdir(exist_ok=True);p.write_bytes(b"synthetic")
            p=root/"loras/qwen_image_2.1_turbo_lora.safetensors";p.parent.mkdir();p.write_bytes(b"adapter fixture")
            for model in MODELS:
                discovered = catalog(root, model)
                self.assertTrue(discovered["defaultModel"])
                self.assertNotIn("lora", discovered["defaultModel"])
                if model == FLUX:self.assertTrue(discovered["defaultPromptUpsampler"])
                else:self.assertNotIn("promptUpsamplers",discovered)
                self.assertNotIn("constantLora",discovered)
            for key in ("diffusion_model", "text_encoder", "prompt_upsampler", "vae"):
                p=root/("manual-"+key+".safetensors");p.write_bytes(b"custom")
            parts={"modelFile":"manual-diffusion_model.safetensors","textEncoder":"manual-text_encoder.safetensors",
                   "promptUpsampler":"manual-prompt_upsampler.safetensors","vae":"manual-vae.safetensors"}
            files=resolve_components(root,parts,FLUX)
            self.assertTrue(all(Path(v["path"]).name.startswith("manual-") for v in files.values()))
            self.assertGreater(len(extra_paths(root,parts,FLUX)),1)
            # Browsing a different folder must not require copying checkpoint files.
            parts["modelFile"]=str(root/"manual-diffusion_model.safetensors")
            self.assertEqual(resolve_components(root,parts,FLUX)["diffusion_model"]["path"],str((root/parts["modelFile"]).resolve()))

    def test_family_discovery_falls_back_to_a_compatible_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for key,(filename,folders) in MODELS[MODEL]["files"].items():
                if key=="diffusion_model":filename="qwen_image_2.1_custom_int8.safetensors"
                p=root/folders[0]/filename;p.parent.mkdir(exist_ok=True);p.write_bytes(b"fixture")
            self.assertIn("custom",resolve_components(root,{},MODEL)["diffusion_model"]["path"])

    def test_missing_edit_adapter_does_not_block_full_generation_but_blocks_masked_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for family in MODELS:
                for filename,folders in MODELS[family]["files"].values():
                    p=root/folders[0]/filename;p.parent.mkdir(exist_ok=True);p.write_bytes(b"fixture")
                parts={**component_defaults(),"constantLora":"loras/unavailable.safetensors"}
                settings={**defaults(),"engine":"direct","modelsDir":str(root),"model":family,"presets":{family:parts}}
                health={"service":"genstudio-direct","ready":True,"modelsDir":str(root),"model":family,"components":parts}
                host=EngineHost(settings,spawn=Mock(side_effect=AssertionError("No process may start")),request=Mock(return_value=health))
                host.direct_url="http://127.0.0.1:54321"
                health["owner"]=host.worker_id
                self.assertTrue(host.ensure(studio=True)["ready"])
                with self.assertRaisesRegex(ValueError,"missing or empty"):
                    host.ensure(studio=False)


class LoraTests(unittest.TestCase):
    def test_comfy_masked_graphs_apply_lora_to_model_and_encoder_but_studio_never_does(self):
        for family in MODELS:
            config={key:value[0] for key,value in MODELS[family]["files"].items()}
            config.update(constant_lora="helper.safetensors",constant_lora_strength=.7)
            if family==MODEL:
                masked=build_workflow(config,"image.png","mask.png",0,"remove")
                studio=studio_workflow(config,[],0,"scene",512,512,{"TextEncodeQwenImage21":{"input":{}}})
                self.assertEqual(masked["8"]["inputs"]["clip"],["15",1])
            else:
                masked=workflow(config,[],0,"remove",512,512,constant_lora=True)
                studio=workflow(config,[],0,"scene",512,512)
                self.assertEqual(masked["7"]["inputs"]["clip"],["15",1])
                self.assertEqual(masked["6"]["inputs"]["clip"],["5",0],"never patch the prompt upsampler")
            self.assertEqual(masked["4"]["inputs"]["model"],["15",0])
            self.assertEqual(masked["15"]["inputs"]["strength_model"],.7)
            self.assertNotIn("15",studio)

    def test_direct_helper_patches_clones_and_zero_strength_does_not_load_anything(self):
        base_model,base_clip,new_model,new_clip=object(),object(),object(),object()
        self.assertEqual(apply_constant(base_model,base_clip,"missing",{"constantLora":"missing","constantLoraStrength":0},lambda _:None),(base_model,base_clip))
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);p=root/"loras/helper.safetensors";p.parent.mkdir();p.write_bytes(b"fake adapter")
            sd=types.ModuleType("comfy.sd");sd.load_lora_for_models=Mock(return_value=(new_model,new_clip))
            utils=types.ModuleType("comfy.utils");utils.load_torch_file=Mock(return_value={"fake":"weights"})
            comfy=types.ModuleType("comfy");comfy.__path__=[];comfy.sd=sd;comfy.utils=utils
            with patch.dict(sys.modules,{"comfy":comfy,"comfy.sd":sd,"comfy.utils":utils}):
                result=apply_constant(base_model,base_clip,root,{"constantLora":"loras/helper.safetensors","constantLoraStrength":.75},lambda _:None)
            self.assertEqual(result,(new_model,new_clip));sd.load_lora_for_models.assert_called_once_with(base_model,base_clip,{"fake":"weights"},.75,.75)
            utils.load_torch_file.assert_called_once_with(str(p.resolve()),safe_load=True)

    def test_qwen_masked_path_uses_the_adapter_before_sampling_and_studio_uses_the_base(self):
        class SampleReached(Exception):pass
        base=object();clip=types.SimpleNamespace(tokenize=lambda *a,**k:{},encode_from_tokens_scheduled=lambda t:[[object(),{}]])
        pipeline=DirectPipeline("unused",components={"constantLora":"helper.safetensors"});pipeline.load=lambda p:(base,clip,object())
        with patch("genstudio.lora.apply_constant",side_effect=SampleReached) as apply:
            with self.assertRaises(SampleReached):pipeline.generate(Image.new("RGBA",(32,32)),Image.new("L",(32,32),255),1,"remove",threading.Event(),lambda _:None)
            apply.assert_called_once()
        torch=types.ModuleType("torch");torch.inference_mode=contextlib.nullcontext;torch.zeros=lambda *a,**k:object();torch.tensor=lambda *a,**k:object();torch.float32="float32"
        comfy=types.ModuleType("comfy");comfy.__path__=[]
        sample=types.ModuleType("comfy.sample");sample.prepare_noise=lambda *a:object()
        def sampling(model,*a,**k):self.assertIs(model,base);raise SampleReached()
        sample.sample_custom=sampling
        samplers=types.ModuleType("comfy.samplers");samplers.sampler_object=lambda name:name;comfy.sample=sample;comfy.samplers=samplers
        with patch.dict(sys.modules,{"torch":torch,"numpy":types.ModuleType("numpy"),"comfy":comfy,"comfy.sample":sample,"comfy.samplers":samplers}),patch("genstudio.lora.apply_constant",side_effect=AssertionError("Studio must not apply Constant LoRA")):
            with self.assertRaises(SampleReached):pipeline.generate_studio([],1,"scene",256,256,threading.Event(),lambda _:None)

    def test_flux_only_enables_edit_adapter_for_the_masked_entrypoint(self):
        pipe=FluxPipeline("unused",components={"constantLora":"helper.safetensors"})
        pipe.generate_studio=Mock(return_value=Image.new("RGBA",(32,32)))
        pipe.generate(Image.new("RGBA",(32,32)),Image.new("L",(32,32),255),1,"remove",threading.Event(),lambda _:None)
        self.assertTrue(pipe.generate_studio.call_args.kwargs["_constant_lora"])


if __name__=="__main__":unittest.main()
