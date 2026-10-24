"""What a storable model configuration says about itself (schema v1 9-3 `model_versions`).

A predictor that may be stored carries a `spec` (ModelSpec). The evaluation store writes one
`model_versions` row per (model_name, version) from it; the runner's own settings (training
window, availability lag, market rules) are not part of the spec, the store adds them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# modeling-v1 8-2 families (schema v1 9-1 item 4: the list lives in Python).
BASELINE_HOME = "baseline_home"
BASELINE_SEASON_WR = "baseline_season_wr"
MARKET = "market"


@dataclass(frozen=True)
class ModelSpec:
    """Identity and reproduction information of one model configuration."""

    model_name: str
    version: str
    model_family: str
    hyperparameters: Mapping[str, Any] = field(default_factory=dict)
    feature_version: str | None = None  # None: the model reads no features row
    tuning_data: str | None = None  # None: nothing was tuned (modeling-v1 6-4)
