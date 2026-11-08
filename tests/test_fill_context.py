"""Source context and seam composition; synthetic pixels only."""
import base64
from contextlib import nullcontext
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw

from genfill.image_ops import inward_feather, png_bytes
from genfill.server import JobManager
from genstudio.edit import edit
from genstudio.flux_pipeline import FluxPipeline
from genstudio.direct_worker import Worker


def encoded(image):
    return base64.b64encode(png_bytes(image)).decode('ascii')


class FillContextTests(unittest.TestCase):
    def test_native_fill_preserves_source_while_remove_keeps_a_hole(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            Image.new('RGBA', (32, 32), 'red').save(folder / 'input.png')
            Image.new('L', (32, 32), 255).save(folder / 'mask.png')
            calls = []
            def request(url, body=None, **kwargs):
                if url.endswith('/jobs'):
                    calls.append(body)
                    return {'id': 'fixture'}
                if url.endswith('/result'):
                    return {'image': encoded(Image.new('RGBA', (32, 32), 'blue')),
                            'mask': encoded(Image.new('L', (32, 32), 255)), 'x': 0, 'y': 0}
                return {'state': 'done', 'message': 'Done'}
            for prompt in ('A blue jacket', None):
                edit(folder, {}, lambda _: None, request=request, configure=lambda *a: None, prompt=prompt)
            self.assertFalse(calls[0]['masked_input'])
            self.assertTrue(calls[1]['masked_input'])
            self.assertGreater(calls[0]['feather'], calls[1]['feather'])

    def test_source_pixels_reach_fill_backend_and_outside_mask_is_untouched(self):
        image = Image.new('RGBA', (64, 64), (200, 30, 10, 255))
        mask = Image.new('L', image.size)
        ImageDraw.Draw(mask).rectangle((16, 16, 47, 47), fill=255)
        calls = []
        class Backend:
            def generate_edit(self, source, mask, *args, **kwargs):
                calls.append(source.getpixel((24, 24)))
                return Image.new('RGBA', source.size, 'blue')
        manager = JobManager({'diffusion_model': 'fixture'}, client=Backend())
        job = manager.submit({'image': encoded(image), 'mask': encoded(mask), 'prompt': 'A jacket',
                              'mode': 'fill', 'preserve_roi': True, 'return_rectangle': True,
                              'masked_input': False, 'feather': 16})
        manager.pool.shutdown(wait=True)
        self.assertEqual(job.state, 'done', job.message)
        from genfill.image_ops import decode_png
        result = decode_png(job.result['mask'], 64 * 64).convert('L')
        self.assertEqual(calls, [(200, 30, 10, 255)])
        self.assertEqual(result.getpixel((15, 24)), 0)
        self.assertLess(result.getpixel((16, 24)), 64)
        self.assertEqual(result.getpixel((32, 32)), 255)

    def test_flux_fill_uses_one_intact_studio_reference(self):
        pipeline = FluxPipeline('unused')
        image = Image.new('RGBA', (32, 32), (220, 30, 10, 255))
        mask = Image.new('L', image.size, 255)
        pipeline.generate_studio = Mock(return_value=image)
        pipeline.generate_fill(image, mask, 42, 'A jacket', threading.Event(), lambda _: None)
        references = pipeline.generate_studio.call_args.args[0]
        self.assertEqual(len(references), 1)
        self.assertEqual(references[0].tobytes(), image.tobytes())
        self.assertTrue(pipeline.generate_studio.call_args.kwargs['_constant_lora'])

    def test_worker_routes_fill_and_remove_to_different_pipeline_paths(self):
        for mode in ('fill', 'remove'):
            worker = Worker('unused')
            worker.pipeline.generate_fill = Mock(return_value=Image.new('RGBA', (32, 32), 'blue'))
            worker.pipeline.generate = Mock(return_value=Image.new('RGBA', (32, 32), 'red'))
            with patch('genstudio.direct_worker.gpu_lease', return_value=nullcontext()), \
                    patch('genstudio.direct_worker.release_gpu'):
                job = worker.submit({'image': encoded(Image.new('RGB', (32, 32))),
                                     'mask': encoded(Image.new('L', (32, 32), 255)),
                                     'seed': 1, 'prompt': 'fixture', 'mode': mode})
                worker.pool.shutdown(wait=True)
            self.assertEqual(worker.jobs[job['id']]['state'], 'done')
            self.assertEqual(worker.pipeline.generate_fill.call_count, int(mode == 'fill'))
            self.assertEqual(worker.pipeline.generate.call_count, int(mode == 'remove'))

    def test_feather_does_not_erase_tiny_selection_or_fade_whole_canvas(self):
        full = Image.new('L', (32, 32), 255)
        self.assertEqual(inward_feather(full, 16).tobytes(), full.tobytes())
        single = Image.new('L', (32, 32))
        single.putpixel((16, 16), 255)
        self.assertEqual(inward_feather(single, 16).tobytes(), single.tobytes())


if __name__ == '__main__':
    unittest.main()
