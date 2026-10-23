# 2022-23 시즌 추가 수집 보고서 (P-026)

- **언제:** 2026-10-23 11:03~11:07 UTC (한국 20:03~20:07). launchd 수집 시각(16:00, 다음날 07:45·08:45·10:45 KST)을 피했다.
- **어디서:** 개발 DB `presagio`(127.0.0.1:5433, 컨테이너 `presagio-postgres-1`), 마이그레이션 `0002`(모델링 표 4개, 비어 있음), 이 맥의 집 네트워크, worktree `feat/P-026-season-2022-23`
- **코드:** 1차 백필(run 107)은 main `66ba391`, 중립 경기 보완 뒤 실행(run 112, 116)은 이 PR의 커밋 `fe1f2db`(`ingestion_runs.code_version`). 둘 다 커밋된 코드로 돌렸다(미커밋 변경 없음).
- **설정:** `.env.example` 그대로(`HTTP_MIN_INTERVAL=1.0`, 타임아웃 10초, 재시도 3회, `ODDS_API_KEY` 비움)
- **용도:** Elo 워밍업, `prev_season_*`, Elo carry 탐색(H-04)의 시즌 경계(D-049 M-11). **학습·평가 대상이 아니다.** 배당은 없다.
- 형식은 단계 1 보고서(`backfill-report-stage1.md`)를 따랐다.

## 0. 결과 한눈에

| 완료 기준 | 결과 | 근거 |
| --- | --- | --- |
| 1. `backfill --season 2022-23` 종료 0, 호출 ≤ 4(재실행 포함 ≤ 8), dry-run 먼저 | O | 2장, 7장. 1차 4호출(teams skip), 재실행 포함 합계 **7호출**, 재시도 0, 403/429 0 |
| 2. `games` = 일정의 002·004·005 수, 스탯 = 종료 × 2, 품질 체크 fail 0 | O | 3장, 6장. 1,230 + 84 + 6 = 1,320 = 일정 응답 수, 스탯 2,640 = 1,320 × 2. 16 pass + 1 skip, fail 0 |
| 3. 다른 시즌 불변, 재실행 후 불변, 모델링 표 0행 | O | 5장 |
| 4. 중립 경기 확인·보완 | O | 4장. 소스 `isNeutral`은 1,394경기 전부 false. 멕시코시티·파리 2경기를 보완 목록에 넣고 `--force`로 다시 받아 `is_neutral` 0 → 2 |

## 1. 시작 전 상태

브리핑 시점과 숫자가 하나 다르다: `team_game_stats`가 7,946이 아니라 **7,950**이었다. launchd 매일 수집(run 102~106, 10-23 16:00 KST)이 2026-27 종료 2경기를 더해서다(2026-27 스탯 28 → 32). 아래 "시작 전"은 이 백필 직전(run 106 뒤)에 잰 값이다.

| season | games | is_neutral | team_game_stats |
| --- | --- | --- | --- |
| 2023-24 | 1,319 | 5 | 2,636 |
| 2024-25 | 1,321 | 6 | 2,640 |
| 2025-26 | 1,322 | 6 | 2,642 |
| 2026-27 | 1,207 | 4 | 32 |
| 합계 | 5,169 | 21 | 7,950 |

## 2. 실행 순서와 결과

| # | 명령 | 부모 run | 종료 코드 | 호출 | 걸린 시간 | 결과 |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | `backfill --season 2022-23 --dry-run` | - | 0 | 0 | - | 5단위 중 teams skip("done; last succeeded (run 97)", 168시간 안), 4 run. "4 call(s), about 11 s" |
| 1 | `backfill --season 2022-23` | 107 | 0 | 4 | 8.1초 | 일정 1,320경기, 게임로그 정규 1,230·플레이인 6·플레이오프 84 전부 succeeded, 실패 0 |
| - | (중립 경기 보완 커밋 `fe1f2db`, 4장) | | | | | |
| 2 | `backfill --season 2022-23 --season-type regular --force --dry-run` | - | 0 | 0 | - | teams·일정·정규 게임로그 3단위, "3 call(s)" |
| 3 | `backfill --season 2022-23 --season-type regular --force` | 112 | 0 | 3 | 4.0초 | 일정을 새 로더로 다시 받음. 3단위 succeeded |
| 4 | `backfill --season 2022-23` (1과 같은 명령) | 116 | 0 | 0 | 0.0초 | 5단위 전부 skip(끝난 시즌, 최신 run succeeded) |

일정만 다시 받는 옵션이 없어서 3번은 `--season-type regular --force`로 했다. D-042 2항대로 `--force`는 teams와 그 시즌 일정까지 다시 받는다. 시즌 전체 `--force`(teams 포함 5호출)를 쓰면 합계가 9가 되어 완료 기준 1의 8을 넘으므로, 가장 좁은 3호출 단위를 골랐다.

### 2-1. 1차 실행(run 107)의 단위별 상태

| # | job_type | scope_key | status | 호출 | 성공/실패/전체 | 쓴 행 | run |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | teams | nba_api | skipped | 0 | | 0 | |
| 2 | schedule | season=2022-23 | succeeded | 1 | 1320/0/1320 | 1,320 | 108 |
| 3 | gamelog | season=2022-23;season_type=Regular Season | succeeded | 1 | 1230/0/1230 | 3,690 | 109 |
| 4 | gamelog | season=2022-23;season_type=PlayIn | succeeded | 1 | 6/0/6 | 18 | 110 |
| 5 | gamelog | season=2022-23;season_type=Playoffs | succeeded | 1 | 84/0/84 | 252 | 111 |

`partial`로 빠진 경기는 없다. 일정의 `postponedStatus`는 1,394경기 전부 `"N"`, `gameStatus`는 전부 3(종료)이었다. `gameSubtype`은 전부 빈 값이다(인시즌 토너먼트는 2023-24부터). run 108의 `skipped_game_types`는 프리시즌 70, 올스타 4.

## 3. 확인 쿼리 (완료 기준 2)

### 3-1. 경기 수 = 일정 응답의 002·004·005 수

일정 raw를 유형 자리로 센 수(잘라내기 전):

```bash
docker compose exec postgres psql -U presagio -d presagio -c "WITH r AS (SELECT response_json FROM raw_api_responses WHERE endpoint = 'scheduleleaguev2' AND request_params->>'Season' = '2022-23' ORDER BY last_fetched_at DESC LIMIT 1), g AS (SELECT gm FROM r, jsonb_array_elements(r.response_json->'leagueSchedule'->'gameDates') gd, jsonb_array_elements(gd->'games') gm) SELECT substr(gm->>'gameId',3,1) AS type_digit, count(*) FROM g GROUP BY 1 ORDER BY 1"
```

| 001 프리시즌 | 002 정규 | 003 올스타 | 004 플레이오프 | 005 플레이인 | 006 NBA컵 결승 |
| --- | --- | --- | --- | --- | --- |
| 70 | 1,230 | 4 | 84 | 6 | 0 (없음) |

DB의 `games`:

```bash
docker compose exec postgres psql -U presagio -d presagio -c "SELECT season, count(*) FILTER (WHERE substr(game_id,3,1)='2') AS regular, count(*) FILTER (WHERE substr(game_id,3,1)='5') AS playin, count(*) FILTER (WHERE substr(game_id,3,1)='4') AS playoffs, count(*) FILTER (WHERE substr(game_id,3,1)='6') AS cup_final, count(*) AS total, count(*) FILTER (WHERE status_code=3) AS final, count(*) FILTER (WHERE is_neutral) AS neutral FROM games WHERE season = '2022-23' GROUP BY season"
```

| season | regular | playin | playoffs | cup_final | total | final | neutral |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2022-23 | 1230 | 6 | 84 | 0 | 1320 | 1320 | 2 |

정규·플레이인·플레이오프 모두 일정 응답과 정확히 같다. NBA컵 결승(006)은 2022-23에 없다(인시즌 토너먼트는 2023-24부터). 플레이오프 84 = 1라운드 43 + 2라운드 25 + 컨퍼런스 결승 11 + 파이널 5(일정 `gameLabel`별 수).

### 3-2. 종료 경기의 홈팀 승률, 평균 총점, 중립 수

```bash
docker compose exec postgres psql -U presagio -d presagio -c "SELECT CASE substr(game_id,3,1) WHEN '2' THEN 'regular' WHEN '5' THEN 'playin' WHEN '4' THEN 'playoffs' WHEN '6' THEN 'cup_final' END AS type, count(*) AS final_games, round(avg((home_score > away_score)::int), 3) AS home_win_rate, round(avg(home_score + away_score), 1) AS avg_total, count(*) FILTER (WHERE is_neutral) AS neutral FROM games WHERE season = '2022-23' AND status_code = 3 GROUP BY ROLLUP (1) ORDER BY 1 NULLS LAST"
```

| 유형 | 종료 경기 | 홈팀 승률 | 평균 총점 | 중립 |
| --- | --- | --- | --- | --- |
| regular | 1,230 | 0.580 | 229.4 | 2 |
| playin | 6 | 0.500 | 215.7 | 0 |
| playoffs | 84 | 0.595 | 219.2 | 0 |
| **전체** | **1,320** | **0.581** | **228.7** | **2** |

상식 확인: 2022-23 정규 홈팀 승률 0.580은 2023-24~2025-26(0.543~0.554)보다 3%p쯤 높다. 2022-23은 홈 승률이 유난히 높았던 시즌으로 알려져 있어 데이터 문제로 보지 않는다(외부 수치와 직접 대조는 안 했다). 평균 총점 229.4는 2023-24(228.4)와 비슷하다. 중립 2경기를 빼도 정규 홈팀 승률은 0.581(1,228경기)로 거의 같다. 플레이오프 총점이 정규보다 10점 낮은 것도 다른 시즌과 같은 경향이다.

### 3-3. 팀 스탯 행 수 = 종료 경기 × 2

```bash
docker compose exec postgres psql -U presagio -d presagio -c "SELECT g.season, count(*) FILTER (WHERE g.status_code=3 AND substr(g.game_id,3,1) IN ('2','4','5')) AS final_games, 2 * count(*) FILTER (WHERE g.status_code=3 AND substr(g.game_id,3,1) IN ('2','4','5')) AS expected_rows, (SELECT count(*) FROM team_game_stats t JOIN games x USING (game_id) WHERE x.season = g.season) AS stat_rows, (SELECT count(*) FROM team_game_stats_primary t JOIN games x USING (game_id) WHERE x.season = g.season) AS primary_rows FROM games g WHERE g.season = '2022-23' GROUP BY g.season"
```

| season | 종료 경기(정규·플레이인·플레이오프) | 기대 행 | team_game_stats | team_game_stats_primary |
| --- | --- | --- | --- | --- |
| 2022-23 | 1320 | 2640 | 2640 | 2640 |

## 4. 중립 경기 확인과 보완 (완료 기준 4)

### 4-1. 소스 플래그

```bash
docker compose exec postgres psql -U presagio -d presagio -c "WITH r AS (SELECT response_json FROM raw_api_responses WHERE endpoint = 'scheduleleaguev2' AND request_params->>'Season' = '2022-23' ORDER BY last_fetched_at DESC LIMIT 1), g AS (SELECT gm FROM r, jsonb_array_elements(r.response_json->'leagueSchedule'->'gameDates') gd, jsonb_array_elements(gd->'games') gm) SELECT count(*) AS games, count(*) FILTER (WHERE gm ? 'isNeutral') AS has_field, count(*) FILTER (WHERE gm->>'isNeutral' = 'true') AS flag_true, jsonb_agg(DISTINCT jsonb_typeof(gm->'isNeutral')) AS json_types FROM g"
```

| games | has_field | flag_true | json_types |
| --- | --- | --- | --- |
| 1394 | 1394 | 0 | `["boolean"]` |

`isNeutral` 필드는 모든 경기에 있고(JSON 불리언) 값은 **전부 false**다. 2023-24와 같다.

### 4-2. raw로 찾은 중립 후보

각 홈팀이 가장 많이 쓴 경기장과 다른 곳에서 열린 정규·플레이인·플레이오프 경기를 뽑았다:

```bash
docker compose exec postgres psql -U presagio -d presagio -c "WITH r AS (SELECT response_json FROM raw_api_responses WHERE endpoint = 'scheduleleaguev2' AND request_params->>'Season' = '2022-23' ORDER BY last_fetched_at DESC LIMIT 1), g AS (SELECT gm FROM r, jsonb_array_elements(r.response_json->'leagueSchedule'->'gameDates') gd, jsonb_array_elements(gd->'games') gm), x AS (SELECT gm->>'gameId' AS game_id, gm->'homeTeam'->>'teamTricode' AS home, gm->'awayTeam'->>'teamTricode' AS away, gm->>'arenaName' AS arena, gm->>'arenaCity' AS city, gm->>'gameDateTimeUTC' AS utc, gm->>'isNeutral' AS neutral, gm->>'gameLabel' AS label FROM g WHERE substr(gm->>'gameId',3,1) IN ('2','4','5','6')), modal AS (SELECT DISTINCT ON (home) home, arena AS m_arena FROM x GROUP BY home, arena ORDER BY home, count(*) DESC) SELECT x.* FROM x JOIN modal USING (home) WHERE x.arena <> modal.m_arena ORDER BY utc"
```

36행이 나왔고 셋으로 나뉜다(2 + 3 + 31).

| 무리 | 경기 | 판단 |
| --- | --- | --- |
| 해외 경기 | `0022200439` 2022-12-17 SAS(홈)–MIA, Mexico City Arena, `arenaCity` = "Mexico City, Mexico" / `0022200678` 2023-01-19 DET(홈)–CHI, Accor Arena, "Paris" | **중립** (브리핑의 알려진 후보 2개와 같다) |
| SAS 연고 밖 홈경기 | `0022200635` Alamodome(San Antonio), `0022201199`·`0022201214` Moody Center(Austin) | 중립 아님. 홈팀 연고 행사이고 2023-24 Austin 경기(`0022300965`)를 P-025가 중립 아님으로 둔 것과 같은 판단 |
| MIA 경기장 이름 변경 | 시즌 중 이름이 FTX Arena(22경기, ~01-13) → Miami-Dade Arena(18경기, 01-14~04-01) → Kaseya Center(13경기, 04-09~)로 바뀜. 가장 많이 쓴 FTX Arena가 아닌 31행 | 중립 아님(같은 경기장) |

`gameLabel`은 정규 1,230경기 전부 빈 값이고, 플레이인·플레이오프는 "East Play-In", "East - First Round" 같은 라운드 이름뿐이라 중립 표시가 없다. 프리시즌 라스베이거스·아부다비 경기는 저장하지 않는 유형(001)이다.

**게임로그로도 알 수 없다.** 2024-25부터는 중립 경기의 게임로그 `MATCHUP`이 두 행 모두 "X @ Y"였지만(D-042), 2022-23은 두 경기 모두 보통 모양이다(raw 확인: `0022200439` "SAS vs. MIA" / "MIA @ SAS", `0022200678` "DET vs. CHI" / "CHI @ DET"). 그래서 이 두 경기는 적재 때 거부되지도, `neutral_site` 이슈로 잡히지도 않았다. 중립 신호는 일정의 `arenaCity`·`arenaName`뿐이다.

### 4-3. 보완과 증명

D-046 규칙 (b)대로 `src/presagio/ingestion/sources/nba_api/schedule.py`의 `UNFLAGGED_NEUTRAL_GAME_IDS`에 두 game_id를 더했다(5 → 7). 규칙은 `isNeutral OR game_id ∈ 목록` 그대로라 소스의 true를 false로 뒤집는 경로가 없고, 일정·스코어보드 두 파서가 같은 함수를 쓴다. 테스트(`tests/ingestion/sources/test_nba_api_schedule.py`):

- `test_2022_23_neutral_sites_are_true_although_the_source_says_false`: 실제 응답(raw)에서 5경기만 잘라 만든 픽스처 `tests/fixtures/nba_api/scheduleleaguev2__2022-23_neutral.json`(멕시코시티, 파리, Alamodome, Austin, 개막전)으로, 소스 값이 전부 false임과 파싱 결과가 두 경기만 true임을 단언.
- `test_unflagged_neutral_list_is_closed_to_2022_23_and_2023_24_stored_games`: 목록 7개, 시즌은 2022-23·2023-24뿐.
- 변이 확인: 새 ID를 하나씩 목록에서 지우면 각각 테스트 2개가 실패한다.

그 뒤 2022-23 일정을 `--force`로 다시 받았다(2장 3번, run 114). 결과:

```bash
docker compose exec postgres psql -U presagio -d presagio -c "SELECT g.game_id, g.start_time_utc, h.tricode AS home, a.tricode AS away, g.home_score, g.away_score, g.is_neutral FROM games g JOIN teams h ON h.team_id = g.home_team_id JOIN teams a ON a.team_id = g.away_team_id WHERE g.season = '2022-23' AND g.is_neutral ORDER BY g.start_time_utc"
```

| game_id | start_time_utc | home | away | home_score | away_score | is_neutral |
| --- | --- | --- | --- | --- | --- | --- |
| 0022200439 | 2022-12-17 22:00:00+00 | SAS | MIA | 101 | 111 | t |
| 0022200678 | 2023-01-19 20:00:00+00 | DET | CHI | 108 | 126 | t |

`--force` 전 2022-23 `is_neutral` true 0 → 후 2. 다른 시즌 true 수(5/6/6/4)는 그대로다(5장).

## 5. 멱등성 증거 (완료 기준 3)

A는 시작 전(run 106 뒤), B는 1차 백필(run 107) 뒤, C는 `--force` 재수집(run 112) 뒤, D는 같은 명령 재실행(run 116) 뒤다.

```bash
docker compose exec postgres psql -U presagio -d presagio -c "SELECT 'teams' AS t, count(*) FROM teams UNION ALL SELECT 'team_aliases', count(*) FROM team_aliases UNION ALL SELECT 'games', count(*) FROM games UNION ALL SELECT 'team_game_stats', count(*) FROM team_game_stats UNION ALL SELECT 'raw_api_responses', count(*) FROM raw_api_responses UNION ALL SELECT 'ingestion_runs', count(*) FROM ingestion_runs UNION ALL SELECT 'odds_events', count(*) FROM odds_events UNION ALL SELECT 'odds_snapshots', count(*) FROM odds_snapshots UNION ALL SELECT 'features', count(*) FROM features UNION ALL SELECT 'model_versions', count(*) FROM model_versions UNION ALL SELECT 'predictions', count(*) FROM predictions UNION ALL SELECT 'evaluations', count(*) FROM evaluations"
docker compose exec postgres psql -U presagio -d presagio -c "SELECT g.season, count(*) AS games, count(*) FILTER (WHERE is_neutral) AS neutral, (SELECT count(*) FROM team_game_stats s JOIN games g2 USING (game_id) WHERE g2.season = g.season) AS stat_rows FROM games g GROUP BY g.season ORDER BY 1"
```

| 표 | A 시작 전 | B 1차 백필 후 (4호출) | C `--force` 후 (3호출) | D 재실행 후 (0호출) |
| --- | --- | --- | --- | --- |
| teams | 30 | 30 | 30 | 30 |
| team_aliases | 90 | 90 | 90 | 90 |
| games | 5,169 | 6,489 | 6,489 | 6,489 |
| team_game_stats | 7,950 | 10,590 | 10,590 | 10,590 |
| raw_api_responses | 28 | 32 | 33 | 33 |
| ingestion_runs | 106 | 111 | 115 | 116 |
| odds_events | 41 | 41 | 41 | 41 |
| odds_snapshots | 1,520 | 1,520 | 1,520 | 1,520 |
| features | 0 | 0 | 0 | 0 |
| model_versions | 0 | 0 | 0 | 0 |
| predictions | 0 | 0 | 0 | 0 |
| evaluations | 0 | 0 | 0 | 0 |

시즌별(경기 / is_neutral / 팀 스탯 행):

| season | A | B | C | D |
| --- | --- | --- | --- | --- |
| 2022-23 | - | 1320 / 0 / 2640 | 1320 / **2** / 2640 | 1320 / 2 / 2640 |
| 2023-24 | 1319 / 5 / 2636 | 같음 | 같음 | 같음 |
| 2024-25 | 1321 / 6 / 2640 | 같음 | 같음 | 같음 |
| 2025-26 | 1322 / 6 / 2642 | 같음 | 같음 | 같음 |
| 2026-27 | 1207 / 4 / 32 | 같음 | 같음 | 같음 |

읽는 법:
- 늘어난 것은 2022-23 추가분뿐이다: games +1,320, team_game_stats +2,640(= 1,320 × 2). 다른 시즌과 배당·모델링 표는 그대로다.
- C에서 일정·정규 게임로그를 다시 받아 upsert했지만 games·스탯 행 수는 같다. 바뀐 값은 `is_neutral` 2개(의도)와 `fetched_at`·`raw_response_id`·`source`다. D-042 2항 주의대로 일정을 다시 받으면 `games.source`가 `scheduleleaguev2`가 되고, 같은 실행에서 다시 받은 정규 게임로그가 정규 1,230경기를 `leaguegamelog`로 되돌린다. **플레이인 6·플레이오프 84경기는 게임로그를 다시 받지 않아 `games.source = scheduleleaguev2`로 남았다**(점수는 같다. 9장).
- raw가 C에서 1행 는 것은 정규 게임로그(raw 100)다. 같은 요청·같은 크기(390,233바이트)인데 sha256이 달라 새 행이 됐다. 단계 1 보고서 5장의 "같은 내용을 행 순서만 바꿔 줌"과 같은 현상이다(D-042 3항). 일정(raw 94)은 본문이 같아 `fetch_count`만 2가 됐고, teams(raw 1)도 `fetch_count`만 올랐다.
- D는 끝난 시즌의 모든 단위가 succeeded라 5단위 전부 skip, 호출 0. `ingestion_runs`는 부모 1행만 는다.

## 6. 품질 체크 (완료 기준 2)

```bash
uv run presagio quality-check --season 2022-23
uv run presagio quality-check
```

두 번 모두 **16 pass, 0 fail, 0 warn, 0 error, 1 skip, 종료 코드 0.** skip은 `feature_drift`(재계산 함수 없음, P-035)다. 모델링 체크 `features_after_tipoff`·`predictions_after_tipoff`는 빈 표라 pass(0행).

`--season 2022-23` 결과 중 시즌 특성과 관련된 것:

| 체크 | 결과 | 메모 |
| --- | --- | --- |
| `regular_season_games` | PASS | "2022-23: 1230 final". 82경기 시즌이라 기대값 1,230이 그대로 맞는다 |
| `one_game_per_team_day` | PASS 2,640 | 팀×동부 날짜 중복 0 |
| `gamelog_vs_schedule_score` | PASS 1,230 | 정규 게임로그 점수 = 최신 일정 raw 점수 |
| `team_rows_per_game`·`points_match_score`·`stat_arithmetic` | PASS 1,320 / 1,320 / 2,640 | |
| `primary_stats_source` | PASS 1,320 | |
| `past_games_not_final` | PASS 1,320 | |
| `odds_*` 3개 | PASS 0 | 2022-23 배당 없음(범위 밖) |

전체 시즌: `regular_season_games` "2022-23 1230, 2023-24 1230, 2024-25 1230, 2025-26 1230 final; 2026-27 in progress 1206". 2022-23 데이터로 깨진 전 시즌 체크는 없다.

## 7. 호출 수, 시간, 차단·재시도 (완료 기준 1)

```bash
docker compose exec postgres psql -U presagio -d presagio -c "SELECT run_id, scope_key, status, round(extract(epoch FROM finished_at - started_at)::numeric, 1) AS seconds, error_detail->'totals'->>'api_calls' AS api_calls, error_detail->'totals'->>'retries' AS retries, error_detail->'totals'->>'rows_written' AS rows_written, code_version FROM ingestion_runs WHERE job_type = 'backfill' AND run_id >= 107 ORDER BY run_id"
docker compose exec postgres psql -U presagio -d presagio -c "SELECT count(*) AS child_runs, sum(api_calls) AS api_calls, sum(retry_count) AS retries, count(*) FILTER (WHERE status <> 'succeeded') AS not_succeeded FROM ingestion_runs WHERE run_id >= 107 AND job_type <> 'backfill'"
```

| run | 무엇 | status | 초 | 호출 | 재시도 | 쓴 행 | code_version |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 107 | 1차 백필 | succeeded | 8.1 | 4 | 0 | 5,280 | 66ba391 |
| 112 | 정규 `--force`(일정 다시 받기) | succeeded | 4.0 | 3 | 0 | 5,100 | fe1f2db |
| 116 | 재실행 | succeeded | 0.0 | 0 | 0 | 0 | fe1f2db |
| **합계** | | | **12.1** | **7** | **0** | | |

- 자식 run 7개, `api_calls` 합 7, `retry_count` 합 0, succeeded 아닌 run 0. 종료 코드 2(차단) 없음.
- 이 티켓의 raw 응답 전부 HTTP 200(새 raw 5행 `fetch_count` 합 6 + teams raw 1의 재수집 1 = 7). **403·429·5xx·타임아웃 0건.**
- dry-run 예상 11초, 실제 8.1초. 일정 응답 3.9 MB(TOAST 압축 저장), DB 전체 24 MB.

## 8. 사람이 직접 돌려볼 명령

전부 레포 루트에서. 1~4는 외부 호출이 없다.

```bash
# 1) 표별 행 수: games 6489, team_game_stats 10590 (+ launchd가 그 뒤 더한 2026-27 경기)
docker compose exec postgres psql -U presagio -d presagio -c "SELECT season, count(*) AS games, count(*) FILTER (WHERE is_neutral) AS neutral FROM games GROUP BY season ORDER BY season"

# 2) 품질 체크: 16 pass 1 skip, exit code 0
uv run presagio quality-check --season 2022-23

# 3) 3장·4장 쿼리를 위 코드 블록에서 복사해 실행

# 4) 다음 백필이 무엇을 할지: 5단위 전부 skip, 0 call(s)
uv run presagio backfill --season 2022-23 --dry-run

# 5) 테스트
uv run pytest tests/ingestion/sources/test_nba_api_schedule.py -q
```

## 9. 못 한 것 / 한계

- **`docs/design/schema-v1.md` 6-1과 13장은 아직 "2023-24 보완 목록, 5경기"라고 적혀 있다.** 이 티켓 범위(브리핑의 건드릴 파일)에 없어서 고치지 않았다. 지금 목록은 2022-23 2경기를 더한 7경기다. PR 본문 결정 제안 2.
- 2022-23 홈 승률(0.580)이 다른 시즌보다 높은 것은 외부 수치와 직접 대조하지 않았다(알려진 시즌 특성으로 판단).
- 픽스처는 DB의 `jsonb`에서 꺼내 잘랐기 때문에 원본 바이트와 키 순서가 다르다(값은 같다. `jsonb`는 객체 키를 길이·이름 순으로 정렬한다). 파서는 키 순서를 쓰지 않는다.
- **2022-23 플레이인·플레이오프 90경기의 `games.source`가 `scheduleleaguev2`다**(정규 1,230은 `leaguegamelog`). 일정 재수집(C) 뒤 그 유형의 게임로그를 다시 받지 않았기 때문이다. 점수·상태는 게임로그 값과 같고(`gamelog_vs_schedule_score` pass, 팀 스탯은 `team_game_stats.source = leaguegamelog`로 `primary_stats_source` pass), 바뀐 것은 "마지막으로 이 행을 쓴 엔드포인트" 표시뿐이다. 맞추려면 `--season 2022-23 --season-type playin --season-type playoffs --force`(teams·일정 포함 4호출)가 필요한데, 합계 11호출로 완료 기준 1의 8을 넘어서 하지 않았다. 다음 일정 재수집 때 함께 맞추면 된다(PR 결정 제안 3).
- `--force` 재수집이 teams도 다시 받아(run 113) teams의 168시간 갱신 창이 지금부터 다시 시작된다. 행 수·값 변화는 없다(30팀, 별칭 90).
- 2022-23은 학습·평가 대상이 아니다(M-11). 학습 창에서 빼는 하한은 P-033 몫이다. 이 PR은 데이터만 넣었다.
- 스코어보드 경로(`ingest-daily --date` 과거 날짜)로 2022-23 날짜를 돌려 보지는 않았다. 같은 `is_neutral_game()`을 쓰므로 목록의 두 경기는 그 경로에서도 true가 된다(P-025 테스트가 2023-24로 고정한 동작).

## 10. 쉬운 말 요약

Elo 점수를 미리 데워 두고 "지난 시즌 성적" 피처를 만들기 위해, 학습에는 쓰지 않을 2022-23 시즌 1,320경기와 팀 경기 기록 2,640줄을 NBA 통계 사이트에서 4번 요청해 개발 DB에 넣었다. 이 시즌 자료도 2023-24처럼 "중립 경기장" 표시가 전부 꺼져 있어서, 경기장 도시를 보고 멕시코시티·파리 경기 2개를 찾아 코드의 보완 목록에 넣고 일정을 다시 받았더니 두 경기가 제대로 중립으로 표시됐다. 데이터 검사는 모두 통과했고, 다시 돌려도 다른 시즌과 행 수가 하나도 바뀌지 않았으며, 요청은 모두 7번에 막히거나 다시 시도한 적은 없었다.
