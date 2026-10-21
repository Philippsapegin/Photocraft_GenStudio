import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from genstudio import segmentation, segmentation_models as models


class SegmentationTests(unittest.TestCase):
    def test_generation_service_receives_no_segmentation_preferences(self):
        from genstudio import control
        with patch.object(control,"health",return_value={"service":"photocraft-genstudio"}),patch.object(control,"http_json",return_value={}) as request:
            control.run("configure",{"engine":"comfy","segmentation":{"enabled":True}})
            self.assertEqual(request.call_args.args[1]["settings"],{"engine":"comfy"})

    def test_defaults_and_validation_are_independent_of_generator(self):
        from genstudio.settings import validate
        for engine in ("direct", "comfy"):
            result=validate({"engine":engine,"segmentation":{"enabled":True}})
            self.assertEqual(result["segmentation"]["detector"],"yolov8s-worldv2")
            self.assertEqual(result["segmentation"]["segmenter"],"sam3")
        for value in (None,[],{"enabled":"yes"},{"segmenter":"wrong"},{"unknown":True},{"python":"\0"}):
            with self.assertRaises(ValueError):models.validate(value)

    def test_subject_policy_ignores_invalid_and_prefers_large_center(self):
        box=segmentation.choose_box([[0,0,3,3],[10,10,90,90],[0,0,float('nan'),5]],[0.99,0.8,0.9],(100,100))
        self.assertEqual(box,[10,10,90,90])
        self.assertIsNone(segmentation.choose_box([[0,0,0,0]],[1],(100,100)))

    def test_default_downloads_only_selected_detector_and_sam(self):
        spec={"files":[{"id":name,"path":name+".pt","bytes":1} for name in ("yolov8s-worldv2","yolov8m-worldv2","sam3")]}
        with tempfile.TemporaryDirectory() as folder, patch.object(models,"manifest",return_value=spec),patch("scripts.download_flux3.download") as download:
            result=models.ensure_models(folder)
            self.assertEqual(set(result),{"yolov8s-worldv2","sam3"})
            self.assertEqual(download.call_count,2)

    def test_existing_common_model_paths_are_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"sams";path.mkdir();existing=path/"sam3.pt";existing.write_bytes(b"abc")
            self.assertEqual(models.existing(folder,{"path":"segmentation/sam3.pt","bytes":3}),existing)

    def test_cancel_and_disabled_requests_never_prepare_or_spawn(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(segmentation,"ensure_modules") as prepare,patch.object(segmentation.subprocess,"Popen") as spawn:
            with self.assertRaises(ValueError):segmentation.run(folder,{"segmentation":{}},{"kind":"subject"},lambda e:None)
            (Path(folder)/"cancel").touch()
            with self.assertRaises(InterruptedError):segmentation.run(folder,{"segmentation":{"enabled":True}},{"kind":"subject"},lambda e:None)
            prepare.assert_not_called();spawn.assert_not_called()

    def test_preparing_models_never_imports_gpu_or_starts_generator(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(segmentation,"ensure_modules",return_value=Path(folder)),patch.object(segmentation,"ensure_models",return_value={}),patch.object(segmentation.subprocess,"Popen") as spawn:
            events=[]
            segmentation.run(folder,{"engine":"comfy","modelsDir":folder},{"kind":"prepare"},events.append)
            self.assertTrue(events[-1]["done"])
            spawn.assert_not_called()

    def test_inference_guard_stops_before_gpu_process(self):
        import sys
        with tempfile.TemporaryDirectory() as folder,patch.object(segmentation,"ensure_modules",return_value=Path(folder)),patch.object(segmentation,"ensure_models",return_value={}),patch.dict("os.environ",{"GENGATE_NO_INFERENCE":"1"}),patch.object(segmentation.subprocess,"Popen") as spawn:
            with self.assertRaisesRegex(RuntimeError,"disabled for validation"):
                segmentation.run(folder,{"segmentation":{"enabled":True,"python":sys.executable},"modelsDir":folder},{"kind":"subject"},lambda e:None)
            spawn.assert_not_called()


if __name__=="__main__":unittest.main()
