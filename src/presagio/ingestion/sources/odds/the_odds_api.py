"""The Odds API v4 adapter: /sports/basketball_nba/odds and /events -> OddsBatch (D-026).

Response shape (docs/research/odds_api.md ch. 4, v4 guide): a JSON list of events, each with
id, sport_key, commence_time, home_team, away_team and, for /odds, bookmakers[] -> key,
last_update, markets[] -> key, outcomes[] -> name, price, point. /events has no bookmakers.

The key travels only in ApiRequest.secret_params (D-031): it is sent, never stored, hashed
or logged. One /odds call costs markets x regions credits; /events is free.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import SecretStr

from presagio.config import (
    DEFAULT_ODDS_API_BASE_URL,
    DEFAULT_ODDS_API_MARKETS,
    DEFAULT_ODDS_API_REGIONS,
    Settings,
    get_settings,
)
from presagio.ingestion.client import ApiRequest, FetchResult, IngestionClient, RunCounter
from presagio.ingestion.odds_math import ODDS_FORMATS, to_decimal_price
from presagio.ingestion.sources.base import Provenance, QualityIssue, SourceFormatError, require
from presagio.ingestion.sources.odds.base import (
    MARKETS,
    SKIPPED_REJECTED_OUTCOMES,
    OddsBatch,
    OddsEventRecord,
    OddsOutcomeRecord,
    drop_started_prices,
    parse_utc,
)

SOURCE = "the_odds_api"
SPORT_KEY = "basketball_nba"
ODDS_ENDPOINT = "odds"
EVENTS_ENDPOINT = "events"
# team_aliases seed for source 'the_odds_api': tricode -> the name in home_team/away_team.
# NOT yet confirmed against a real NBA response (no public NBA sample, odds_api.md ch. 7):
# these are nba_api static full names. Unknown names never stop a load; the event stays
# unmatched with a quality issue. Confirm with one free /events call and fix here.
TEAM_NAME_SEED: Mapping[str, str] = {
    "ATL": "Atlanta Hawks",
    "BKN": "Brooklyn Nets",
    "BOS": "Boston Celtics",
    "CHA": "Charlotte Hornets",
    "CHI": "Chicago Bulls",
    "CLE": "Cleveland Cavaliers",
    "DAL": "Dallas Mavericks",
    "DEN": "Denver Nuggets",
    "DET": "Detroit Pistons",
    "GSW": "Golden State Warriors",
    "HOU": "Houston Rockets",
    "IND": "Indiana Pacers",
    "LAC": "Los Angeles Clippers",
    "LAL": "Los Angeles Lakers",
    "MEM": "Memphis Grizzlies",
    "MIA": "Miami Heat",
    "MIL": "Milwaukee Bucks",
    "MIN": "Minnesota Timberwolves",
    "NOP": "New Orleans Pelicans",
    "NYK": "New York Knicks",
    "OKC": "Oklahoma City Thunder",
    "ORL": "Orlando Magic",
    "PHI": "Philadelphia 76ers",
    "PHX": "Phoenix Suns",
    "POR": "Portland Trail Blazers",
    "SAC": "Sacramento Kings",
    "SAS": "San Antonio Spurs",
    "TOR": "Toronto Raptors",
    "UTA": "Utah Jazz",
    "WAS": "Washington Wizards",
}
TEAM_NAME_SEED_NOTE = "P-019 seed (nba_api full name), not yet confirmed by a /events response"
# Usage headers the API returns on every response (v4 guide); kept in the run record.
QUOTA_HEADERS = ("x-requests-remaining", "x-requests-used", "x-requests-last")


def quota_of(headers: Mapping[str, str]) -> dict[str, str]:
    """The provider's usage headers present in a response (header names are case-insensitive)."""
    lowered = {name.lower(): value for name, value in headers.items()}
    return {name: lowered[name] for name in QUOTA_HEADERS if name in lowered}


def provenance_of(result: FetchResult) -> Provenance:
    """Provenance of a fetched response: endpoint name, receive time, raw row id."""
    return Provenance(result.request.endpoint, result.fetched_at, result.stored.raw_response_id)


def _side_of(market: str, name: str, home: str, away: str) -> str | None:
    """Normalized side of an outcome name: home/away for h2h and spreads, over/under for totals."""
    if market == "totals":
        lowered = name.lower()
        return lowered if lowered in ("over", "under") else None
    if name == home:
        return "home"
    if name == away:
        return "away"
    return None


def parse_outcomes(
    event: Mapping[str, Any], home: str, away: str, odds_format: str, where: str
) -> tuple[list[OddsOutcomeRecord], list[QualityIssue]]:
    """Every usable price of one event, plus an issue for each price left out."""
    outcomes: list[OddsOutcomeRecord] = []
    issues: list[QualityIssue] = []
    seen: set[tuple[str, str, str]] = set()
    event_id = str(event.get("id"))
    for bookmaker in event.get("bookmakers") or []:
        book = require(bookmaker, "key", where)
        last_update = parse_utc(require(bookmaker, "last_update", f"{where}/{book}"), where)
        for market in require(bookmaker, "markets", f"{where}/{book}"):
            market_key = require(market, "key", f"{where}/{book}")
            if market_key not in MARKETS:
                issues.append(QualityIssue("unrequested_market", event_id, f"{book}: {market_key}"))
                continue
            for outcome in require(market, "outcomes", f"{where}/{book}/{market_key}"):
                name = require(outcome, "name", f"{where}/{book}/{market_key}")
                if not isinstance(name, str):
                    raise SourceFormatError(f"{where}/{book}/{market_key}: name {name!r}")
                label = f"{book}/{market_key}/{name}"
                side = _side_of(market_key, name, home, away)
                if side is None:
                    issues.append(QualityIssue("unknown_outcome_name", event_id, label))
                    continue
                if (book, market_key, side) in seen:
                    issues.append(QualityIssue("duplicate_outcome", event_id, label))
                    continue
                point = outcome.get("point")
                if (market_key == "h2h") != (point is None):
                    kind = "unexpected_point" if market_key == "h2h" else "missing_point"
                    issues.append(QualityIssue(kind, event_id, f"{label}: point={point!r}"))
                    continue
                try:
                    price = to_decimal_price(require(outcome, "price", where), odds_format)
                    record = OddsOutcomeRecord(
                        bookmaker_key=book,
                        bookmaker_last_update=last_update,
                        market_key=market_key,
                        outcome_name=name,
                        outcome_side=side,
                        price_decimal=price,
                        point=None if point is None else to_point(point),
                    )
                except ValueError as exc:
                    issues.append(QualityIssue("bad_price", event_id, f"{label}: {exc}"))
                    continue
                seen.add((book, market_key, side))
                outcomes.append(record)
    return outcomes, issues


def to_point(value: Any) -> Decimal:
    """A spread/total line as Decimal (numeric(6,2)); refuses non-numbers."""
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise ValueError(f"point must be a number, got {value!r}")
    try:
        point = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"point must be a number, got {value!r}") from None
    if not point.is_finite() or point != point.quantize(Decimal("0.01")) or abs(point) >= 10_000:
        raise ValueError(f"point {value!r} does not fit numeric(6,2)")
    return point


def parse_odds(
    data: Any,
    *,
    provenance: Provenance,
    odds_format: str = "decimal",
    capture_label: str | None = None,
    quota: Mapping[str, str] | None = None,
    sport_key: str = SPORT_KEY,
) -> OddsBatch:
    """A /odds or /events response -> OddsBatch captured at provenance.fetched_at.

    A malformed event is rejected alone (the rest are kept); a price that cannot be used is
    left out with a quality issue; prices of events that had already started are dropped.
    """
    if odds_format not in ODDS_FORMATS:
        raise ValueError(f"unknown odds format {odds_format!r}")
    if not isinstance(data, list):
        raise SourceFormatError(f"{SOURCE}: expected a JSON list of events, got {type(data)}")
    captured_at = provenance.fetched_at
    events: list[OddsEventRecord] = []
    rejected: dict[str, str] = {}
    issues: list[QualityIssue] = []
    rejected_outcomes = 0
    for index, raw_event in enumerate(data):
        key = str(raw_event.get("id")) if isinstance(raw_event, Mapping) else f"#{index}"
        where = f"{SOURCE}/{provenance.source}/{key}"
        try:
            event_id = require(raw_event, "id", where)
            if require(raw_event, "sport_key", where) != sport_key:
                issues.append(
                    QualityIssue("other_sport", event_id, f"sport_key={raw_event['sport_key']!r}")
                )
                continue
            home = require(raw_event, "home_team", where)
            away = require(raw_event, "away_team", where)
            if not isinstance(home, str) or not isinstance(away, str) or home == away:
                raise SourceFormatError(f"{where}: bad teams {home!r} / {away!r}")
            commence = parse_utc(require(raw_event, "commence_time", where), where)
            outcomes, outcome_issues = parse_outcomes(raw_event, home, away, odds_format, where)
        except SourceFormatError as exc:
            rejected[key] = str(exc)
            continue
        issues.extend(outcome_issues)
        rejected_outcomes += sum(1 for i in outcome_issues if i.kind != "unrequested_market")
        events.append(OddsEventRecord(event_id, home, away, commence, tuple(outcomes)))
    kept, skipped = drop_started_prices(tuple(events), captured_at)
    skipped[SKIPPED_REJECTED_OUTCOMES] = rejected_outcomes
    return OddsBatch(
        source=SOURCE,
        captured_at=captured_at,
        capture_kind="live",
        events=kept,
        provenance=provenance,
        capture_label=capture_label,
        rejected=rejected,
        skipped=skipped,
        issues=tuple(issues),
        quota=dict(quota or {}),
    )


class TheOddsApiSource:
    """OddsSource for NBA odds from The Odds API v4, sent through IngestionClient."""

    name = SOURCE

    def __init__(
        self,
        client: IngestionClient,
        api_key: str | SecretStr,
        *,
        base_url: str = DEFAULT_ODDS_API_BASE_URL,
        regions: str = DEFAULT_ODDS_API_REGIONS,
        markets: str = DEFAULT_ODDS_API_MARKETS,
        odds_format: str = "decimal",
        sport_key: str = SPORT_KEY,
    ) -> None:
        """Requires a non-empty key; use from_settings to get None when no key is configured."""
        secret = api_key if isinstance(api_key, SecretStr) else SecretStr(api_key)
        if not secret.get_secret_value().strip():
            raise ValueError("The Odds API key is empty; the odds adapter is disabled")
        if odds_format not in ODDS_FORMATS:
            raise ValueError(f"unknown odds format {odds_format!r}")
        self.client = client
        self._api_key = secret
        self.base_url = base_url.rstrip("/")
        self.regions = regions
        self.markets = markets
        self.odds_format = odds_format
        self.sport_key = sport_key

    @classmethod
    def from_settings(
        cls, client: IngestionClient, settings: Settings | None = None
    ) -> TheOddsApiSource | None:
        """The adapter configured from ODDS_API_* settings, or None when ODDS_API_KEY is empty."""
        s = settings if settings is not None else get_settings()
        if s.odds_api_key is None:
            return None
        return cls(
            client,
            s.odds_api_key,
            base_url=s.odds_api_base_url,
            regions=s.odds_api_regions,
            markets=s.odds_api_markets,
        )

    def __repr__(self) -> str:
        """Never show the key."""
        return f"TheOddsApiSource(base_url={self.base_url!r}, sport_key={self.sport_key!r})"

    def _request(self, endpoint: str, params: Mapping[str, str]) -> ApiRequest:
        """GET {base}/sports/{sport}/{endpoint} with the key as a secret parameter."""
        return ApiRequest(
            source=SOURCE,
            endpoint=endpoint,
            url=f"{self.base_url}/sports/{self.sport_key}/{endpoint}",
            params=params,
            secret_params={"apiKey": self._api_key.get_secret_value()},
        )

    def odds_request(self) -> ApiRequest:
        """The /odds request: costs (markets x regions) credits."""
        return self._request(
            ODDS_ENDPOINT,
            {"regions": self.regions, "markets": self.markets, "oddsFormat": self.odds_format},
        )

    def events_request(self) -> ApiRequest:
        """The /events request: free, lists upcoming events without prices."""
        return self._request(EVENTS_ENDPOINT, {})

    def fetch_odds(
        self, run: RunCounter | None = None, *, capture_label: str | None = None
    ) -> OddsBatch:
        """One /odds call, captured at the time we received it."""
        result = self.client.fetch(self.odds_request(), run)
        return parse_odds(
            result.data,
            provenance=provenance_of(result),
            odds_format=self.odds_format,
            capture_label=capture_label,
            quota=quota_of(result.headers),
            sport_key=self.sport_key,
        )

    def fetch_events(self, run: RunCounter | None = None) -> OddsBatch:
        """One /events call (no credits): events without prices."""
        result = self.client.fetch(self.events_request(), run)
        return parse_odds(
            result.data,
            provenance=provenance_of(result),
            odds_format=self.odds_format,
            quota=quota_of(result.headers),
            sport_key=self.sport_key,
        )
