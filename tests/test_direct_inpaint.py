"""Qwen mask wiring with tiny CPU tensors and fake model parts; no weights/CUDA."""
import importlib.util
import sys
import threading
import types
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from genstudio.core_pipeline import DirectPipeline, inpaint_prompt, inpaint_references


class InpaintReferenceTests(unittest.TestCase):
    def test_edit_mask_is_an_explicit_reference_not_image_transparency(self):
        image = Image.new("RGBA", (32, 32), (220, 30, 10, 170))
        mask = Image.new("L", image.size)
        mask.putpixel((16, 16), 1)
        reference, separate_mask = inpaint_references(image, mask)
        self.assertEqual(reference.getpixel((16, 16)), (127, 127, 127, 255))
        self.assertEqual(reference.getpixel((0, 0)), image.getpixel((0, 0)))
        self.assertEqual(separate_mask.getpixel((16, 16)), (255, 255, 255, 255))
        self.assertEqual(separate_mask.getpixel((0, 0)), (0, 0, 0, 255))
        self.assertEqual(image.getpixel((16, 16)), (220, 30, 10, 170))

    def test_empty_and_wrong_size_masks_fail_before_model_loading(self):
        pipeline = DirectPipeline("unused")
        pipeline.load = Mock(side_effect=AssertionError("must not load weights"))
        for size in ((32, 32), (31, 32)):
            with self.subTest(size=size), self.assertRaises(ValueError):
                pipeline.generate(Image.new("RGB", (32, 32)), Image.new("L", size),
                                  0, "A tree", threading.Event(), lambda _: None)
        pipeline.load.assert_not_called()

    def test_prompt_names_both_references_and_preserves_the_edit_instruction(self):
        prompt = inpaint_prompt("A small red boat")
        self.assertIn("image 1", prompt)
        self.assertIn("mask in image 2", prompt)
        self.assertIn("White in image 2 marks the region to replace", prompt)
        self.assertIn("A small red boat", prompt)
        self.assertNotIn("Fill the transparent region", prompt)


@unittest.skipUnless(importlib.util.find_spec("torch"), "CPU tensor tests use the installed engine Python")
class InpaintSamplingTests(unittest.TestCase):
    def generate(self, mask, via_worker=False):
        import torch
        import numpy as np
        calls = {}
        encoded = []
        class Vae:
            def encode(self, pixels):
                encoded.append(pixels.clone())
                return torch.full((1, 64, 2, 2), 0.25 * len(encoded))
            def decode(self, samples):
                self.samples = samples
                return torch.tensor([0.0, 0.8, 0.1, 0.0]).expand(1, 32, 32, 4).clone()
        class Clip:
            def tokenize(self, prompt, **kwargs):
                calls["prompt"], calls["vision"] = prompt, kwargs
                return {}
            def encode_from_tokens_scheduled(self, tokens):
                return [[torch.zeros(1, 1, 1), {"image_slots": [0, 1]}]]
        modules = {name: types.ModuleType(name) for name in
                   ("comfy", "comfy.sample", "comfy.samplers", "comfy.model_management")}
        for name in ("sample", "samplers", "model_management"):
            setattr(modules["comfy"], name, modules["comfy." + name])
        modules["comfy.sample"].prepare_noise = lambda latent, seed: torch.ones_like(latent)
        def sample(model, noise, cfg, sampler, sigmas, positive, negative, latent, **kwargs):
            calls.update(positive=positive, latent=latent.clone(), noise_mask=kwargs.get("noise_mask"), sigmas=sigmas)
            # Simulate the sampler contract: the changed pixels are visible only
            # if the inpainting mask is actually passed, not merely stored in UI.
            amount = kwargs.get("noise_mask")
            if amount is None:
                return latent
            return latent * (1 - amount) + amount
        modules["comfy.sample"].sample_custom = sample
        modules["comfy.samplers"].sampler_object = lambda _: "synthetic Euler"
        pipeline = DirectPipeline("unused")
        pipeline.load = Mock(return_value=(object(), Clip(), Vae()))
        image = Image.new("RGBA", mask.size, (220, 30, 10, 255))
        with patch.dict(sys.modules, modules), patch.object(torch.cuda, "init", side_effect=AssertionError("no CUDA")):
            if via_worker:
                import base64
                from genfill.image_ops import decode_png, png_bytes
                from genstudio.direct_worker import Worker
                worker = Worker("unused")
                worker.pipeline = pipeline
                encode_picture = lambda picture: base64.b64encode(png_bytes(picture)).decode("ascii")
                from contextlib import nullcontext
                with patch("genstudio.direct_worker.gpu_lease", return_value=nullcontext()), \
                        patch("genstudio.direct_worker.release_gpu"):
                    try:
                        job = worker.submit({"image": encode_picture(image), "mask": encode_picture(mask),
                                             "seed": 42, "prompt": "A green tree"})
                    finally:
                        worker.pool.shutdown(wait=True)
                result = worker.jobs[job["id"]]
                self.assertEqual(result["state"], "done", result["message"])
                output = decode_png(result["result"]["image"], 32 * 32)
            else:
                output = pipeline.generate(image, mask, 42, "A green tree", threading.Event(), lambda _: None)
        self.assertEqual(len(encoded), 2)
        self.assertEqual(len(calls["vision"]["images"]), 2)
        self.assertEqual(len(calls["positive"][0][1]["reference_latents"]), 2)
        self.assertTrue(torch.equal(calls["latent"], torch.full((1, 64, 2, 2), 0.25)), "preserve the image latent, not a zero-filled canvas")
        self.assertEqual(calls["positive"][0][1]["image_slots"], [0, 1])
        self.assertEqual(calls["sigmas"].numel() - 1, 8)
        self.assertEqual(tuple(calls["noise_mask"].shape), (1, 1, 2, 2))
        self.assertTrue(np.isfinite(np.asarray(output)).all())
        return output, calls

    def test_white_is_regenerated_black_is_protected_and_fill_is_visible(self):
        mask = Image.new("L", (32, 32))
        mask.paste(255, (16, 16, 32, 32))
        output, calls = self.generate(mask)
        self.assertEqual(calls["noise_mask"].flatten().tolist(), [0.0, 0.0, 0.0, 1.0])
        self.assertEqual(output.getpixel((24, 24)), (0, 204, 26, 255))
        self.assertEqual(output.getpixel((0, 0))[3], 0, "the fill alpha override is limited to its mask")

    def test_one_pixel_soft_stroke_survives_vae_downsampling(self):
        mask = Image.new("L", (32, 32))
        mask.putpixel((18, 18), 1)
        output, calls = self.generate(mask)
        self.assertEqual(calls["noise_mask"].flatten().tolist(), [0.0, 0.0, 0.0, 1.0])
        self.assertEqual(output.getpixel((18, 18))[3], 255)

    def test_worker_png_roundtrip_runs_the_real_pipeline_with_fake_model_parts(self):
        mask = Image.new("L", (32, 32))
        mask.paste(255, (16, 16, 32, 32))
        output, calls = self.generate(mask, via_worker=True)
        self.assertEqual(calls["noise_mask"].flatten().tolist(), [0.0, 0.0, 0.0, 1.0])
        self.assertEqual(output.getpixel((24, 24)), (0, 204, 26, 255))


if __name__ == "__main__":
    unittest.main()
