"""Retry read-only job polling while a busy local worker temporarily stalls."""
import time
from urllib.error import HTTPError, URLError


def poll(url, *, request, cancelled, pause, progress=lambda _:None, timeout=3, max_stall=120):
    started=time.monotonic()
    while True:
        if cancelled():
            from genfill.comfy import Cancelled
            raise Cancelled()
        try:
            return request(url,timeout=timeout)
        except HTTPError:
            raise
        except (TimeoutError,URLError,ConnectionError,OSError):
            if time.monotonic()-started>=max_stall:
                raise TimeoutError("The local engine is not responding. Check its status in Preferences > GenGate.") from None
            progress("Waiting for the local engine...")
            pause(0.25)
