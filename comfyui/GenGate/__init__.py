"""GenGate's widget-free ComfyUI bridge. SPDX-License-Identifier: GPL-3.0-or-later."""
from .nodes import GenGateIN, GenGateOUT, GenGateApplyLoRAs

NODE_CLASS_MAPPINGS = {"GenGateIN2": GenGateIN, "GenGateOUT": GenGateOUT, "GenGateApplyLoRAs": GenGateApplyLoRAs}
NODE_DISPLAY_NAME_MAPPINGS = {"GenGateIN2": "GenGate IN", "GenGateOUT": "GenGate OUT", "GenGateApplyLoRAs": "GenGate Apply LoRAs"}
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
