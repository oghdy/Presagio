# 배당 API 후보 조사 (P-018)

- **작성:** data-engineer, 2026-10-22
- **목적:** 배당 데이터 소스를 사람이 고를 수 있도록 후보를 비교한다(D-010 사람 결정 게이트).
- **용도 전제:** 배당은 **모델 평가의 기준점**으로만 쓴다(`PRESAGIO_OVERVIEW.md` 1장 비목표). 베팅 추천, 수익 시뮬레이션은 하지 않는다. 약관도 이 용도("저장 + 분석 + 공개 포트폴리오 대시보드에 우리 지표 표시") 기준으로 읽었다.
- **확인 방법:** 아래 모든 가격, 한도, 약관은 표에 적은 **공식 페이지를 2026-10-22에 직접 읽어서** 옮겼다. 기억으로 쓴 값은 없다. 페이지에 없거나 서로 어긋나는 값은 "확인 못 함" 또는 "문서끼리 다름"으로 적었다.
- **API 호출:** 키를 발급받지 않았고 API를 호출하지 않았다. 키 없이 받을 수 있는 공개 샘플 파일 1개(The Odds API의 S3 샘플)만 1회 내려받아 응답 구조를 확인했다(아래 4장).

---

## 1. 한눈에 보기

| 후보 | 무료로 매일 수집 가능? | 2023-24 ~ 2025-26 과거 배당 | 과거 배당 비용 | 저장 허용 | 판정 |
| --- | --- | --- | --- | --- | --- |
| **The Odds API** | 가능 (월 500 크레딧 안에 들어감) | **있음** (NBA는 2020-06-27부터) | 유료 플랜 1개월 ($59로 충분, 계산은 3장) | **명시적 허용** ("무기한 보관") | ✅ 추천 |
| SportsGameOdds | 가능 (월 2.5k 오브젝트) | 깊이 확인 못 함 | Pro $299/월 이상 | 해지 시 **전부 삭제 의무** | ❌ |
| Odds-API.io | 신규 무료 키 발급 중단 | **없음** (마감 배당은 2025-12부터) | 유료 £49/월~ | 재배포 금지, 보관 조항 없음, 해지 후 삭제 의무 명시 없음 | ❌ |
| OddsPapi | 확인 못 함 | **없음** (2026-01부터) | 확인 못 함 | 확인 못 함 | ❌ |
| SportsDataIO | **불가** (무료 체험은 UEFA 챔스만) | 깊이 확인 못 함 | 가격 비공개(영업 문의) | 확인 못 함 | ❌ |

**쉬운 말 요약:** 단계 4의 시장 비교에 필요한 2023-24 ~ 2025-26 시즌 배당을 파는 곳 중, 가격이 공개되어 있고 우리가 데이터를 계속 보관해도 된다고 약관에 적어 둔 곳은 The Odds API 하나뿐이었다.

---

## 2. 후보별 상세 (완료 기준 1)

### 2.1 The Odds API

| 항목 | 내용 | 출처 (2026-10-22 확인) |
| --- | --- | --- |
| 무료 한도 | Starter: 월 500 크레딧, "Most bookmakers" | [pricing](https://the-odds-api.com/) |
| 유료 가격 | 20K: $30/월(2만 크레딧) / 100K: $59/월(10만) / 5M: $119/월(500만) / 15M: $249/월(1,500만). 모두 "All bookmakers" | [pricing](https://the-odds-api.com/) |
| 과금 단위 | 현재 배당 `/v4/sports/{sport}/odds`: **마켓 수 × 지역 수** 크레딧. 과거 배당 `/v4/historical/sports/{sport}/odds`: **10 × 마켓 수 × 지역 수**. `/events`는 무료(쿼터 차감 없음) | [v4 가이드](https://the-odds-api.com/liveapi/guides/v4/) |
| 요청 1회가 주는 경기 수 | `/odds`: 배당이 열린 "upcoming and live games" 전부(완료 경기는 안 줌). 과거: 그 시각의 "snapshot of games" 전부. 공개 샘플에서 1회 응답에 15경기 확인 | v4 가이드, 4장 |
| NBA 머니라인/스프레드/토탈 | 제공. 마켓 키 `h2h`, `spreads`, `totals` (featured markets). 스포츠 키 `basketball_nba` | v4 가이드, [historical](https://the-odds-api.com/historical-odds-data/) |
| 과거 데이터 | 2020-06-06부터(featured markets). **NBA 최초 스냅숏 2020-06-27T03:55:00Z.** 10분 간격, 2022-09부터 5분 간격. `date`보다 같거나 이른 가장 가까운 스냅숏을 준다 | [historical](https://the-odds-api.com/historical-odds-data/), v4 가이드 |
| 과거 데이터 플랜 조건 | **문서끼리 다름.** historical 페이지는 "only available on paid usage plans", 가격표는 Starter 행에도 "Historical Odds"를 표시. → 유료가 필요하다고 보고 계산했다 | 위 두 페이지 |
| 북메이커 수 | 전체 "over 100 bookmakers"([홈](https://the-odds-api.com/)). 미국: `us` 지역 11곳(DraftKings, FanDuel, BetMGM, Caesars 등), `us2` 지역 11곳([bookmakers](https://the-odds-api.com/sports-odds-data/bookmaker-apis.html)) | 홈, bookmakers |
| 약관 (저장/분석) | 허용으로 명시: "Storing our data and retaining it indefinitely", "Using our data to train statistical and machine learning models", "Using our data in research papers and analytical dashboards", 파생값 계산·표시. 금지: 원본 데이터를 자체 API/피드/다운로드 파일로 재배포. 출처 표기 의무 없음 | [terms](https://the-odds-api.com/terms-and-conditions.html) |
| 응답 샘플 | 현재 배당: v4 가이드 본문 JSON 예시. 과거 배당: [historical-nfl.json](https://public-odds-api-sample-data.s3.amazonaws.com/historical-nfl.json) (NBA 샘플은 공개 안 됨) | v4 가이드, historical |
| 속도 제한 | 초과 시 429. "spacing out requests over several seconds" 권장 | v4 가이드 |

### 2.2 SportsGameOdds

| 항목 | 내용 | 출처 (2026-10-22 확인) |
| --- | --- | --- |
| 무료 한도 | Amateur: 월 2.5k objects, 분당 10요청, 10분 갱신, 북메이커 9곳, 리그 8개 | [pricing](https://sportsgameodds.com/pricing) |
| 유료 가격 | Rookie $99/월(10만 objects, 77곳) / Pro $299/월(무제한, 82곳) / All-Star 별도 협의 | [pricing](https://sportsgameodds.com/pricing) |
| 과금 단위 | 응답의 최상위 항목 1개 = 1 object (경기 10개 = 10 objects). 마켓·북메이커 수와 무관하게 경기당 1회 | [pricing](https://sportsgameodds.com/pricing) |
| NBA 머니라인/스프레드/토탈 | NBA가 리그 목록에 있고 Moneylines/Spreads/Over-Unders를 마켓으로 표시. NBA 전용으로 세 마켓이 다 있다는 문장은 못 찾음 | [pricing](https://sportsgameodds.com/pricing) |
| 과거 데이터 | "Historical depth is available on the Pro plan and above". `includeOpenCloseOdds=true`로 오픈/마감 배당. **몇 시즌 전까지 있는지 확인 못 함** | [historical use-case](https://sportsgameodds.com/use-cases/historical-odds-data-api), [FAQ](https://sportsgameodds.com/sportsgameodds-faq/) |
| 북메이커 수 | 무료 9곳, Pro 82곳 ("85+ books" 표기도 있음) | pricing, historical use-case |
| 약관 (저장/분석) | 구독 기간 한정 라이선스. **해지 후 "permanently delete or destroy all Data"(DB, 캐시, 백업 포함)**. 데이터 덤프 재배포 금지. 공개 대시보드 표시는 명시 조항 없음 | [terms](https://sportsgameodds.com/terms) |
| 응답 샘플 | 공개 샘플 파일 못 찾음. 위 historical use-case 페이지의 파라미터 설명만 있음 | - |

**탈락 이유:** 과거 배당이 Pro($299/월)부터이고 깊이가 불명확하다. 더 큰 문제는 해지하면 받은 데이터를 지워야 해서, 한 달만 결제하고 백필한 뒤 계속 보관하는 방식이 약관상 안 된다. 평가 결과를 재현하려면 원본을 계속 보관해야 하므로(원칙 2) 맞지 않는다.

### 2.3 Odds-API.io

| 항목 | 내용 | 출처 (2026-10-22 확인) |
| --- | --- | --- |
| 무료 한도 | "existing free keys keep working at 100 requests/hour (500/day)". **신규 무료 키 발급 중단** | [docs](https://docs.odds-api.io/) |
| 유료 가격 | Solo £49/월(북메이커 2곳) / Starter £99(5곳) / Growth £179(10곳) / Pro £229(15곳), 모두 시간당 5,000요청. 월 한도 표기 없음 | [pricing](https://odds-api.io/pricing) |
| 요청 1회가 주는 경기 수 | 확인 못 함 | - |
| NBA 머니라인/스프레드/토탈 | 34개 종목에 Basketball 포함, "Every sport and market". NBA 명시 문장은 못 찾음 | pricing, docs |
| 과거 데이터 | **마감 배당은 2025-12부터.** 그 전 경기는 점수만 있고 `bookmakers`가 빈 객체. `/v3/historical/closing-lines`는 유료 전용 | [historical guide](https://docs.odds-api.io/guides/historical) |
| 북메이커 수 | 전체 "over 415 bookmakers", 단 플랜별로 고를 수 있는 수가 2~15곳 | docs, pricing |
| 약관 | 9장 "Data Usage and Restrictions": 서면 동의 없이 제3자에게 재판매·재배포·서브라이선스 금지, 경쟁 서비스 구축 금지. 3장: 데이터를 "statistical analysis, data modeling" 등에 쓸 수 있다. **저장·보관 기간 조항은 없음.** 19장 해지: "your right to use the Service will cease immediately"뿐이고 **해지 후 삭제 의무는 명시 없음**. 공개 대시보드 표시 조항도 없음 | [terms](https://odds-api.io/terms) |
| 응답 샘플 | historical guide 본문의 closing-lines JSON 예시 | [historical guide](https://docs.odds-api.io/guides/historical) |

**탈락 이유:** 2023-24, 2024-25 시즌 배당이 없다. 2025-26도 2025-12 이후만 있다.

### 2.4 OddsPapi

| 항목 | 내용 | 출처 (2026-10-22 확인) |
| --- | --- | --- |
| 무료 한도 / 유료 가격 | 가격 페이지가 계산기 형태(JS)라 플랜과 숫자를 읽지 못함. **확인 못 함** | [pricing](https://oddspapi.io/en/pricing) |
| 과거 데이터 | "All historical odds data since January 2026 is available." 요청당 북메이커 최대 3곳, 엔드포인트별 5,000ms 쿨다운 | [historical odds docs](https://oddspapi.io/en/docs/get-historical-odds) |
| NBA 마켓, 북메이커 수, 약관 | 확인 못 함 | - |
| 응답 샘플 | 위 docs 페이지의 예시 JSON | historical odds docs |

**탈락 이유:** 과거 데이터가 2026-01부터라 필요한 세 시즌이 없다.

### 2.5 SportsDataIO

| 항목 | 내용 | 출처 (2026-10-22 확인) |
| --- | --- | --- |
| 무료 한도 | "The SportsDataIO API Free Trial only provides access to the UEFA Champions League" → NBA는 무료로 못 씀 | [NBA API docs](https://sportsdata.io/developers/api-documentation/nba) |
| 유료 가격 | 문서에 없음. 영업 문의. **확인 못 함** | - |
| NBA 머니라인/스프레드/토탈 | 제공. "Pre-Game Odds by Date"가 "full-game odds (moneyline, spread, total)"를 날짜 단위로 반환. 라인 무브먼트 엔드포인트 별도 | NBA API docs |
| 과거 데이터 | 라인 무브먼트 엔드포인트가 "meant for historical data". **몇 시즌 전까지인지 확인 못 함** | NBA API docs |
| 북메이커 수 / 약관 | 확인 못 함 | - |
| 응답 샘플 | 공개 샘플 링크 못 찾음 | - |

**탈락 이유:** NBA 무료 체험이 없고 가격이 공개되지 않아 비용을 추정할 수 없다.

---

## 3. 호출 수와 비용 추정 (완료 기준 2)

### 3.1 계산 기준
- 한 시즌 = 정규시즌 약 1,230경기 + 플레이오프 약 80경기 = **약 1,310경기**.
- 하루 평균 7경기 → **경기가 있는 날 약 187일** (1,310 ÷ 7).
- 수집은 하루 2회: ① 예측 시점, ② 마감 직전.
- 마켓 3개(`h2h`, `spreads`, `totals`), 지역 1개(`us`, 북메이커 11곳) 기준. 지역을 `us2`까지 넓히면 크레딧이 2배다.
- 시장 비교에 필요한 과거 구간: 2023-24, 2024-25, 2025-26 = **3시즌, 약 561일**.

### 3.2 The Odds API

요청 1회 = 그 시각에 배당이 열린 **앞으로의(그리고 진행 중인) NBA 경기 전부**. 그래서 경기 수가 아니라 **날 수 × 하루 호출 수**로 센다. 그날 경기만 주는 것이 아니다(공개 샘플 스냅숏 1개의 15경기가 2021-11-25 ~ 11-30, 6일에 걸쳐 있었다). 그래서 P-019에서 `commence_time`(또는 `commenceTimeFrom/To` 파라미터)으로 그날 경기만 골라야 하고, 이미 시작한 경기의 live 배당이 섞일 수 있으니 `commence_time` 이후 배당은 버리는 규칙이 필요하다.

| 구분 | 계산 | 시즌 합계 | 한 달 최대(31일) | 필요한 플랜 |
| --- | --- | --- | --- | --- |
| 매일 수집 (현재 배당) | 187일 × 2회 × 3크레딧 | 374회, **1,122크레딧** | 62회, 186크레딧 | **Starter 무료 (500)** |
| 매일 수집, 마감을 tip-off 시간대별로 4회 | 187일 × 4회 × 3 | 748회, 2,244크레딧 | 372크레딧 | 무료 (500) |
| 과거 백필 3시즌, 하루 2스냅숏 | 561일 × 2회 × 30크레딧(10×3×1) | 1,122회, **33,660크레딧** | (한 번에) | **100K $59 × 1개월** |
| 과거 백필 3시즌, 하루 4스냅숏 | 561일 × 4회 × 30 | 2,244회, 67,320크레딧 | (한 번에) | 100K $59 × 1개월 |
| 과거 백필 3시즌, 하루 4스냅숏, `us`+`us2` | 561 × 4 × 60 | 134,640크레딧 | (한 번에) | 5M $119 × 1개월 또는 100K 2개월 $118 |

- **결론:** 매일 수집은 무료로 충분하다. 과거 세 시즌은 **100K 플랜($59)을 한 달만** 쓰면 하루 4스냅숏까지 넉넉히 받는다.
- **상한:** 100K 1개월로 받을 수 있는 건 100,000 ÷ (561일 × 30크레딧) = **하루 약 5.94스냅숏**이다. 즉 **백필 스냅숏은 하루 최대 5회(`us` 1지역)까지**(5회 = 84,150크레딧). 6회면 100,980크레딧으로 초과하므로 5M $119 × 1개월 또는 100K 2개월($118)이 필요하다. 지역을 늘리면 상한이 그만큼 줄어든다(`us`+`us2`면 하루 2회까지). 남는 크레딧(약 3~6만)은 재시도 여유분이다. 세금 포함 여부, 자동 갱신 해지 방법은 확인 못 함(결제 페이지에서 사람이 확인).
- "마감 직전"을 하루 1번으로 잡으면 늦은 경기(예: 서부 10:30pm ET)는 몇 시간 전 배당이 "마감"으로 기록된다. 그래서 tip-off 시간대별 스냅숏(하루 3~4회)을 권한다. 경기 시각은 nba_api 일정으로 알 수 있고, `/events`는 크레딧이 들지 않는다. 정확한 규칙은 P-019에서 정한다.
- **오늘 수집하지 않은 날은 나중에 유료 과거 API로만 메울 수 있다.** 2026-27 시즌 매일 수집은 무료 키로 빨리 시작할수록 좋다.

### 3.3 SportsGameOdds (비교용)

과금 단위가 "경기 1개 = 1 object"다.

| 구분 | 계산 | 시즌 합계 | 한 달 최대 | 필요한 플랜 |
| --- | --- | --- | --- | --- |
| 매일 수집 | 1,310경기 × 2회 | 2,620 objects | 약 434 objects (31일 × 7경기 × 2) | Amateur 무료 (2.5k) |
| 과거 백필 3시즌 | Pro 필요 | - | - | **Pro $299 × 1개월 이상**, 해지 후 데이터 삭제 의무 |

### 3.4 나머지 후보
- Odds-API.io, OddsPapi: 필요한 과거 구간이 없어서 비용을 계산할 의미가 없다. 매일 수집만 보면 Odds-API.io는 신규 무료 키가 없으니 최소 £49/월.
- SportsDataIO: 가격 비공개로 계산 불가.

---

## 4. 직접 확인한 응답 구조 (The Odds API 공개 샘플)

키 없이 받을 수 있는 공개 S3 샘플을 1회 내려받아 `jq`로 구조만 봤다(NBA 샘플은 공개되어 있지 않아 NFL 샘플).

```bash
curl -sS -m 20 -o historical-nfl.json https://public-odds-api-sample-data.s3.amazonaws.com/historical-nfl.json
jq '{keys: keys, timestamp, n_games: (.data|length), first: (.data[0] | {commence_time, home_team, away_team, n_books: (.bookmakers|length), markets: [.bookmakers[0].markets[].key]})}' historical-nfl.json
```

확인한 것:
- 최상위 키: `timestamp`, `previous_timestamp`, `next_timestamp`, `data`. 스냅숏 간격은 10분(`11:45` → `11:55` → `12:05`, 2021년 데이터라 10분 간격 시절).
- 스냅숏 1개에 **15경기**가 들어 있었다 → "요청 1회 = 그 시각의 경기 전부"라는 계산 전제와 맞다.
- 경기마다 `id`, `commence_time`(UTC, `Z`), `home_team`, `away_team`, `bookmakers[]`. 첫 경기에 북메이커 19곳.
- 북메이커마다 `key`, `last_update`(UTC), `markets[]`. 마켓 키 `h2h`, `spreads`, `totals`. outcome은 `name`, `price`(이 샘플은 미국식 배당 예: `-125`, `104`), `point`(스프레드/토탈 기준점).
- 시점 관리에 필요한 시각 정보(스냅숏 `timestamp`, 북메이커별 `last_update`, `commence_time`)가 모두 있다 → `odds_snapshots`의 기록 시각과 "예측 시점 vs 마감" 구분(risks B3)에 쓸 수 있다.

---

## 5. 추천안과 차선책 (완료 기준 3)

### 추천안: The Odds API — 매일 수집은 무료 Starter, 과거 3시즌은 100K 플랜 1개월($59)로 한 번에 백필

이유:
1. **필요한 과거 구간이 있다.** NBA 배당이 2020-06부터 5~10분 간격 스냅숏으로 있어 2023-24 ~ 2025-26 시즌의 예측 시점·마감 배당을 모두 받을 수 있다. 다른 공개 가격 후보 중 이 구간을 확실히 주는 곳이 없었다.
2. **돈이 적게, 한 번만 든다.** 매일 수집은 월 186~372크레딧이라 무료 500 안에 들어간다. 과거 백필은 약 3.4만~6.7만 크레딧이라 $59 한 달이면 끝난다.
3. **약관이 우리 용도와 맞다.** 무기한 보관, ML 학습, 분석 대시보드, 파생값(우리 확률, 시장 확률) 표시를 명시적으로 허용한다. 금지는 원본을 데이터 상품으로 재배포하는 것뿐이므로, 원본 배당을 그대로 내려받게 하는 공개 API/파일만 만들지 않으면 된다.
4. **구조가 단순하다.** 요청 1회가 그 시각의 NBA 경기 전부를 주고, 응답에 스냅숏 시각과 북메이커별 갱신 시각이 있어 시점 관리가 쉽다.

주의할 점:
- 과거 데이터가 무료 플랜에서 되는지는 공식 문서끼리 말이 다르다. 유료가 필요하다고 보고 계획했다.
- 무료 플랜은 "Most bookmakers"라서 유료 기간과 북메이커 구성이 다를 수 있다. 평가에는 특정 북메이커 몇 곳(예: DraftKings, FanDuel) 또는 합의 확률을 기준으로 쓰면 영향이 작다. P-019에서 정한다.
- 자동 갱신이면 백필 후 사람이 다운그레이드해야 한다.

### 차선책: The Odds API 무료 Starter만 쓰고, 과거 3시즌은 "시장 비교 불가"로 표시 ($0)

- 2026-27 시즌부터 매일 수집한 배당으로만 시장 비교를 한다. 비용은 0이다.
- 대신 단계 4의 시장 비교가 현재 시즌 데이터가 쌓일 때까지 불가능하고, 표본이 한 시즌 일부뿐이다(risks B2의 "없는 기간은 정직하게 표시"에 해당).
- 나중에 마음이 바뀌면 같은 API의 과거 엔드포인트로 언제든 백필할 수 있다. 응답 형식이 같아서 어댑터를 새로 짤 필요가 없다. 그래서 차선책에서 추천안으로 옮기는 비용이 작다.
- 다른 회사를 차선책으로 두지 않은 이유: SportsGameOdds는 과거 배당에 월 $299 이상이 들고 해지 후 데이터 삭제 의무가 있다. Odds-API.io와 OddsPapi는 필요한 구간이 없다. SportsDataIO는 가격이 비공개다.

---

## 6. 사람이 결정할 질문

> **The Odds API로 정하고, 매일 수집은 무료 키로 바로 시작하고, 2023-24 ~ 2025-26 과거 배당은 100K 플랜($59)을 한 달만 결제해서 받을까?**
>
> - 예 → 사람이 The Odds API 키를 발급받아 `.env`에 넣는다(커밋 금지). 백필 달에만 100K로 올렸다가 내린다. **조건: 백필 스냅숏은 하루 5회 이하(`us` 1지역).** 그보다 많이 받으려면 $119 또는 $118이 든다. P-019 시작.
> - 아니오(차선책) → 무료 키만 발급. 과거 3시즌은 "시장 비교 불가"로 기록하고 P-019 시작. **되돌릴 수 있다:** 나중에 마음이 바뀌면 같은 API의 과거 엔드포인트로 같은 $59에 백필할 수 있고, 응답 형식이 같아 어댑터를 다시 짤 필요가 없다.
>
> **시간 민감:** 지금은 2026-27 시즌 초반이다. 어느 쪽을 고르든 **무료 키 발급이 하루 늦어질 때마다 유료 과거 API로만 메울 수 있는 날이 하루씩 늘어난다.** 무료 키 발급만이라도 먼저 해 두면 좋다.

## 7. 확인 못 한 것
- The Odds API 무료 플랜에서 과거 엔드포인트가 되는지(문서끼리 다름), 세금 포함 가격, 플랜 변경·해지 절차.
- The Odds API NBA 전용 응답 샘플(공개 샘플이 없음. 키 발급 후 P-019에서 1회 호출해 `tests/fixtures/`에 저장).
- SportsGameOdds 과거 데이터 깊이, OddsPapi 약관과 가격, SportsDataIO 가격과 과거 깊이.
