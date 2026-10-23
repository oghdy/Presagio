"""Read-only data quality checks (P-020): run_checks() returns a QualityReport."""

from presagio.quality.checks import (
    CHECK_NAMES,
    CheckResult,
    CheckSettings,
    FeatureRecompute,
    QualityReport,
    Scope,
    StoredFeature,
    run_checks,
)

__all__ = [
    "CHECK_NAMES",
    "CheckResult",
    "CheckSettings",
    "FeatureRecompute",
    "QualityReport",
    "Scope",
    "StoredFeature",
    "run_checks",
]
