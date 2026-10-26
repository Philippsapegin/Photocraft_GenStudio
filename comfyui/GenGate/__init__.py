"""GenGate's widget-free ComfyUI bridge. SPDX-License-Identifier: GPL-3.0-or-later."""
from .nodes import GenGateIN, GenGateOUT

NODE_CLASS_MAPPINGS = {"GenGateIN": GenGateIN, "GenGateOUT": GenGateOUT}
NODE_DISPLAY_NAME_MAPPINGS = {"GenGateIN": "GenGate IN", "GenGateOUT": "GenGate OUT"}
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
