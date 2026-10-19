"""Tiny CPU tensors with fake model parts, never CUDA or real weights."""
import importlib.util
import sys
import threading
import types
import unittest
from unittest.mock import patch
from PIL import Image
from genstudio.core_pipeline import DirectPipeline


@unittest.skipUnless(importlib.util.find_spec("torch"),"CPU sampling tests use the installed engine Python")
class SamplingTests(unittest.TestCase):
    def run_generation(self,reference_count):
        import torch
        calls={}
        class Vae:
            def encode(self,pixels):return torch.ones((1,64,2,2))
            def decode(self,latents):return torch.zeros((1,256,256,4))
        class Clip:
            def tokenize(self,prompt,**kwargs):calls["tokens"]=(prompt,kwargs);return {}
            def encode_from_tokens_scheduled(self,tokens):return [[torch.zeros(1),{}]]
        def sample(model,noise,cfg,sampler,sigmas,positive,negative,latent,**kwargs):
            calls.update(latent=latent,positive=positive,kwargs=kwargs,sigmas=sigmas,cfg=cfg)
            return latent
        comfy=types.ModuleType("comfy");comfy.__path__=[]
        samples=types.ModuleType("comfy.sample");samples.prepare_noise=lambda latent,seed:torch.zeros_like(latent);samples.sample_custom=sample
        samplers=types.ModuleType("comfy.samplers");samplers.sampler_object=lambda name:name
        comfy.sample=samples;comfy.samplers=samplers
        pipeline=DirectPipeline("not loaded");pipeline.load=lambda progress:(object(),Clip(),Vae())
        refs=[Image.new("RGBA",(32,32),(20,80,120,128)) for _ in range(reference_count)]
        with patch.dict(sys.modules,{"comfy":comfy,"comfy.sample":samples,"comfy.samplers":samplers}),patch("torch.cuda._lazy_init",side_effect=AssertionError("CUDA is forbidden")):
            image=pipeline.generate_studio(refs,5,"Use image 10" if refs else "A landscape",256,256,threading.Event(),lambda _:None)
        self.assertEqual(image.size,(256,256));self.assertEqual(tuple(calls["latent"].shape),(1,64,16,16))
        self.assertEqual(int(torch.count_nonzero(calls["latent"])),0,"full generation starts with an empty target, not the reference latent")
        self.assertNotIn("noise_mask",calls["kwargs"],"full generation must not reuse the edit mask")
        self.assertEqual(calls["cfg"],1.0);self.assertEqual(len(calls["sigmas"]),9)
        return calls

    def test_text_only_generation_has_no_reference_conditioning(self):
        calls=self.run_generation(0)
        self.assertEqual(calls["tokens"][1]["images"],[])
        self.assertTrue(calls["tokens"][1]["keep_vision"])
        self.assertNotIn("reference_latents",calls["positive"][0][1])

    def test_ten_references_keep_exact_slot_order(self):
        calls=self.run_generation(10)
        self.assertEqual(len(calls["tokens"][1]["images"]),10)
        self.assertEqual(len(calls["positive"][0][1]["reference_latents"]),10)
        self.assertFalse(calls["tokens"][1]["keep_vision"])


if __name__=="__main__":unittest.main()
