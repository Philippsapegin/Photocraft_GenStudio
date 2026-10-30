import base64
import json
from pathlib import Path
import tempfile
import threading
import unittest

from PIL import Image
from genfill.image_ops import png_bytes
from genfill.workflow import TURBO_SIGMAS, turbo_sigmas
from genstudio.conditioning import negative
from genstudio.lifecycle import EngineHost
from genstudio.settings import defaults, validate_components
from genstudio.studio import request


class GenerationControlTests(unittest.TestCase):
    def test_blank_steps_preserve_default_and_overrides_are_bounded(self):
        self.assertIsNone(validate_components({})['constantLoraSteps'])
        self.assertEqual(validate_components({'constantLoraSteps':4})['constantLoraSteps'],4)
        for value in (False,0,-1,201,4.5,'4'):
            with self.assertRaises(ValueError):validate_components({'constantLoraSteps':value})
        self.assertEqual(turbo_sigmas(),TURBO_SIGMAS)
        for count in (1,4,8,16,200):
            sigmas=turbo_sigmas(count)
            self.assertEqual(len(sigmas),count+1)
            self.assertEqual((sigmas[0],sigmas[-1]),(1,0))
            self.assertTrue(all(a>b for a,b in zip(sigmas,sigmas[1:])))

    def test_negative_text_is_validated_and_encoded_only_when_present(self):
        positive=[['positive',{'reference_latents':['reference']}]]
        class Clip:
            def tokenize(self,text,**kwargs):return text
            def encode_from_tokens_scheduled(self,tokens):return [[tokens,{'pooled_output':'negative pool'}]]
        self.assertIs(negative(None,'',positive),positive)
        result=negative(Clip(),'text, watermark',positive)
        self.assertEqual(result[0][0],'text, watermark')
        self.assertEqual(result[0][1]['reference_latents'],['reference'])
        self.assertEqual(result[0][1]['pooled_output'],'negative pool')
        value=request({'prompt':'A scene','negativePrompt':'text, watermark'})
        self.assertEqual(value['negativePrompt'],'text, watermark')
        for value in (False,'\0','a'*12001):
            with self.assertRaises(ValueError):request({'prompt':'A scene','negativePrompt':value})

    def test_native_tools_choose_separate_graphs_and_preserve_mask_coordinates(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            graph={'in':{'class_type':'GenGateIN2','inputs':{}},'out':{'class_type':'GenGateOUT2','inputs':{'images':['in',0]}}}
            fill=root/'fill.json';fill.write_text(json.dumps(graph))
            graph['invert']={'class_type':'ImageInvert','inputs':{'image':['in',0]}}
            graph['out']['inputs']['images']=['invert',0]
            remove=root/'remove.json';remove.write_text(json.dumps(graph))
            host=EngineHost({**defaults(),'engine':'comfy','modelsDir':str(root),'fillWorkflow':str(fill),'removeWorkflow':str(remove),
                             'presets':{'qwen-image-2.1-turbo':{'constantLoraSteps':4}}})
            calls=[]
            image=Image.new('RGBA',(12,8),'red');mask=Image.new('L',image.size,0);mask.putpixel((3,4),255)
            def run(definition,data,*args):
                calls.append((definition,data))
                return {'images':[{'image':base64.b64encode(png_bytes(image)).decode()}]}
            host.generate_workflow=run
            for mode in ('fill','remove'):
                output=host.generate_edit(image,mask,7,'Remove the object',threading.Event(),lambda _:None,mode=mode)
                self.assertEqual(output.size,image.size)
            self.assertNotIn('invert',calls[0][0]['graph']);self.assertIn('invert',calls[1][0]['graph'])
            source=calls[0][1]['references'][0]
            self.assertEqual(source.getpixel((3,4))[3],0)
            self.assertEqual(source.getpixel((2,4))[3],255)
            self.assertEqual(image.getpixel((3,4))[3],255)
            self.assertEqual(calls[0][1]['steps'],4)
            host.settings['fillWorkflow']=''
            with self.assertRaisesRegex(ValueError,'Ai Fill'):
                host.generate_edit(image,mask,7,'prompt',threading.Event(),lambda _:None)
