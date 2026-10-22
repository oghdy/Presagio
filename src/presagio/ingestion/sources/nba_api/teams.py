"""nba_api teams: the library's static list, checked against CommonTeamYears (schema 5-1).

static.teams has every field the teams table needs but no response to point at;
CommonTeamYears is a real response but lacks city and nickname. So the static list is the
content and CommonTeamYears confirms which franchises are active and their abbreviations.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nba_api.stats.endpoints import commonteamyears
from nba_api.stats.static import teams as static_teams

from presagio.ingestion.client import IngestionClient, RunCounter, nba_api_request
from presagio.ingestion.sources.base import (
    Provenance,
    QualityIssue,
    SourceFormatError,
    TeamAliasRecord,
    TeamBatch,
    TeamRecord,
    require,
)
from presagio.ingestion.sources.nba_api.schedule import LEAGUE_ID, provenance_of

TEAMS_SOURCE = "nba_api.static"
# team_aliases.source for names nba_api itself uses (odds-provider aliases come in P-019).
ALIAS_SOURCE = "nba_api"


def active_franchises(data: Any) -> dict[int, str | None]:
    """CommonTeamYears rows whose MAX_YEAR is the latest year -> {team_id: abbreviation}."""
    result_sets = require(data, "resultSets", "commonteamyears")
    if not result_sets:
        raise SourceFormatError("commonteamyears: empty resultSets")
    headers = require(result_sets[0], "headers", "commonteamyears")
    rows = require(result_sets[0], "rowSet", "commonteamyears")
    needed = ("TEAM_ID", "MAX_YEAR", "ABBREVIATION")
    missing = [name for name in needed if name not in headers]
    if missing or not rows:
        raise SourceFormatError(f"commonteamyears: missing columns {missing} or no rows")
    team, last, abbr = (headers.index(name) for name in needed)
    latest = max(row[last] for row in rows)
    return {row[team]: row[abbr] for row in rows if row[last] == latest}


def build_team_batch(
    static: list[dict[str, Any]], team_years: Any, provenance: Provenance
) -> TeamBatch:
    """Static team dicts -> records and aliases, with CommonTeamYears disagreements as issues."""
    records = tuple(
        TeamRecord(
            team_id=team["id"],
            tricode=team["abbreviation"],
            city=team["city"],
            nickname=team["nickname"],
            full_name=team["full_name"],
        )
        for team in static
    )
    aliases = tuple(
        TeamAliasRecord(ALIAS_SOURCE, alias, record.team_id)
        for record in records
        for alias in (record.full_name, record.tricode)
    )
    active = active_franchises(team_years)
    known = {record.team_id: record for record in records}
    issues = [
        QualityIssue("team_not_in_static_list", str(team_id), f"active in CommonTeamYears ({abbr})")
        for team_id, abbr in sorted(active.items())
        if team_id not in known
    ]
    for team_id, record in sorted(known.items()):
        if team_id not in active:
            issues.append(
                QualityIssue("team_not_active", str(team_id), f"{record.tricode} not active")
            )
        elif active[team_id] != record.tricode:
            issues.append(
                QualityIssue(
                    "tricode_mismatch",
                    str(team_id),
                    f"static {record.tricode}, CommonTeamYears {active[team_id]}",
                )
            )
    return TeamBatch(records, aliases, provenance, tuple(issues))


class NbaApiTeamSource:
    """TeamSource over nba_api: static.teams content, CommonTeamYears verification."""

    name = "nba_api"

    def __init__(
        self,
        client: IngestionClient,
        league_id: str = LEAGUE_ID,
        static: Callable[[], list[dict[str, Any]]] = static_teams.get_teams,
    ) -> None:
        """static is injectable so tests can simulate a library list that went stale."""
        self.client = client
        self.league_id = league_id
        self.static = static

    def fetch_teams(self, run: RunCounter | None = None) -> TeamBatch:
        """One CommonTeamYears call; the records' fetched_at is when that check was received."""
        endpoint = commonteamyears.CommonTeamYears(league_id=self.league_id, get_request=False)
        result = self.client.fetch(nba_api_request(endpoint), run)
        checked = provenance_of(result)
        # The rows' content comes from the library list, so name it as the source.
        provenance = Provenance(TEAMS_SOURCE, checked.fetched_at, checked.raw_response_id)
        return build_team_batch(self.static(), result.data, provenance)
