"""Temporal benchmark, paired cluster bootstrap, and Gate G1 decision package."""

from .gate_g1_decider import decide_gate_g1
from .paired_bootstrap import (
    check_mature_label_coverage,
    estimate_paired_bootstrap_ci,
    paired_bootstrap_ci,
    run_paired_cluster_bootstrap,
)
from .temporal_benchmark import (
    SealedCohortAlreadyUsedError,
    estimate_temporal_benchmark,
    evaluate_temporal_cohort,
    replay_temporal_benchmark,
)

__all__ = [
    "SealedCohortAlreadyUsedError",
    "check_mature_label_coverage",
    "decide_gate_g1",
    "estimate_paired_bootstrap_ci",
    "estimate_temporal_benchmark",
    "evaluate_temporal_cohort",
    "paired_bootstrap_ci",
    "replay_temporal_benchmark",
    "run_paired_cluster_bootstrap",
]
