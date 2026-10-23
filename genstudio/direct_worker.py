"""Standalone AI worker. Reuses local checkpoints without a ComfyUI server."""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
import uuid

from genfill.image_ops import decode_png, png_bytes
from genfill.comfy import Cancelled
from .core_pipeline import DirectPipeline
from .models import MODEL, FLUX, FIBO, MODELS, specification
from .processes import gpu_lease, release_gpu


class Worker:
    def __init__(self, model, text_encoder="", vae="", model_id=MODEL, *, components=None, owner=""):
        self.owner = owner
        specification(model_id)
        self.model_id = model_id
        if model_id == FIBO:
            from .fibo_pipeline import FiboPipeline
            self.pipeline = FiboPipeline(model, text_encoder, vae, components=components)
        elif model_id == FLUX:
            from .flux_pipeline import FluxPipeline
            self.pipeline = FluxPipeline(model, text_encoder, vae, components=components)
        else:
            self.pipeline = DirectPipeline(model, text_encoder, vae, components=components)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="genstudio-direct")
        self.jobs = {}
        self.lock = threading.RLock()

    def submit(self, data):
        if not isinstance(data, dict):
            raise ValueError("Expected a generation request object.")
        studio = None
        if data.get("kind") == "studio":
            from .studio import request
            studio = request(data, self.model_id)
            from .lora import validate_studio
            validate_studio(studio['loras'], self.pipeline.models)
            image = mask = None
        else:
            image, mask = decode_png(data.get("image"), 16 << 20), decode_png(data.get("mask"), 16 << 20)
            if image.size != mask.size:
                raise ValueError("Image and mask sizes differ.")
        from genfill.image_ops import bounded_int
        seed = bounded_int(data.get("seed", 0), 0, 2**53 - 1, "Seed")
        prompt = data.get("prompt")
        if not isinstance(prompt, str) or len(prompt.encode("utf-8")) > (12000 if studio else 8000):
            raise ValueError("Invalid prompt.")
        with self.lock:
            if any(job["state"] not in ("done", "error", "cancelled") for job in self.jobs.values()):
                raise ValueError("Direct engine is busy.")
            self.jobs.clear()
            uid = uuid.uuid4().hex
            job = {"id": uid, "state": "queued", "message": "Queued", "cancel": threading.Event()}
            self.jobs[uid] = job
            self.pool.submit(self.run, job, image, mask, seed, prompt, studio)
            return self.public(job)

    def public(self, job):
        return {key: job[key] for key in ("id", "state", "message")}

    def run(self, job, image, mask, seed, prompt, studio=None):
        def progress(message):
            with self.lock:
                job["message"] = message
        try:
            with self.lock:
                job["state"] = "running"
            if os.environ.get("GENGATE_NO_INFERENCE") == "1":
                raise RuntimeError("Generation inference is disabled for validation")
            with gpu_lease(cancelled=job["cancel"].is_set,
                           waiting=lambda: progress("Waiting for AI selection to release the GPU\u2026")):
                failure = None
                try:
                    output = self.pipeline.generate_studio(studio["references"], seed, prompt, studio["width"], studio["height"], job["cancel"], progress, loras=studio['loras']) if studio else self.pipeline.generate(image, mask, seed, prompt, job["cancel"], progress)
                except Cancelled:
                    failure = "cancelled", "Cancelled"
                except Exception as error:
                    failure = "error", str(error)
                    # Discard a partially loaded/broken pipeline, and clear its
                    # traceback before offloading and handing off the GPU.
                    self.pipeline.pipe = None
                finally:
                    release_gpu()
                if failure:
                    with self.lock:
                        job.update(state=failure[0], message=failure[1])
                    return
            with self.lock:
                if job["cancel"].is_set():
                    raise Cancelled()
                job["result"] = {"image": base64.b64encode(png_bytes(output)).decode("ascii")}
                job.update(state="done", message="Done")
        except (Cancelled, InterruptedError):
            with self.lock:
                job.update(state="cancelled", message="Cancelled")
        except Exception as error:
            with self.lock:
                job.update(state="error", message=str(error))


class Handler(BaseHTTPRequestHandler):
    def dispatch(self):
        from genstudio.service import Handler as BaseHandler
        send = lambda code, data: BaseHandler.send_json(self, code, data)
        if self.headers.get("Origin"):
            return send(403, {"error": "Browser requests are disabled."})
        worker = self.server.worker
        try:
            if self.command == "GET" and self.path == "/health":
                problems = worker.pipeline.check()
                return send(200, {"service": "genstudio-direct", "ready": not problems,
                                  "owner": worker.owner,
                                  "inpaint_protocol": 2, "model": worker.model_id,
                                  "studio_protocol": 2,
                                  "components": worker.pipeline.components,
                                  "textEncoder": worker.pipeline.text_encoder, "vae": worker.pipeline.vae,
                                  "modelsDir": str(worker.pipeline.models.resolve()), "problems": problems})
            if self.command == "POST" and self.path == "/jobs":
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise ValueError("JSON content type required.")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 192 * 1024 * 1024:
                    raise ValueError("Invalid request size.")
                return send(202, worker.submit(json.loads(self.rfile.read(length))))
            parts = self.path.strip("/").split("/")
            with worker.lock:
                job = worker.jobs.get(parts[1]) if len(parts) in (2, 3) and parts[0] == "jobs" else None
                if job is None:
                    return send(404, {"error": "Job not found."})
                if len(parts) == 2 and self.command == "GET":
                    return send(200, worker.public(job))
                if len(parts) == 3 and parts[2] == "cancel" and self.command == "POST":
                    job["cancel"].set()
                    return send(200, worker.public(job))
                if len(parts) == 3 and parts[2] == "result" and self.command == "GET":
                    return send(200, job["result"]) if job["state"] == "done" else send(409, {"error": "Result not ready."})
            return send(404, {"error": "Unknown route."})
        except (ValueError, KeyError, TypeError) as error:
            return send(400, {"error": str(error)})

    do_GET = dispatch
    do_POST = dispatch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", required=True)
    parser.add_argument("--text-encoder", default="")
    parser.add_argument("--vae", default="")
    parser.add_argument("--model", default=MODEL, choices=tuple(MODELS))
    parser.add_argument("--components", type=json.loads)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--owner", required=True)
    args = parser.parse_args()
    worker = Worker(args.models, args.text_encoder, args.vae, args.model, components=args.components, owner=args.owner)
    problems = worker.pipeline.check()
    if problems:
        raise SystemExit("\n".join(problems))
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.worker = worker
    server.serve_forever()


if __name__ == "__main__":
    main()
