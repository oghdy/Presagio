"""Quality report output: a plain text table (standard library only) or JSON, and exit codes."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from presagio.quality.checks import STATUSES, QualityReport

EXIT_OK = 0
EXIT_FAILED = 1  # a check failed or could not run (or the database was unreachable)
EXIT_USAGE = 64  # bad arguments, as in the backfill command (D-037)

HEADERS = ("#", "check", "target", "status", "passed", "failed", "examples", "description")
NUMERIC = frozenset({"#", "passed", "failed"})


def format_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    """Plain text table with a dashed header rule; numeric columns right-aligned."""
    cells = [[str(h) for h in headers]] + [["" if c is None else str(c) for c in r] for r in rows]
    widths = [max(len(row[i]) for row in cells) for i in range(len(headers))]
    lines = []
    for row in cells:
        parts = [
            cell.rjust(width) if header in NUMERIC else cell.ljust(width)
            for cell, width, header in zip(row, widths, headers, strict=True)
        ]
        lines.append("  ".join(parts).rstrip())
    lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(lines)


def render_text(report: QualityReport) -> str:
    """The table, notes under it, and a one-line summary with the exit code."""
    rows = []
    for n, r in enumerate(report.results, 1):
        examples = ", ".join(r.examples)
        if r.failed > len(r.examples):
            examples += f" (+{r.failed - len(r.examples)} more)"
        rows.append(
            (n, r.name, r.target, r.status.upper(), r.passed, r.failed, examples, r.description)
        )
    lines = [
        f"Quality check: {report.scope.describe()} ({len(report.results)} checks, read only)",
        format_table(HEADERS, rows),
    ]
    notes = [(n, r) for n, r in enumerate(report.results, 1) if r.note]
    if notes:
        lines.append("")
        lines.append("Notes:")
        lines.extend(f"  {n}. {r.name}: {r.note}" for n, r in notes)
    summary = ", ".join(f"{report.count(s)} {s}" for s in STATUSES)
    lines.append("")
    lines.append(f"Summary: {summary} -> exit code {report.exit_code}")
    return "\n".join(lines)


def render_json(report: QualityReport) -> str:
    """Machine-readable report (for the alerts of stage 6)."""
    return json.dumps(report.as_dict(), ensure_ascii=False, indent=2)
