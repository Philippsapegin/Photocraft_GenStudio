"""Remove transport/rectangle tests with synthetic pixels only; no neural inference."""
import base64
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image

from genfill.image_ops import decode_png, masked_reference, png_bytes
from genfill.server import JobManager
from genstudio.edit import edit, REMOVE_PROMPT


def encoded(image):
    return base64.b64encode(png_bytes(image)).decode("ascii")


class FakeModel:
    model_name = "synthetic test pixels, not Qwen"
    def __init__(self):
        self.inputs = []
    def generate(self, image, mask, seed, prompt, cancel, progress):
        self.inputs.append((image.copy(), mask.copy(), prompt))
        return Image.new("RGBA", image.size, (20, 180, 40, 255))


class RemoveRectangleTests(unittest.TestCase):
    def test_missing_area_is_transparent_and_does_not_leak_original_rgb(self):
        image = Image.new("RGBA", (3, 1), (255, 0, 0, 255))
        mask = Image.new("L", image.size)
        mask.putpixel((1, 0), 1)
        result = masked_reference(image, mask)
        self.assertEqual(result.getpixel((1, 0)), (255, 255, 255, 0))
        self.assertEqual(result.getpixel((0, 0)), image.getpixel((0, 0)))
        self.assertEqual(image.getpixel((1, 0)), (255, 0, 0, 255))

    def test_whole_rectangle_and_editable_mask_are_returned_separately(self):
        image = Image.new("RGBA", (416, 288), (200, 20, 10, 255))
        mask = Image.new("L", image.size)
        for y in range(100, 120):
            for x in range(200, 220):
                mask.putpixel((x, y), 255)
        model = FakeModel()
        manager = JobManager({"diffusion_model": "test"}, model)
        try:
            job = manager.submit({"image": encoded(image), "mask": encoded(mask),
                                  "preserve_roi": True, "return_rectangle": True, "masked_input": True})
            deadline = time.monotonic() + 5
            while job.state not in ("done", "error") and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(job.state, "done", job.message)
            result = decode_png(job.result["image"], 1000000)
            coverage = decode_png(job.result["mask"], 1000000)
            self.assertEqual(result.size, image.size)
            self.assertEqual((job.result["x"], job.result["y"]), (0, 0))
            self.assertEqual(result.getpixel((0, 0)), (20, 180, 40, 255), "context pixels exist behind the mask")
            self.assertEqual(coverage.getpixel((0, 0)), 0)
            self.assertGreater(coverage.getpixel((210, 110)), 250)
            self.assertEqual(model.inputs[0][0].getpixel((210, 110)), (255, 255, 255, 0))
        finally:
            manager.shutdown()

    def test_new_protocol_options_are_typed_before_a_job_is_queued(self):
        manager = JobManager({"diffusion_model": "test"}, FakeModel())
        try:
            for key in ("preserve_roi", "return_rectangle", "masked_input"):
                with self.assertRaises(ValueError):
                    manager.submit({"image": encoded(Image.new("RGB", (32, 32))),
                                    "mask": encoded(Image.new("L", (32, 32), 255)), key: "yes"})
            self.assertFalse(manager.jobs)
        finally:
            manager.shutdown()


class NativeTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="PhotoCraft Remove with spaces ")
        self.folder = Path(self.temp.name)
        for name, image in (("input.png", Image.new("RGBA", (32, 32), "red")),
                            ("mask.png", Image.new("L", (32, 32), 255))):
            (self.folder / name).write_bytes(png_bytes(image))
        self.requests = []
        self.configured = []
    def tearDown(self):
        self.temp.cleanup()
    def configure(self, *args):
        self.configured.append(args)
    def request(self, url, body=None, **kwargs):
        self.requests.append((url, body))
        if url.endswith("/cancel"):
            return {"state": "cancelled"}
        if url.endswith("/jobs"):
            return {"id": "our-job"}
        if url.endswith("/result"):
            return {"image": encoded(Image.new("RGBA", (32, 32), "green")),
                    "mask": encoded(Image.new("L", (32, 32), 255)), "x": 0, "y": 0}
        return {"state": "done", "message": "Done"}

    def test_static_prompt_and_rectangle_round_trip(self):
        events = []
        edit(self.folder, {"engine": "direct"}, events.append, request=self.request, configure=self.configure)
        self.assertEqual(self.configured, [("configure", {"engine": "direct"})])
        body = next(body for url, body in self.requests if url.endswith("/jobs"))
        self.assertEqual(body["prompt"], REMOVE_PROMPT)
        self.assertTrue(body["masked_input"] and body["preserve_roi"] and body["return_rectangle"])
        self.assertTrue((self.folder / "result.png").is_file())
        self.assertTrue((self.folder / "result-mask.png").is_file())
        self.assertTrue(events[-1]["done"])
        self.assertFalse(any(url.endswith("/cancel") for url, _ in self.requests))

    def test_cancel_reaches_only_the_submitted_job_and_writes_no_result(self):
        def emit(event):
            if event.get("message") == "Done":
                (self.folder / "cancel").touch()
        with self.assertRaises(InterruptedError):
            edit(self.folder, {}, emit, request=self.request, configure=self.configure)
        cancelled = [url for url, _ in self.requests if url.endswith("/cancel")]
        self.assertEqual(len(cancelled), 1)
        self.assertTrue(cancelled[0].endswith("/jobs/our-job/cancel"))
        self.assertFalse((self.folder / "result.png").exists())

    def test_cancel_before_connect_starts_nothing(self):
        (self.folder / "cancel").touch()
        with self.assertRaises(InterruptedError):
            edit(self.folder, {}, lambda _: None, request=self.request, configure=self.configure)
        self.assertFalse(self.configured or self.requests)

    def test_real_http_file_bridge_uses_only_the_isolated_synthetic_backend(self):
        from genstudio import control
        from genstudio.service import create_server
        from genstudio.settings import defaults
        class Host(FakeModel):
            settings = defaults()
            lock = threading.RLock()
            def config(self):
                return {"diffusion_model":"test"}
            def status(self):
                return {"ready":True,"state":"ready"}
        model = Host()
        server = create_server(model.settings, port=0, host=model)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(control,"BASE",f"http://127.0.0.1:{server.server_port}"),patch("genstudio.service.save",lambda _:None):
                edit(self.folder,model.settings,lambda _:None)
            self.assertEqual(len(model.inputs),1)
            self.assertEqual(model.inputs[0][0].getpixel((0,0)),(255,255,255,0))
            self.assertTrue((self.folder/"result.png").is_file())
        finally:
            server.shutdown()
            server.manager.shutdown()
            server.server_close()
