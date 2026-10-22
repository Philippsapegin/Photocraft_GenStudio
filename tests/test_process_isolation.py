"""Regression coverage for stale hosts and competing Direct GPU processes.

All HTTP servers and pipelines here are synthetic. No model weights or CUDA.
"""
import base64
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from PIL import Image
from genfill.image_ops import png_bytes
from genstudio import control, segmentation
from genstudio.direct_worker import Worker
from genstudio.lifecycle import EngineHost, http_json
from genstudio.processes import identity, process_lock, release_gpu
from genstudio.service import create_server
from genstudio.settings import defaults


@contextmanager
def serving(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if hasattr(server, "studio"):
            server.studio.shutdown()
            server.manager.shutdown()


class IsolationTests(unittest.TestCase):
    def test_no_endpoint_never_probes_the_legacy_global_port(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(control, "ENDPOINT", Path(folder) / "missing.json"), \
                patch.object(control, "BASE", control.DEFAULT_BASE), \
                patch.dict("os.environ", {"GENSTUDIO_SERVICE_URL": ""}), patch.object(control, "http_json") as request:
            self.assertFalse(control.run("status")["ready"])
            request.assert_not_called()

    def test_old_host_is_never_sent_current_settings_and_new_host_is_started_once(self):
        calls = []
        class Legacy(BaseHTTPRequestHandler):
            def do_GET(self):
                calls.append("health")
                data = json.dumps({"service": "photocraft-genstudio", "version": "0.5.0"}).encode()
                self.send_response(200); self.end_headers(); self.wfile.write(data)
            def do_POST(self):
                calls.append("WRONG HOST")
                self.send_response(400); self.end_headers()
            def log_message(self, *args):
                pass
        host = EngineHost(defaults(), request=Mock(side_effect=URLError("offline")))
        with tempfile.TemporaryDirectory() as folder, \
                serving(ThreadingHTTPServer(("127.0.0.1", 0), Legacy)) as old, \
                serving(create_server(port=0, host=host)) as new:
            endpoint = Path(folder) / "endpoint.json"
            endpoint.write_text(json.dumps({"url": old, "identity": identity()}))
            def start(*args, **kwargs):
                endpoint.write_text(json.dumps({"url": new, "identity": identity()}))
                return Mock(poll=Mock(return_value=None))
            with patch.object(control, "ROOT", Path(folder)), patch.object(control, "ENDPOINT", endpoint), \
                    patch.object(control, "BASE", control.DEFAULT_BASE), \
                    patch.object(control, "hidden_process", side_effect=start) as spawn, \
                    patch("genstudio.service.save"), patch.dict("os.environ", {"GENSTUDIO_SERVICE_URL": ""}):
                control.run("configure", {**defaults(), "engine": "direct"})
                control.run("configure", {**defaults(), "engine": "direct"})
                self.assertEqual(spawn.call_count, 1)
                self.assertEqual(control.BASE, new)
                self.assertEqual(host.engine, "direct")
            self.assertIn("health", calls)
            self.assertNotIn("WRONG HOST", calls)

    def test_explicit_incompatible_service_is_rejected_before_configuration(self):
        with patch.dict("os.environ", {"GENSTUDIO_SERVICE_URL": "http://127.0.0.1:12345"}), \
                patch.object(control, "BASE", "http://127.0.0.1:12345"), \
                patch.object(control, "http_json", return_value={"service": "photocraft-genstudio", "version": "0.5.0"}) as request, \
                patch.object(control, "hidden_process") as spawn:
            with self.assertRaisesRegex(RuntimeError, "another version or installation"):
                control.run("configure", defaults())
            self.assertEqual(request.call_count, 1)
            spawn.assert_not_called()

    def test_same_version_of_another_installation_is_incompatible(self):
        other = {**identity(), "runtimeRoot": "another installation"}
        with patch.object(control, "BASE", "http://127.0.0.1:12345"), \
                patch.object(control, "http_json", return_value={"service": "photocraft-genstudio", "identity": other}):
            self.assertIsNone(control.health())

    def test_status_never_reconfigures_from_a_segmentation_request_or_stale_preset(self):
        host = EngineHost(defaults(), request=Mock(side_effect=URLError("offline")))
        with serving(create_server(port=0, host=host)) as base:
            http_json(base + "/engine", {"action": "status", "settings": {"engine": "direct", "model": "stale"}})
            self.assertEqual(host.engine, "comfy")

    def test_generator_configuration_does_not_overwrite_selection_preferences(self):
        settings = {**defaults(), "segmentation": {"enabled": True, "detector": "yolov8l-worldv2"}}
        host = EngineHost(settings, request=Mock(side_effect=URLError("offline")))
        before = dict(host.settings["segmentation"])
        with serving(create_server(port=0, host=host)) as base, patch("genstudio.service.save"):
            http_json(base + "/engine", {"action": "configure", "settings": {"engine": "direct"}})
        self.assertEqual(host.settings["segmentation"], before)

    def test_status_and_reconfiguration_do_not_wait_for_startup_lock(self):
        host = EngineHost(defaults(), request=Mock(side_effect=URLError("offline")))
        held, release = threading.Event(), threading.Event()
        def startup():
            with host.lock:
                host.message = "Starting the local AI engine"
                held.set()
                release.wait(5)
        thread = threading.Thread(target=startup)
        thread.start(); self.assertTrue(held.wait(2))
        try:
            with serving(create_server(port=0, host=host)) as base:
                before = time.monotonic()
                result = http_json(base + "/engine", {"action": "status"}, timeout=1)
                self.assertEqual(result["state"], "starting")
                with self.assertRaises(HTTPError):
                    http_json(base + "/engine", {"action": "configure", "settings": defaults()}, timeout=1)
                self.assertLess(time.monotonic() - before, 1)
        finally:
            release.set(); thread.join(5)

    def test_direct_worker_requires_the_owning_hosts_identity(self):
        settings = {**defaults(), "engine": "direct"}
        host = EngineHost(settings)
        host.direct_url = "http://127.0.0.1:54321"
        info = {"service": "genstudio-direct", "ready": True, "modelsDir": settings["modelsDir"],
                "model": settings["model"], "textEncoder": "", "vae": ""}
        host.request = Mock(return_value=info)
        self.assertFalse(host.probe())
        info["owner"] = "other host"
        self.assertFalse(host.probe())
        info["owner"] = host.worker_id
        self.assertTrue(host.probe())

    def test_segmentation_worker_cancels_while_waiting_without_importing_inference(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            waiting = threading.Event()
            errors = []
            def select():
                try:
                    segmentation.worker({"folder": folder})
                except InterruptedError:
                    pass
                except Exception as error:
                    errors.append(error)
            def lease(**kw):
                kw["waiting"] = waiting.set
                return process_lock(path / "gpu.lock", **kw)
            with patch.object(segmentation, "gpu_lease", side_effect=lease), \
                    patch.object(segmentation, "_worker") as infer:
                with process_lock(path / "gpu.lock"):
                    thread = threading.Thread(target=select)
                    thread.start(); self.assertTrue(waiting.wait(2))
                    (path / "cancel").touch()
                    thread.join(3)
                    self.assertFalse(thread.is_alive())
                self.assertFalse(errors)
                infer.assert_not_called()

    def test_lock_is_released_by_the_os_when_its_process_crashes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "gpu.lock"
            script = "from genstudio.processes import process_lock; import sys; " \
                     "guard=process_lock(sys.argv[1]); guard.__enter__(); print('ready',flush=True); sys.stdin.read()"
            child = subprocess.Popen([sys.executable, "-c", script, str(path)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                self.assertEqual(child.stdout.readline().strip(), b"ready")
                with self.assertRaises(TimeoutError):
                    with process_lock(path, timeout=0.1):
                        self.fail("A second process acquired a held GPU lock")
                child.kill(); child.wait(timeout=5)
                with process_lock(path, timeout=1):
                    pass
            finally:
                if child.poll() is None:
                    child.kill(); child.wait(timeout=5)
                child.stdin.close(); child.stdout.close(); child.stderr.close()

    def test_generation_failure_offloads_before_selection_can_acquire_gpu(self):
        with tempfile.TemporaryDirectory() as folder:
            lock = Path(folder) / "gpu.lock"
            def offload():
                with self.assertRaises(TimeoutError):
                    with process_lock(lock, timeout=0.05):
                        self.fail("GPU lock was released before offloading")
            worker = Worker("synthetic-models")
            worker.pipeline.generate = Mock(side_effect=RuntimeError("Synthetic out-of-memory"))
            encode = lambda im: base64.b64encode(png_bytes(im)).decode()
            try:
                with patch("genstudio.direct_worker.gpu_lease", side_effect=lambda **kw: process_lock(lock, **kw)), \
                        patch("genstudio.direct_worker.release_gpu", side_effect=offload) as release, \
                        patch.dict("os.environ", {"GENGATE_NO_INFERENCE": "0"}):
                    job = worker.submit({"image": encode(Image.new("RGB", (64, 64))),
                                         "mask": encode(Image.new("L", (64, 64), 255)), "seed": 1, "prompt": "synthetic"})
                    worker.pool.shutdown(wait=True)
                    self.assertEqual(worker.jobs[job["id"]]["state"], "error")
                    release.assert_called_once()
                    with process_lock(lock, timeout=1):
                        pass
            finally:
                worker.pool.shutdown(wait=True)

    def test_idle_gpu_release_does_not_import_torch_or_cuda(self):
        before = set(sys.modules)
        release_gpu()
        self.assertNotIn("torch", set(sys.modules) - before)


if __name__ == "__main__":
    unittest.main()
