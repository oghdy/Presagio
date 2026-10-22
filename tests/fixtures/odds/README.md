# The Odds API 픽스처 (P-019)

| 파일 | 무엇 | 출처 |
| --- | --- | --- |
| `the_odds_api_nba_odds_2026-10-22.json` | **실제** `/v4/sports/basketball_nba/odds?regions=us&markets=h2h,spreads,totals&oddsFormat=decimal` 응답(2026-10-22 12:39Z 수신, HTTP 200, 41경기). 3경기(PHI-CLE, OKC-DEN, 북메이커 1곳뿐인 SAS-LAL)와 경기당 북메이커 최대 3곳으로 자름 | 실제 호출 1회(3크레딧) |
| `the_odds_api_nba_events_2026-10-22.json` | **실제** `/v4/sports/basketball_nba/events` 응답(같은 시각, 41경기)에서 같은 3경기 | 실제 호출 1회(무료) |
| `the_odds_api_nba_odds_synthetic.json` | 합성 `/odds` 응답. 실제 응답에 없는 경우(이미 시작한 경기, 일정에 없는 경기)를 시험하려고 유지 | 직접 만듦 |
| `the_odds_api_nba_events_synthetic.json` | 합성 `/events` 응답 | 직접 만듦 |

- 실제 파일은 받은 JSON을 파싱해 경기·북메이커만 고른 뒤 `indent=1`로 다시 쓴 것이다(값은 그대로, 바이트 배치는 원본과 다름). 원본 바이트는 로컬 DB `raw_api_responses`에 있다.
- 요청 URL·헤더는 저장하지 않았다(키가 쿼리스트링에 있다). 저장 후 키 문자열로 `grep -cF` 해서 0회임을 확인했다. `test_odds_fixtures_carry_no_key`가 키 파라미터 이름과 `id` 밖의 32자리 hex를 막는다.
- 팀 이름 30개는 nba_api `full_name`과 같았다(시드 확인 완료).
