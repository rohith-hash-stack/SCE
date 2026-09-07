"""Layer 5 of the Semantic Knowledge Engine: the single, repository-wide
Global Repository Archetype - a federation of the Layer 3 (`subsystems.py`)
and Layer 4 (`flows.py`) results, not a flat metric computed independently
of them (mitigation strategy #3: "compute intent bottom-up ... aggregate
global archetype as federation of constituent modules").

Applies the spec's own decision matrix, in the priority order given (a
repository can satisfy more than one rule - e.g. a CLI tool that is also
algorithmically pure - so the first match wins, most-specific-intent
first):

  1. High fan divergence AND Phi_ASSERT > 0.5           -> E2E_VERIFICATION_HARNESS
  2. Low fan divergence AND Phi_NETWORK > 0.35 AND
     Phi_STORAGE > 0.25                                 -> REST_BACKEND_SERVICE
  3. Phi_PURE > 0.80                                     -> ALGORITHMIC_DATA_LIBRARY
  4. High Phi_PROC AND max critical-path depth < 3       -> CLI_AUTOMATION_TOOL

**One deliberate reinterpretation**: the spec's fourth rule reads
"delta_max < 3", but `delta_u` (`FlowEngineResult.relative_depth`) is
dimensionless by construction (Section 1.D: `delta_u in [0, 1]`) - a
literal `< 3` would always be true and the rule would degenerate to "high
Phi_PROC alone". Read instead as the max *integer* `critical_path_depth`
across `flows.py`'s per-entry-root pipelines (a CLI tool's command
handlers are characteristically shallow, few-hop dispatchers into the
real logic, which is exactly what an integer hop-count threshold like
"< 3" is a sensible bound for) - documented here rather than silently
reinterpreted.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from prism.analysis.flow_engine import FlowEngineResult
from prism.graph.flows import FlowContract
from prism.graph.subsystems import SubsystemProfile

E2E_VERIFICATION_HARNESS = "E2E_VERIFICATION_HARNESS"
REST_BACKEND_SERVICE = "REST_BACKEND_SERVICE"
ALGORITHMIC_DATA_LIBRARY = "ALGORITHMIC_DATA_LIBRARY"
CLI_AUTOMATION_TOOL = "CLI_AUTOMATION_TOOL"
GENERAL_PURPOSE_APPLICATION = "GENERAL_PURPOSE_APPLICATION"

#: `nabla_graph` (`FlowEngineResult.fan_divergence`) thresholds - "high"
#: means more entry roots than sinks (a test harness's characteristic
#: shape: many `test_*` entries converging on a small, shared set of
#: assertion sinks), "low" means the reverse (a service with a handful of
#: route handlers fanning out to many different I/O sinks).
HIGH_FAN_DIVERGENCE = 1.0
LOW_FAN_DIVERGENCE = 0.5

PHI_ASSERT_THRESHOLD = 0.5
PHI_NETWORK_THRESHOLD = 0.35
PHI_STORAGE_THRESHOLD = 0.25
PHI_PURE_THRESHOLD = 0.80
PHI_PROC_THRESHOLD = 0.40
CLI_MAX_CRITICAL_DEPTH = 3


@dataclass
class RepositoryProfile:
    archetype: str = GENERAL_PURPOSE_APPLICATION
    fan_divergence: float = 0.0
    global_sink_mass: dict = field(default_factory=dict)
    max_critical_path_depth: int = 0
    subsystem_count: int = 0

    def to_dict(self) -> dict:
        return {
            "archetype": self.archetype,
            "fan_divergence": self.fan_divergence,
            "global_sink_mass": self.global_sink_mass,
            "max_critical_path_depth": self.max_critical_path_depth,
            "subsystem_count": self.subsystem_count,
        }


def classify_repository(
    result: FlowEngineResult,
    subsystems: dict[str, SubsystemProfile],
    flows: dict[str, FlowContract],
) -> RepositoryProfile:
    phi = result.global_sink_mass
    nabla = result.fan_divergence
    max_depth = max((f.critical_path_depth for f in flows.values()), default=0)

    if nabla >= HIGH_FAN_DIVERGENCE and phi["ASSERT_SIGNAL"] > PHI_ASSERT_THRESHOLD:
        archetype = E2E_VERIFICATION_HARNESS
    elif nabla <= LOW_FAN_DIVERGENCE and phi["IO_NETWORK"] > PHI_NETWORK_THRESHOLD and phi["IO_STORAGE"] > PHI_STORAGE_THRESHOLD:
        archetype = REST_BACKEND_SERVICE
    elif phi["PURE_LEAF"] > PHI_PURE_THRESHOLD:
        archetype = ALGORITHMIC_DATA_LIBRARY
    elif phi["PROC_LIFECYCLE"] > PHI_PROC_THRESHOLD and max_depth < CLI_MAX_CRITICAL_DEPTH:
        archetype = CLI_AUTOMATION_TOOL
    else:
        archetype = GENERAL_PURPOSE_APPLICATION

    return RepositoryProfile(
        archetype=archetype,
        fan_divergence=round(nabla, 4),
        global_sink_mass=phi.as_dict(),
        max_critical_path_depth=max_depth,
        subsystem_count=len(subsystems),
    )
