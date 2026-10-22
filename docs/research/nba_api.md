# nba_api 엔드포인트 조사 보고서 (P-010)

- **작성:** Data Engineer, 2026-10-22
- **라이브러리:** `nba_api` **1.11.6** (dev 의존성 그룹에만 추가. 런타임 의존성 여부는 P-014에서 결정)
- **함께 설치된 것:** pandas 3.0.6, numpy 2.5.4, requests 2.34.2 (nba_api가 끌고 옴)
- **확인 방법:** `scripts/explore_nba_api.py`로 실제 호출 **40번**, 라이브러리 소스(`.venv/.../nba_api/`) 직접 읽기. 아래 내용은 전부 이 두 가지로 확인한 것이다. 확인하지 못한 것은 "(미확인)"이라고 적었다.
- **응답 샘플:** `tests/fixtures/nba_api/*.json` (21개, 합계 328KB, 파일당 최대 36KB)

## 0. 한눈에 보는 결론

1. **일정과 결과**는 `ScheduleLeagueV2`를 시즌당 **1번** 부르면 다 온다(프리시즌~결승, 약 1,400경기, UTC 시각 포함).
2. **팀 박스스코어(기본 스탯)**는 `LeagueGameLog`를 시즌당 **3번**(정규/플레이인/플레이오프) 부르면 전 경기 양 팀 행이 다 온다. 경기마다 부르는 `BoxScoreTraditionalV3`는 시즌당 약 1,320번이 필요하다. 그래서 백필은 "시즌 단위 엔드포인트 위주"로 하면 수천 번이 아니라 **시즌당 4번**으로 끝난다.
3. 40번 호출(최소 간격 0.6초 포함)에서 **차단·403·429·타임아웃은 한 번도 없었다.** 그래도 표본이 작으니 권장 간격은 **1.0초**로 둔다(4장).

## 1. 용도별 엔드포인트 표 (완료 기준 1)

호출 수는 "한 시즌(2025-26 기준: 정규 1,230 + 플레이인 6 + 플레이오프 85경기)"을 다 받는 데 필요한 수다.

| 용도 | 엔드포인트 (nba_api 클래스) | 필수 파라미터 | 응답의 주요 컬럼 | 한 시즌 호출 수 | 실측 응답 |
| --- | --- | --- | --- | --- | --- |
| 팀 목록 | `nba_api.stats.static.teams.get_teams()` (네트워크 없음, 라이브러리에 내장) | 없음 | `id`, `full_name`, `abbreviation`, `nickname`, `city`, `state`, `year_founded` | **0** | 30팀 |
| 팀 목록 (API) | `stats.endpoints.commonteamyears.CommonTeamYears` | `league_id`(기본 `"00"`) | `LEAGUE_ID`, `TEAM_ID`, `MIN_YEAR`, `MAX_YEAR`, `ABBREVIATION` | **1** (전체 역사 한 번) | 45행, 그중 `MAX_YEAR == "2026"`인 현역 30팀. 내장 목록 30팀과 ID가 정확히 같음 |
| 시즌 일정 + 결과 | `stats.endpoints.scheduleleaguev2.ScheduleLeagueV2` | `season`(예 `"2025-26"`), `league_id`(기본 `"00"`) | `gameId`, `gameCode`, `gameStatus`(1 예정, 3 종료), `gameStatusText`, `gameDateTimeUTC`, `gameDateTimeEst`, `gameLabel`, `gameSubLabel`, `gameSubtype`, `seriesGameNumber`, `seriesText`, `ifNecessary`, `postponedStatus`, `isNeutral`, `arenaName`, `homeTeam{teamId, teamTricode, score, wins, losses, seed}`, `awayTeam{...}` | **1** | 4.0~4.4MB, 약 2초. 2023-24 1,396경기 / 2024-25 1,400 / 2025-26 1,400 / 2026-27 1,274(현재까지 발표분) |
| 시즌 결과 (팀 행 단위) + 팀 박스스코어 | `stats.endpoints.leaguegamelog.LeagueGameLog` | `season`, `season_type_all_star`(`"Regular Season"` / `"PlayIn"` / `"Playoffs"`), `player_or_team_abbreviation="T"`(팀) | `SEASON_ID`, `TEAM_ID`, `TEAM_ABBREVIATION`, `GAME_ID`, `GAME_DATE`, `MATCHUP`(`"DEN vs. LAL"`=홈, `"LAL @ DEN"`=원정), `WL`, `MIN`, `FGM`, `FGA`, `FG3M`, `FG3A`, `FTM`, `FTA`, `OREB`, `DREB`, `REB`, `AST`, `STL`, `BLK`, `TOV`, `PF`, `PTS`, `PLUS_MINUS` | **3** (시즌 유형별 1번) | 정규 2,460행(=1,230경기×2), 플레이오프 164~170행, 플레이인 12행. 390KB, 0.2~1.4초 |
| 팀 박스스코어 (경기 단위) | `stats.endpoints.boxscoretraditionalv3.BoxScoreTraditionalV3` | `game_id`(예 `"0022300061"`) | `boxScoreTraditional.homeTeam/awayTeam.statistics{fieldGoalsMade, fieldGoalsAttempted, threePointersMade, ..., reboundsOffensive, reboundsDefensive, assists, steals, blocks, turnovers, foulsPersonal, points, plusMinusPoints}`, `starters`, `bench`, `players[]` | **약 1,320** (경기당 1번) | 18~23KB, 0.2~0.3초 |
| 경기 요약 (쿼터 점수, 관중, 심판) | `stats.endpoints.boxscoresummaryv3.BoxScoreSummaryV3` | `game_id` | `gameTimeUTC`, `gameEt`, `gameStatus`, `period`, `duration`, `attendance`, `homeTeam.periods[{period, periodType, score}]`, `officials`, `arena` | 약 1,320 (필요할 때만) | 22KB |
| 날짜별 일정·결과 (매일 증분용) | `stats.endpoints.scoreboardv3.ScoreboardV3` | `game_date`(`"YYYY-MM-DD"`, 미국 동부 날짜) | `scoreboard.gameDate`, `games[{gameId, gameStatus, gameStatusText, gameTimeUTC, gameEt, period, homeTeam{teamId, score, wins, losses}, awayTeam{...}}]` | 시즌 날짜 수(약 170~240) | 5~41KB, 0.2~0.3초 |
| 오늘 경기 (라이브) | `live.nba.endpoints.scoreboard.ScoreBoard` (`cdn.nba.com`) | 없음 | `scoreboard.gameDate`, `games[{gameId, gameStatus, gameTimeUTC, gameEt, homeTeam, awayTeam}]` | 해당 없음 | 35KB, 0.07초 |
| (참고) 경기 검색 | `stats.endpoints.leaguegamefinder.LeagueGameFinder` | 필수 없음. 이번엔 `player_or_team_abbreviation="T"`, `season_nullable`, `season_type_nullable`, `league_id_nullable="00"` | `LeagueGameLog`와 같은 컬럼(순서 다름, `VIDEO_AVAILABLE` 없음) | 3 | 2025-26 플레이오프 170행, `GAME_ID` 집합이 `LeagueGameLog`와 같음 |

**3시즌 백필 호출 수 추정**
- 시즌 단위 엔드포인트만 쓰면: 3시즌 × (`ScheduleLeagueV2` 1 + `LeagueGameLog` 3) = **12번**, 1분 안쪽.
- 경기별 `BoxScoreTraditionalV3`까지 받으면: 3시즌 × 약 1,320 = **약 3,960번**. 호출 1번이 "대기 1.0초 + 응답 약 0.25초"라서 **약 80분**.

## 2. 시즌 데이터가 실제로 조회됨 (완료 기준 2)

모든 대상 시즌에서 정규시즌과 플레이오프가 실제로 응답에 있었다. 샘플은 전부 `tests/fixtures/nba_api/`에 있다.

| 시즌 | 정규시즌 증거 | 플레이오프 증거 | 일정 픽스처 |
| --- | --- | --- | --- |
| 2023-24 | `leaguegamelog__2023-24_RegularSeason.json`: `0022300061` DEN 119 vs LAL 107 (2023-10-24), 전체 2,460행 | `leaguegamelog__2023-24_Playoffs.json`: `0042300111` NYK 111 vs PHI 104, 전체 164행 | `scheduleleaguev2__2023-24.json` |
| 2024-25 | `leaguegamelog__2024-25_RegularSeason.json`: `0022400061` BOS 132 vs NYK 109, 2,460행 | `leaguegamelog__2024-25_Playoffs.json`: `0042400121` NYK 123 vs DET 112, 168행 | `scheduleleaguev2__2024-25.json` |
| 2025-26 | `leaguegamelog__2025-26_RegularSeason.json`: `0022500001` OKC 125 vs HOU 124, 2,460행 | `leaguegamelog__2025-26_Playoffs.json`: `0042500121` NYK 113 vs ATL 102, 170행. 플레이인 `leaguegamelog__2025-26_PlayIn.json` 12행. 박스스코어 `boxscoretraditionalv3__0042500121.json` | `scheduleleaguev2__2025-26.json` |
| 2026-27 (현재) | `leaguegamelog__2026-27_RegularSeason.json`: `0022600001` DET 121 vs BOS 113 (2026-10-20), 조회 시점 28행(=14경기 종료). 박스스코어 `boxscoretraditionalv3__0022600001.json`, 오늘 일정 `scoreboardv3__2026-10-22.json`(2경기 예정) | 아직 없음(시즌 진행 중) | `scheduleleaguev2__2026-27.json` (종료 2, 예정 1, 팀 미정 1, NBA컵 결승 1) |

일정 응답 전체로 센 경기 수(잘라내기 전):

| 시즌 | 001 프리시즌 | 002 정규 | 003 올스타 | 004 플레이오프 | 005 플레이인 | 006 NBA컵 결승 |
| --- | --- | --- | --- | --- | --- | --- |
| 2023-24 | 73 | 1,230 | 4 | 82 | 6 | 1 |
| 2024-25 | 73 | 1,230 | 6 | 84 | 6 | 1 |
| 2025-26 | 71 | 1,230 | 7 | 85 | 6 | 1 |
| 2026-27 | 67 | 1,206 (발표분) | 0 | 0 | 0 | 1 (예정) |

## 3. 경기 ID, 시즌, 경기 유형, 시간대 (완료 기준 3)

### 3-1. 경기 ID 체계
- **10자리 문자열**이고 앞에 0이 붙는다(`"0022300061"`). 숫자로 바꾸면 앞자리 0이 사라지니 **문자열로 다룬다.**
- 구조: `00` + **유형 1자리** + **시즌 시작 연도 2자리** + **번호 5자리**.
  - 유형: `1` 프리시즌, `2` 정규시즌, `3` 올스타, `4` 플레이오프, `5` 플레이인, `6` NBA컵 결승.
  - 연도: `23` = 2023-24 시즌. 4시즌 모든 경기에서 확인(예외 0건).
- 정규시즌 번호(`00001`~`01230`)는 **날짜 순서가 아니다.** 2023-24 개막 경기가 `0022300061`이었다. 정렬은 반드시 `gameDateTimeUTC`로 한다.
- 끝난 시즌은 `01201`~`01230`번까지 있고, 그중 일부가 NBA컵 8강·4강이다(2024-25: `01201`~`01204`, `01229`, `01230`). 나머지는 컵 조별리그 뒤에 일정이 채워진 경기로 보인다(추정). 2026-27은 지금 `01205`~`01228` 중 24개 번호가 비어 있다(아직 발표 안 된 경기로 보임, 추정).
- 플레이오프 번호는 `00` + 라운드 1자리 + 시리즈 1자리 + 게임 1자리다(예 `0042300111` = 1라운드, 시리즈 1, 1차전). 2024-25에서 84경기 전부 "게임 자리 = `seriesGameNumber`", "라운드+시리즈 자리 = 한 매치업"이 맞았다. 플레이인도 같은 모양(`0052400101`, `0052400201`)이다.

### 3-2. 시즌 구분
- 요청 파라미터: `season="2023-24"` 문자열.
- 응답 안에서 세 가지 표기가 섞여 있다.
  - `ScheduleLeagueV2`: `leagueSchedule.seasonYear = "2023-24"`
  - `LeagueGameLog`: `SEASON_ID = "22023"` (= 유형 1자리 + 시작 연도 4자리. 플레이오프는 `"42023"`, 플레이인은 `"52025"`)
  - 경기 ID의 3~4번째 자리 `23`
- 주의: nba_api의 기본값 `Season.default`는 **import한 순간의 내 컴퓨터 시계**로 계산된다(`parameters.py`의 `SeasonYear`, 9월 이전이면 작년). 어댑터에서는 시즌을 항상 명시해서 넘긴다.

### 3-3. 경기 유형(정규/플레이오프) 구분
- 가장 확실한 방법은 **경기 ID 3번째 자리**(위 3-1). 4시즌 모든 경기에서 일관됐다.
- `LeagueGameLog`는 요청할 때 `season_type_all_star`로 나눠서 받는다. `"Regular Season"` 응답의 `GAME_ID` 집합은 일정의 `002` 경기 1,230개와 **정확히 같았고**, NBA컵 결승(`006`)은 정규에도 플레이오프에도 **들어 있지 않았다**(공식 정규시즌 기록에 안 들어가는 경기라서로 보임).
- NBA컵 8강·4강은 `002`(정규시즌 기록에 포함)이고 `gameSubtype = "in-season-knockout"`, 조별리그는 `gameSubtype = "in-season"`, `gameLabel = "Emirates NBA Cup"`으로 표시된다.
- `gameLabel` 문자열은 시즌마다 바뀐다(`"East - First Round"` → `"East First Round"`, `"West Play-In"` → `"SoFi Play-In Tournament"`). **유형 판단에 쓰면 안 된다.**

### 3-4. 시간대와 UTC 변환
- **`gameDateTimeUTC`(일정), `gameTimeUTC`(스코어보드·요약)가 진짜 UTC다.** 예: `"2023-10-24T23:30:00Z"`. 그대로 `datetime.fromisoformat()`으로 읽으면 된다.
- **함정:** `gameDateTimeEst`, `gameEt`는 **미국 동부 현지 시각인데 끝에 `Z`가 붙어 있다**(`"2023-10-24T19:30:00Z"`). 이걸 UTC로 읽으면 4~5시간 틀린다. 쓰지 않는다.
- 검증: 4시즌 5,470경기 전부 `UTC → America/New_York 변환 == gameDateTimeEst`(서머타임 포함, 불일치 0건), 그리고 일정의 날짜 묶음(`gameDates[].gameDate = "10/24/2023 00:00:00"`)은 **동부 날짜**였다(불일치 0건). `ScoreboardV3(game_date=...)`도 동부 날짜를 받는다.
- **시각 미정 경기:** 2026-27의 NBA컵 8강 같은 미정 경기는 `gameStatusText = "TBD"`, `gameDateTimeUTC = "2026-12-04T05:00:00Z"`(= 동부 자정), 팀 ID `0`으로 온다. 이 05:00Z는 진짜 시작 시각이 아니다.
- 저장 규칙 제안: `gameDateTimeUTC`를 UTC로 저장하고, 한국시간/동부시간은 조회할 때 `zoneinfo`로 바꾼다(P-011 완료 기준 4와 같음).

## 4. 요청 간 딜레이 권장값과 근거 (완료 기준 4)

### 실제로 한 것 (2026-10-22 07:45~07:47 UTC, 이 맥의 집 네트워크, 기본 헤더)

| 묶음 | 호출 수 | 설정한 간격 | 실제 호출 시작 간격 | 응답 시간 | 결과 |
| --- | --- | --- | --- | --- | --- |
| 팀·일정 | 5 | 1.0초 | 1.4~3.4초 | 0.4~2.4초 (일정 4MB) | 전부 200 |
| 게임로그·박스스코어·스코어보드 | 15 | 1.0초 | 1.2~2.4초 | 0.07~1.4초 | 전부 200 |
| 속도 시험 A (ScoreboardV3 날짜 10개) | 10 | 1.0초 | 1.2~1.3초 | 0.18~0.31초 | 전부 200 |
| 속도 시험 B (ScoreboardV3 다른 날짜 10개) | 10 | **0.6초** | 0.78~0.88초 | 0.18~0.28초 | 전부 200 |
| **합계** | **40** | | | | **403·429·타임아웃·JSON 오류 0건** |

- "간격"은 앞 응답이 끝난 뒤부터 잰 대기 시간이다(스크립트의 `Runner.call`). 호출 로그는 `data/explore_nba_api/call_log_*.json`(gitignore)에 남는다.
- 같은 요청 반복이 캐시를 맞지 않게, 속도 시험 A와 B는 서로 다른 날짜를 불렀다.
- 타임아웃은 10초로 걸었다. 가장 느린 응답은 일정 4.4MB의 2.35초였다.

### 라이브러리 소스에서 확인한 것
- nba_api는 **딜레이도 재시도도 하지 않는다.** `library/http.py`의 `send_api_request`는 `requests.Session.get` 한 번이 전부다.
- 모든 엔드포인트의 기본 `timeout=30`초다. 우리 원칙(10초)과 다르므로 **항상 직접 넘겨야 한다.**
- 기본 헤더는 브라우저처럼 보이는 `User-Agent`, `Referer: https://www.nba.com/` 등이다(`stats/library/http.py`의 `STATS_HEADERS`). 개인정보는 없다. 이번에는 바꾸지 않았다.
- 세션(`requests.Session`)이 클래스 단위로 공유된다(`NBAHTTP._session`). 연결 재사용이 된다는 뜻.

### 권장값
- **요청 간 1.0초**(원칙의 최소 0.6초보다 여유), 타임아웃 10초, 실패 시 지수 백오프 3회(예: 2초, 4초, 8초), **403/429가 오면 재시도 없이 그 실행을 멈춘다**, 연속 실패 2번이면 멈춘다.
- 근거: 0.6초 간격 10번 연속에서도 아무 문제가 없었으니 0.6초도 "지금은" 된다. 하지만 표본이 40번뿐이고 경기별 백필은 수천 번이라 같은 결과가 나온다는 보장이 없다. 1.0초로 해도 시즌 단위 백필은 1분 안쪽, 경기별 박스스코어 3시즌도 약 80분이라 충분히 감당된다.
- 미확인: 몇 번·몇 초 간격부터 차단되는지는 시험하지 않았다(차단을 일부러 유도하는 것은 원칙 4 위반). 클라우드 IP(단계 6의 배포 환경)에서 막히는지도 미확인이다. 단계 6에서 처음 돌릴 때 따로 확인해야 한다.

## 5. nba_api가 막혔을 때 대체 소스 후보 (완료 기준 5)

| 후보 | 무엇을 주나 | 확인 상태 |
| --- | --- | --- |
| `cdn.nba.com` 정적 JSON (nba_api `live` 모듈이 쓰는 곳) | 오늘 스코어보드, 경기별 박스스코어·플레이바이플레이 | `live_scoreboard__todaysScoreboard_00.json`을 실제로 받음(0.07초). `stats.nba.com`과 호스트가 달라 한쪽만 막힐 수 있다. 과거 시즌 전체 일정 제공 여부는 미확인 |
| ESPN 공개(비공식) JSON API (`site.api.espn.com`의 NBA scoreboard) | 날짜별 일정·결과·간단한 박스스코어 | 이름만. 호출 안 함 |
| balldontlie API | 경기·결과·팀 스탯 REST API (API 키 필요, 무료 한도 있음) | 이름만. 키가 필요하므로 비용·약관은 사람 결정 |
| Basketball-Reference | 시즌별 일정·결과·박스스코어 HTML | 이름만. 스크래핑 약관이 엄격해서 마지막 수단 |

어댑터 인터페이스(`docs/team/risks.md` B1)를 "시즌 일정", "날짜별 결과", "경기별 팀 스탯" 단위로 나눠 두면 위 후보 어느 것으로도 바꿀 수 있다.

## 6. P-011 스키마 설계에 영향 줄 관찰

1. **경기 ID는 10자리 문자열(앞자리 0)이다.** 정수 컬럼이면 앞자리가 사라진다. 경기 유형은 ID 3번째 자리로 정해지고, 값이 6가지(프리시즌/정규/올스타/플레이오프/플레이인/NBA컵 결승)다. NBA컵 결승(`006`)은 정규시즌 게임로그에 안 들어가므로, 우리 `games`에 넣을지(그리고 모델 학습에 쓸지) 정해야 한다.
2. **시각은 `gameDateTimeUTC`만 믿는다.** `*Est`/`gameEt`는 가짜 `Z`가 붙은 동부 현지 시각이다. 시각 미정 경기는 `TBD` + 동부 자정(관찰값 05:00Z) + 팀 ID `0`으로 오므로, `games`에 "시각 미정" 표시와 NULL 가능한 팀 ID(또는 미정 경기는 저장 안 함 규칙)가 필요하다. 현재 시즌은 아직 발표 안 된 경기(번호 24개 빈칸)가 나중에 추가되므로 upsert + `fetched_at`이 꼭 필요하다.
3. **같은 스탯이 엔드포인트마다 조금 다르다.** 샘플 3경기 중 1경기(`0022300061`)에서 `LeagueGameLog`의 `TOV`=12, `BoxScoreTraditionalV3`의 `turnovers`=11이었다(박스스코어는 선수 턴오버 합과 같음. 게임로그는 팀 턴오버까지 포함한 값으로 보임, 추정). 나머지 15개 스탯은 6개 팀 행 모두 일치. 또 `BoxScoreTraditionalV3`의 `bench.minutes`는 6개 팀 행 전부 `starters.minutes`와 같은 값이 나와 신뢰할 수 없다. → `team_game_stats`에 행마다 `source`(어느 엔드포인트인지)를 남기고, 포제션 추정 공식에 쓸 `TOV`의 출처를 하나로 정해야 한다.

그 밖에 알아 둘 것:
- 같은 뜻의 값이 엔드포인트마다 타입이 다르다: `ifNecessary`가 일정에서는 문자열 `"false"`, 스코어보드·요약에서는 불리언 `false`.
- 홈/원정은 `LeagueGameLog`에서는 `MATCHUP` 문자열(`vs.` = 홈, `@` = 원정)로만 알 수 있다. 일정·박스스코어는 `homeTeam`/`awayTeam`으로 명확하다.
- v3 응답의 `meta.time`은 원천 데이터가 만들어진 시각이다. 2023-10-24 경기의 박스스코어가 `2025-02-17`로 찍혀 있었다. **과거 경기 기록도 나중에 고쳐질 수 있다**는 뜻이므로 raw 응답에 이 값을 같이 남기면 좋다.
- 라이브 스코어보드의 "오늘"은 미국 동부 기준이고, 동부 새벽 3시 46분에 받았을 때 아직 전날(`2026-10-21`)이었다. 날짜는 `ScoreboardV3(game_date=...)`로 명시해서 받는 편이 안전하다.

## 7. 픽스처를 어떻게 잘랐나

`uv run python scripts/explore_nba_api.py --phase fixtures`가 `data/explore_nba_api/`의 원본을 읽어 아래 규칙으로 잘라 `tests/fixtures/nba_api/`에 쓴다(네트워크 안 씀). **JSON 구조와 키는 원본 그대로**이고 리스트 길이만 줄였다. 들여쓰기 1칸으로 저장했다.

| 픽스처 | 원본 크기 | 자른 방법 |
| --- | --- | --- |
| `scheduleleaguev2__2023-24/2024-25/2025-26.json` | 각 4.4MB, 약 1,400경기 | 날짜 순서로 프리시즌·정규·NBA컵 결승·플레이인·플레이오프 경기를 **유형마다 첫 1경기**만 남김(5경기). 경기 객체 안(방송사, 득점 1위 등)은 그대로. `weeks`는 앞 2개만 |
| `scheduleleaguev2__2026-27.json` | 4.0MB, 1,274경기 | 종료된 정규 2경기, 예정 1경기, 팀 미정(TBD) 1경기, NBA컵 결승 1경기. `weeks` 앞 2개 |
| `leaguegamelog__<시즌>_<유형>.json` (8개) | 정규 390KB 등 | `rowSet`에서 **처음 나온 `GAME_ID` 2개의 행만**(경기당 홈·원정 2행 → 4행). `headers`, `parameters`는 그대로 |
| `leaguegamefinder__2025-26_Playoffs.json` | 34KB | 같은 방법, 2경기 4행 |
| `boxscoretraditionalv3__*.json`(3), `boxscoresummaryv3__0022300061.json`, `scoreboardv3__*.json`(2), `commonteamyears__00.json`, `live_scoreboard__todaysScoreboard_00.json` | 2~45KB | 자르지 않음(원본 그대로) |

`static.teams.get_teams()`는 API 응답이 아니라 라이브러리 내장 데이터라 픽스처로 넣지 않았다.

## 8. 다시 돌리는 법

```bash
uv sync
uv run python scripts/explore_nba_api.py                       # 전체 약 40번 호출 + 픽스처 생성
uv run python scripts/explore_nba_api.py --phase schedule      # 일정만 (4번)
uv run python scripts/explore_nba_api.py --phase rate --delay 0.6 --rate-month 4
uv run python scripts/explore_nba_api.py --phase fixtures      # 네트워크 없이 픽스처만 다시
```

- 원본 응답과 호출 로그는 `data/explore_nba_api/`(gitignore 대상)에 쌓인다. 일정 원본이 시즌당 4MB라 커밋하지 않는다.
- 스크립트는 `--delay`가 0.6초보다 작으면 거부하고, 403/429 또는 연속 실패 2번이면 바로 멈춘다(종료 코드 2).
- 현재 시즌(2026-27) 응답은 날마다 바뀌므로 다시 돌리면 픽스처 내용이 달라질 수 있다.
