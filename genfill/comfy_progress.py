"""Read only this prompt's sampler events from ComfyUI's official WebSocket API."""
import json
from urllib.parse import urlencode, urlparse, urlunparse


class ComfyProgress:
    def __init__(self, base, client_id, progress):
        self.socket = None
        self.progress = progress
        try:
            import websocket
            url = urlparse(base)
            url = urlunparse(("wss" if url.scheme == "https" else "ws", url.netloc,
                              url.path.rstrip("/") + "/ws", "", urlencode({"clientId": client_id}), ""))
            self.socket = websocket.create_connection(url, timeout=2, suppress_origin=True)
            self.socket.settimeout(0.04)
        except Exception:
            # Generation remains usable if a proxy/older server has no progress stream.
            self.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        if self.socket:
            try:
                self.socket.close(timeout=0)
            except Exception:
                pass
            self.socket = None

    def poll(self, prompt_id):
        if self.socket is None:
            return
        for _ in range(64):
            try:
                event = self.socket.recv()
            except TimeoutError:
                return
            except Exception as error:
                # websocket-client's timeout is not derived from built-in TimeoutError.
                if error.__class__.__name__ == "WebSocketTimeoutException":
                    return
                self.close()
                return
            if not isinstance(event, str) or len(event) > 1 << 20:
                continue
            try:
                event = json.loads(event)
                data = event.get("data", {})
                if (event.get("type") != "progress" or data.get("prompt_id") != prompt_id
                        or str(data.get("node")) != "12"):
                    continue
                value, total = data.get("value"), data.get("max")
                if (type(value) is int and type(total) is int and 0 < total < 100000000
                        and 0 <= value <= total):
                    self.progress(f"Generating · {value}/{total}")
            except (ValueError, TypeError, AttributeError):
                continue
