"""Request a fresh API snapshot from ComfyUI's current frontend workflow."""
import asyncio
import json
import math
import time
import uuid


def register():
    from aiohttp import web
    from server import PromptServer
    pending = {}
    routes = PromptServer.instance.routes

    @routes.get('/gengate/active-workflow')
    async def active_workflow(request):
        if len(pending) >= 8:
            return web.json_response({'error': 'Too many workflow requests.'}, status=429)
        nonce = uuid.uuid4().hex
        event, replies = asyncio.Event(), []
        pending[nonce] = event, replies
        try:
            PromptServer.instance.send_sync('gengate.workflow.request', {'nonce': nonce})
            try:
                await asyncio.wait_for(event.wait(), 3)
            except asyncio.TimeoutError:
                return web.json_response({'error': 'Open ComfyUI in your browser and refresh it after updating GenGate nodes. No active workflow responded.'}, status=409)
            # Let other open browser tabs answer; prefer the last one used.
            await asyncio.sleep(.12)
            selected = max(replies, key=lambda item: (item['activeAt'], item['visible']))
            if selected.get('error'):
                return web.json_response({'error': selected['error']}, status=409)
            return web.json_response({'graph': selected['graph']})
        finally:
            pending.pop(nonce, None)

    @routes.post('/gengate/active-workflow')
    async def snapshot(request):
        raw = bytearray()
        async for chunk in request.content.iter_chunked(65536):
            raw.extend(chunk)
            if len(raw) > 8 << 20:
                return web.json_response({'error': 'Workflow exceeds 8 MB.'}, status=413)
        try:
            value = json.loads(raw)
            pair = pending.get(value.get('nonce'))
            active = value.get('activeAt', 0)
            if not pair:
                return web.json_response({'accepted': False})
            if (isinstance(active, bool) or not isinstance(active, (int, float)) or not math.isfinite(active)
                    or not 0 <= active <= time.time() * 1000 + 60000 or len(pair[1]) >= 32):
                raise ValueError('Invalid workflow snapshot.')
            graph, error = value.get('graph'), value.get('error')
            if not isinstance(graph, dict) and not isinstance(error, str):
                raise ValueError('Invalid workflow snapshot.')
            pair[1].append({'activeAt': active, 'visible': value.get('visible') is True,
                            'graph': graph, 'error': str(error)[:1000] if error else None})
            pair[0].set()
            return web.json_response({'accepted': True})
        except (ValueError, TypeError, AttributeError):
            return web.json_response({'error': 'Invalid workflow snapshot.'}, status=400)
