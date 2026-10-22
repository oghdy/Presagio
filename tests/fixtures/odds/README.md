# The Odds API 픽스처 (P-019)

| 파일 | 무엇 | 출처 |
| --- | --- | --- |
| `the_odds_api_nba_odds_synthetic.json` | `/v4/sports/basketball_nba/odds?regions=us&markets=h2h,spreads,totals&oddsFormat=decimal` 응답 모양의 **합성** 데이터. 3경기(이미 시작한 경기 1, 2026-27 일정 `0022600006`과 같은 경기 1, 일정에 없는 경기 1), 북메이커 2곳, 마켓 3개 | 직접 만듦 |
| `the_odds_api_nba_events_synthetic.json` | `/v4/sports/basketball_nba/events` 응답 모양의 **합성** 데이터(배당 없음) | 직접 만듦 |

- **합성인 이유:** API 키는 사람의 `.env`에만 있고, NBA 공개 샘플이 없다(`docs/research/odds_api.md` 7장). 구조(최상위 리스트, 경기 `id`/`sport_key`/`commence_time`/`home_team`/`away_team`/`bookmakers[]`, 북메이커 `key`/`last_update`/`markets[]`, outcome `name`/`price`/`point`)는 공개 S3 샘플(`odds_api.md` 4장, NFL)과 v4 가이드에서 확인한 것만 썼다.
- **팀 이름은 미확인:** `home_team`/`away_team` 문자열은 nba_api `full_name`이다. 실제 NBA 응답과 같은지는 아직 모른다.
- **실제 응답으로 바꿀 때:** 키 사용 호출 1~2회 결과를 `the_odds_api_nba_odds_<날짜>.json`, `the_odds_api_nba_events_<날짜>.json`으로 저장한다. 요청 URL·헤더는 저장하지 않는다(키가 쿼리스트링에 있다). 응답 본문에는 키가 없지만 저장 후 `grep`으로 확인하고, 경기 3~4개·북메이커 2~3곳으로 줄인다.
