"""Exercise quantized Embedding transfers using tiny CPU tensors, never weights."""
import argparse
import json
import os
from pathlib import Path
import sys
import weakref


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--core', type=Path)
    args = parser.parse_args()
    runtime = args.runtime.resolve()
    core = (args.core or runtime / '.runtime/fibo-core').resolve()
    os.environ['CUDA_VISIBLE_DEVICES'] = ''
    sys.path[:0] = [str(core / 'deps'), str(core), str(runtime)]
    import torch
    assert not torch.cuda.is_initialized()

    def forbidden(*a, **kw):
        raise AssertionError('CUDA initialization is forbidden in this CPU check')
    torch.cuda._lazy_init = forbidden
    from comfy.cli_args import args as options
    options.cpu = True
    options.disable_dynamic_vram = True
    options.disable_async_offload = True
    options.disable_pinned_memory = True
    import comfy.ops as ops
    from genstudio.model_compat import quantized_embeddings

    def tiny(factory):
        embedding = factory.Embedding(4, 4, device='cpu', dtype=torch.float32)
        marker = {'format': 'int8_tensorwise', 'convrot': True, 'convrot_groupsize': 4}
        embedding.load_state_dict({
            'weight': torch.arange(-8, 8, dtype=torch.int8).reshape(4, 4),
            'weight_scale': torch.ones(4, 1),
            'comfy_quant': torch.tensor(list(json.dumps(marker).encode()), dtype=torch.uint8)})
        return embedding

    baseline = tiny(ops.mixed_precision_ops(compute_dtype=torch.float32))
    reference = weakref.ref(baseline.weight)
    try:
        baseline.to(dtype=torch.bfloat16)
    except RuntimeError as error:
        assert "Couldn't swap Embedding.weight" in str(error)
        baseline_error = str(error)
        baseline_cause = str(error.__cause__)
    else:
        raise AssertionError('The unpatched pinned core must reproduce the reported swap failure')
    del baseline, reference

    assert quantized_embeddings(ops)
    assert not quantized_embeddings(ops)
    factory = ops.mixed_precision_ops(compute_dtype=torch.float32)
    scenarios = []
    for inference in (False, True):
        with torch.inference_mode(inference):
            embedding = tiny(factory)
        packed = embedding.weight._qdata.clone()
        scale = embedding.weight._params.scale.clone()
        embedding.register_buffer('probe', torch.ones(1))
        for dtype in (torch.bfloat16, torch.float32):
            reference = weakref.ref(embedding.weight)
            with torch.inference_mode(inference):
                embedding.to(dtype=dtype)
            assert embedding.weight.dtype == dtype and embedding.probe.dtype == dtype
            assert embedding.weight.device.type == 'cpu'
            assert embedding.weight._qdata.dtype == torch.int8
            assert torch.equal(embedding.weight._qdata, packed)
            assert torch.equal(embedding.weight._params.scale, scale)
            assert tuple(embedding.weight._params.orig_shape) == (4, 4)
            assert embedding.weight._params.convrot and embedding.weight._params.convrot_groupsize == 4
        # Offloading happens after the inference context exits too.
        reference = weakref.ref(embedding.weight)
        embedding.cpu()
        assert not embedding.weight.is_inference()
        assert torch.equal(embedding.weight._qdata, packed)
        scenarios.append({'inference_mode': inference, 'passed': True})
    assert not torch.cuda.is_initialized()
    report = {'passed': True, 'real_inference': False, 'weights_loaded': False,
              'cuda_initialized': False, 'torch': torch.__version__, 'runtime': str(runtime),
              'baseline_error': baseline_error, 'baseline_cause': baseline_cause, 'scenarios': scenarios}
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
