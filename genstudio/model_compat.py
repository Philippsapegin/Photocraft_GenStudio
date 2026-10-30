"""Compatibility for the private Direct model library; no model/tensor imports."""
from functools import wraps


def quantized_embeddings(ops):
    """Use the core's quantized transfer path for its own Embedding class.

    PyTorch's default Module._apply swaps wrapper tensors in place, which fails
    when a quantized Parameter has weak references or other swap restrictions.
    The pinned core already rewraps such weights for its quantized Linear layers.
    Older factories and upstream Embedding-specific implementations are retained.
    This changes only the model-library module in this isolated worker process.
    """
    factory = getattr(ops, "mixed_precision_ops", None)
    transfer = getattr(ops, "_quantized_apply", None)
    if not callable(factory) or not callable(transfer) or getattr(factory, "_gengate_embeddings", False):
        return False

    @wraps(factory)
    def compatible(*args, **kwargs):
        operations = factory(*args, **kwargs)
        embedding = vars(operations).get("Embedding")
        if isinstance(embedding, type) and "_apply" not in vars(embedding):
            embedding._apply = transfer
        return operations

    compatible._gengate_embeddings = True
    ops.mixed_precision_ops = compatible
    return True
