from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import threading
import time
import socket
import uuid
from urllib.request import Request, urlopen
from urllib.error import URLError
from urllib.parse import urlparse

from genfill.comfy import ComfyClient
from genfill.server import load_config
from .settings import ROOT, validate, active_components, component_defaults

DIRECT_URL = "http://127.0.0.1:8767"


def http_json(url, body=None, timeout=3):
    data = json.dumps(body).encode() if body is not None else None
    req = Request(url, data=data, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=timeout) as response:
        return json.load(response)


def hidden_process(command, *, cwd=ROOT, stdout=None, stderr=None):
    env = os.environ.copy()
    for key in ("PYTHONHOME", "PYTHONPATH"):
        env.pop(key, None)
    env["PYTHONUTF8"] = "1"
    return subprocess.Popen(command, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                            stdout=stdout, stderr=stderr,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


class EngineHost:
    """Owns only processes it started; never stops an attached ComfyUI instance."""

    def __init__(self, settings, spawn=hidden_process, request=http_json):
        self.settings = validate(settings)
        self.spawn, self.request = spawn, request
        self.process = None
        self.logs = []
        self.lock = threading.RLock()
        self.work_lock = threading.RLock()
        self.busy = False
        self.message = "Stopped"
        self.runtime = ROOT / ".runtime/genstudio"
        self.direct_url = None
        self.worker_status = {}
        self.worker_id = uuid.uuid4().hex

    def config(self):
        config = load_config()
        config["model"] = self.settings["model"]
        config["comfy_url"] = self.settings["comfyUrl"].rstrip("/")
        from .models import resolve_components, resolve_lora, specification
        components = active_components(self.settings)
        config["constant_lora"] = components["constantLora"]
        config["constant_lora_strength"] = components["constantLoraStrength"]
        config.update({key: value[0] for key, value in specification(self.settings["model"])["files"].items()})
        try:
            config.update({key: value["name"] for key, value in resolve_components(self.settings["modelsDir"], components, self.settings["model"]).items()})
        except (ValueError, OSError):
            pass  # An actionable error is returned on Start, without breaking the settings service.
        try:
            lora = resolve_lora(self.settings["modelsDir"], components)
            if lora:
                config["constant_lora"] = lora["name"]
        except (ValueError, OSError):
            pass  # Only masked edits require this optional file.
        return config

    @property
    def engine(self):
        return self.settings["engine"]

    @property
    def model_name(self):
        return "ComfyUI workflow" if self.engine == "comfy" else self.config()["diffusion_model"]

    def policy(self):
        return self.settings[self.engine + "Startup"]

    def configure(self, value):
        settings = validate(value)
        with self.lock:
            if self.busy:
                raise ValueError("Wait for the current AI request before changing the engine.")
            # Policy-only changes do not restart a running engine.
            operational = ("model", "engine", "comfyUrl", "comfyDir", "comfyPython", "directPython", "modelsDir")
            from .comfy_workflow import enabled
            if (any(settings[k] != self.settings[k] for k in operational) or active_components(settings) != active_components(self.settings)
                    or enabled(settings) != enabled(self.settings)):
                self.stop()
            self.settings = settings

    def probe(self):
        self.worker_status = {}
        if self.engine == "direct" and self.direct_url is None:
            return False
        url = self.settings["comfyUrl"].rstrip("/") if self.engine == "comfy" else self.direct_url
        try:
            info = self.request(url + ("/system_stats" if self.engine == "comfy" else "/health"))
            if self.engine == "comfy":
                return isinstance(info.get("system"), dict)
            components = active_components(self.settings)
            # Older workers can still serve default, adapter-free presets.
            old_components = {**component_defaults(),
                              "textEncoder": info.get("textEncoder", ""), "vae": info.get("vae", "")}
            matched = (info.get("service") == "genstudio-direct" and info.get("ready") is True
                    and info.get("owner") == self.worker_id
                    and Path(info.get("modelsDir", "")).resolve() == Path(self.settings["modelsDir"]).resolve()
                    and info.get("components", old_components) == components
                    and info.get("model", "qwen-image-2.1-turbo") == self.settings["model"])
            if matched:
                self.worker_status = info
            return matched
        except (OSError, ValueError, URLError):
            return False

    def status(self):
        if not self.lock.acquire(blocking=False):
            return {"engine": self.engine, "state": "starting", "ready": False,
                    "serviceReady": False, "modelState": "unknown", "modelLoaded": None,
                    "owned": self.process is not None, "busy": self.busy,
                    "message": self.message, "startup": self.policy()}
        try:
            ready = self.probe()
            exited = self.process is not None and self.process.poll() is not None
            if exited:
                self.message = "Engine exited. See .runtime/genstudio logs."
            model_state = self.worker_status.get("modelState", "unknown") if ready and self.engine == "direct" else "unknown"
            message = self.message
            if ready:
                if self.engine == "comfy":
                    message = "ComfyUI connected"
                else:
                    message = {
                        "unloaded": "Direct service running · model loads on first generation",
                        "loading": "Loading model components…",
                        "loaded": "Direct service running · model components loaded",
                        "error": "Direct model error: " + self.worker_status.get("modelError", "See the generation error."),
                    }.get(model_state, "Direct service running · model load status unavailable")
            return {"engine": self.engine, "state": "ready" if ready else "stopped",
                    "workerUrl": self.direct_url if self.engine == "direct" else None,
                    "ready": ready, "serviceReady": ready, "owned": self.process is not None and not exited,
                    "busy": self.busy, "message": message,
                    "modelState": model_state,
                    "modelLoaded": self.worker_status.get("modelLoaded") if ready and self.engine == "direct" else None,
                    "startup": self.policy()}
        finally:
            self.lock.release()

    def ensure(self, event="request", progress=lambda message: None, cancel=None, studio=False, workflow=None):
        if event not in ("manual", "request", "appStart"):
            raise ValueError("Unknown start event.")
        with self.lock:
            if cancel and cancel.is_set():
                from genfill.comfy import Cancelled
                raise Cancelled()
            from .models import resolve_components, resolve_lora, extra_paths
            from .comfy_workflow import enabled, inspect as inspect_workflow
            custom = enabled(self.settings)
            definition = workflow
            components = active_components(self.settings)
            if not custom:
                resolve_components(self.settings["modelsDir"], components, self.settings["model"])
            if not studio and event == "request" and not custom:
                resolve_lora(self.settings["modelsDir"], components)
            if self.probe():
                if self.engine == "comfy":
                    if custom and definition is not None:
                        inspect_workflow(ComfyClient(self.config()), definition)
                    elif not custom:
                        _, problems = ComfyClient(self.config()).inspect(studio=True) if studio else ComfyClient(self.config()).inspect()
                        if problems:
                            raise ValueError("\n".join(problems))
                return self.status()
            allowed = event == "manual" or self.policy() == "firstRequest" and event == "request" or self.policy() == "appStart"
            if not allowed:
                raise ValueError("Automatic start is off. Start the engine in Filter > Plug-ins > GenGate.")
            self.message = "Starting the local AI engine. This may take a moment."
            progress(self.message)
            if self.engine == "comfy":
                python = Path(self.settings["comfyPython"])
                folder = Path(self.settings["comfyDir"])
                if not python.is_file() or not (folder / "main.py").is_file():
                    raise ValueError("Set the ComfyUI folder and Python executable in GenGate.")
                if enabled(self.settings):
                    from .comfy_workflow import install_nodes
                    install_nodes(str(folder))
                self.runtime.mkdir(parents=True, exist_ok=True)
                paths_file = self.runtime / "model-paths.yaml"
                paths_file.write_text(json.dumps(extra_paths(self.settings["modelsDir"], None if custom else components, self.settings["model"])), encoding="utf-8")
                command = [str(python), "-s", "main.py", "--listen", "127.0.0.1", "--port",
                           str(urlparse(self.settings["comfyUrl"]).port),
                           "--disable-comfy-compiler", "--reserve-vram", "1", "--preview-method", "none",
                           "--disable-dynamic-vram", "--disable-async-offload", "--disable-pinned-memory",
                           "--database-url", "sqlite:///" + str(self.runtime / "comfy.db").replace("\\", "/"),
                           "--extra-model-paths-config", str(paths_file)]
                if not enabled(self.settings):
                    command += ["--disable-all-custom-nodes", "--whitelist-custom-nodes", "LanPaint", "GenGate"]
                cwd = folder
            else:
                python = Path(self.settings["directPython"])
                if not python.is_file():
                    raise ValueError("Direct AI runtime is not installed. Run scripts/Setup-Direct.ps1, then select its Python executable.")
                if self.process is None or self.process.poll() is not None:
                    with socket.socket() as reservation:
                        reservation.bind(("127.0.0.1", 0))
                        port = reservation.getsockname()[1]
                    self.worker_id = uuid.uuid4().hex
                    self.direct_url = f"http://127.0.0.1:{port}"
                port = urlparse(self.direct_url).port
                command = [str(python), "-X", "utf8", str(ROOT / "scripts/run_direct.py"), "--models", self.settings["modelsDir"],
                           "--port", str(port), "--owner", self.worker_id,
                           "--text-encoder", components["textEncoder"], "--vae", components["vae"], "--model", self.settings["model"],
                           "--components", json.dumps(components)]
                cwd = ROOT
            if self.process is None or self.process.poll() is not None:
                self.runtime.mkdir(parents=True, exist_ok=True)
                for handle in self.logs:
                    handle.close()
                self.logs = [open(self.runtime / (self.engine + suffix), "ab", buffering=0)
                             for suffix in (".log", "-errors.log")]
                self.process = self.spawn(command, cwd=cwd, stdout=self.logs[0], stderr=self.logs[1])
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                if cancel and cancel.is_set():
                    from genfill.comfy import Cancelled
                    raise Cancelled()
                if self.probe():
                    if self.engine == "comfy":
                        if custom and definition is not None:
                            inspect_workflow(ComfyClient(self.config()), definition)
                        elif not custom:
                            _, problems = ComfyClient(self.config()).inspect(studio=True) if studio else ComfyClient(self.config()).inspect()
                            if problems:
                                raise ValueError("\n".join(problems))
                    self.message = "Ready"
                    return self.status()
                if self.process.poll() is not None:
                    detail = (self.runtime / (self.engine + "-errors.log")).read_text(encoding="utf-8", errors="replace")[-2000:]
                    raise RuntimeError("Engine failed to start. " + detail)
                time.sleep(0.25)
            raise RuntimeError("Engine startup timed out. See .runtime/genstudio logs.")

    def stop(self):
        with self.lock:
            if self.busy:
                raise ValueError("Wait for the current AI request before stopping the engine.")
            owned = self.process is not None and self.process.poll() is None
            if owned:
                self.process.terminate()
                try:
                    self.process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            self.process = None
            self.direct_url = None
            for handle in self.logs:
                handle.close()
            self.logs = []
            self.message = "Stopped" if owned else "No engine process owned by GenGate."
            return {"stopped": owned, "message": self.message}

    def inspect(self):
        status = self.status()
        if not status["ready"]:
            return {}, [status["message"]]
        if self.engine == "comfy":
            from .comfy_workflow import IN_NODE, OUT_NODE
            info=ComfyClient(self.config()).request('/object_info',timeout=30)
            missing=[name for name in (IN_NODE,OUT_NODE) if name not in info]
            return info, ['Install/update GenGate nodes and restart ComfyUI: '+', '.join(missing)] if missing else []
        return {}, []

    def generate(self, image, mask, seed, prompt, cancel, progress):
        if self.engine=='comfy':
            return self.generate_edit(image,mask,seed,prompt,cancel,progress,mode='fill')
        with self.work_lock:
            return self._generate(image, mask, seed, prompt, cancel, progress)

    def generate_edit(self, image, mask, seed, prompt, cancel, progress, *, mode="fill"):
        if self.engine != 'comfy':
            return self.generate(image, mask, seed, prompt, cancel, progress)
        if mode not in ('fill', 'remove'):
            raise ValueError('Unknown editing tool.')
        from .comfy_workflow import read_workflow
        path = self.settings['fillWorkflow' if mode == 'fill' else 'removeWorkflow']
        if not path:
            raise ValueError('Choose the ' + ('Ai Fill' if mode == 'fill' else 'Remove Brush') + ' workflow in GenGate Settings.')
        definition = read_workflow(path)
        from PIL import ImageOps
        reference = image.convert('RGBA')
        reference.putalpha(ImageOps.invert(mask.convert('L')))
        components = active_components(self.settings)
        from .models import resolve_lora
        adapter = resolve_lora(self.settings['modelsDir'], components)
        data = {'references': [reference], 'prompt': prompt, 'sourcePrompt': prompt, 'negativePrompt': '',
                'width': image.width, 'height': image.height, 'batch': 1, 'seed': seed, 'steps': components['constantLoraSteps'],
                'loras': [{'path': adapter['name'], 'strength': components['constantLoraStrength']}] if adapter else [],
                'workflowPath': path}
        result = self.generate_workflow(definition, data, cancel, progress)
        if len(result['images']) != 1:
            raise ValueError('An editing workflow must return exactly one image.')
        from genfill.image_ops import decode_png
        row = result['images'][0]
        output = decode_png(row['image'], 32 << 20).convert('RGBA')
        if output.size != image.size:
            raise ValueError(f'Editing workflow must return {image.width} x {image.height} pixels; got {output.width} x {output.height}.')
        if row.get('mask'):
            from PIL import ImageChops
            output.putalpha(ImageChops.multiply(output.getchannel('A'), decode_png(row['mask'], 32 << 20).convert('L')))
        return output

    def _generate(self, image, mask, seed, prompt, cancel, progress):
        with self.lock:
            self.busy = True
        try:
            self.ensure("request", progress, cancel)
            if self.engine != "direct":
                raise ValueError('The engine changed. Submit the editing workflow again.')
            from .direct_client import generate
            return generate(image, mask, seed, prompt, cancel, progress, base=self.direct_url)
        finally:
            with self.lock:
                self.busy = False

    def generate_studio(self, references, seed, prompt, width, height, cancel, progress, *, loras=None, negative_prompt=""):
        if self.engine=='comfy':
            raise ValueError('ComfyUI Studio uses workflow jobs with the active browser graph.')
        with self.work_lock:
            with self.lock:
                self.busy = True
            try:
                self.ensure("request", progress, cancel, studio=True)
                from .direct_client import generate_studio
                return generate_studio(references, seed, prompt, width, height, cancel, progress, base=self.direct_url, loras=loras, negative_prompt=negative_prompt)
            finally:
                with self.lock:
                    self.busy = False

    def generate_workflow(self, definition, data, cancel, progress):
        from .comfy_workflow import enabled, generate
        if not enabled(self.settings):
            raise ValueError("Select ComfyUI workflow mode in the GenGate studio.")
        with self.work_lock:
            with self.lock:
                self.busy = True
            try:
                if definition is None:
                    self.ensure("request", progress, cancel, studio=True)
                    from .comfy_workflow import active_workflow
                    definition = active_workflow(ComfyClient(self.config()))
                self.ensure("request", progress, cancel, studio=True, workflow=definition)
                return generate(ComfyClient(self.config()), definition, data, self.config(), cancel, progress, settings=self.settings)
            finally:
                with self.lock:
                    self.busy = False
