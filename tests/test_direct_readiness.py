"""Readiness uses a synthetic worker; no checkpoints, tensor imports or CUDA."""
import base64
from contextlib import nullcontext
from http.server import ThreadingHTTPServer
from pathlib import Path
import json
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError

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

    def test_unreachable_running_worker_explains_transport_failure_and_recovers(self):
        host = EngineHost({**defaults(), "engine": "direct", "modelsDir": self.directory.name})
        host.direct_url = "http://127.0.0.1:12345"
        host.process = Mock(poll=Mock(return_value=None))
        host.request = Mock(side_effect=URLError("synthetic connection refused"))
        with self.assertLogs("genstudio.lifecycle", level="WARNING") as log:
            status = host.status()
            host.status()
        self.assertEqual(len(log.output), 1, "identical idle probe failures must not flood the log")
        self.assertFalse(status["ready"])
        self.assertTrue(status["owned"])
        self.assertIn("process is running", status["message"])
        self.assertIn(host.direct_url, status["message"])
        self.assertIn("synthetic connection refused", status["message"])
        host.request = Mock(return_value={**self.worker.health(), "owner": host.worker_id})
        self.assertTrue(host.status()["ready"])
        self.assertEqual(host.probe_error, "")
        host.process.terminate.assert_not_called()

    def test_readiness_reports_worker_mismatch_and_dependency_problems(self):
        host = EngineHost({**defaults(), "engine": "direct", "modelsDir": self.directory.name})
        host.direct_url = "http://127.0.0.1:12345"
        info = {**self.worker.health(), "owner": host.worker_id}
        host.request = Mock(return_value=info)
        for change, reason in (({"owner": "another host"}, "another engine host"),
                               ({"components": {}}, "different model components"),
                               ({"ready": False, "problems": ["Missing dependency: synthetic"]}, "Missing dependency: synthetic")):
            host.request.return_value = {**info, **change}
            self.assertFalse(host.probe())
            self.assertIn(reason, host.probe_error)
            self.assertFalse(host.worker_status)

    def test_start_timeout_preserves_last_probe_reason_without_restarting_live_worker(self):
        host = EngineHost({**defaults(), "engine": "direct", "modelsDir": self.directory.name})
        host.direct_url = "http://127.0.0.1:12345"
        host.settings["directPython"] = __file__
        host.process = Mock(poll=Mock(return_value=None))
        host.spawn = Mock(side_effect=AssertionError("An existing process must not be replaced"))
        host.request = Mock(side_effect=TimeoutError("synthetic health request timed out"))
        with patch("genstudio.models.resolve_components"), patch("genstudio.lifecycle.time.monotonic", side_effect=[0, 301]):
            with self.assertRaisesRegex(RuntimeError, "Engine startup timed out.*12345.*synthetic health request timed out"):
                host.ensure("manual")
        host.spawn.assert_not_called()
        host.process.terminate.assert_not_called()

    def test_local_transport_bypasses_a_system_proxy_even_without_a_bypass_rule(self):
        from http.server import BaseHTTPRequestHandler
        from urllib.request import ProxyHandler, build_opener
        proxy_calls = []
        class Proxy(BaseHTTPRequestHandler):
            def do_GET(self):
                proxy_calls.append(self.path)
                payload = json.dumps({"service": "synthetic-system-proxy"}).encode()
                self.send_response(200); self.end_headers(); self.wfile.write(payload)
            def log_message(self, *args):
                pass
        proxy = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
        worker = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker.worker = self.worker
        threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (proxy, worker)]
        for thread in threads: thread.start()
        try:
            url = f"http://127.0.0.1:{worker.server_port}/health"
            proxy_url = f"http://127.0.0.1:{proxy.server_port}"
            with patch("urllib.request.getproxies", return_value={"http": proxy_url}), patch("urllib.request.proxy_bypass", return_value=False):
                # Confirm this fixture routes the old default transport to the
                # proxy; neither server sends any traffic outside this test.
                with build_opener(ProxyHandler()).open(url, timeout=2) as response:
                    self.assertEqual(json.load(response)["service"], "synthetic-system-proxy")
                self.assertEqual(http_json(url)["service"], "genstudio-direct")
                self.assertEqual(len(proxy_calls), 1)
        finally:
            for server in (proxy, worker): server.shutdown(); server.server_close()
            for thread in threads: thread.join(3)


if __name__ == "__main__":
    unittest.main()
