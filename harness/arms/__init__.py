"""Arm registry. `build_arm(arm_id, **kw)` constructs any arm; Arms 2/3/4 are
M2/M3 stubs whose methods raise NotImplementedError."""
from __future__ import annotations

from harness.arms.arm0_parametric import Arm0Parametric
from harness.arms.arm1_rag import Arm1RAG
from harness.arms.arm2_priompt import Arm2Priompt
from harness.arms.arm3_lsp import Arm3PyrightLSP
from harness.arms.arm4_agent import Arm4AgentLoop
from harness.arms.arm5_prism import Arm5Prism
from harness.arms.oracle import Oracle

ARM_CLASSES = {
    "arm0": Arm0Parametric, "arm1": Arm1RAG, "arm2": Arm2Priompt, "arm3": Arm3PyrightLSP,
    "arm4": Arm4AgentLoop, "arm5": Arm5Prism, "oracle": Oracle,
}


def build_arm(arm_id: str, **kwargs):
    return ARM_CLASSES[arm_id](**kwargs)
