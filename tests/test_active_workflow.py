"""Fresh active-browser snapshots; no model code or user browser is used."""
import asyncio
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
try:
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
except ImportError:
    web = None


@unittest.skipIf(web is None, 'HTTP extension checks use the existing ComfyUI Python')
class ActiveWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        routes = web.RouteTableDef()
        self.replies = []
        self.tasks = []
        self.nonces = []
        def send(event, value):
            self.assertEqual(event, 'gengate.workflow.request')
            self.nonces.append(value['nonce'])
            for reply in self.replies:
                self.tasks.append(asyncio.create_task(self.client.post('/gengate/active-workflow', json={**reply, 'nonce': value['nonce']})))
        server = types.SimpleNamespace(PromptServer=types.SimpleNamespace(instance=types.SimpleNamespace(routes=routes, send_sync=send)))
        spec=importlib.util.spec_from_file_location('test_live_bridge',ROOT/'comfyui/GenGate/bridge.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with patch.dict(sys.modules, {'server':server}):module.register()
        app=web.Application(client_max_size=10 << 20);app.add_routes(routes)
        self.client=TestClient(TestServer(app));await self.client.start_server()

    async def asyncTearDown(self):
        if self.tasks:await asyncio.gather(*self.tasks)
        await self.client.close()

    async def test_each_request_reads_unsaved_graph_from_last_used_tab(self):
        now=time.time()*1000
        self.replies=[{'activeAt':now-1000,'visible':True,'graph':{'text':'old tab'}},
                      {'activeAt':now,'visible':False,'graph':{'text':'unsaved edit'}}]
        one=await (await self.client.get('/gengate/active-workflow')).json()
        self.assertEqual(one['graph']['text'],'unsaved edit')
        self.replies=[{'activeAt':now,'visible':True,'graph':{'text':'changed again'}}]
        two=await (await self.client.get('/gengate/active-workflow')).json()
        self.assertEqual(two['graph']['text'],'changed again')
        self.assertNotEqual(*self.nonces)
        late=await (await self.client.post('/gengate/active-workflow',json={**self.replies[0],'nonce':self.nonces[0]})).json()
        self.assertFalse(late['accepted'])

    async def test_no_browser_and_serialization_failure_are_explicit(self):
        response=await self.client.get('/gengate/active-workflow')
        self.assertEqual(response.status,409)
        self.assertIn('browser',(await response.json())['error'])
        self.replies=[{'activeAt':time.time()*1000,'error':'Cannot serialize current graph'}]
        response=await self.client.get('/gengate/active-workflow')
        self.assertEqual(response.status,409)
        self.assertIn('serialize',(await response.json())['error'])


class FrontendSnapshotTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'),'JavaScript module checks use Node')
    def test_real_extension_serializes_current_graph_without_queueing(self):
        result=subprocess.run(['node','--experimental-vm-modules',str(ROOT/'tests/fixtures/active_workflow.mjs')],
                              capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
