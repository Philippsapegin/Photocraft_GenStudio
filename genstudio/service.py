from __future__ import annotations

import json
from http.server import ThreadingHTTPServer

from genfill.server import Handler as JobHandler, JobManager
from .lifecycle import EngineHost
from .settings import load, save, validate
from . import VERSION


class Handler(JobHandler):
    server_version = "PhotoCraftGenStudio/" + VERSION

    def dispatch(self):
        if self.headers.get("Origin"):
            return self.reject_browser()
        if self.command == "GET" and self.path == "/health":
            return self.send_json(200, {"service": "photocraft-genstudio", "version": VERSION})
        if self.command == "POST" and self.path == "/engine":
            try:
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise ValueError("Content-Type: application/json required.")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 64 * 1024:
                    raise ValueError("Invalid control request size.")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("Expected a control request object.")
                action = body.get("action")
                if action not in ("status", "start", "stop", "ensure", "configure"):
                    raise ValueError("Unknown engine action.")
                host = self.server.host
                with host.lock:
                    settings = validate(body["settings"]) if body.get("settings") is not None else None
                    changed = settings is not None and settings != host.settings
                    with self.server.manager.lock:
                        active = any(job.state not in ("done", "error", "cancelled")
                                     for job in self.server.manager.jobs.values())
                    if active and (changed or action == "stop"):
                        raise ValueError("Wait for the current AI request before changing the engine.")
                    if changed:
                        host.configure(settings)
                        save(host.settings)
                    if action in ("start", "ensure"):
                        result = host.ensure("manual" if action == "start" else body.get("event", "request"))
                    elif action == "stop":
                        stopped = host.stop()
                        result = {**host.status(), **stopped}
                    else:
                        result = host.status()
                return self.send_json(200, result)
            except (ValueError, RuntimeError, OSError) as error:
                return self.send_json(400, {"error": str(error)})
        return super().dispatch()

    do_GET = dispatch
    do_POST = dispatch


def create_server(settings=None, port=8766, host=None):
    host = host or EngineHost(settings or load())
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.host = host
    server.manager = JobManager(host.config(), client=host)
    return server


def main():
    server = create_server()
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.manager.shutdown()
        server.host.stop()
        server.server_close()


if __name__ == "__main__":
    main()
