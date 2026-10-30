"""GenGate's widget-free ComfyUI bridge. SPDX-License-Identifier: GPL-3.0-or-later."""
from .nodes import GenGateIN, GenGateOUT, GenGateApplyLoRAs, GenGateEditSteps
import sys

WEB_DIRECTORY = './web'
if 'server' in sys.modules:
    from .bridge import register
    register()

NODE_CLASS_MAPPINGS = {"GenGateIN2": GenGateIN, "GenGateOUT2": GenGateOUT, "GenGateApplyLoRAs": GenGateApplyLoRAs, "GenGateEditSteps": GenGateEditSteps}
NODE_DISPLAY_NAME_MAPPINGS = {"GenGateIN2": "GenGate IN", "GenGateOUT2": "GenGate OUT", "GenGateApplyLoRAs": "GenGate Apply LoRAs", "GenGateEditSteps": "GenGate Edit Steps"}
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
