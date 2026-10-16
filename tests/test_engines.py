"""Engine lifecycle and real HTTP transport. Never loads model weights."""
from contextlib import contextmanager
from io import BytesIO
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from PIL import Image

from genstudio.settings import defaults, validate, load, save
from genstudio.lifecycle import EngineHost
from genstudio.service import create_server
from genstudio.direct_worker import Worker


class Process:
    def __init__(self):
        self.stopped = False
    def poll(self):
        return 0 if self.stopped else None
    def terminate(self):
        self.stopped = True
    def wait(self, **kwargs):
        return 0


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="AI engine with spaces ")
        # Windows runners may return an 8.3 short TEMP path; production model
        # resolution deliberately canonicalizes junctions and aliases.
        self.root = Path(self.temp.name).resolve()
        (self.root / "main.py").write_text("# test")
        (self.root / "python.exe").write_text("# test")
        from genstudio.models import FILES
        for name, folders in FILES.values():
            path = self.root / "models" / folders[0] / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"checkpoint fixture - no real weights")
        self.settings = {**defaults(), "comfyDir": str(self.root), "comfyPython": str(self.root / "python.exe"),
                         "modelsDir": str(self.root / "models")}
        self.started = []
        self.online = False
        self.host = EngineHost(self.settings, self.spawn, self.request)
        self.host.runtime = self.root / "logs"

    def tearDown(self):
        self.host.busy = False
        self.host.stop()
        self.temp.cleanup()

    def request(self, url, **kwargs):
        if self.online:
            return {"system": {"os": "win32"}}
        raise URLError("offline")

    def spawn(self, command, **kwargs):
        self.started.append((command, kwargs))
        self.online = True
        return Process()

    @contextmanager
    def inspect(self):
        with patch("genstudio.lifecycle.ComfyClient.inspect", return_value=({}, [])):
            yield

    def test_off_blocks_request_but_allows_manual_start(self):
        self.host.settings["comfyStartup"] = "off"
        with self.assertRaisesRegex(ValueError, "Automatic start is off"):
            self.host.ensure("request")
        self.assertFalse(self.started)
        with self.inspect():
            self.assertTrue(self.host.ensure("manual")["ready"])
        self.assertEqual(len(self.started), 1)

    def test_first_request_policy_does_not_start_at_app_launch(self):
        with self.assertRaisesRegex(ValueError, "Automatic start is off"):
            self.host.ensure("appStart")
        with self.inspect():
            notices = []
            self.host.ensure("request", notices.append)
            self.host.ensure("request", notices.append)
        self.assertEqual(len(self.started), 1)
        self.assertEqual(len(notices), 1)
        command, kwargs = self.started[0]
        self.assertEqual(command[0], str(self.root / "python.exe"))
        self.assertIn("--disable-dynamic-vram", command)
        self.assertIn("--extra-model-paths-config", command)
        paths = json.loads((self.host.runtime / "model-paths.yaml").read_text())
        self.assertEqual(Path(paths["genstudio"]["base_path"]), self.root / "models")
        self.assertEqual(kwargs["cwd"], self.root)

    def test_app_start_policy_and_concurrent_requests_start_once(self):
        self.host.settings["comfyStartup"] = "appStart"
        with self.inspect():
            threads = [threading.Thread(target=self.host.ensure, args=("appStart",)) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(len(self.started), 1)

    def test_switch_stops_owned_engine_and_keeps_separate_policies(self):
        with self.inspect():
            self.host.ensure("manual")
        process = self.host.process
        self.host.configure({**self.settings, "engine": "direct", "comfyStartup": "appStart", "directStartup": "off"})
        self.assertTrue(process.stopped)
        self.assertIsNone(self.host.process)
        self.assertEqual(self.host.policy(), "off")
        self.assertEqual(self.host.settings["comfyStartup"], "appStart")

    def test_attached_external_server_is_reused_and_never_stopped(self):
        self.online = True
        self.host.settings["comfyStartup"] = "off"
        with self.inspect():
            self.assertTrue(self.host.ensure("request")["ready"])
        self.assertFalse(self.host.stop()["stopped"])
        self.assertFalse(self.started)

    def test_busy_engine_cannot_switch_or_stop(self):
        self.host.busy = True
        with self.assertRaisesRegex(ValueError, "Wait"):
            self.host.configure({**self.settings, "engine": "direct"})
        with self.assertRaisesRegex(ValueError, "Wait"):
            self.host.stop()
        self.assertEqual(self.host.engine, "comfy")

    def test_policy_only_change_keeps_running_engine(self):
        with self.inspect():
            self.host.ensure("manual")
        process = self.host.process
        self.host.configure({**self.settings, "comfyStartup": "off"})
        self.assertIs(self.host.process, process)
        self.assertFalse(process.stopped)

    def test_direct_does_not_fall_back_to_comfy_or_download_models(self):
        self.host.configure({**self.settings, "engine": "direct", "directPython": str(self.root / "missing.exe")})
        with self.assertRaisesRegex(ValueError, "not installed"):
            self.host.ensure("manual")
        self.assertFalse(self.started)

    def test_invalid_config_does_not_mutate_current_engine(self):
        with self.assertRaises(ValueError):
            self.host.configure({**self.settings, "comfyUrl": "http://example.com:8190"})
        self.assertEqual(self.host.settings, self.settings)


class SettingsTests(unittest.TestCase):
    def test_roundtrip_and_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            settings = {**defaults(), "directStartup": "appStart", "comfyStartup": "off"}
            save(settings, path)
            self.assertEqual(settings, load(path))
            for bad in ({"engine": "bogus"}, {"directStartup": True}, {"directModel": True}, {"comfyUrl": "http://127.0.0.1:0"}, {"unknown": "x"}):
                with self.assertRaises(ValueError):
                    save(bad, path)
                self.assertEqual(settings, load(path))


class ModelSourceTests(unittest.TestCase):
    def test_standalone_flat_and_comfy_nested_use_original_files(self):
        from genstudio.models import FILES, resolve
        with tempfile.TemporaryDirectory(prefix="Models source ") as temp:
            root = Path(temp)
            for name, folders in FILES.values():
                (root / name).write_bytes(b"fixture")
            flat = resolve(root)
            self.assertEqual(len(flat), 3)
            for key, (name, folders) in FILES.items():
                target = root / folders[0] / "Qwen" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                (root / name).rename(target)
            nested = resolve(root)
            self.assertTrue(all(value["name"].startswith("Qwen/") for value in nested.values()))
            self.assertEqual(sum(1 for p in root.rglob("*.safetensors")), 3)
            self.assertTrue(all(Path(value["path"]).is_file() for value in nested.values()))

    def test_missing_and_ambiguous_sources_fail_without_downloading(self):
        from genstudio.models import FILES, resolve
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(ValueError, "missing"):
                resolve(root)
            for name, _ in FILES.values():
                (root / name).write_bytes(b"fixture")
            name = FILES["diffusion_model"][0]
            (root / "diffusion_models").mkdir()
            (root / "diffusion_models" / name).write_bytes(b"other fixture")
            with self.assertRaisesRegex(ValueError, "multiple copies"):
                resolve(root)


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.host = EngineHost(defaults(), request=lambda *a, **k: (_ for _ in ()).throw(URLError("offline")))
        self.server = create_server(port=0, host=self.host)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server.manager.shutdown()
        self.thread.join()

    def call(self, data=None, headers=None, route="/engine"):
        payload = json.dumps(data).encode() if data is not None else None
        with urlopen(Request(self.base + route, data=payload, headers=headers or {"Content-Type": "application/json"}), timeout=5) as reply:
            return json.load(reply)

    def test_real_host_identification_and_status_without_generation(self):
        self.assertEqual(self.call(route="/health")["service"], "photocraft-genstudio")
        self.assertFalse(self.call({"action": "status"})["ready"])

    def test_refresh_with_unchanged_settings_is_available_while_engine_is_busy(self):
        self.host.busy = True
        try:
            self.assertTrue(self.call({"action": "status", "settings": self.host.settings})["busy"])
            with self.assertRaises(HTTPError):
                self.call({"action": "configure", "settings": {**self.host.settings, "engine": "direct"}})
            self.assertEqual(self.host.engine, "comfy")
        finally:
            self.host.busy = False
        self.assertFalse(self.server.manager.jobs)

    def test_bad_browser_requests_and_actions_rejected(self):
        for data, headers in (({"action": "bad"}, None), ({"action": "status"}, {"Origin": "https://example.com"})):
            with self.assertRaises(HTTPError) as caught:
                self.call(data, headers)
            self.assertIn(caught.exception.code, (400, 403))


class DirectWorkerTests(unittest.TestCase):
    def test_worker_contract_with_lightweight_pipeline(self):
        from genfill.image_ops import png_bytes
        import base64
        worker = Worker("missing-model")
        with self.assertRaisesRegex(ValueError, "request object"):
            worker.submit([])
        calls = []
        def generate(image, mask, seed, prompt, cancel, progress):
            calls.append((image.size, mask.size, seed, prompt))
            return Image.new("RGBA", image.size, "red")
        worker.pipeline.generate = generate
        encode = lambda image: base64.b64encode(png_bytes(image)).decode()
        job = worker.submit({"image": encode(Image.new("RGB", (64, 64))), "mask": encode(Image.new("L", (64, 64), 255)), "seed": 42, "prompt": "a tree"})
        worker.pool.shutdown(wait=True)
        result = worker.jobs[job["id"]]
        self.assertEqual(result["state"], "done")
        self.assertEqual(calls, [((64, 64), (64, 64), 42, "a tree")])
        self.assertEqual(Image.open(BytesIO(base64.b64decode(result["result"]["image"]))).getpixel((0, 0)), (255, 0, 0, 255))
        self.assertIsNone(worker.pipeline.pipe)


if __name__ == "__main__":
    unittest.main()
