"""Conditional ML branch package: candidate training, local explainers, shadow runner & Rules-First serving router."""

from src.models.serving_router import (
    DEFAULT_SERVING_MODE,
    FALLBACK_SERVING_MODE,
    PREDICTIVE_SERVING_MODE,
    ServingRouteDecision,
    get_active_scoring_mode,
    resolve_serving_route,
    route_queue_scoring,
)
from src.models.shadow_runner import (
    SHADOW_SCORING_MODE,
    persist_shadow_scores,
    run_shadow_batch,
    score_shadow_batch,
)

__all__ = [
    "DEFAULT_SERVING_MODE",
    "FALLBACK_SERVING_MODE",
    "PREDICTIVE_SERVING_MODE",
    "SHADOW_SCORING_MODE",
    "ServingRouteDecision",
    "get_active_scoring_mode",
    "persist_shadow_scores",
    "resolve_serving_route",
    "route_queue_scoring",
    "run_shadow_batch",
    "score_shadow_batch",
]


