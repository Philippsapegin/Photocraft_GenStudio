"""Standalone Qwen worker. Reuses local INT8 weights without a ComfyUI server."""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import uuid

from genfill.image_ops import decode_png, png_bytes
from genfill.comfy import Cancelled
from .core_pipeline import DirectPipeline


class Worker:
    def __init__(self, model):
        self.pipeline = DirectPipeline(model)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qwen-direct")
        self.jobs = {}
        self.lock = threading.RLock()

    def submit(self, data):
        if not isinstance(data, dict):
            raise ValueError("Expected a generation request object.")
        image, mask = decode_png(data.get("image"), 1024 * 1024), decode_png(data.get("mask"), 1024 * 1024)
        if image.size != mask.size:
            raise ValueError("Image and mask sizes differ.")
        from genfill.image_ops import bounded_int
        seed = bounded_int(data.get("seed", 0), 0, 2**53 - 1, "Seed")
        prompt = data.get("prompt")
        if not isinstance(prompt, str) or len(prompt) > 8000:
            raise ValueError("Invalid prompt.")
        with self.lock:
            if any(job["state"] not in ("done", "error", "cancelled") for job in self.jobs.values()):
                raise ValueError("Direct engine is busy.")
            self.jobs.clear()
            uid = uuid.uuid4().hex
            job = {"id": uid, "state": "queued", "message": "Queued", "cancel": threading.Event()}
            self.jobs[uid] = job
            self.pool.submit(self.run, job, image, mask, seed, prompt)
            return self.public(job)

    def public(self, job):
        return {key: job[key] for key in ("id", "state", "message")}

    def run(self, job, image, mask, seed, prompt):
        def progress(message):
            with self.lock:
                job["message"] = message
        try:
            with self.lock:
                job["state"] = "running"
            output = self.pipeline.generate(image, mask, seed, prompt, job["cancel"], progress)
            with self.lock:
                if job["cancel"].is_set():
                    raise Cancelled()
                job["result"] = {"image": base64.b64encode(png_bytes(output)).decode("ascii")}
                job.update(state="done", message="Done")
        except Cancelled:
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
                                  "inpaint_protocol": 2,
                                  "modelsDir": str(worker.pipeline.models.resolve()), "problems": problems})
            if self.command == "POST" and self.path == "/jobs":
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise ValueError("JSON content type required.")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length < 24 * 1024 * 1024:
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
    args = parser.parse_args()
    worker = Worker(args.models)
    problems = worker.pipeline.check()
    if problems:
        raise SystemExit("\n".join(problems))
    server = ThreadingHTTPServer(("127.0.0.1", 8767), Handler)
    server.worker = worker
    server.serve_forever()


if __name__ == "__main__":
    main()
