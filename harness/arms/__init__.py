"""Arm registry. `build_arm(arm_id, **kw)` constructs any arm; Arm 4 is an
M3 stub whose methods raise NotImplementedError."""
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
    if arm_id == "arm5":
        from harness import config as C
        if C.PRISM_PRODUCTION_ROUTING or C.PRISM_T5_RULE_SELECTOR:
            # Experiment configs (harness/experiments/production_routing):
            # same arm id, same scoring; imported only when a flag is on.
            from harness.experiments.production_routing.arms import ProductionRoutingArm5, RuleSelectorArm5
            return (ProductionRoutingArm5 if C.PRISM_PRODUCTION_ROUTING else RuleSelectorArm5)(**kwargs)
    return ARM_CLASSES[arm_id](**kwargs)
