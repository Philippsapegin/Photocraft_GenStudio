"""Full-canvas generation jobs, separate from masked editing."""
from __future__ import annotations
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import secrets
import threading
import time
import uuid

from genfill.comfy import Cancelled
from genfill.image_ops import bounded_int, decode_png, png_bytes
from genfill.progress import sampling_fraction
from genfill.server import Job, TERMINAL


def request(value, model="qwen-image-2.1-turbo", *, workflow=False):
    from .models import specification
    spec = specification(model)
    if not isinstance(value, dict):
        raise ValueError("Expected a generation request.")
    prompt = value.get("prompt", "")
    negative = value.get("negativePrompt", "")
    if not isinstance(negative, str) or len(negative.encode("utf-8")) > 12000 or "\0" in negative:
        raise ValueError("Negative prompt must contain at most 12000 UTF-8 bytes.")
    if not isinstance(prompt, str) or not workflow and not prompt.strip() or len(prompt.encode("utf-8")) > 12000 or "\0" in prompt:
        raise ValueError("Enter a prompt (up to 12000 UTF-8 bytes).")
    refs = value.get("references", [])
    if not isinstance(refs, list) or len(refs) > (10 if workflow else spec["references"]):
        raise ValueError(f"{spec['label']} supports up to {spec['references']} reference images.")
    if not workflow and spec.get('edit_only') and not refs:
        raise ValueError('Add a source image to edit with FIBO 2.0 Edit Turbo.')
    from .lora import validate_studio
    loras = validate_studio(value.get('loras', []))
    width = bounded_int(value.get("width", 1024), 256, 2048, "Width")
    height = bounded_int(value.get("height", 1024), 256, 2048, "Height")
    grid = 1 if workflow else spec["grid"]
    if width % grid or height % grid:
        raise ValueError(f"Generation dimensions must be multiples of {grid}.")
    count = bounded_int(value.get("batch", 1), 1, 8, "Batch size")
    seed = bounded_int(value.get("seed", secrets.randbelow(2**32)), 0, 2**53 - 9, "Seed")
    images = []
    from PIL import Image
    for encoded in refs:
        image = decode_png(encoded, 16 << 20).convert("RGBA")
        if workflow:
            images.append(image)
            continue
        image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        # Follow the selected model's reference grid; do not distort its ratio
        # by resizing every image to the output canvas dimensions.
        w, h = (max(grid, round(side / grid) * grid) for side in image.size)
        images.append(image.resize((w, h), Image.Resampling.LANCZOS))
    data = {"prompt": prompt, "negativePrompt": negative, "references": images, "width": width, "height": height, "batch": count, "seed": seed, "loras": loras}
    if workflow:
        source_prompt = value.get("sourcePrompt", prompt)
        if not isinstance(source_prompt, str) or len(source_prompt.encode()) > 12000 or "\0" in source_prompt:
            raise ValueError("Invalid source prompt.")
        data["sourcePrompt"] = source_prompt
    return data


class StudioJobs:
    def __init__(self, host):
        self.host = host
        self.lock = threading.RLock()
        self.jobs = {}
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="genstudio-full")

    def submit(self, body):
        model = self.host.settings["model"]
        from .comfy_workflow import enabled, parse_workflow
        workflow = enabled(self.host.settings)
        mode = (self.host.settings.get("studioMode", "preset"), self.host.settings.get("studioWorkflow", ""), self.host.settings.get("engine", "direct"))
        data = request(body, model, workflow=True) if workflow else request(body, model)
        if workflow:
            data["workflow"] = parse_workflow(body["workflowGraph"]) if "workflowGraph" in body else None
            data["workflowPath"] = ""
        from .lora import validate_studio
        if data['loras'] and not workflow:
            validate_studio(data['loras'], self.host.settings['modelsDir'])
        # Publish the queued job under the same lock used by /engine changes.
        # Validation may decode large images, so do it before taking that lock,
        # then reject a concurrent model change rather than using the wrong one.
        with self.host.lock, self.lock:
            if self.host.settings["model"] != model or mode != (self.host.settings.get("studioMode", "preset"), self.host.settings.get("studioWorkflow", ""), self.host.settings.get("engine", "direct")):
                raise ValueError("The selected model changed. Submit the generation again.")
            active = [job for job in self.jobs.values() if job.state not in TERMINAL]
            if len(active) >= 4:
                raise ValueError("The generation queue is full. Wait for a result.")
            # Keep at most two completed batches. Images, unlike status records,
            # can be large; clients copy them into documents on completion.
            ended = [key for key, job in self.jobs.items() if job.state in TERMINAL]
            for key in ended[:-1]:
                del self.jobs[key]
            job = Job(uuid.uuid4().hex)
            self.jobs[job.id] = job
            self.pool.submit(self.run, job, data)
            return job

    def run(self, job, data):
        images = []
        def progress(message):
            with self.lock:
                job.message = message
                fraction = sampling_fraction(message)
                if fraction is not None:
                    job.progress = fraction if "workflow" in data else (len(images) + fraction) / data["batch"]
        try:
            if job.cancel.is_set():
                raise Cancelled()
            job.state = "running"
            if "workflow" in data:
                result = self.host.generate_workflow(data["workflow"], data, job.cancel, progress)
                if job.cancel.is_set():
                    raise Cancelled()
                with self.lock:
                    job.result = result
                    job.state, job.message, job.progress = "done", "Done", 1.0
                return
            for index in range(data["batch"]):
                if job.cancel.is_set():
                    raise Cancelled()
                output = self.host.generate_studio(data["references"], data["seed"] + index,
                    data["prompt"], data["width"], data["height"], job.cancel, progress,
                    **({'loras': data['loras']} if data['loras'] else {}),
                    **({'negative_prompt': data['negativePrompt']} if data['negativePrompt'] else {}))
                if output.size != (data["width"], data["height"]):
                    raise RuntimeError("The engine returned the wrong generation dimensions.")
                images.append({"image": base64.b64encode(png_bytes(output)).decode("ascii"), "seed": data["seed"] + index})
            if job.cancel.is_set():
                raise Cancelled()
            with self.lock:
                job.result = {"images": images, "prompt": data["prompt"]}
                job.state, job.message, job.progress = "done", "Done", 1.0
        except Cancelled:
            job.state, job.message = "cancelled", "Cancelled"
        except Exception as error:
            job.state, job.message = "error", str(error)

    def shutdown(self):
        with self.lock:
            for job in self.jobs.values():
                job.cancel.set()
        self.pool.shutdown(wait=False, cancel_futures=True)


def dispatch(handler):
    if not handler.path.startswith("/studio/jobs"):
        return False
    manager = handler.server.studio
    try:
        if handler.path == "/studio/jobs" and handler.command == "POST":
            if handler.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("Content-Type: application/json required.")
            length = int(handler.headers.get("Content-Length", "0"))
            if not 0 < length <= 256 << 20:
                raise ValueError("Generation request is too large.")
            job = manager.submit(json.loads(handler.rfile.read(length)))
            handler.send_json(202, job.public())
        else:
            parts = handler.path.strip("/").split("/")
            if len(parts) == 4 and parts[3] == "cancel" and handler.command == "POST":
                # Consume the small request body before closing HTTP/1.0.
                # Otherwise Windows can reset the connection and discard the
                # reply, even though cancellation itself already succeeded.
                length = int(handler.headers.get("Content-Length", "0"))
                if not 0 <= length <= 64 << 10:
                    raise ValueError("The cancel request exceeds the size limit.")
                handler.connection.settimeout(5)
                if len(handler.rfile.read(length)) != length:
                    raise ValueError("Incomplete cancel request.")
            with manager.lock:
                job = manager.jobs.get(parts[2]) if len(parts) in (3, 4) else None
                if job is None:
                    handler.send_json(404, {"error": "Generation job not found."})
                elif len(parts) == 3 and handler.command == "GET":
                    handler.send_json(200, job.public())
                elif len(parts) == 4 and parts[3] == "cancel" and handler.command == "POST":
                    job.cancel.set()
                    handler.send_json(200, job.public())
                elif len(parts) == 4 and parts[3] == "result" and handler.command == "GET" and job.state == "done":
                    handler.send_json(200, job.result)
                else:
                    handler.send_json(400, {"error": "Generation result is not available."})
    except (ValueError, OSError) as error:
        handler.send_json(400, {"error": str(error)})
    return True
