from __future__ import annotations

from io import BytesIO
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
import uuid

from PIL import Image

from .image_ops import png_bytes
from .workflow import REQUIRED_NODES, STUDIO_NODES, build_workflow, studio_workflow
from .comfy_progress import ComfyProgress


class Cancelled(Exception):
    pass


class ComfyClient:
    def __init__(self, config: dict):
        self.config = config
        self.base = config["comfy_url"].rstrip("/")
        url = urlparse(self.base)
        if url.scheme not in ("http", "https") or not url.hostname or url.query or url.fragment:
            raise ValueError("Invalid ComfyUI address.")

    def request(self, route: str, body=None, *, timeout=20, raw=False, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        request = Request(self.base + route, data=data,
                          headers=headers or ({"Content-Type": "application/json"} if data else {}))
        try:
            with urlopen(request, timeout=timeout) as response:
                content = response.read()
            return content if raw else json.loads(content)
        except HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:2500]
            raise RuntimeError(f"ComfyUI HTTP {error.code}: {detail}") from error
        except (URLError, TimeoutError, OSError) as error:
            raise RuntimeError(f"ComfyUI is unavailable at {self.base}: {error}") from error
        except json.JSONDecodeError as error:
            raise RuntimeError("ComfyUI returned an invalid response. Check its log and available Windows memory.") from error

    @staticmethod
    def choices(info: dict, node: str, input_name: str) -> list:
        schema = info[node]["input"]
        value = {**schema.get("required", {}), **schema.get("optional", {})}[input_name]
        if isinstance(value[0], list):
            return value[0]
        return value[1].get("options", []) if len(value) > 1 else []

    def inspect(self, studio=False, constant_lora=None) -> tuple[dict, list[str]]:
        info = self.request("/object_info", timeout=30)
        flux = self.config.get("model") == "flux-3-image-klein"
        fibo = self.config.get('model') == 'fibo-2-edit-turbo'
        if flux:
            from genstudio.flux_workflow import NODES
        if fibo:
            from genstudio.fibo_workflow import NODES
        required = NODES if flux or fibo else STUDIO_NODES if studio else REQUIRED_NODES
        problems = [f"Missing node {name}. " + ("Update ComfyUI with native FIBO 2 support, or select Direct." if fibo else "Update ComfyUI to 0.41.3 or newer." if flux else "Update ComfyUI." if studio else "Update ComfyUI and install LanPaint.")
                    for name in required if name not in info]
        components = [("UNETLoader", "unet_name", "diffusion_model"),
                                        ("CLIPLoader", "clip_name", "text_encoder"),
                                        ("VAELoader", "vae_name", "vae")]
        if flux:
            components.append(("CLIPLoader", "clip_name", "prompt_upsampler"))
            if "CLIPLoader" in info and "flux3" not in self.choices(info, "CLIPLoader", "type"):
                problems.append("FLUX 3 Klein requires ComfyUI 0.41.3 or newer. Update your ComfyUI, or select Direct.")
        if fibo and 'CLIPLoader' in info and 'fibo2' not in self.choices(info, 'CLIPLoader', 'type'):
            problems.append('FIBO 2 requires ComfyUI with native TextEncodeFibo2 support (2026-10-21 or newer), or select Direct.')
        edit = not studio if constant_lora is None else constant_lora
        if edit and self.config.get("constant_lora") and self.config.get("constant_lora_strength", 1.0):
            if "LoraLoader" not in info:
                problems.append("Missing node LoraLoader. Update ComfyUI.")
            else:
                components.append(("LoraLoader", "lora_name", "constant_lora"))
        for node, field, config_key in components:
            if node in info and self.config[config_key] not in self.choices(info, node, field):
                problems.append("Missing model: " + self.config[config_key])
        system = self.request("/system_stats").get("system", {})
        if (system.get("os") == "win32" and system.get("comfyui_version") == "0.37.0"
                and "--disable-dynamic-vram" not in system.get("argv", [])):
            problems.append("ComfyUI 0.37.0 on Windows: start a dedicated instance with "
                            "Start-Comfy.ps1 to disable dynamic VRAM.")
        return info, problems

    def upload(self, name: str, data: bytes) -> str:
        boundary = "genfill" + uuid.uuid4().hex
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
                f"filename=\"{name}\"\r\nContent-Type: image/png\r\n\r\n").encode()
        body += data + f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"type\"\r\n\r\ninput\r\n--{boundary}--\r\n".encode()
        request = Request(self.base + "/upload/image", data=body,
                          headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urlopen(request, timeout=90) as response:
            result = json.loads(response.read())
        folder = result.get("subfolder", "")
        return (folder + "/" if folder else "") + result["name"]

    def generate(self, image: Image.Image, mask: Image.Image, seed: int, prompt: str,
                 cancel, progress) -> Image.Image:
        if self.config.get("model") in ('flux-3-image-klein', 'fibo-2-edit-turbo'):
            from genstudio.core_pipeline import inpaint_references
            from genstudio.flux_pipeline import masked_prompt
            reference, coverage = inpaint_references(image, mask)
            instruction = masked_prompt(prompt)
            if self.config.get('model') == 'fibo-2-edit-turbo':
                instruction = 'Edit <image_1> using the white edit region in <image_2>. The gray region is missing content. ' + prompt + ' Return the edited first image only.'
            return self.generate_studio([reference, coverage], seed, instruction,
                                        image.width, image.height, cancel, progress, _constant_lora=True)
        info, problems = self.inspect()
        if problems:
            raise RuntimeError("\n".join(problems))
        if cancel.is_set():
            raise Cancelled()
        uid = uuid.uuid4().hex
        progress("Sending image and mask to ComfyUI…")
        image_name = self.upload(f"genfill-{uid}.png", png_bytes(image))
        mask_name = self.upload(f"genfill-{uid}-mask.png", png_bytes(mask))
        if cancel.is_set():
            raise Cancelled()
        workflow = build_workflow(self.config, image_name, mask_name, seed, prompt, info)
        return self.run_workflow(workflow, cancel, progress, uid)

    def generate_studio(self, references, seed, prompt, width, height, cancel, progress, *, _constant_lora=False, loras=None):
        info, problems = self.inspect(studio=True, constant_lora=_constant_lora)
        if problems:
            raise RuntimeError("\n".join(problems))
        from genstudio.lora import validate_studio
        loras = validate_studio(loras or [])
        if loras:
            if 'LoraLoader' not in info:
                raise RuntimeError('Missing node LoraLoader. Update ComfyUI.')
            available = self.choices(info, 'LoraLoader', 'lora_name')
            for item in loras:
                if item['path'] not in available:
                    raise RuntimeError('ComfyUI cannot find LoRA: ' + item['path'])
        uid = uuid.uuid4().hex
        names = []
        for index, image in enumerate(references):
            if cancel.is_set():
                raise Cancelled()
            progress(f"Sending image {index + 1} to ComfyUI…")
            if self.config.get("model") == "flux-3-image-klein":
                image = Image.alpha_composite(Image.new("RGBA", image.size, "white"), image.convert("RGBA")).convert("RGB")
            names.append(self.upload(f"genstudio-{uid}-{index}.png", png_bytes(image)))
        if self.config.get('model') == 'fibo-2-edit-turbo':
            from genstudio.fibo_workflow import workflow as fibo_workflow
            workflow = fibo_workflow(self.config, names, seed, prompt, width, height, loras=loras, constant_lora=_constant_lora)
        elif self.config.get("model") == "flux-3-image-klein":
            from genstudio.flux_workflow import workflow as flux_workflow
            workflow = flux_workflow(self.config, names, seed, prompt, width, height, info, constant_lora=_constant_lora)
        else:
            workflow = studio_workflow(self.config, names, seed, prompt, width, height, info)
        if loras and self.config.get('model') != 'fibo-2-edit-turbo':
            from genstudio.lora import studio_graph
            model, clip = studio_graph(workflow, workflow['4']['inputs']['model'], ['2', 0], loras)
            workflow['4']['inputs']['model'] = model
            workflow['7' if self.config.get('model') == 'flux-3-image-klein' else '8']['inputs']['clip'] = clip
        return self.run_workflow(workflow, cancel, progress, uid)

    def run_workflow(self, workflow, cancel, progress, uid):
        if cancel.is_set():
            raise Cancelled()
        client_id = "photocraft-genstudio-" + uid
        with ComfyProgress(self.base, client_id, progress) as stream:
            result = self.request("/prompt", {"prompt": workflow, "client_id": client_id})
            if result.get("node_errors"):
                raise RuntimeError("ComfyUI rejected the workflow: " + json.dumps(result["node_errors"]))
            prompt_id = result["prompt_id"]
            deadline = time.monotonic() + self.config.get("job_timeout", 1800)
            try:
                while time.monotonic() < deadline:
                    if cancel.is_set():
                        raise Cancelled()
                    stream.poll(prompt_id)
                    history = self.request("/history/" + prompt_id)
                    if prompt_id in history:
                        item = history[prompt_id]
                        status = item.get("status", {})
                        if status.get("status_str") == "error":
                            messages = status.get("messages", [])
                            detail = next((m[1].get("exception_message", "Model error")
                                           for m in reversed(messages) if m[0] == "execution_error"), "ComfyUI error")
                            raise RuntimeError(detail)
                        images = item.get("outputs", {}).get("14", {}).get("images", [])
                        if images:
                            data = self.request("/view?" + urlencode(images[0]), raw=True, timeout=60)
                            with Image.open(BytesIO(data)) as output:
                                return output.convert("RGBA")
                        raise RuntimeError("ComfyUI finished the job without an image.")
                    queue = self.request("/queue")
                    running = any(entry[1] == prompt_id for entry in queue.get("queue_running", []))
                    progress("Loading the model / generating…" if running else "Waiting for ComfyUI…")
                    cancel.wait(0.25)
                raise RuntimeError("Generation timed out. Check the ComfyUI log.")
            except (Cancelled, RuntimeError):
                # Delete only our own pending task; never interrupt somebody else's generation.
                try:
                    self.request("/queue", {"delete": [prompt_id]})
                except RuntimeError:
                    pass
                raise
