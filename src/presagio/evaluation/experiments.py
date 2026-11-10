"""Registry of what `presagio backtest run --experiment <id>` can run (D-043).

An experiment id names a predictor factory. Win baselines (design 5-1) are registered under
their baseline ids (B0..B3), total baselines (design 5-3) under T-Bn (T-B0 here; T-B1 needs
the team-stat features, after P-035), hypotheses under H-NN. H-00 is the framework dummy of
P-032: it has no ModelSpec, so it cannot be stored.

Parameterised ids (P-037 A2, H-04): the Elo baseline also runs with other constants,
`B2[k=25,hca=75,carry=0.9]`. Only B2 takes parameters and only the keys k, hca and carry
(the margin-of-victory multiplier `mov` is H-04(b) and stays refused); every key is optional
and a missing one keeps its D-049 M-7 starting value. EloBaseline validates the values and
names the version (D-063 item 3: "1" with the starting values, else 1-k{k}-hca{hca}-c{carry}).
The canonical form lists the given keys in the order k, hca, carry with values printed by
`{:g}`, the same formatting as the version suffix.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from presagio.evaluation.dummy import ConstantPredictor
from presagio.evaluation.walkforward import Predictor
from presagio.models.baselines import AlwaysHomeBaseline, MarketBaseline, SeasonWinRateBaseline
from presagio.models.elo import DEFAULT_CARRY, DEFAULT_HCA, DEFAULT_K, EloBaseline
from presagio.models.spec import ModelSpec
from presagio.models.totals import LeagueTotalBaseline

# Experiments that accept `[key=value,...]` and the keys they accept, in canonical order.
ELO_EXPERIMENT = "B2"
ELO_PARAMS = ("k", "hca", "carry")
# D-049 M-7 starting values of the searchable Elo constants (design 5-2).
ELO_START: Mapping[str, float] = MappingProxyType(
    {"k": DEFAULT_K, "hca": DEFAULT_HCA, "carry": DEFAULT_CARRY}
)
_ID_RE = re.compile(r"^(?P<base>[A-Za-z0-9-]+)(?:\[(?P<params>[^\[\]]*)\])?$")
_KEY_RE = re.compile(r"^[a-z_]+$")


class ExperimentIdError(ValueError):
    """An experiment id that is unknown or malformed (the CLI exits 64)."""


@dataclass(frozen=True)
class Experiment:
    """One registered experiment: its id, what it runs and a one-line description.

    `uses_market` marks a market baseline (B3): comparisons that include it are made on the
    games with a prediction-time market only (design 4-6 set (b)).
    """

    id: str
    description: str
    factory: Callable[..., Predictor]
    uses_market: bool = False
    params: Mapping[str, float] = field(default_factory=dict)

    def make(self) -> Predictor:
        """A fresh predictor instance (with this experiment's parameters, if any)."""
        if not self.params:
            return self.factory()
        predictor = self.factory(**self.params)
        # The class name describes the starting constants; say which ones this run uses.
        k = self.params.get("k", ELO_START["k"])
        hca = self.params.get("hca", ELO_START["hca"])
        carry = self.params.get("carry", ELO_START["carry"])
        predictor.name = f"B2 Elo rating (K={k:g}, HCA={hca:g}, carry {carry:g}; design 5-2)"
        return predictor


def _num(value: float) -> str:
    """A parameter value as written in canonical ids: 25 -> '25', 0.9 -> '0.9'."""
    return f"{value:g}"


def format_params(params: Mapping[str, float]) -> str:
    """'k=25,hca=75,carry=0.9' in canonical key order (only the keys given)."""
    return ",".join(f"{key}={_num(params[key])}" for key in ELO_PARAMS if key in params)


def parse_param_value(key: str, raw: str) -> float:
    """A finite number for parameter `key`; ExperimentIdError otherwise."""
    try:
        value = float(raw)
    except ValueError:
        raise ExperimentIdError(f"{key}: not a number: {raw!r}") from None
    if not math.isfinite(value):
        raise ExperimentIdError(f"{key}: must be a finite number, got {raw!r}")
    return value


def check_param_key(experiment: str, key: str) -> None:
    """ExperimentIdError unless `key` is a searchable constant of `experiment`."""
    if experiment != ELO_EXPERIMENT:
        raise ExperimentIdError(f"experiment {experiment} takes no parameters (only B2 does)")
    if key == "mov":
        raise ExperimentIdError(
            "mov (margin-of-victory multiplier) is not part of v1: H-04(b) is on hold until "
            "the formula's primary source is confirmed (design 5-2)"
        )
    if key not in ELO_PARAMS:
        raise ExperimentIdError(f"unknown B2 parameter {key!r} (allowed: {', '.join(ELO_PARAMS)})")


def resolve_experiment(experiment_id: str) -> Experiment:
    """The registered experiment or a parameterised B2; ExperimentIdError when the id is
    unknown, malformed, names a key other than k / hca / carry, repeats a key, or carries
    a value EloBaseline refuses. The returned Experiment's id is the canonical form."""
    text = experiment_id.strip()
    match = _ID_RE.match(text)
    if not match:
        raise ExperimentIdError(
            f"not an experiment id like B2 or B2[k=25,hca=75,carry=0.9]: {experiment_id!r}"
        )
    base = EXPERIMENTS.get(match.group("base"))
    if base is None:
        raise ExperimentIdError(
            f"unknown experiment {match.group('base')!r} (known: {', '.join(EXPERIMENTS)})"
        )
    raw = match.group("params")
    if raw is None:
        return base
    params: dict[str, float] = {}
    items = raw.split(",")
    if not raw.strip():
        raise ExperimentIdError(f"empty parameter list in {experiment_id!r}")
    for item in items:
        key, sep, value = (part.strip() for part in item.partition("="))
        if not sep or not _KEY_RE.match(key) or not value:
            raise ExperimentIdError(f"not key=value: {item.strip()!r} in {experiment_id!r}")
        check_param_key(base.id, key)
        if key in params:
            raise ExperimentIdError(f"parameter {key} given twice in {experiment_id!r}")
        params[key] = parse_param_value(key, value)
    try:
        base.factory(**params)  # EloBaseline validates the values
    except ValueError as exc:
        raise ExperimentIdError(f"{experiment_id!r}: {exc}") from None
    ordered = {key: params[key] for key in ELO_PARAMS if key in params}
    return Experiment(
        id=f"{base.id}[{format_params(ordered)}]",
        description=f"{base.description} with {format_params(ordered)}",
        factory=base.factory,
        uses_market=base.uses_market,
        params=MappingProxyType(ordered),
    )


def split_experiment_list(value: str) -> list[str]:
    """'B0,B2[k=25,hca=75]' -> ['B0', 'B2[k=25,hca=75]'] (commas inside brackets stay)."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in value:
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth < 0:
                raise ExperimentIdError(f"unbalanced ']' in {value!r}")
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if depth != 0:
        raise ExperimentIdError(f"unbalanced '[' in {value!r}")
    parts.append("".join(current).strip())
    if any(not p for p in parts):
        raise ExperimentIdError(f"empty experiment id in {value!r}")
    return parts


def model_spec(predictor: object) -> ModelSpec | None:
    """The predictor's ModelSpec, or None when it is not a storable model (the dummy)."""
    spec = getattr(predictor, "spec", None)
    return spec if isinstance(spec, ModelSpec) else None


EXPERIMENTS: dict[str, Experiment] = {
    e.id: e
    for e in (
        Experiment(
            "H-00",
            "framework dummy (p_home 0.5, total = league mean); not storable",
            ConstantPredictor,
        ),
        Experiment("B0", "always home: season cumulative home win rate", AlwaysHomeBaseline),
        Experiment("B1", "season win rate difference, clipped", SeasonWinRateBaseline),
        Experiment(
            "B2",
            "Elo rating (design 5-2): K=20, HCA=100, season carry 0.75, warmed up on earlier "
            "seasons",
            EloBaseline,
        ),
        Experiment(
            "B3", "prediction-time market probability (R3)", MarketBaseline, uses_market=True
        ),
        Experiment(
            "T-B0",
            "league mean total of the last 100 games (design 5-3 T-B0); totals only",
            LeagueTotalBaseline,
        ),
    )
}
