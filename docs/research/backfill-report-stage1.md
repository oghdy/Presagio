# 단계 1 백필 보고서 (P-021)

- **언제:** 2026-10-22 14:52~15:00 UTC (한국 10-22 23:52 ~ 10-23 00:00)
- **어디서:** 개발 DB `presagio`(127.0.0.1:5433, PostgreSQL 컨테이너 `presagio-postgres-1`), 마이그레이션 `0001`, 이 맥의 집 네트워크
- **코드:** main `d544359` + 이 PR의 수정 커밋 `e1adc9c`(중립 경기장 경기, 아래 4-1). 첫 백필(run 7)만 `d544359`, 나머지 run은 전부 `e1adc9c`(`ingestion_runs.code_version`)
- **설정:** `.env.example` 그대로(`HTTP_MIN_INTERVAL=1.0`, 타임아웃 10초, 재시도 3회, `ODDS_API_KEY` 비움)
- **범위:** 2023-24, 2024-25, 2025-26(정규 + 플레이인 + 플레이오프)과 2026-27(진행 중, 10-22 기준). 배당 과거 백필은 하지 않았다(D-026 보류).

## 0. 결과 한눈에

| 완료 기준 | 결과 | 근거 |
| --- | --- | --- |
| 1. 시즌별 경기 수, 홈팀 승률, 평균 총점 표 | O | 3장 |
| 2. 백필 재실행 후 행 수 불변(전후 count) | O | 5장. 일반 재실행(4호출)과 `--force` 전체 재실행(17호출) 모두 경기·스탯·raw 행 수 그대로 |
| 3. 품질 체크 전부 통과 | O | 6장. 시즌별 4회 + 전체 1회 모두 14 pass, 종료 코드 0. 첫 실행에서 FAIL 1건(95경기)이 나와 원인 2개를 처리한 뒤의 결과다 |
| 4. 소요 시간, 호출 수, 차단·재시도 횟수 | O | 7장. 이 티켓 전체 nba_api 68호출, 재시도 0, 403/429 0, 모든 응답 HTTP 200 |

## 1. 시작 전 상태

이 DB는 비어 있지 않았다. P-014·P-017·P-019 때 사람 승인으로 돌린 실제 호출 결과가 남아 있었다.

| run_id | job_type | scope_key | status | 메모 |
| --- | --- | --- | --- | --- |
| 1 | teams | nba_api | succeeded | 30팀, 별칭 60 |
| 2 | schedule | season=2025-26 | succeeded | 1,322경기 |
| 3 | gamelog | season=2025-26;season_type=Playoffs | succeeded | **P-014 시절 로더**(경기 점수만 갱신, 팀 스탯 0행). 4-2 참고 |
| 4 | schedule | season=2026-27 | succeeded | 1,207경기 |
| 5, 6 | odds_events, odds | the_odds_api | succeeded | 41이벤트, 796스냅숏(10-22 실제 호출 1회) |

표별 행 수는 5장 표의 "시작 전" 열에 있다(`teams` 30, `games` 2,529, `team_game_stats` 0, `raw_api_responses` 6, DB 12 MB).

## 2. 실행 순서와 결과

| # | 명령 | 부모 run | 종료 코드 | 호출 | 걸린 시간 | 결과 |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | `backfill --season 2023-24 --season 2024-25 --season 2025-26 --season 2026-27 --dry-run` | - | 0 | 0 | - | 계획 17단위 중 14 run, 3 skip. "14 call(s), about 38 s" |
| 1 | 같은 명령, 실제 | 7 | **1** | 14 | 16.7초 | 12 succeeded, **2 partial**(정규시즌 게임로그 2024-25, 2025-26 각 5경기 거부), 3 skipped |
| - | `quality-check` 전체 | - | **1** | 0 | - | `team_rows_per_game` FAIL 95경기 (6장) |
| - | (수정 커밋 `e1adc9c`, 4-1) | | | | | |
| 2 | 1과 같은 명령(재개) | 22 | 0 | 6 | 6.9초 | partial 2단위 + 진행 중 시즌 4단위만 다시 받음, 전부 succeeded |
| 3 | `backfill --season 2025-26 --season-type playoffs --force` | 29 | 0 | 3 | 3.6초 | 오래된 succeeded 단위(run 3) 다시 받기(4-2) |
| - | `quality-check --season <각 시즌>` 4회 + 전체 1회 | - | 0 ×5 | 0 | - | 전부 14 pass (6장) |
| 4 | 1과 같은 명령(재개) | 33 | 0 | 4 | 4.1초 | 끝난 시즌 전부 skip, 2026-27만 |
| 5 | 1과 같은 명령 + `--force` | 38 | 0 | 17 | 20.5초 | 17단위 전부 succeeded |
| 6 | 1과 같은 명령(재개) | 56 | 0 | 4 | 4.5초 | 멱등성 증거용 다시 실행(5장) |
| 7 | 1과 같은 명령 + `--force` | 61 | 0 | 17 | 19.4초 | 멱등성 증거용 다시 실행(5장) |
| 8 | `ingest-daily --date 2026-10-22 --dry-run` | - | 0 | 0 | - | 2호출 계획 |
| 9 | `ingest-daily --date 2026-10-22` | 79 | 0 | 2 | 약 1.7초 | 어제(동부 10-21) 11경기 결과, 오늘(동부 10-22) 2경기 일정, 배당 skipped, 품질 체크 13 pass 1 skip |
| 10 | 같은 명령 다시 | 82 | 0 | 1 | 약 0.4초 | 어제 결과는 done이라 skip, 오늘 일정만 |

솔직하게 적을 것: 4·5번 실행 사이와 뒤의 행 수 스냅숏은 **내 셸 스크립트 실수**(zsh에서 명령을 변수에 담아 실행 → `command not found`)로 저장되지 않았다. 백필 자체는 정상 종료(코드 0)였다. 그래서 같은 순서(재개 → `--force`)를 6·7번으로 한 번 더 돌리며 스냅숏을 제대로 남겼다. 이 21호출도 7장 합계에 들어 있다.

### 2-1. 1차 실행(run 7)의 단위별 상태

| # | job_type | scope_key | status | 호출 | 성공/실패/전체 | 쓴 행 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | teams | nba_api | skipped | 0 | | |
| 2 | schedule | season=2023-24 | succeeded | 1 | 1319/0/1319 | 1319 |
| 3 | gamelog | season=2023-24;season_type=Regular Season | succeeded | 1 | 1230/0/1230 | 3690 |
| 4 | gamelog | season=2023-24;season_type=PlayIn | succeeded | 1 | 6/0/6 | 18 |
| 5 | gamelog | season=2023-24;season_type=Playoffs | succeeded | 1 | 82/0/82 | 246 |
| 6 | schedule | season=2024-25 | succeeded | 1 | 1321/0/1321 | 1321 |
| 7 | gamelog | season=2024-25;season_type=Regular Season | **partial** | 1 | 1225/5/1230 | 3675 |
| 8 | gamelog | season=2024-25;season_type=PlayIn | succeeded | 1 | 6/0/6 | 18 |
| 9 | gamelog | season=2024-25;season_type=Playoffs | succeeded | 1 | 84/0/84 | 252 |
| 10 | schedule | season=2025-26 | skipped | 0 | | |
| 11 | gamelog | season=2025-26;season_type=Regular Season | **partial** | 1 | 1225/5/1230 | 3675 |
| 12 | gamelog | season=2025-26;season_type=PlayIn | succeeded | 1 | 6/0/6 | 18 |
| 13 | gamelog | season=2025-26;season_type=Playoffs | skipped | 0 | | |
| 14 | schedule | season=2026-27 | succeeded | 1 | 1207/0/1207 | 1207 |
| 15 | gamelog | season=2026-27;season_type=Regular Season | succeeded | 1 | 14/0/14 | 42 |
| 16 | gamelog | season=2026-27;season_type=PlayIn | succeeded | 1 | 0/0/0 | 0 |
| 17 | gamelog | season=2026-27;season_type=Playoffs | succeeded | 1 | 0/0/0 | 0 |

2025-26 일정(10번)이 skip인 것은 D-037대로다. run 2가 시즌 종료(2026-10-15 UTC) 뒤인 10-22에 succeeded로 끝났기 때문이다.

## 3. 확인 쿼리 (완료 기준 1)

### 3-1. 시즌별·유형별 경기 수

```bash
docker compose exec postgres psql -U presagio -d presagio -c "SELECT season, count(*) FILTER (WHERE substr(game_id,3,1)='2') AS regular, count(*) FILTER (WHERE substr(game_id,3,1)='5') AS playin, count(*) FILTER (WHERE substr(game_id,3,1)='4') AS playoffs, count(*) FILTER (WHERE substr(game_id,3,1)='6') AS cup_final, count(*) AS total, count(*) FILTER (WHERE status_code=3) AS final FROM games GROUP BY season ORDER BY season"
```

| season | regular | playin | playoffs | cup_final | total | final |
| --- | --- | --- | --- | --- | --- | --- |
| 2023-24 | 1230 | 6 | 82 | 1 | 1319 | 1319 |
| 2024-25 | 1230 | 6 | 84 | 1 | 1321 | 1321 |
| 2025-26 | 1230 | 6 | 85 | 1 | 1322 | 1322 |
| 2026-27 | 1206 | 0 | 0 | 1 | 1207 | 14 |

`docs/research/nba_api.md` 2장 표(일정 응답 전체로 센 수)와 **정규·플레이인·플레이오프·NBA컵 결승 모두 정확히 같다.** 차이는 하나뿐이고 설계대로다: 프리시즌(001, 71~73경기)과 올스타(003, 4~7경기)는 저장하지 않는다(설계안 v1, run의 `error_detail.skipped_game_types`에 개수 기록). 2026-27의 정규 1,206은 "발표분"이다. NBA컵 조별리그가 끝난 뒤 남은 24경기가 일정에 추가되면 주 1회 일정 백필(D-039 3)이 넣는다. 2026-27 NBA컵 결승 1경기는 팀 미정이라 예정 상태다.

### 3-2. 종료 경기의 홈팀 승률과 평균 총점

```bash
docker compose exec postgres psql -U presagio -d presagio -c "SELECT season, CASE substr(game_id,3,1) WHEN '2' THEN 'regular' WHEN '5' THEN 'playin' WHEN '4' THEN 'playoffs' WHEN '6' THEN 'cup_final' END AS type, count(*) AS final_games, round(avg((home_score > away_score)::int), 3) AS home_win_rate, round(avg(home_score + away_score), 1) AS avg_total FROM games WHERE status_code = 3 GROUP BY ROLLUP (season, 2) ORDER BY season NULLS LAST, 2 NULLS LAST"
```

| season | 유형 | 종료 경기 | 홈팀 승률 | 평균 총점 |
| --- | --- | --- | --- | --- |
| 2023-24 | regular | 1230 | 0.543 | 228.4 |
| 2023-24 | playin | 6 | 0.833 | 215.0 |
| 2023-24 | playoffs | 82 | 0.585 | 211.2 |
| 2023-24 | cup_final | 1 | 1.000 | 232.0 |
| **2023-24** | **전체** | **1319** | **0.547** | **227.3** |
| 2024-25 | regular | 1230 | 0.544 | 227.7 |
| 2024-25 | playin | 6 | 0.500 | 223.3 |
| 2024-25 | playoffs | 84 | 0.571 | 217.5 |
| 2024-25 | cup_final | 1 | 0.000 | 178.0 |
| **2024-25** | **전체** | **1321** | **0.545** | **227.0** |
| 2025-26 | regular | 1230 | 0.554 | 231.2 |
| 2025-26 | playin | 6 | 0.667 | 224.7 |
| 2025-26 | playoffs | 85 | 0.553 | 215.9 |
| 2025-26 | cup_final | 1 | 1.000 | 237.0 |
| **2025-26** | **전체** | **1322** | **0.555** | **230.2** |
| 2026-27 | regular | 14 | 0.571 | 228.6 |
| **전체** | | **3976** | **0.549** | **228.2** |

상식 확인: 정규시즌 홈팀 승률 54~55%는 NBA의 알려진 홈 어드밴티지 범위이고, 2023-24 평균 총점 228.4는 그 시즌 리그 평균 팀 득점(약 114점)의 두 배와 맞는다. 플레이오프 총점이 정규보다 10~16점 낮은 것도 알려진 경향이다. 2024-25 NBA컵 결승(MIL 97 - OKC 81, 라스베이거스 중립 경기, 일정상 홈 OKC)이 총점 178로 튀는 것은 실제 결과다.

중립 경기장 경기(4-1)는 "홈"이 일정상 지정 홈 팀이다. 정규시즌에서 중립 경기 10경기를 빼고 계산해도 홈팀 승률은 2024-25 0.544, 2025-26 0.554로 셋째 자리까지 같다(일정 raw의 `isNeutral`로 걸러 확인).

### 3-3. 팀 스탯 행 수 = 종료 경기 × 2

```bash
docker compose exec postgres psql -U presagio -d presagio -c "SELECT g.season, count(*) FILTER (WHERE g.status_code=3 AND substr(g.game_id,3,1) IN ('2','4','5')) AS final_games, 2 * count(*) FILTER (WHERE g.status_code=3 AND substr(g.game_id,3,1) IN ('2','4','5')) AS expected_rows, (SELECT count(*) FROM team_game_stats t JOIN games x USING (game_id) WHERE x.season = g.season) AS stat_rows, (SELECT count(*) FROM team_game_stats_primary t JOIN games x USING (game_id) WHERE x.season = g.season) AS primary_rows FROM games g GROUP BY g.season ORDER BY g.season"
```

| season | 종료 경기(정규·플레이인·플레이오프) | 기대 행 | team_game_stats | team_game_stats_primary |
| --- | --- | --- | --- | --- |
| 2023-24 | 1318 | 2636 | 2636 | 2636 |
| 2024-25 | 1320 | 2640 | 2640 | 2640 |
| 2025-26 | 1321 | 2642 | 2642 | 2642 |
| 2026-27 | 14 | 28 | 28 | 28 |

NBA컵 결승(006)은 게임로그에 없어서 스탯이 없다. 박스스코어를 필요할 때만 받는 설계(D-023)라 정상이고, 품질 체크 `team_rows_per_game`도 이 경기를 뺀다.

### 3-4. 배당 표

```bash
docker compose exec postgres psql -U presagio -d presagio -c "SELECT (SELECT count(*) FROM odds_events) AS odds_events, (SELECT count(*) FROM odds_snapshots) AS odds_snapshots, (SELECT count(*) FROM odds_events e JOIN games g USING (game_id) WHERE g.season <> '2026-27') AS odds_events_past_seasons"
```

`odds_events` 41, `odds_snapshots` 796(10-22 사람 승인 호출 1회분, 전부 2026-27 경기), 지난 시즌 배당 0. 과거 배당 백필은 D-026 보류라 0이 정상이다.

## 4. 발견한 문제와 처리

### 4-1. 중립 경기장 경기 10개가 게임로그에서 거부됨 → 우리 버그, 수정함

- **증상:** run 7에서 2024-25, 2025-26 정규시즌 게임로그가 각 5경기 `matchup_ambiguous: cannot tell home/away: ['IND @ SAS', 'SAS @ IND']`로 거부되어 `partial`. 이 10경기는 팀 스탯이 0행이라 품질 체크 `team_rows_per_game`이 FAIL.
- **확인한 것(추측 아님):** 일정 raw(`ScheduleLeagueV2`)에서 10경기 모두 `isNeutral = true`였다.

| game_id | 장소 | 행사 |
| --- | --- | --- |
| 0022400147 | Mexico City | NBA Mexico City Game (WAS-MIA) |
| 0022400621, 0022400633 | Paris | NBA Paris Games (IND-SAS 2연전) |
| 0022401229, 0022401230 | Las Vegas | Emirates NBA Cup 동·서부 준결승 |
| 0022500147 | Mexico City | NBA Mexico City Game (DET-DAL) |
| 0022500578 | Berlin | NBA Berlin Game (ORL-MEM) |
| 0022500602 | London | NBA London Game (MEM-ORL) |
| 0022501229, 0022501230 | Las Vegas | Emirates NBA Cup 동·서부 준결승 |

  게임로그는 이런 경기에서 두 팀 행을 모두 원정 표기("X @ Y")로 준다. 우리 파서는 "vs."가 정확히 하나여야 홈을 정할 수 있어서 거부했다. 2023-24 일정 raw에는 `isNeutral = true`인 경기가 0개였고(필드는 있음), 그래서 그 시즌은 문제가 없었다.
- **원인 판단:** 소스 특성이 아니라 **우리 버그**다. D-034가 "홈/원정은 일정이 진실"이라고 정했고, 로더는 이미 홈/원정을 `games`에서 가져오고 점수와 스탯을 팀 ID로 붙인다. 그런데 파서가 그 전에 경기를 버렸다. 고치지 않으면 2026-27에 예정된 중립 경기 3개(파리, 맨체스터, 멕시코시티)가 매일 수집에서도 같은 이유로 막힌다.
- **수정(커밋 `e1adc9c`, `src/presagio/ingestion/sources/nba_api/schedule.py`의 `parse_game_log`):** 두 행이 서로 다른 두 팀이고 둘 다 "@"이면 거부하지 않는다. 팀 ID 순서로 방향을 정한 결과를 내고 `neutral_site` 이슈(`ingestion_runs.error_detail.quality_issues`)를 남긴다. 방향은 어차피 로더가 일정 기준으로 바로잡으므로(점수는 팀 ID로 매칭) 결과에 영향이 없다. 두 행이 모두 "vs."인 경우(관찰된 적 없음)는 기존대로 거부한다. 변경은 21줄.
- **테스트:** 3개 추가(파서, 팀 스탯 파서, DB 로더: 일정이 DEN 홈인 경기를 게임로그가 둘 다 "@"로 줘도 DEN이 `is_home`, 점수 119-107, run `succeeded`). 수정을 되돌리면 3개 모두 실패하는 것을 확인했다. 전체 `PRESAGIO_REQUIRE_DB=1 uv run pytest -q` 422 passed.
- **결과:** run 22에서 두 단위 모두 1230/0/1230 succeeded.
- **덤:** 수정 뒤 팀 ID 순서와 일정 홈이 다른 경기에는 `home_away_mismatch` 이슈도 함께 남는다(일정 유지라고 적힌 정보성 이슈). 운영에 영향은 없지만 이슈 목록이 조금 시끄럽다.

### 4-2. 2025-26 플레이오프 85경기의 팀 스탯 0행 → 개발 DB 이력 문제, `--force` 1회로 처리

- **증상:** 위 FAIL 95경기 중 85경기는 2025-26 플레이오프 전부.
- **원인:** run 3(2026-10-22 11:18 UTC)은 P-014 때 돌린 **게임로그 로더의 옛 버전**(점수만 갱신, 팀 스탯 저장 전)이었다. 그 run이 `succeeded`이고 시즌 종료 뒤에 시작했으므로 D-037 재개 규칙상 "끝난 단위"로 skip된다. 코드 버그가 아니라 "로더의 의미가 바뀌기 전에 성공한 run"이 남아 있던 개발 DB 이력 문제다. 운영 DB에서는 생기지 않는다(처음부터 지금 로더로 채우므로).
- **처리:** `uv run presagio backfill --season 2025-26 --season-type playoffs --force`(3호출: teams, 2025-26 일정, 플레이오프 게임로그). run 32에서 85/0/85, 255행.
- **부작용(실패 아님):** 이 명령이 2025-26 일정도 다시 받으면서 그 시즌 정규·플레이인 경기의 `games.source`가 `scheduleleaguev2`로 돌아갔다. 그래서 품질 체크 7번(`gamelog_vs_schedule_score`, "게임로그로 점수를 넣은 경기"만 비교)의 대상이 3,963경기에서 2,737경기로 줄었다. 점수는 일정에서 온 같은 값이라 틀린 것은 없다. 이후 `--force` 전체 실행(run 38, 61)이 게임로그를 다시 받아 정상으로 돌아왔다.

## 5. 멱등성 증거 (완료 기준 2)

같은 백필을 다시 돌려도 행이 늘지 않는다. A는 채우기와 보수가 끝난 시점, B는 일반 재실행(run 56: 끝난 시즌 13단위 skip, 진행 중 2026-27 4단위 다시 받음) 뒤, C는 `--force`로 끝난 시즌까지 17단위 전부 다시 받은(run 61) 뒤, D는 매일 수집 2회(run 79, 82) 뒤다.

```bash
docker compose exec postgres psql -U presagio -d presagio -c "SELECT 'teams' AS t, count(*) FROM teams UNION ALL SELECT 'team_aliases', count(*) FROM team_aliases UNION ALL SELECT 'games', count(*) FROM games UNION ALL SELECT 'team_game_stats', count(*) FROM team_game_stats UNION ALL SELECT 'odds_events', count(*) FROM odds_events UNION ALL SELECT 'odds_snapshots', count(*) FROM odds_snapshots UNION ALL SELECT 'raw_api_responses', count(*) FROM raw_api_responses UNION ALL SELECT 'ingestion_runs', count(*) FROM ingestion_runs"
```

| 표 | 시작 전 | A 채운 뒤 | B 재실행 후 (4호출) | C `--force` 후 (17호출) | D 매일 수집 2회 후 |
| --- | --- | --- | --- | --- | --- |
| teams | 30 | 30 | 30 | 30 | 30 |
| team_aliases | 90 | 90 | 90 | 90 | 90 |
| games | 2,529 | 5,169 | 5,169 | 5,169 | 5,169 |
| team_game_stats | 0 | 7,946 | 7,946 | 7,946 | 7,946 |
| odds_events | 41 | 41 | 41 | 41 | 41 |
| odds_snapshots | 796 | 796 | 796 | 796 | 796 |
| raw_api_responses | 6 | 22 | 22 | 22 | 24 |
| ingestion_runs | 6 | 55 | 60 | 78 | 83 |

시즌별로도 A·B·C가 모두 같다(경기 / 종료 / 팀 스탯 행): 2023-24 1319/1319/2636, 2024-25 1321/1321/2640, 2025-26 1322/1322/2642, 2026-27 1207/14/28.

읽는 법:
- `games`, `team_game_stats`는 upsert라 다시 받아도 그대로다. 끝난 시즌을 통째로 다시 받은 C에서도 같다.
- `raw_api_responses`는 `(request_hash, response_sha256)`가 유일 키라 **본문이 같은 응답은 새 행이 아니라 `fetch_count`만 오른다**. B·C에서 21번 다시 받았는데 행이 22 그대로인 이유다. D에서 2행 늘어난 것은 매일 수집이 처음 부르는 스코어보드 날짜 2개(동부 10-21, 10-22)의 응답이다.
- 다만 nba_api가 같은 내용을 행 순서만 바꿔 줄 때가 있어(raw 16↔45, 18↔24: 같은 요청, 같은 크기, 다른 sha256) `--force` 재실행(그리고 진행 중 시즌을 다시 받는 일반 재실행)에서 raw 행이 늘 수 있다. 이번에는 A 이전에 두 번 생겼다: raw 24(2026-27 정규 게임로그, run 22의 재개 실행), raw 45(2025-26 플레이인 게임로그, run 38의 `--force`). 그래서 **멱등성 판단 대상은 `games`·`team_game_stats` 행 수다.** raw는 "받은 원본을 그대로 남긴다"는 표라 늘어도 틀린 것이 아니다.
- `ingestion_runs`는 실행 기록이라 실행마다 느는 것이 정상이다(부모 1 + 단위마다 자식 1).

## 6. 품질 체크 (완료 기준 3)

### 6-1. 처리 전 (run 7 직후, 전체 시즌)

`Summary: 12 pass, 1 fail, 1 warn, 0 error, 0 skip -> exit code 1`

- `team_rows_per_game` **FAIL** 95경기: 중립 경기 10(4-1) + 2025-26 플레이오프 85(4-2)
- `recent_failed_runs` WARN 3: run 7(부모 partial), 13, 16(게임로그 partial). 같은 원인.

### 6-2. 처리 후

```bash
uv run presagio quality-check --season 2023-24
uv run presagio quality-check --season 2024-25
uv run presagio quality-check --season 2025-26
uv run presagio quality-check --season 2026-27
uv run presagio quality-check
```

| 범위 | pass | fail | warn | error | skip | 종료 코드 |
| --- | --- | --- | --- | --- | --- | --- |
| 2023-24 | 14 | 0 | 0 | 0 | 0 | 0 |
| 2024-25 | 14 | 0 | 0 | 0 | 0 | 0 |
| 2025-26 | 14 | 0 | 0 | 0 | 0 | 0 |
| 2026-27 | 14 | 0 | 0 | 0 | 0 | 0 |
| 전체 | 14 | 0 | 0 | 0 | 0 | 0 |

전체 범위 표(모든 백필과 매일 수집이 끝난 뒤, 마지막으로 돌린 값):

| # | 체크 | 상태 | 통과 | 실패 |
| --- | --- | --- | --- | --- |
| 1 | team_rows_per_game | PASS | 3973 | 0 |
| 2 | points_match_score | PASS | 3973 | 0 |
| 3 | stat_arithmetic | PASS | 7946 | 0 |
| 4 | final_scores_present | PASS | 3976 | 0 |
| 5 | regular_season_games | PASS | 4 | 0 |
| 6 | one_game_per_team_day | PASS | 10324 | 0 |
| 7 | gamelog_vs_schedule_score | PASS | 3962 | 0 |
| 8 | primary_stats_source | PASS | 3973 | 0 |
| 9 | teams_and_aliases | PASS | 30 | 0 |
| 10 | odds_after_start | PASS | 796 | 0 |
| 11 | odds_unmatched_events | PASS | 41 | 0 |
| 12 | odds_events_per_game | PASS | 41 | 0 |
| 13 | past_games_not_final | PASS | 3965 | 0 |
| 14 | recent_failed_runs | PASS | 83 | 0 |

메모: 5번 노트 "2026-27: in progress, 1206 incl. scheduled (14 final)"(진행 중 시즌은 실패로 치지 않음, D-040). 14번 노트 "3 redone by a later succeeded run"(6-1의 partial 3개가 나중에 성공으로 덮임). 7번 통과 수는 시점마다 다르다. run 29 직후에는 4-2의 부작용으로 2,737이었고, `--force` 전체 실행이 게임로그를 다시 넣은 뒤에는 3,973이어야 하고(그 시점에는 체크를 돌리지 않아 계산값), 매일 수집 뒤 실제로 돌린 값이 3,962다(동부 10-21의 11경기는 이제 스코어보드가 점수를 넣은 경기라 "게임로그로 점수를 넣은 경기"에서 빠진다). 어느 시점에도 실패는 0이다. 매일 수집의 `--date 2026-10-21` 범위 체크는 13 pass, 1 skip(5번 시즌 경기 수는 하루 범위에서 의미 없음, 설계대로).

## 7. 호출 수, 시간, 차단·재시도 (완료 기준 4)

부모 run의 `error_detail.totals`:

```bash
docker compose exec postgres psql -U presagio -d presagio -c "SELECT run_id, scope_key, status, started_at, finished_at, round(extract(epoch FROM finished_at - started_at)::numeric, 1) AS seconds, error_detail->'totals'->>'api_calls' AS api_calls, error_detail->'totals'->>'retries' AS retries, error_detail->'totals'->>'rows_written' AS rows_written, code_version FROM ingestion_runs WHERE job_type = 'backfill' ORDER BY run_id"
```

| run | 무엇 | status | 시작 (UTC) | 끝 (UTC) | 초 | 호출 | 재시도 | 쓴 행 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 7 | 1차 백필 | partial | 14:52:22.4 | 14:52:39.1 | 16.7 | 14 | 0 | 15,481 |
| 22 | 재개 | succeeded | 14:56:29.6 | 14:56:36.5 | 6.9 | 6 | 0 | 8,629 |
| 29 | 2025-26 플레이오프 `--force` | succeeded | 14:56:47.3 | 14:56:50.8 | 3.6 | 3 | 0 | 1,667 |
| 33 | 재개 | succeeded | 14:57:31.8 | 14:57:35.9 | 4.1 | 4 | 0 | 1,249 |
| 38 | `--force` 전체 | succeeded | 14:57:37.3 | 14:57:57.8 | 20.5 | 17 | 0 | 17,178 |
| 56 | 재개 | succeeded | 14:58:13.5 | 14:58:18.0 | 4.5 | 4 | 0 | 1,249 |
| 61 | `--force` 전체 | succeeded | 14:58:19.7 | 14:58:39.1 | 19.4 | 17 | 0 | 17,178 |
| **합계** | | | | | **75.7** | **65** | **0** | |

매일 수집 2회(run 79, 82)가 3호출을 더해 **이 티켓 전체 nba_api 68호출**, 재시도 0, 실행 시간 합 약 78초다. 첫 호출(14:52:22)부터 마지막 매일 수집 종료(14:59:51)까지는 7분 30초인데, 대부분은 내가 결과를 읽고 버그를 고친 시간이다. "4시즌을 비어 있는 DB에 처음부터 채우는 데" 드는 시간은 `--force` 전체 실행(run 38, 61)이 가장 가깝다. **17호출, 약 20초**다(dry-run 예상 38초보다 빠름: 실제 응답이 가정한 2.5초·1.5초보다 짧았다).

차단·재시도:

```bash
docker compose exec postgres psql -U presagio -d presagio -c "SELECT http_status, count(*) AS raw_rows, sum(fetch_count) AS fetches FROM raw_api_responses GROUP BY 1"
docker compose exec postgres psql -U presagio -d presagio -c "SELECT count(*) AS child_runs, sum(api_calls) AS api_calls, sum(retry_count) AS retries, count(*) FILTER (WHERE status = 'aborted') AS aborted FROM ingestion_runs WHERE run_id >= 7"
```

- raw 24행 전부 HTTP 200, `fetch_count` 합 74 = 시작 전 6 + 이번 68. **403·429·5xx·타임아웃 0건.**
- `aborted` run 0, 종료 코드 2 없음, 재시도(`retry_count`) 합 0.
- 요청 간격은 기본 1.0초(D-024, 앞 응답이 끝난 때부터 잰다. 그 사이 DB 쓰기 시간도 대기에 포함된다). 단위 하나(1호출 + 대기 + 적재)는 0.2~2.0초 걸렸다.

용량:

```bash
docker compose exec postgres psql -U presagio -d presagio -c "SELECT count(*) AS raw_rows, pg_size_pretty(pg_total_relation_size('raw_api_responses')) AS raw_total_size, pg_size_pretty(pg_total_relation_size('games')) AS games_size, pg_size_pretty(pg_total_relation_size('team_game_stats')) AS stats_size, pg_size_pretty(pg_database_size('presagio')) AS db_size FROM raw_api_responses"
```

| 항목 | 시작 전 | 끝난 뒤 |
| --- | --- | --- |
| raw_api_responses 행 | 6 | 24 |
| raw_api_responses 전체 크기(인덱스·TOAST 포함) | 1,640 kB | 4,680 kB |
| 응답 본문 합(`response_bytes`, 압축 전) | | 일정 5행 20 MB, 게임로그 14행 1.2 MB, 스코어보드 2행 35 kB(매일 수집), 팀 2 kB, 배당 91 kB |
| games / team_game_stats | | 4.4 MB / 3.8 MB |
| DB 전체 | 12 MB | 21 MB |

일정 JSON(시즌당 약 4.4 MB)이 PostgreSQL TOAST 압축으로 약 1/5로 줄어 저장된다.

## 8. 매일 흐름 확인 (채워진 DB 위에서)

- `uv run presagio ingest-daily --date 2026-10-22 --dry-run`: 팀 skip(run 62에서 168시간 안에 갱신), 어제(동부 10-21) 결과 1호출, 게임로그 skip(백필로 이미 그날 경기가 모두 종료), 오늘(동부 10-22) 일정 1호출, 배당 skip(`ODDS_API_KEY` 없음), 품질 체크. "2 call(s)".
- 실제 1회(run 79): 종료 0, nba_api 2호출, 어제 11경기 succeeded, 오늘 2경기 succeeded, 배당 `skipped`, 품질 체크 13 pass 1 skip.
- 같은 명령 2회째(run 82): 어제 결과는 "done; last succeeded (run 80)"으로 skip, 오늘 일정만 1호출. 경기·스탯 행 수 그대로(5장 D열).
- 이 실행은 한국 시각 10-22 23:59에 돌았다. 보통 시각(16:00 KST, D-038)이 아니라서 `--date`로 날짜를 고정했다.

## 9. 사람이 직접 돌려볼 명령

전부 레포 루트에서. 외부 호출이 없는 것(1~3)부터 보면 된다.

```bash
# 1) DB가 떠 있는지
docker compose up -d --wait

# 2) 표별 행 수 (5장 D열과 같아야 한다: games 5169, team_game_stats 7946)
docker compose exec postgres psql -U presagio -d presagio -c "SELECT 'games' AS t, count(*) FROM games UNION ALL SELECT 'team_game_stats', count(*) FROM team_game_stats UNION ALL SELECT 'raw_api_responses', count(*) FROM raw_api_responses"

# 3) 품질 체크 (외부 호출 0, 읽기만 함). 14 pass, exit code 0
uv run presagio quality-check

# 4) 3장 쿼리 3개를 위 코드 블록에서 복사해 실행

# 5) 다음 백필이 무엇을 할지 (외부 호출 0). 끝난 시즌은 skip, 2026-27만 run 4개
uv run presagio backfill --season 2023-24 --season 2024-25 --season 2025-26 --season 2026-27 --dry-run

# 6) (선택, nba_api 4호출) 실제 재실행 후 2)를 다시 돌려 숫자가 같은지
uv run presagio backfill --season 2023-24 --season 2024-25 --season 2025-26 --season 2026-27
```

## 10. 못 한 것 / 한계

- **배당 과거 백필 없음**(D-026 보류). `odds_*`는 10-22의 실제 호출 1회분(2026-27 41경기)뿐이다. 단계 3~4의 "시장과 비교"는 지금 데이터로는 2026-27 앞으로 쌓이는 경기만 가능하다.
- **중립 경기 여부가 DB에 없다.** raw에만 있다. `isNeutral = true`: 2024-25·2025-26 정규 각 5, NBA컵 결승 각 1, 2026-27 예정 3. 2023-24는 `isNeutral`이 전부 false라 `arenaCity`/`gameLabel`로만 찾을 수 있다: 정규 4경기(`0022300172` 멕시코시티, `0022300527` 파리, `0022301229`·`0022301230` 라스베이거스 인시즌 토너먼트 준결승). 홈 어드밴티지 피처(단계 2)는 이 경기들에서 틀린다. 영향은 작다(3-2: 승률 셋째 자리까지 같음). PR 본문의 결정 제안 3 참고.
- **2023-24 일정은 `isNeutral`이 모두 false**였다(멕시코시티·파리 경기와 인시즌 토너먼트 준결승이 있었던 시즌인데도). 그 시즌 게임로그는 "vs."로 줘서 적재에는 문제가 없었다. 중립 여부는 `isNeutral`로는 알 수 없고 `arenaCity`/`gameLabel`로 위 4경기를 찾을 수 있다.
- 4-1 수정 뒤에도 두 행이 모두 "vs."인 경기는 여전히 거부된다(관찰된 적 없어서 그대로 둠).
- 개발 DB에만 있던 이력 문제(4-2)는 `--force` 1회로 처리했다. 같은 일이 다른 개발 환경에서 생기면 같은 방법으로 처리한다.
- 박스스코어(경기별) 백필은 하지 않았다(D-023, 기본 스탯은 게임로그로 충분). NBA컵 결승 3경기는 팀 스탯이 없다.
- 클라우드 IP에서의 차단 여부는 여전히 미확인이다(단계 6에서 처음 돌릴 때 확인, `docs/research/nba_api.md` 4장).

## 11. 쉬운 말 요약

NBA 공식 통계 사이트에서 지난 세 시즌과 이번 시즌 경기 5,169개와 팀 경기 기록 7,946줄을 받아 개발용 DB에 넣었다. 처음 돌렸을 때 해외·중립 경기장 경기 10개가 빠졌다. 그 경기들은 "어느 팀이 홈인지"가 통계 자료에 이상하게 적혀 있었기 때문인데, 우리는 원래 일정표를 믿기로 했으니 그 기준으로 받도록 코드를 고쳤다. 고친 뒤에는 데이터 검사 14개가 모두 통과했고, 같은 명령을 다시 돌려도 행 수가 하나도 늘지 않았다. 사이트에 요청한 횟수는 모두 68번이었고 막히거나 다시 시도한 적은 한 번도 없었다.
