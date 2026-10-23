"""Walk-forward evaluation framework (modeling design v1.2 ch. 4 and 6, ticket P-032).

Modules: timing (R1/R2 time rules, backtest as_of), market (margin removal, consensus,
reference bookmaker, R3 prediction-time odds, market_status), clv (closing odds and CLV; the
only reader of the view odds_closing_lines), metrics (6-1 formulas and the bootstrap),
holdout (2025-26 guard), walkforward (the runner and the Predictor / TrainingMatrixSource
interfaces), scoring (per-prediction evaluation), report (markdown tables), dummy (H-00
predictors), data (read-only queries), cli (`presagio backtest run`).

presagio.features and presagio.models never import this package (design 9-2 Q4).
"""
