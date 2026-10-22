"""FLUX3 integration contracts. Synthetic files/images only; no torch or weights."""
import base64
import hashlib
import importlib.util
from io import BytesIO
import json
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

from PIL import Image
from genfill.image_ops import png_bytes
from genfill.comfy import ComfyClient
from genstudio.models import MODEL, FLUX, MODELS, catalog, resolve
from genstudio.settings import defaults, validate
from genstudio.lifecycle import EngineHost
from genstudio.direct_worker import Worker
from genstudio.studio import request
from genstudio.flux_pipeline import expansion_prompt, masked_prompt, FluxPipeline, caption_key
from genstudio.flux_workflow import workflow, NODES


class FluxTests(unittest.TestCase):
    def test_shared_models_discovery_chooses_only_the_selected_family(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for spec in MODELS.values():
                for name, folders in spec["files"].values():
                    path = root / folders[0] / "nested" / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"synthetic checkpoint")
            before = sorted(str(p) for p in root.rglob("*"))
            for model in (MODEL, FLUX):
                files = resolve(root, model=model)
                self.assertIn(MODELS[model]["files"]["diffusion_model"][0], files["diffusion_model"]["path"])
                choices = catalog(root, model)
                self.assertEqual(choices["referenceLimit"], 10)
                self.assertTrue(choices["defaultTextEncoder"].endswith(MODELS[model]["text_encoders"][0]))
                self.assertEqual(len([x for x in choices["vaes"] if x["compatible"]]), 1)
            self.assertIn("prompt_upsampler", resolve(root, model=FLUX))
            self.assertEqual(before, sorted(str(p) for p in root.rglob("*")))

    def test_incomplete_download_is_never_discovered_as_a_model(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name, folders in MODELS[FLUX]["files"].values():
                path = root / folders[0] / (name + ".part")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"partial")
            self.assertEqual(catalog(root, FLUX)["defaultTextEncoder"], "")
            with self.assertRaisesRegex(ValueError, "missing"):
                resolve(root, model=FLUX)

    def test_switching_model_preserves_engine_and_startup_policies(self):
        for engine in ("direct", "comfy"):
            data = validate({**defaults(), "model": FLUX, "engine": engine, "directStartup": "off"})
            self.assertEqual(data["model"], FLUX)
            self.assertEqual(data["engine"], engine)
            self.assertEqual(data["directStartup"], "off")
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            validate({"model": "flux-2-klein"})

    def test_readiness_cannot_attach_to_a_worker_for_the_other_model(self):
        settings = {**defaults(), "model": FLUX, "engine": "direct"}
        def probe(url):
            return {"service": "genstudio-direct", "ready": True, "modelsDir": settings["modelsDir"],
                    "textEncoder": "", "vae": "", "model": MODEL}
        host = EngineHost(settings, request=probe)
        host.direct_url = "http://127.0.0.1:54321"
        self.assertFalse(host.probe())
        host.request = lambda url: {**probe(url), "model": FLUX, "owner": host.worker_id}
        self.assertTrue(host.probe())

    def test_worker_selection_is_lazy_and_does_not_import_a_model_library(self):
        import sys
        before = set(sys.modules)
        worker = Worker("unused", model_id=FLUX)
        try:
            self.assertIsInstance(worker.pipeline, FluxPipeline)
            self.assertIsNone(worker.pipeline.pipe)
            self.assertFalse(any(n == "torch" or n.startswith("comfy.") for n in set(sys.modules) - before))
        finally:
            worker.pool.shutdown()

    def test_reference_cap_and_model_grid_are_validated_before_queuing(self):
        image = base64.b64encode(png_bytes(Image.new("RGBA", (48, 80), "red"))).decode()
        payload = {"prompt": "Use <ref_image_0>", "width": 272, "height": 256, "references": [image] * 10}
        self.assertEqual(len(request(payload, FLUX)["references"]), 10)
        with self.assertRaisesRegex(ValueError, "10 reference"):
            request({**payload, "references": [image] * 11}, FLUX)
        with self.assertRaisesRegex(ValueError, "32"):
            request(payload, MODEL)

    def test_a_concurrent_model_change_cannot_queue_a_request_for_the_wrong_pipeline(self):
        from genstudio.studio import StudioJobs
        host = types.SimpleNamespace(settings={"model": FLUX}, lock=threading.RLock())
        jobs = StudioJobs(host)
        def validate_then_switch(body, model):
            result = request(body, model)
            host.settings["model"] = MODEL
            return result
        try:
            with patch("genstudio.studio.request", side_effect=validate_then_switch):
                with self.assertRaisesRegex(ValueError, "model changed"):
                    jobs.submit({"prompt": "scene", "width": 272, "height": 256})
            self.assertFalse(jobs.jobs)
        finally:
            jobs.shutdown()

    def test_distilled_graph_uses_flux_encoder_scheduler_and_reference_order(self):
        config = {key: value[0] for key, value in MODELS[FLUX]["files"].items()}
        graph = workflow(config, [f"ref-{n}.png" for n in range(10)], 14, "Use <ref_image_9>", 1024, 768)
        self.assertEqual(graph["2"]["inputs"]["type"], "flux3")
        self.assertEqual(graph["12"]["inputs"]["steps"], 16)
        self.assertEqual(graph["12"]["inputs"]["cfg"], 1.0)
        self.assertEqual(graph["12"]["inputs"]["scheduler"], "simple")
        self.assertEqual(graph["9"]["class_type"], "EmptyFlux2LatentImage")
        self.assertEqual(graph["8"]["inputs"]["images.image9"], ["47", 0])
        self.assertEqual(graph["49"]["inputs"]["conditioning"], ["46", 0])
        self.assertEqual(graph["12"]["inputs"]["positive"], ["49", 0])
        self.assertIn("<ref_image_9>", graph["6"]["inputs"]["prompt"])
        self.assertEqual(graph["6"]["inputs"]["sampling_mode.top_k"], 1)
        self.assertIn("ref_image_0", graph["6"]["inputs"]["system_prompt"])
        self.assertTrue(all(n["class_type"] in NODES for n in graph.values()))

    def test_text_only_graph_does_not_require_a_reference_tensor(self):
        config = {key: value[0] for key, value in MODELS[FLUX]["files"].items()}
        graph = workflow(config, [], 1, "A forest", 1024, 1024)
        self.assertNotIn("8", graph)
        self.assertNotIn("image", graph["6"]["inputs"])
        self.assertEqual(graph["7"]["inputs"]["text"], ["6", 0])
        self.assertEqual(graph["14"]["class_type"], "SaveImage")

    def test_comfy_dispatch_preserves_reference_transparency_and_uses_the_flux_graph(self):
        config = {key: value[0] for key, value in MODELS[FLUX]["files"].items()}
        config.update(model=FLUX, comfy_url="http://127.0.0.1:8190")
        client = ComfyClient(config)
        client.inspect = lambda **kwargs: ({}, [])
        pixels, graphs = [], []
        def upload(name, data):
            with Image.open(BytesIO(data)) as image:
                pixels.append(image.convert("RGBA").getpixel((0, 0)))
            return name
        client.upload = upload
        client.run_workflow = lambda graph, *args: graphs.append(graph)
        client.generate_studio([Image.new("RGBA", (32, 32), (255, 0, 0, 0))], 2, "scene", 256, 256,
                              threading.Event(), lambda msg: None)
        self.assertEqual(pixels, [(255, 255, 255, 255)])
        self.assertEqual(graphs[0]["2"]["inputs"]["type"], "flux3")

    def test_old_comfy_reports_unsupported_flux_instead_of_running_qwen(self):
        config = {key: value[0] for key, value in MODELS[FLUX]["files"].items()}
        config.update(model=FLUX, comfy_url="http://127.0.0.1:8190")
        info = {name: {"input": {"required": {}}} for name in NODES}
        for node, field, value in (("UNETLoader", "unet_name", config["diffusion_model"]),
                                  ("CLIPLoader", "clip_name", config["text_encoder"]),
                                  ("VAELoader", "vae_name", config["vae"])):
            info[node]["input"]["required"][field] = [[value, config["prompt_upsampler"]]]
        info["CLIPLoader"]["input"]["required"]["type"] = [["qwen_image"]]
        client = ComfyClient(config)
        client.request = lambda route, **kw: info if route == "/object_info" else {"system": {}}
        self.assertTrue(any("0.41.3" in p for p in client.inspect(studio=True)[1]))
        info["CLIPLoader"]["input"]["required"]["type"] = [["flux3"]]
        self.assertEqual(client.inspect()[1], [])

    def test_masked_flux_uses_two_distinct_references_and_never_exposes_the_mask(self):
        from genstudio.core_pipeline import inpaint_references
        source = Image.new("RGBA", (32, 16), (220, 0, 0, 255))
        mask = Image.new("L", source.size)
        mask.putpixel((4, 5), 255)
        reference, coverage = inpaint_references(source, mask)
        self.assertEqual(reference.getpixel((4, 5)), (127, 127, 127, 255))
        self.assertEqual(reference.getpixel((0, 0)), source.getpixel((0, 0)))
        self.assertEqual(coverage.getpixel((4, 5)), (255, 255, 255, 255))
        prompt = masked_prompt("Remove this object.")
        self.assertIn("<ref_image_0>", prompt)
        self.assertIn("<ref_image_1>", prompt)
        self.assertIn("Remove this object", prompt)

    def test_official_prompt_templates_cover_text_image_and_layout(self):
        system, text = expansion_prompt("A forest", [], 1024, 768)
        self.assertIn("TARGET CANVAS: 1024:768", text)
        self.assertIn("text-to-image", system)
        system, text = expansion_prompt("Use <ref_image_0>", [object()], 768, 1024)
        self.assertIn("ref_image_0", system)
        self.assertIn("<ref_image_0>", text)
        system, _ = expansion_prompt('scene [{"bbox":[1,2,3,4]}]', [], 1024, 1024)
        self.assertIn("JSON array", system)

    def test_caption_reuse_depends_on_pixels_order_prompt_and_canvas(self):
        red, blue = Image.new("RGBA", (32, 16), "red"), Image.new("RGBA", (32, 16), "blue")
        key = caption_key([red, blue], "scene", 1024, 768)
        self.assertEqual(key, caption_key([red.copy(), blue.copy()], "scene", 1024, 768))
        self.assertNotEqual(key, caption_key([blue, red], "scene", 1024, 768))
        self.assertNotEqual(key, caption_key([red, blue], "another scene", 1024, 768))
        self.assertNotEqual(key, caption_key([red, blue], "scene", 768, 1024))

    def test_prompt_upsampler_releases_previous_pipeline_and_restores_progress_hook(self):
        import sys
        events = []
        utils = types.ModuleType("comfy.utils")
        utils.PROGRESS_BAR_HOOK = object()
        previous = utils.PROGRESS_BAR_HOOK
        utils.set_progress_bar_global_hook = lambda value: setattr(utils, "PROGRESS_BAR_HOOK", value)
        memory = types.ModuleType("comfy.model_management")
        memory.unload_all_models = lambda: events.append("unload")
        memory.soft_empty_cache = lambda: None
        comfy = types.ModuleType("comfy")
        comfy.__path__ = []
        comfy.utils, comfy.model_management = utils, memory
        cancel = threading.Event()
        class LLM:
            def tokenize(self, text, **kwargs):
                self.arguments = kwargs
                return text
            def generate(self, tokens, **kwargs):
                utils.PROGRESS_BAR_HOOK(1, 50, None, node_id="synthetic")
                return "caption"
            def decode(self, generated):
                return "</think>A scene from <ref_image_0>."
        llm = LLM()
        sd = types.SimpleNamespace(load_clip=lambda *a, **kw: llm, CLIPType=types.SimpleNamespace(FLUX3=37))
        pipeline = FluxPipeline("unused")
        pipeline.pipe = object()
        messages = []
        with patch.dict(sys.modules, {"comfy": comfy, "comfy.utils": utils, "comfy.model_management": memory}), \
             patch("genstudio.flux_pipeline.import_core", return_value=(sd, None)), \
             patch("genstudio.flux_pipeline.validate_upsampler"), \
                patch("genstudio.flux_pipeline.resolve_components", return_value={"prompt_upsampler": {"path": "synthetic"}}):
            result = pipeline.expand(["reference"], 7, "Use <ref_image_0>", 1024, 1024, cancel, messages.append)
            self.assertEqual(result, "A scene from <ref_image_0>.")
            self.assertEqual(llm.arguments["images"], ["reference"])
            self.assertIsNone(pipeline.pipe)
            self.assertIs(utils.PROGRESS_BAR_HOOK, previous)
            self.assertEqual(events, ["unload", "unload"])
            self.assertIn("FLUX 3 \u00b7 expanding prompt \u00b7 1 tokens", messages)
            from genfill.progress import sampling_fraction
            self.assertIsNone(sampling_fraction(messages[-1]))
            cancel.set()
            with self.assertRaises(Exception) as caught:
                pipeline.expand([], 7, "scene", 1024, 1024, cancel, lambda msg: None)
            from genfill.comfy import Cancelled
            self.assertIsInstance(caught.exception, Cancelled)
            self.assertIs(utils.PROGRESS_BAR_HOOK, previous)

    def test_conditioning_only_h3_cannot_be_used_as_a_prompt_upsampler(self):
        import struct
        from genstudio.flux_pipeline import validate_upsampler
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "custom.safetensors"
            def checkpoint(metadata):
                data = json.dumps({"__metadata__": metadata}).encode()
                path.write_bytes(struct.pack("<Q", len(data)) + data)
            checkpoint({"minimax_h3_lm_head": "false", "minimax_h3_final_norm": "false"})
            with self.assertRaisesRegex(ValueError, "conditioning encoder"):
                validate_upsampler(path)
            with patch("genstudio.flux_pipeline.resolve_components", return_value={"prompt_upsampler": {"path": str(path)}}), \
                    patch("genstudio.flux_pipeline.import_core") as gpu:
                with self.assertRaisesRegex(ValueError, "conditioning encoder"):
                    FluxPipeline(folder).expand([], 0, "prompt", 1024, 1024, threading.Event(), lambda msg: None)
                gpu.assert_not_called()
            # Custom full checkpoints remain allowed; a filename is not proof
            # that an encoder is truncated or incapable of generating text.
            checkpoint({"format": "pt", "source_repo": "custom prompt upsampler"})
            validate_upsampler(path)
            path.write_bytes(b"incomplete")
            with self.assertRaisesRegex(ValueError, "Cannot read"):
                validate_upsampler(path)


class DownloadTests(unittest.TestCase):
    def test_resume_requests_only_missing_bytes_and_checks_the_response_range(self):
        script = Path(__file__).resolve().parents[1] / "scripts/download_flux3.py"
        spec = importlib.util.spec_from_file_location("flux_download", script)
        loader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(loader)
        content = b"synthetic resumable checkpoint"
        entry = {"path": "model.safetensors", "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        class Response(BytesIO):
            status = 206
            headers = {"Content-Range": f"bytes 5-{len(content)-1}/{len(content)}"}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "model.safetensors.part").write_bytes(content[:5])
            calls = []
            def fetch(req, **kwargs):
                calls.append(req.get_header("Range"))
                return Response(content[5:])
            with patch.object(loader, "urlopen", side_effect=fetch):
                loader.download(entry, root, {"repository": "fixture", "revision": "pin"}, lambda *args: None)
            self.assertEqual(calls, ["bytes=5-"])
            self.assertEqual((root / "model.safetensors").read_bytes(), content)

    def test_download_preserves_existing_weights_and_verifies_completed_partials(self):
        script = Path(__file__).resolve().parents[1] / "scripts/download_flux3.py"
        spec = importlib.util.spec_from_file_location("flux_download", script)
        loader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(loader)
        content = b"small synthetic checkpoint"
        entry = {"path": "vae/model.safetensors", "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / entry["path"]
            target.parent.mkdir()
            partial = target.with_suffix(".safetensors.part")
            partial.write_bytes(content)
            events = []
            loader.download(entry, root, {"repository":"fixture", "revision":"synthetic"}, lambda *args: events.append(args))
            self.assertEqual(target.read_bytes(), content)
            self.assertFalse(partial.exists())
            loader.download(entry, root, {"repository":"fixture", "revision":"synthetic"}, lambda *args: events.append(args))
            target.write_bytes(b"preserve my existing file")
            with self.assertRaisesRegex(ValueError, "preserved"):
                loader.download(entry, root, {"repository":"fixture", "revision":"synthetic"}, lambda *args: None)
            self.assertEqual(target.read_bytes(), b"preserve my existing file")


if __name__ == "__main__":
    unittest.main()
