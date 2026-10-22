"""An OddsSource over prepared batches: no HTTP, for tests and dry runs of the loader.

It shows the loader depends only on the OddsSource interface: any provider (or a test)
that can produce OddsBatch records can be loaded the same way.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from presagio.ingestion.client import RunCounter
from presagio.ingestion.sources.odds.base import OddsBatch


class FixtureOddsSource:
    """Returns the given batches one per call, in order; raises LookupError when used up."""

    def __init__(self, batches: Iterable[OddsBatch], name: str = "fixture") -> None:
        """Keep the batches; name is what ingestion_runs.scope_key will show."""
        self.name = name
        self._batches = list(batches)
        self.calls = 0

    def _next(self, run: RunCounter | None) -> OddsBatch:
        """Pop the next batch, counted as one call like a real source would."""
        if not self._batches:
            raise LookupError("FixtureOddsSource has no batches left")
        self.calls += 1
        if run is not None:
            run.add_api_calls(1, 0)
        return self._batches.pop(0)

    def fetch_odds(
        self, run: RunCounter | None = None, *, capture_label: str | None = None
    ) -> OddsBatch:
        """The next batch, labelled like a live capture would be."""
        batch = self._next(run)
        return batch if capture_label is None else replace(batch, capture_label=capture_label)

    def fetch_events(self, run: RunCounter | None = None) -> OddsBatch:
        """The next batch with its prices removed."""
        batch = self._next(run)
        return replace(batch, events=tuple(replace(e, outcomes=()) for e in batch.events))
