import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from genstudio.comfy_workflow import parse_workflow
from genstudio.settings import defaults
from genstudio.workflow_components import apply


class WorkflowComponentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.settings={**defaults(),'engine':'comfy','modelsDir':str(self.root),'presets':{}}
        self.graph={'in':{'class_type':'GenGateIN2','inputs':{}},'out':{'class_type':'GenGateOUT2','inputs':{'images':['in',0]}},
            'model':{'class_type':'UNETLoader','_meta':{'title':'GenGate: Model'},'inputs':{'unet_name':'old.safetensors','weight_dtype':'default'}},
            'other':{'class_type':'UNETLoader','inputs':{'unet_name':'refiner.safetensors','weight_dtype':'fp8_e4m3fn'}}}
        self.info={'UNETLoader':{'input':{'required':{'unet_name':[['nested/new.safetensors','other/new.safetensors']], 'weight_dtype':[['default','fp8_e4m3fn']]}}}}
        self.model=self.root/'diffusion_models/nested/new.safetensors';self.model.parent.mkdir(parents=True);self.model.write_bytes(b'synthetic sentinel, never read as weights')

    def override(self,**values):
        self.settings['presets']={self.settings['model']:values}

    def test_automatic_is_identity_even_without_markers_or_local_models(self):
        graph=parse_workflow(self.graph)
        self.assertIs(apply(graph,self.settings,{}),graph)

    def test_only_marked_loader_changes_on_a_copy_and_the_effective_hash_changes(self):
        original=parse_workflow(self.graph);before=copy.deepcopy(original)
        self.override(modelFile='diffusion_models/nested/new.safetensors')
        with patch('pathlib.Path.open',side_effect=AssertionError('No checkpoint contents may be read')):
            result=apply(original,self.settings,self.info)
        self.assertEqual(original,before)
        self.assertEqual(result['graph']['model']['inputs'],{'unet_name':'nested/new.safetensors','weight_dtype':'default'})
        self.assertEqual(result['graph']['other'],original['graph']['other'])
        self.assertNotEqual(result['sha256'],original['sha256'])

    def test_missing_duplicate_or_unsupported_bindings_never_guess(self):
        self.override(modelFile=str(self.model))
        for change,pattern in ((lambda g:g['model'].pop('_meta'),'exactly one'),
            (lambda g:g['other'].update(_meta={'title':'GenGate: Model'}),'exactly one'),
            (lambda g:g['model'].update(class_type='CustomMultiModelLoader'),'not a supported'),
            (lambda g:g['model']['inputs'].update(unet_name=['other',0]),'linked input')):
            graph=copy.deepcopy(self.graph);change(graph);original=parse_workflow(graph);before=copy.deepcopy(original)
            with self.assertRaisesRegex(ValueError,pattern):apply(original,self.settings,self.info)
            self.assertEqual(original,before)

    def test_exact_registered_relative_filename_is_required(self):
        self.override(modelFile=str(self.model))
        info=copy.deepcopy(self.info);info['UNETLoader']['input']['required']['unet_name']=[['new.safetensors']]
        with self.assertRaisesRegex(ValueError,'does not list'):apply(parse_workflow(self.graph),self.settings,info)
        info['UNETLoader']['input']['required']['unet_name']=[['nested/new.safetensors','nested\\new.safetensors']]
        with self.assertRaisesRegex(ValueError,'unambiguously'):apply(parse_workflow(self.graph),self.settings,info)
        self.override(modelFile=str(self.root.parent/'outside.safetensors'))
        with self.assertRaisesRegex(ValueError,'shared Models'):apply(parse_workflow(self.graph),self.settings,self.info)

    def test_encoder_and_vae_have_independent_bindings(self):
        graph=copy.deepcopy(self.graph);info=copy.deepcopy(self.info)
        for key,label,kind,field,folder in [('textEncoder','Text encoder','CLIPLoader','clip_name','text_encoders'),('vae','VAE','VAELoader','vae_name','vae')]:
            path=self.root/folder/'new.safetensors';path.parent.mkdir();path.write_bytes(b'never load')
            graph[key]={'class_type':kind,'_meta':{'title':'GenGate: '+label},'inputs':{field:'old.safetensors'}}
            info[kind]={'input':{'required':{field:[['new.safetensors']]}}}
        self.override(textEncoder='text_encoders/new.safetensors',vae='vae/new.safetensors')
        result=apply(parse_workflow(graph),self.settings,info)
        self.assertEqual(result['graph']['textEncoder']['inputs']['clip_name'],'new.safetensors')
        self.assertEqual(result['graph']['vae']['inputs']['vae_name'],'new.safetensors')
        self.assertEqual(result['graph']['model'],graph['model'])

    def test_host_passes_current_settings_to_studio_and_tool_graph_transport(self):
        from genstudio.lifecycle import EngineHost
        import threading
        host=EngineHost(self.settings);definition=parse_workflow(self.graph)
        with patch.object(host,'ensure'),patch('genstudio.comfy_workflow.generate',return_value={}) as generate:
            host.generate_workflow(definition,{},threading.Event(),lambda _:None)
        self.assertEqual(generate.call_args.kwargs['settings'],host.settings)

    def test_browse_path_through_a_shared_models_directory_link_keeps_its_relative_name(self):
        alias=self.root/'models-alias'
        try:alias.symlink_to(self.root,target_is_directory=True)
        except OSError as error:self.skipTest('Directory links unavailable: '+str(error))
        self.settings['modelsDir']=str(alias)
        self.override(modelFile=str(alias/'diffusion_models/nested/new.safetensors'))
        result=apply(parse_workflow(self.graph),self.settings,self.info)
        self.assertEqual(result['graph']['model']['inputs']['unet_name'],'nested/new.safetensors')
