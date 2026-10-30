"""Readiness uses a synthetic worker; no checkpoints, tensor imports or CUDA."""
import base64
from contextlib import nullcontext
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from genfill.image_ops import png_bytes
from genstudio.direct_worker import Handler, Worker
from genstudio.lifecycle import EngineHost, http_json
from genstudio.settings import component_defaults, defaults


class DirectReadinessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.pipeline = Mock(models=Path(self.directory.name), components=component_defaults(), text_encoder="", vae="", pipe=None)
        self.pipeline.check.return_value = []
        with patch("genstudio.direct_worker.DirectPipeline", return_value=self.pipeline):
            self.worker = Worker(self.directory.name, owner="synthetic-owner")
        self.addCleanup(self.worker.pool.shutdown, wait=True)

    def test_health_reports_service_running_without_loading_the_model(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.worker = self.worker
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = http_json(f"http://127.0.0.1:{server.server_port}/health")
            self.assertTrue(result["ready"])
            self.assertEqual(result["modelState"], "unloaded")
            self.assertFalse(result["modelLoaded"])
            self.pipeline.load.assert_not_called()
        finally:
            server.shutdown(); server.server_close(); thread.join(3)

    def test_generation_reports_loading_then_loaded_and_preserves_errors(self):
        entered, finish_loading, loaded, finish_job = (threading.Event() for _ in range(4))
        def generate(*args):
            entered.set()
            if not finish_loading.wait(3): raise RuntimeError("test loading timeout")
            self.pipeline.pipe = (object(), object(), object())
            loaded.set()
            if not finish_job.wait(3): raise RuntimeError("test job timeout")
            return Image.new("RGB", (64, 64))
        self.pipeline.generate.side_effect = generate
        encode = lambda image: base64.b64encode(png_bytes(image)).decode()
        data = {"image": encode(Image.new("RGB", (64, 64))), "mask": encode(Image.new("L", (64, 64))), "prompt": "synthetic", "seed": 1}
        with patch("genstudio.direct_worker.gpu_lease", return_value=nullcontext()), patch("genstudio.direct_worker.release_gpu"), patch.dict("os.environ", {"GENGATE_NO_INFERENCE": "0"}):
            try:
                job = self.worker.submit(data)
                self.assertTrue(entered.wait(3))
                self.assertEqual(self.worker.health()["modelState"], "loading")
                finish_loading.set(); self.assertTrue(loaded.wait(3))
                self.assertEqual(self.worker.health()["modelState"], "loaded")
                finish_job.set()
                self.worker.pool.shutdown(wait=True)
                self.assertEqual(self.worker.jobs[job["id"]]["state"], "done")
                self.assertTrue(self.worker.health()["modelLoaded"])
            finally:
                finish_loading.set(); finish_job.set()
        self.pipeline.generate.side_effect = RuntimeError("synthetic model failure")
        job = {"id": "failure", "state": "queued", "message": "", "cancel": threading.Event()}
        with patch("genstudio.direct_worker.gpu_lease", return_value=nullcontext()), patch("genstudio.direct_worker.release_gpu"), patch.dict("os.environ", {"GENGATE_NO_INFERENCE": "0"}):
            self.worker.run(job, Image.new("RGB", (64, 64)), Image.new("L", (64, 64)), 1, "synthetic")
        health = self.worker.health()
        self.assertTrue(health["ready"], "the service remains available after a failed model job")
        self.assertEqual(health["modelState"], "error")
        self.assertFalse(health["modelLoaded"])
        self.assertIn("synthetic model failure", health["modelError"])

    def test_host_distinguishes_service_model_and_legacy_worker_readiness(self):
        host = EngineHost({**defaults(), "engine": "direct", "modelsDir": self.directory.name})
        host.direct_url = "http://127.0.0.1:1"
        info = self.worker.health()
        info["owner"] = host.worker_id
        host.request = Mock(return_value=info)
        status = host.status()
        self.assertTrue(status["ready"])
        self.assertTrue(status["serviceReady"])
        self.assertFalse(status["modelLoaded"])
        self.assertIn("loads on first generation", status["message"])
        for state in ("loading", "loaded", "error"):
            info.update(modelState=state, modelLoaded=state == "loaded", modelError="synthetic failure")
            self.assertEqual(host.status()["modelState"], state)
        for key in ("modelState", "modelLoaded", "modelError"): info.pop(key)
        status = host.status()
        self.assertEqual(status["modelState"], "unknown")
        self.assertIsNone(status["modelLoaded"])
        self.assertIn("status unavailable", status["message"])
        comfy = EngineHost({**defaults(), "engine": "comfy"}, request=Mock(return_value={"system": {}}))
        self.assertEqual(comfy.status()["message"], "ComfyUI connected")


if __name__ == "__main__":
    unittest.main()
