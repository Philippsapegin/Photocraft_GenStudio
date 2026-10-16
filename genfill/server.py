from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlparse
import uuid

from . import __version__
from .comfy import Cancelled, ComfyClient
from .image_ops import bounded_int, decode_png, prepare_edit, png_bytes, result_patch
from .workflow import edit_prompt
from .progress import sampling_fraction

ROOT = Path(__file__).resolve().parents[1]
TERMINAL = {"done", "error", "cancelled"}


@dataclass
class Job:
    id: str
    state: str = "queued"
    message: str = "Queued"
    progress: float | None = None
    created: float = field(default_factory=time.time)
    cancel: threading.Event = field(default_factory=threading.Event)
    result: dict | None = None

    def public(self):
        return {"id": self.id, "state": self.state, "message": self.message, "progress": self.progress}


class JobManager:
    def __init__(self, config: dict, client=None):
        self.config = config
        self.client = client or ComfyClient(config)
        self.jobs: dict[str, Job] = {}
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="genfill")

    def submit(self, body: dict) -> Job:
        if not isinstance(body, dict):
            raise ValueError("Expected a JSON object.")
        max_pixels = self.config.get("max_pixels", 32000000)
        image = decode_png(body.get("image"), max_pixels)
        mask = decode_png(body.get("mask"), max_pixels)
        seed = bounded_int(body.get("seed", secrets.randbelow(2**32)), 0, 2**53 - 1, "Seed")
        prompt = edit_prompt(body.get("mode", "fill"), body.get("prompt", ""))
        feather = bounded_int(body.get("feather", 4), 0, 64, "Edge softness")
        rectangle = body.get("return_rectangle", False)
        preserve_roi = body.get("preserve_roi", False)
        masked_input = body.get("masked_input", False)
        if any(not isinstance(value, bool) for value in (rectangle, preserve_roi, masked_input)):
            raise ValueError("Rectangle, ROI and masked-input options must be booleans.")
        edit = prepare_edit(image, mask, context=body.get("context", 96),
                            max_size=body.get("max_size", 768), grow=body.get("grow", 8),
                            preserve_roi=preserve_roi)
        with self.lock:
            # Bound memory, releasing old result PNGs and their masks.
            for key, job in list(self.jobs.items()):
                if job.state in TERMINAL and time.time() - job.created > 3600:
                    del self.jobs[key]
            active = [job for job in self.jobs.values() if job.state not in TERMINAL]
            if len(active) >= 4:
                raise ValueError("The queue is full. Wait for the current generation to finish.")
            if len(self.jobs) >= 20:
                oldest = next((key for key, value in self.jobs.items() if value.state in TERMINAL), None)
                if oldest:
                    del self.jobs[oldest]
            job = Job(uuid.uuid4().hex)
            self.jobs[job.id] = job
            self.pool.submit(self._run, job, edit, seed, prompt, feather, rectangle, masked_input)
            return job

    def _run(self, job, edit, seed, prompt, feather, rectangle=False, masked_input=False):
        def progress(message):
            with self.lock:
                job.message = message
                fraction = sampling_fraction(message)
                if fraction is not None:
                    job.progress = max(job.progress or 0.0, fraction)
        try:
            with self.lock:
                if job.cancel.is_set():
                    raise Cancelled()
                job.state = "running"
            from .image_ops import masked_reference
            reference = masked_reference(edit.model_image, edit.model_mask) if masked_input else edit.model_image
            output = self.client.generate(reference, edit.model_mask, seed, prompt, job.cancel, progress)
            if job.cancel.is_set():
                raise Cancelled()
            patch = result_patch(edit, output, feather)
            result = {"image": base64.b64encode(png_bytes(patch)).decode("ascii"),
                      "x": edit.box[0], "y": edit.box[1], "seed": seed,
                      "width": patch.width, "height": patch.height,
                      "model": getattr(self.client, "model_name", self.config["diffusion_model"])}
            if rectangle:
                # Keep every generated pixel on the new layer; the editor owns a separate,
                # editable layer mask. Legacy clients still receive an alpha-clipped patch.
                from PIL import Image, ImageChops, ImageFilter
                full = output.convert("RGBA").resize(patch.size, Image.Resampling.LANCZOS)
                coverage = edit.mask.crop(edit.box)
                if feather:
                    coverage = ImageChops.multiply(coverage, coverage.filter(ImageFilter.GaussianBlur(feather)))
                result["image"] = base64.b64encode(png_bytes(full)).decode("ascii")
                result["mask"] = base64.b64encode(png_bytes(coverage)).decode("ascii")
            with self.lock:
                if job.cancel.is_set():
                    raise Cancelled()
                job.result, job.state, job.message = result, "done", "Done"
        except Cancelled:
            with self.lock:
                job.state, job.message = "cancelled", "Cancelled"
        except Exception as error:
            with self.lock:
                job.state = "cancelled" if job.cancel.is_set() else "error"
                job.message = "Cancelled" if job.cancel.is_set() else str(error)

    def shutdown(self):
        with self.lock:
            for job in self.jobs.values():
                job.cancel.set()
        self.pool.shutdown(wait=True, cancel_futures=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "GIMPGenFill/" + __version__

    def log_message(self, fmt, *args):
        print(f"{self.log_date_time_string()} {fmt % args}", flush=True)

    def send_json(self, status: int, value):
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def reject_browser(self):
        # Drain a bounded small body so closing the socket does not discard the
        # rejection on Windows. Never process or allocate the supplied image.
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if 0 < length <= 64 * 1024:
                self.connection.settimeout(2)
                self.rfile.read(length)
        except (ValueError, OSError):
            pass
        return self.send_json(403, {"error": "Browser requests are disabled."})

    def dispatch(self):
        # The GIMP client has no Origin. Browser pages cannot submit local jobs.
        if self.headers.get("Origin"):
            return self.reject_browser()
        path = urlparse(self.path).path
        manager = self.server.manager
        try:
            if self.command == "GET" and path == "/health":
                try:
                    _, problems = manager.client.inspect()
                except Exception as error:
                    problems = [str(error)]
                return self.send_json(200, {"service": "gimp-genfill", "version": __version__,
                                           "ready": not problems, "problems": problems,
                                           "model": manager.config["diffusion_model"],
                                           "comfy_url": manager.config["comfy_url"],
                                           "clip_device": manager.config.get("clip_device", "default")})
            if self.command == "POST" and path == "/jobs":
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    return self.send_json(415, {"error": "Content-Type: application/json required."})
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 192 * 1024 * 1024:
                    return self.send_json(413, {"error": "The request exceeds the size limit."})
                self.connection.settimeout(60)
                body = json.loads(self.rfile.read(length))
                return self.send_json(202, manager.submit(body).public())
            pieces = path.strip("/").split("/")
            if len(pieces) in (2, 3) and pieces[0] == "jobs":
                if len(pieces) == 3 and pieces[2] == "cancel" and self.command == "POST":
                    # Consume the request before closing this HTTP/1.0 connection.
                    # Windows can reset a socket with unread body bytes, losing the
                    # cancellation response even though the operation succeeded.
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 <= length <= 64 * 1024:
                        return self.send_json(413, {"error": "The cancel request exceeds the size limit."})
                    self.connection.settimeout(5)
                    if len(self.rfile.read(length)) != length:
                        raise ValueError("Incomplete cancel request.")
                with manager.lock:
                    job = manager.jobs.get(pieces[1])
                    if job is None:
                        return self.send_json(404, {"error": "Job not found."})
                    if len(pieces) == 2 and self.command == "GET":
                        return self.send_json(200, job.public())
                    if len(pieces) == 3 and pieces[2] == "result" and self.command == "GET":
                        if job.state != "done":
                            return self.send_json(409, {"error": "The result is not ready yet."})
                        return self.send_json(200, job.result)
                    if len(pieces) == 3 and pieces[2] == "cancel" and self.command == "POST":
                        if job.state not in TERMINAL:
                            job.cancel.set()
                            job.state, job.message = "cancelled", "Cancelled"
                        return self.send_json(200, job.public())
            return self.send_json(404, {"error": "Route not found."})
        except (ValueError, KeyError, TypeError) as error:
            return self.send_json(400, {"error": str(error)})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as error:
            return self.send_json(500, {"error": str(error)})

    do_GET = dispatch
    do_POST = dispatch


def create_server(config: dict, host="127.0.0.1", port=8765, client=None):
    server = ThreadingHTTPServer((host, port), Handler)
    server.manager = JobManager(config, client)
    return server


def load_config(path: Path | None = None) -> dict:
    config = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
    path = path or ROOT / "config.local.json"
    if path.exists():
        config.update(json.loads(path.read_text(encoding="utf-8-sig")))
    return config


def main():
    parser = argparse.ArgumentParser(description="GIMP GenFill local bridge")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = create_server(load_config(args.config), port=args.port)
    print(f"GIMP GenFill: http://127.0.0.1:{server.server_port} · Ctrl+C to stop", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.manager.shutdown()


if __name__ == "__main__":
    main()
