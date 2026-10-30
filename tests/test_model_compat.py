"""Compatibility glue tests use fake modules; no tensor libraries or checkpoints."""
from pathlib import Path
from types import ModuleType, SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from genstudio.model_compat import quantized_embeddings
from genstudio.core_pipeline import import_core


class ModelCompatibilityTests(unittest.TestCase):
    def test_factory_preserves_arguments_and_uses_the_cores_quantized_transfer(self):
        events = []
        class Embedding:
            pass
        class Operations:
            pass
        Operations.Embedding = Embedding
        def original(*args, **kwargs):
            events.append((args, kwargs))
            return Operations
        def transfer(module, fn, recurse=True):
            events.append((module, fn, recurse))
            return module
        ops = SimpleNamespace(mixed_precision_ops=original, _quantized_apply=transfer)
        self.assertTrue(quantized_embeddings(ops))
        wrapped = ops.mixed_precision_ops
        self.assertFalse(quantized_embeddings(ops))
        self.assertIs(ops.mixed_precision_ops, wrapped)
        generated = wrapped({'layers': 'fixture'}, compute_dtype='fixture-dtype')
        self.assertEqual(events[0], (({'layers': 'fixture'},), {'compute_dtype': 'fixture-dtype'}))
        embedding = generated.Embedding()
        conversion = object()
        self.assertIs(embedding._apply(conversion, recurse=False), embedding)
        self.assertEqual(events[1], (embedding, conversion, False))

    def test_existing_embedding_transfer_is_retained(self):
        class Embedding:
            def _apply(self, fn, recurse=True):
                return 'upstream-specific transfer'
        class Operations:
            pass
        Operations.Embedding = Embedding
        def original():
            return Operations
        ops = SimpleNamespace(mixed_precision_ops=original, _quantized_apply=lambda *a: None)
        original_transfer = Embedding._apply
        quantized_embeddings(ops)
        self.assertIs(ops.mixed_precision_ops().Embedding._apply, original_transfer)

    def test_older_cores_and_inherited_plain_embeddings_are_retained(self):
        self.assertFalse(quantized_embeddings(SimpleNamespace()))
        def original():
            return None
        ops = SimpleNamespace(mixed_precision_ops=original)
        self.assertFalse(quantized_embeddings(ops))
        self.assertIs(ops.mixed_precision_ops, original)
        class PlainEmbedding:
            pass
        class Base:
            Embedding = PlainEmbedding
        class Operations(Base):
            pass
        ops = SimpleNamespace(mixed_precision_ops=lambda: Operations, _quantized_apply=lambda *a: None)
        quantized_embeddings(ops)
        self.assertIs(ops.mixed_precision_ops().Embedding, PlainEmbedding)
        self.assertNotIn('_apply', vars(PlainEmbedding))

    def test_direct_import_installs_the_adapter_before_any_checkpoint_loader(self):
        class Embedding:
            pass
        class Operations:
            pass
        Operations.Embedding = Embedding
        comfy = ModuleType('comfy')
        comfy.__path__ = []
        modules = {'comfy': comfy}
        for name in ('cli_args', 'ops', 'sd', 'sample'):
            module = ModuleType('comfy.' + name)
            setattr(comfy, name, module)
            modules[module.__name__] = module
        comfy.cli_args.args = SimpleNamespace()
        comfy.ops.mixed_precision_ops = lambda: Operations
        comfy.ops._quantized_apply = lambda *a, **kw: None
        with tempfile.TemporaryDirectory() as folder, patch.dict('sys.modules', modules):
            root = Path(folder)
            (root/'comfy').mkdir()
            (root/'comfy/sd.py').write_text('# synthetic module, no weights')
            import sys
            with patch.object(sys, 'path', list(sys.path)):
                sd, sample = import_core(cpu=True, core=root)
            self.assertIs(sd, comfy.sd)
            self.assertIs(sample, comfy.sample)
            self.assertTrue(comfy.cli_args.args.cpu)
            self.assertIs(comfy.ops.mixed_precision_ops().Embedding._apply, comfy.ops._quantized_apply)


if __name__ == '__main__':
    unittest.main()
