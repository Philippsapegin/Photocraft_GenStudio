"""Real PNG/file/queue contracts with synthetic data; no torch or checkpoints."""
import base64
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

try:
    import numpy as np
except ImportError:
    np = None
from PIL import Image
from genfill.comfy import ComfyClient, Cancelled
from genfill.image_ops import png_bytes
from genstudio import comfy_workflow as bridge
from genstudio.settings import defaults, validate
from genstudio.studio import StudioJobs, request

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gengate_bridge_nodes", ROOT / "comfyui/GenGate/nodes.py")
nodes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nodes)


def graph():
    return {"source": {"class_type": "GenGateIN2", "inputs": {}},
            "invert": {"class_type": "ImageInvert", "inputs": {"image": ["source", 0]}},
            "return": {"class_type": "GenGateOUT2", "inputs": {"images": ["invert", 0]}}}


class Tensor:
    """NumPy-backed test input; the bridge must preserve its pixel values."""
    def __init__(self, value): self.value = np.asarray(value)
    @property
    def shape(self): return self.value.shape
    def __getitem__(self, key): return Tensor(self.value[key])
    def __iter__(self): return (Tensor(item) for item in self.value)
    def detach(self): return self
    def float(self): return self
    def cpu(self): return self
    def numpy(self): return self.value


class NodeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.context = {"protocol": bridge.PROTOCOL, "request_id": "f" * 32, "prompt": "A field", "source_prompt": "Original field", "width": 1024, "height": 768, "seed": 7, "batch": 2, "loras": []}
        self.modules = patch.dict(sys.modules, {"folder_paths": types.SimpleNamespace(get_temp_directory=lambda: str(self.root)),
                                               "torch": types.SimpleNamespace(from_numpy=Tensor)})
        self.modules.start()
    def tearDown(self):
        self.modules.stop(); self.temp.cleanup()
    def image(self, color, size, index):
        name = f"GenGate-{self.context['request_id']}-ref-{index}.png"
        Image.new("RGBA", size, color).save(self.root / name)
        return name + " [temp]"
    def test_nodes_have_sockets_and_hidden_context_without_editable_widgets(self):
        self.assertFalse(nodes.GenGateIN.INPUT_TYPES()["required"])
        self.assertEqual(list(nodes.GenGateIN.RETURN_NAMES), bridge.PORT_NAMES)
        self.assertFalse(set(nodes.GenGateIN.RETURN_NAMES) & {"preset", "model", "text_encoder", "vae", "document", "layer", "selection", "references", "reference_masks", "reference_count", "metadata", "source_prompt"})
        for kind, *options in nodes.GenGateOUT.INPUT_TYPES()["optional"].values():
            if kind in ("STRING", "INT"):
                self.assertTrue(options[0]["forceInput"])
        self.assertTrue(nodes.GenGateOUT.OUTPUT_NODE)
    @unittest.skipIf(np is None, "Pixel bridge tests use NumPy from the ComfyUI Python")
    def test_individual_references_keep_order_dimensions_and_alpha(self):
        self.context["references"] = [self.image((10, 20, 30, 64), (3, 2), 1), self.image("red", (5, 7), 2)]
        values = dict(zip(nodes.GenGateIN.RETURN_NAMES, nodes.GenGateIN().receive(extra_pnginfo={"gengate": self.context})))
        self.assertEqual(values["image"].shape, (1, 2, 3, 3))
        self.assertEqual(values["reference_2"].shape, (1, 7, 5, 3))
        self.assertAlmostEqual(float(values["mask"].numpy()[0, 0, 0]), 1 - 64 / 255, places=6)
        self.assertEqual(values["prompt"], "A field")
        self.assertAlmostEqual(float(values["reference_2_mask"].numpy()[0, 0, 0]), 0.0)
    def test_text_only_skips_images_and_connected_missing_reference_is_an_error(self):
        definition = {"source": {"inputs": {}}, "text": {"inputs": {"text": ["source", 2]}}}
        result = nodes.GenGateIN().receive(extra_pnginfo={"gengate": self.context}, prompt_graph=definition, unique_id="source")
        self.assertIsNone(result[0]); self.assertEqual(result[2], "A field")
        definition["image"] = {"inputs": {"image": ["source", 0]}}
        with self.assertRaisesRegex(ValueError, "not available"):
            nodes.GenGateIN().receive(extra_pnginfo={"gengate": self.context}, prompt_graph=definition, unique_id="source")
    def test_lora_helper_chains_selected_strengths_and_validates_before_loading(self):
        calls = []
        class Loader:
            def load_lora(self, model, clip, path, model_strength, clip_strength):
                calls.append((model, clip, path, model_strength, clip_strength))
                return model + "+" + path, None if clip is None else clip + "+" + path
        folder = types.SimpleNamespace(get_filename_list=lambda _: ["style.safetensors", "nested\\detail.safetensors"])
        with patch.dict(sys.modules, {"nodes": types.SimpleNamespace(LoraLoader=Loader), "folder_paths": folder}):
            apply = nodes.GenGateApplyLoRAs().apply
            self.assertEqual(apply("model", "[]", "clip"), ("model", "clip"))
            selected = [{"path":"style.safetensors","strength":.7},{"path":"nested/detail.safetensors","strength":-.2}]
            result = apply("model", json.dumps(selected), "clip")
            self.assertEqual(calls[0], ("model", "clip", "style.safetensors", .7, .7))
            self.assertEqual(calls[1][0], "model+style.safetensors")
            self.assertEqual(calls[1][3:], (-.2, -.2))
            self.assertTrue(result[0].endswith("detail.safetensors"))
            self.assertIsNone(apply("model", json.dumps(selected), None)[1])
            for value in ([{"path":"../bad.safetensors"}], [{"path":"style.safetensors","strength":float("nan")}], selected+[selected[0]], selected+[{"path":"missing.safetensors"}]):
                calls.clear()
                with self.assertRaises(ValueError): apply("model", json.dumps(value))
                self.assertFalse(calls, "Validate the full recipe before opening any weights")
            self.assertEqual(apply("model", '[{"path":"missing.safetensors","strength":0}]'), ("model", None))
    def test_request_identity_cache_and_path_escape_are_checked(self):
        first = nodes.GenGateIN.IS_CHANGED(extra_pnginfo={"gengate": self.context})
        other = {**self.context, "request_id": "a" * 32}
        self.assertNotEqual(first, nodes.GenGateIN.IS_CHANGED(extra_pnginfo={"gengate": other}))
        for name in ("../private.png [temp]", "C:/private.png [temp]", "outside.png [temp]", "ordinary.png"):
            with self.assertRaises(ValueError): nodes.asset_path(name, self.context["request_id"])
        with self.assertRaises(ValueError): nodes.context({})
    def test_negative_port_and_edit_steps_follow_request_without_an_out_prompt(self):
        value={**self.context,'negative_prompt':'text, watermark','steps':None}
        received=dict(zip(nodes.GenGateIN.RETURN_NAMES,nodes.GenGateIN().receive(extra_pnginfo={'gengate':value})))
        self.assertEqual(received['negative_prompt'],'text, watermark')
        self.assertNotIn('prompt',nodes.GenGateOUT.INPUT_TYPES()['optional'])
        self.assertEqual(nodes.GenGateEditSteps().steps(16,extra_pnginfo={'gengate':value}),(16,))
        value['steps']=4
        self.assertEqual(nodes.GenGateEditSteps().steps(16,extra_pnginfo={'gengate':value}),(4,))
    @unittest.skipIf(np is None, "Pixel bridge tests use NumPy from the ComfyUI Python")
    def test_output_batches_masks_prompts_and_multiple_list_calls_do_not_overwrite(self):
        image = Tensor(np.full((2, 3, 4, 3), .25, np.float32))
        mask = Tensor(np.full((1, 3, 4), .5, np.float32))
        out = nodes.GenGateOUT()
        one = out.send(image, mask=mask, layer_name="Retouch", extra_pnginfo={"gengate": self.context}, unique_id="return")
        two = out.send(image, extra_pnginfo={"gengate": self.context}, unique_id="return")
        rows = one["ui"]["gengate"][0]["images"]
        self.assertEqual(len(rows), 2); self.assertNotIn("prompt", rows[0])
        self.assertNotEqual(rows[0]["image"], two["ui"]["gengate"][0]["images"][0]["image"])
        with Image.open(self.root / "gengate" / rows[0]["image"]["filename"]) as saved:
            self.assertEqual(saved.size, (4, 3)); self.assertEqual(saved.getpixel((0, 0)), (64, 64, 64))
        with Image.open(self.root / "gengate" / rows[1]["mask"]["filename"]) as saved:
            self.assertEqual(saved.getpixel((0, 0)), 128)
    @unittest.skipIf(np is None, "Pixel bridge tests use NumPy from the ComfyUI Python")
    def test_output_rejects_mismatched_masks_and_nonfinite_pixels(self):
        image = Tensor(np.ones((1, 2, 3, 3), np.float32))
        with self.assertRaisesRegex(ValueError, "mask"):
            nodes.GenGateOUT().send(image, mask=Tensor(np.ones((1, 1, 1))), extra_pnginfo={"gengate": self.context})
        image.value[0, 0, 0, 0] = float("nan")
        with self.assertRaisesRegex(ValueError, "non-finite"):
            nodes.GenGateOUT().send(image, extra_pnginfo={"gengate": self.context})


class WorkflowTests(unittest.TestCase):
    def test_parser_preserves_arbitrary_nodes_ids_and_parameters(self):
        value = graph(); value["invert"]["inputs"]["custom"] = {"anything": [1, "two"]}
        parsed = bridge.parse_workflow(value)
        self.assertEqual(parsed["graph"], value)
        self.assertIsNot(parsed["graph"], value)
        self.assertEqual(parsed["outputs"], ["return"])
        self.assertEqual(parsed["sha256"], bridge.parse_workflow({"prompt": value})["sha256"])
        named = {"prompt": {"class_type": "GenGateIN2", "inputs": {}}, "output": {"class_type": "GenGateOUT2", "inputs": {"images": ["prompt", 0]}}}
        self.assertEqual(bridge.parse_workflow(named)["inputs"], ["prompt"])
        with self.assertRaisesRegex(ValueError, "Export"):
            bridge.parse_workflow({"nodes": [], "links": []})
        del value["source"]
        with self.assertRaisesRegex(ValueError, "IN"):
            bridge.parse_workflow(value)
    def test_legacy_api_ports_migrate_by_name_or_request_reexport(self):
        value = graph(); value["source"]["class_type"] = "GenGateIN"
        value["return"]["inputs"]["prompt"] = ["source", 5]
        value["text"]={"class_type":"CLIPTextEncode","inputs":{"text":["source",5]}}
        value["return"]["class_type"]="GenGateOUT"
        parsed = bridge.parse_workflow(value)
        self.assertNotIn("prompt",parsed["graph"]["return"]["inputs"])
        self.assertEqual(parsed["graph"]["source"]["class_type"], bridge.IN_NODE)
        self.assertEqual(parsed["graph"]["text"]["inputs"]["text"], ["source", 2])
        self.assertEqual(value["return"]["inputs"]["prompt"], ["source", 5])
        value["text"]["inputs"]["text"] = ["source", 14]
        with self.assertRaisesRegex(ValueError, "removed.*Recreate"):
            bridge.parse_workflow(value)
    def test_studio_only_mode_is_independent_of_the_preset(self):
        settings = validate({**defaults(), "engine": "direct", "studioMode": "workflow"})
        self.assertFalse(bridge.enabled(settings))
        settings["engine"] = "comfy"; settings["studioMode"] = "preset"; self.assertTrue(bridge.enabled(settings))
        source = base64.b64encode(png_bytes(Image.new("RGBA", (301, 257), "red"))).decode()
        result = request({"prompt": "", "references": [source] * 10}, "fibo-2-edit-turbo", workflow=True)
        self.assertEqual(result["references"][0].size, (301, 257))
        self.assertEqual(len(result["references"]), 10)
        with self.assertRaises(ValueError): request({"prompt": "", "references": []}, "fibo-2-edit-turbo")
    def test_node_install_is_owned_idempotent_and_preserves_local_edits(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / "main.py").write_text("# fake Comfy, never execute")
            self.assertTrue(bridge.install_nodes(folder)["changed"])
            self.assertFalse(bridge.install_nodes(folder)["changed"])
            target = root / "custom_nodes/GenGate/nodes.py"
            target.write_text("# local edit")
            with self.assertRaisesRegex(ValueError, "edited locally"): bridge.install_nodes(folder)
            self.assertEqual(target.read_text(), "# local edit")
    def test_new_managed_files_do_not_replace_unmanaged_user_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'main.py').write_text('# fixture')
            bridge.install_nodes(directory)
            folder=root/'custom_nodes/GenGate';marker=folder/'gengate-nodes.json'
            metadata=json.loads(marker.read_text());metadata['files'].pop('bridge.py');marker.write_text(json.dumps(metadata))
            (folder/'bridge.py').write_text('# user code')
            with self.assertRaisesRegex(ValueError,'unmanaged'):bridge.install_nodes(directory)
            self.assertEqual((folder/'bridge.py').read_text(),'# user code')

    def test_queue_uses_graph_batch_and_keeps_result_masks_without_presets(self):
        settings = {**defaults(), "engine": "comfy", "studioMode": "workflow", "studioWorkflow": "fixture.json"}
        result = {"images": [{"image": "fake", "width": 4096, "height": 2048, "mask": "mask", "prompt": "Altered"}], "workflow": {"path": "fixture.json", "sha256": "f" * 64}}
        host = types.SimpleNamespace(settings=settings, lock=threading.RLock(), generate_workflow=lambda *args: result)
        jobs = StudioJobs(host)
        try:
            job = jobs.submit({"prompt": "", "references": [], "batch": 3, "workflowGraph": graph()})
            for _ in range(100):
                if job.state in ("done", "error"): break
                job.cancel.wait(.01)
            self.assertEqual(job.state, "done", job.message)
            self.assertIs(job.result, result)
        finally: jobs.shutdown()
    def test_transport_reads_only_matching_out_nodes_and_preserves_size_mask_recipe(self):
        class Client:
            def request(self, route, *args, **kwargs):
                if route == "/object_info": return {"GenGateIN2": {}, "GenGateOUT2": {}, "ImageInvert": {}}
                image = Image.new("L", (17, 9), 128) if "mask" in route else Image.new("RGB", (17, 9), "blue")
                return png_bytes(image)
            def upload(self, name, raw, **kwargs): return name + " [temp]"
            def assert_envelope(self, envelope):
                assert not set(envelope) & {"model", "text_encoder", "vae", "preset", "document", "layer", "selection", "document_info"}
            def run_workflow(self, workflow, cancel, progress, uid, **kwargs):
                self.uid = uid
                envelope = kwargs["extra_data"]["extra_pnginfo"]["gengate"]
                self.assert_envelope(envelope)
                descriptor = {"filename": "GenGate-" + uid + "-result.png", "subfolder": "gengate", "type": "temp"}
                packet = {"protocol": bridge.PROTOCOL, "request_id": uid, "images": [{"image": descriptor, "mask": {**descriptor, "filename": "GenGate-" + uid + "-mask.png"}, "prompt": "new prompt", "name": "Workflow", "seed": 12, "loras": []}]}
                return {"outputs": {"14": {"images": [{"filename": "unrelated.png"}]}, "return": {"gengate": [packet]}}}
        data = {"prompt": "input", "references": [], "seed": 7, "width": 1024, "height": 1024, "batch": 1, "loras": [], "workflowPath": "fixture.json"}
        result = bridge.generate(Client(), bridge.parse_workflow(graph()), data, {}, threading.Event(), lambda _: None)
        row = result["images"][0]
        self.assertEqual((row["width"], row["height"]), (17, 9))
        self.assertTrue(row["mask"]); self.assertEqual(row["prompt"], "input")
        self.assertEqual(result["workflow"]["path"], "fixture.json")
        class Stale(Client):
            def run_workflow(self, *args, **kwargs):
                result = super().run_workflow(*args, **kwargs)
                result["outputs"]["return"]["gengate"][0]["request_id"] = "old"
                return result
        with self.assertRaisesRegex(RuntimeError, "cached"):
            bridge.generate(Stale(), bridge.parse_workflow(graph()), data, {}, threading.Event(), lambda _: None)
