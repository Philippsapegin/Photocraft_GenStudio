"""Pixel geometry regressions; synthetic images only, no model inference."""
import base64
from contextlib import nullcontext
import tempfile
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from PIL import Image
from genfill.image_ops import prepare_edit, restore_result, png_bytes, decode_png
from genstudio.edit import edit
from genstudio.settings import defaults, validate


class GeometryTests(unittest.TestCase):
    def test_grid_padding_preserves_every_source_pixel_and_restores_without_scaling(self):
        size = (763, 509)
        data = bytes(channel for y in range(size[1]) for x in range(size[0])
                     for channel in (x % 256, y % 256, (x + y) % 256, 255))
        source = Image.frombytes("RGBA", size, data)
        mask = Image.new("L", size, 255)
        prepared = prepare_edit(source, mask, grow=0, preserve_roi=True, pixel_aligned=True)
        self.assertEqual(prepared.model_image.size, (768, 512))
        self.assertEqual(prepared.model_image.crop((0, 0, *size)).tobytes(), source.tobytes())
        self.assertEqual(prepared.model_image.getpixel((767, 511)), source.getpixel((762, 508)))
        self.assertEqual(restore_result(prepared, prepared.model_image).tobytes(), source.tobytes())
        self.assertEqual(prepared.model_mask.getpixel((767, 511)), 0)

    def test_reported_large_crop_keeps_its_actual_pixel_resolution(self):
        size = (2528, 1696)
        source = Image.new("RGBA", size, "red")
        prepared = prepare_edit(source, Image.new("L", size, 255), grow=0, preserve_roi=True, pixel_aligned=True)
        self.assertEqual(prepared.model_image.size, size)
        self.assertEqual(restore_result(prepared, prepared.model_image).size, size)

    def test_economy_keeps_small_crops_pixel_identical(self):
        for size in ((763, 509), (32, 32), (768, 768), (128, 768)):
            with self.subTest(size=size):
                source = Image.effect_noise(size, 30).convert("RGBA")
                mask = Image.new("L", size, 255)
                full = prepare_edit(source, mask, grow=0, preserve_roi=True, pixel_aligned=True)
                economy = prepare_edit(source, mask, grow=0, preserve_roi=True, pixel_aligned=True, downsample=True)
                self.assertIsNone(economy.resized_size)
                self.assertEqual(economy.model_image.tobytes(), full.model_image.tobytes())
                self.assertEqual(economy.model_mask.tobytes(), full.model_mask.tobytes())
                self.assertEqual(restore_result(economy, economy.model_image).tobytes(), source.tobytes())

    def test_economy_reduces_progressively_up_to_two_without_distorting_to_the_grid(self):
        previous_ratio = 1
        for size in ((769, 511), (1024, 768), (1536, 1024), (2528, 1696), (3072, 2048), (4096, 1024)):
            with self.subTest(size=size):
                source = Image.new("RGBA", size, "red")
                mask = Image.new("L", size)
                mask.putpixel((size[0] // 2, size[1] // 2), 1)
                prepared = prepare_edit(source, mask, grow=0, preserve_roi=True, pixel_aligned=True, downsample=True)
                content_size = prepared.resized_size or prepared.padded_size
                self.assertTrue(all(n % 32 == 0 for n in prepared.model_image.size))
                ratios = [prepared.padded_size[i] / content_size[i] for i in (0, 1)]
                self.assertTrue(all(1 <= ratio <= 2 for ratio in ratios))
                self.assertGreaterEqual(ratios[0], previous_ratio)
                previous_ratio = ratios[0]
                # Uniform scaling is rounded only to pixels; grid alignment is padding.
                self.assertLessEqual(abs(content_size[0] / prepared.padded_size[0] - content_size[1] / prepared.padded_size[1]), 1 / min(prepared.padded_size))
                if size[0] >= 3072:
                    self.assertEqual(ratios, [2, 2])
                self.assertIsNotNone(prepared.model_mask.getbbox(), "A tiny stroke must survive the reduction")
                self.assertEqual(restore_result(prepared, prepared.model_image).size, size)

    def test_economy_removes_model_grid_padding_before_restoring_coordinates(self):
        size = (1024, 768)
        source = Image.new("RGBA", size, (13, 27, 55, 255))
        prepared = prepare_edit(source, Image.new("L", size, 255), grow=0, preserve_roi=True, pixel_aligned=True, downsample=True)
        self.assertEqual(prepared.model_image.size, (896, 672))
        self.assertEqual(prepared.resized_size, (887, 666))
        generated = Image.new("RGBA", prepared.model_image.size, "red")
        generated.paste((13, 27, 55, 255), (0, 0, *prepared.resized_size))
        self.assertEqual(restore_result(prepared, generated).tobytes(), source.tobytes())

    def test_economy_output_does_not_shrink_when_a_crop_grows_near_the_half_size_threshold(self):
        previous = 0
        for width in range(3050, 3081):
            size = (width, 32)
            prepared = prepare_edit(Image.new("RGBA", size), Image.new("L", size, 255),
                                    grow=0, preserve_roi=True, pixel_aligned=True, downsample=True)
            self.assertGreaterEqual(prepared.model_image.width, previous)
            previous = prepared.model_image.width

    def test_wrong_engine_dimensions_are_rejected_instead_of_stretched(self):
        prepared = prepare_edit(Image.new("RGB", (763, 509)), Image.new("L", (763, 509), 255), pixel_aligned=True, preserve_roi=True)
        with self.assertRaisesRegex(ValueError, "different dimensions"):
            restore_result(prepared, Image.new("RGBA", (512, 512)))

    def test_checkbox_defaults_off_and_rejects_non_boolean_values(self):
        self.assertFalse(defaults()["reduceEditResolution"])
        self.assertFalse(validate({})["reduceEditResolution"])
        self.assertTrue(validate({"reduceEditResolution": True})["reduceEditResolution"])
        for value in (None, 0, "true", []):
            with self.assertRaises(ValueError):
                validate({"reduceEditResolution": value})

    def test_native_transport_has_no_768_pixel_cap_and_forwards_the_checkbox(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            for name, image in (("input.png", Image.new("RGBA", (32, 32))), ("mask.png", Image.new("L", (32, 32), 255))):
                (folder / name).write_bytes(png_bytes(image))
            for enabled in (False, True):
                captured = []
                def request(url, body=None, **kwargs):
                    if url.endswith("/jobs"):
                        captured.append(body); return {"id": "synthetic"}
                    if url.endswith("/result"):
                        encode = lambda image: base64.b64encode(png_bytes(image)).decode()
                        return {"image": encode(Image.new("RGBA", (32, 32))), "mask": encode(Image.new("L", (32, 32))), "x": 0, "y": 0}
                    return {"state": "done", "message": "Done"}
                edit(folder, {"reduceEditResolution": enabled}, lambda e: None, request=request, configure=lambda *a: None)
                self.assertTrue(captured[0]["pixel_aligned"])
                self.assertEqual(captured[0]["downsample"], enabled)
                self.assertNotIn("max_size", captured[0])

    def test_direct_transport_accepts_a_large_edit_without_the_old_one_megapixel_limit(self):
        from genstudio.direct_worker import Worker
        worker = Worker("synthetic")
        size = (2528, 1696)
        calls = []
        def generate(image, mask, *args):
            calls.append(image.size)
            return Image.new("RGBA", image.size, "green")
        worker.pipeline.generate = generate
        encode = lambda image: base64.b64encode(png_bytes(image)).decode()
        with patch("genstudio.direct_worker.gpu_lease", return_value=nullcontext()), patch("genstudio.direct_worker.release_gpu"):
            job = worker.submit({"image": encode(Image.new("RGB", size)), "mask": encode(Image.new("L", size, 255)), "seed": 0, "prompt": "synthetic"})
            worker.pool.shutdown(wait=True)
        result = worker.jobs[job["id"]]
        self.assertEqual(result["state"], "done", result["message"])
        self.assertEqual(calls, [size])
        self.assertEqual(decode_png(result["result"]["image"], 16 << 20).size, size)

    def test_flux_masked_entrypoint_accepts_native_sizes_while_studio_keeps_its_limit(self):
        from genstudio.flux_pipeline import FluxPipeline
        pipeline = FluxPipeline("synthetic")
        class NoGpu(RuntimeError): pass
        with patch.object(pipeline, "check", return_value=[]), \
                patch("genstudio.flux_pipeline.import_core", side_effect=NoGpu("No GPU may be imported")) as core:
            with self.assertRaises(NoGpu):
                pipeline.generate(Image.new("RGBA", (2528, 1696)), Image.new("L", (2528, 1696), 255),
                                  0, "synthetic", threading.Event(), lambda _: None)
            core.assert_called_once()
            with self.assertRaisesRegex(ValueError, "dimensions"):
                pipeline.generate_studio([], 0, "synthetic", 2528, 1696, threading.Event(), lambda _: None)

    def test_full_rectangle_returns_unpadded_coordinates_and_original_mask(self):
        from genfill.server import JobManager
        from genfill.server import load_config
        import time
        class Echo:
            def generate(self, image, mask, *args): return image
        manager = JobManager(load_config(), Echo())
        encode = lambda image: base64.b64encode(png_bytes(image)).decode()
        source = Image.new("RGBA", (763, 509), (13, 27, 55, 255))
        mask = Image.new("L", source.size); mask.paste(255, (201, 101, 462, 303))
        try:
            job = manager.submit({"image": encode(source), "mask": encode(mask), "pixel_aligned": True,
                                  "preserve_roi": True, "return_rectangle": True, "grow": 0, "feather": 0})
            deadline = time.monotonic() + 5
            while job.state not in ("done", "error") and time.monotonic() < deadline: time.sleep(.01)
            self.assertEqual(job.state, "done", job.message)
            self.assertEqual((job.result["x"], job.result["y"]), (0, 0))
            self.assertEqual(decode_png(job.result["image"], 16 << 20).tobytes(), source.tobytes())
            self.assertEqual(decode_png(job.result["mask"], 16 << 20).tobytes(), mask.tobytes())
        finally:
            manager.shutdown()


if __name__ == "__main__": unittest.main()
