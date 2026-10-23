# Presagio 백로그 (티켓 보드)

> 상태 변경은 **PM만** 한다. 작업자는 PR 본문에 결과를 적는다.
> 티켓 양식과 규칙: `docs/team/handoff-templates.md` A, `docs/team/workflow.md` 1장.
> 상태: `TODO` → `IN PROGRESS` → `REVIEW` → `DONE`. 막히면 `BLOCKED(이유)`.
> 🧑 표시는 "사람 승인 게이트"가 있는 티켓이다. 승인 전에는 구현을 시작하지 않는다.

## 현재 단계: 2 (피처 생성 + 베이스라인 모델). 2026-10-23 사람 승인(D-045). 2026-10-23 Q-015~Q-017 승인(D-046~D-048), P-030 브리핑 확정. P-024·P-025·P-029·P-030 동시 진행 중(파일 겹침 없음).

### 보드 한눈에 보기

| 티켓 | 제목 | 역할 | 상태 | 의존 |
| --- | --- | --- | --- | --- |
| P-000 | 회사 구조 셋업 | (창업 세션) | DONE | - |
| P-001 | uv 프로젝트 뼈대 + ruff + pytest + pre-commit | data-engineer | DONE | - |
| P-002 | Docker Compose PostgreSQL + 설정 로딩 | data-engineer | DONE | P-001 |
| P-003 | GitHub Actions CI (lint + test) | platform-engineer | DONE | P-001 |
| P-010 | nba_api 엔드포인트 조사 (스파이크) | data-engineer | DONE | P-001 |
| P-011 | 🧑 raw/clean 스키마 설계안 v1 | data-engineer | DONE | P-010 |
| P-012 | SQLAlchemy 모델 + Alembic 초기 마이그레이션 | data-engineer | DONE | P-002, P-011 승인 |
| P-013 | 공통 수집 레이어: 재시도/딜레이/타임아웃/raw 저장/ingestion_runs | data-engineer | DONE | P-012 |
| P-014 | nba_api 어댑터: 팀, 일정, 결과 → teams, games | data-engineer | DONE | P-010, P-013 |
| P-015 | nba_api 어댑터: 팀 박스스코어 → team_game_stats | data-engineer | DONE | P-014 |
| P-016 | 백필 CLI (시즌/날짜 범위, 재개 가능, 멱등) | data-engineer | DONE | P-014, P-015 |
| P-017 | 증분 수집 CLI (전날 결과 + 오늘 일정) | data-engineer | DONE | P-016 |
| P-018 | 🧑 배당 API 후보 조사 (스파이크) | data-engineer | DONE | P-001 |
| P-019 | 배당 어댑터 인터페이스 + odds_snapshots 저장 | data-engineer | DONE | P-012, P-018 승인 |
| P-020 | 데이터 품질 체크 모듈 + CLI | data-engineer | DONE | P-015 |
| P-021 | 3시즌 + 현재 시즌 실제 백필 + 확인 쿼리 보고 | data-engineer | DONE | P-016, P-020 |
| P-022 | 단계 0~1 학습 노트 + 회고 | pm | DONE | P-021 |
| P-023 | CI에 postgres service container + REQUIRE_DB (+ concurrency 수정) | platform-engineer | DONE | P-002 |

병렬로 돌릴 수 있는 것: P-001 다음에 **P-002, P-003, P-010, P-018**은 서로 다른 파일을 건드리므로 동시에 가능하다. 단, 같은 역할(data-engineer)이 두 티켓을 동시에 하지는 않는다. P-003은 platform-engineer가 한다.

---

## 단계 0. 프로젝트 셋업

### P-000 회사 구조 셋업
- **상태:** DONE (2026-10-21)
- **담당 역할:** 창업 세션 (이 구조를 만든 세션)
- **목표:** 멀티 에이전트 "회사" 구조를 파일로 만든다.
- **산출물:** `CLAUDE.md`, `.claude/agents/*.md`, `docs/team/*`, `docs/backlog.md`, `docs/decisions.md`, `docs/experiments.md`, `docs/learning/*`, `README.md`, `.gitignore`, `.github/pull_request_template.md`
- **메모:** 이후 PM 세션을 새로 띄워서 P-001부터 진행한다.

### P-001 uv 프로젝트 뼈대 + ruff + pytest + pre-commit
- **상태:** DONE (2026-10-22, PR #1 squash merge → main `7f06c2a`)
- **담당 역할:** data-engineer
- **단계:** 0
- **의존:** 없음
- **목표:** 누구든 `git clone` 후 명령 몇 개로 테스트를 돌릴 수 있는 파이썬 프로젝트 뼈대를 만든다. 비밀값이 커밋되지 않도록 훅을 건다.
- **범위:**
  - `pyproject.toml` (프로젝트 메타, 의존성, ruff/pytest 설정)
  - `src/presagio/__init__.py`, `src/presagio/config.py`(빈 뼈대), 8장 디렉토리 구조의 빈 패키지들(`__init__.py`만)
  - `tests/conftest.py`, `tests/test_smoke.py`
  - `.pre-commit-config.yaml` (ruff, ruff-format, gitleaks 또는 detect-secrets 중 하나)
  - `.python-version`
  - `README.md`의 "실행 방법" 섹션 갱신
- **범위 밖:** DB, Docker, 실제 수집 코드.
- **읽어야 할 파일:** `CLAUDE.md`, `PRESAGIO_OVERVIEW.md` 4장과 8장, `docs/team/risks.md` B6, B8
- **완료 기준:**
  1. `uv`가 설치되어 있고(없으면 설치 방법을 README에 적는다) `uv sync`가 성공한다.
  2. `uv run pytest -q`가 통과한다(스모크 테스트 1개 이상).
  3. `uv run ruff check .`와 `uv run ruff format --check .`가 통과한다.
  4. `uv run pre-commit run --all-files`가 통과하고, 비밀값 탐지 훅이 포함되어 있다. 가짜 키를 넣은 파일로 훅이 막는 것을 PR에 증명한다.
  5. Python 3.12로 고정되어 있다(`.python-version`, `requires-python`).
- **확인 방법:** 위 명령 4개.
- **메모:** 2026-10-21 기준 이 맥에 uv 미설치. 설치 명령과 확인 결과를 PR에 적을 것.
  - 2026-10-21 PM: 브리핑 작성, 사람 확인 후 data-engineer 세션 시작(Opus 5.5, 노력 높음, D-011).
  - 2026-10-21 PM: PR #1(https://github.com/oghdy/Presagio/pull/1) 도착. reviewer 세션 시작(Opus 5.5, 노력 높음).
  - 2026-10-21 PM: 리뷰 판정 "수락 가능", 완료 기준 1~5 전부 O. 발견 사항 [낮음] 1건(README가 `pre-commit run --all-files`로 비밀값 전체 검사가 되는 것처럼 읽힘. 실제 gitleaks 훅은 스테이징 변경만 검사). 결정 D-012, D-013, D-014 기록. 사람 머지 대기.
  - 2026-10-22 PM: 사람이 리뷰어 세션에서 "확인 완료 PR 합치는것으로 결정"이라 말해 리뷰어 세션이 `gh`로 squash merge 수행(머지 계정 oghdy). 머지는 사람 또는 PM 요청으로만 하는 것이 원칙(D-005)이므로 다음부터는 PM 세션에서 진행. README [낮음] 발견 사항은 P-003 범위로 이동. 브리핑에서 런타임 의존성(SQLAlchemy 등) 추가를 범위 밖으로 명시했고, `uv.lock`은 커밋 대상에 포함했다.

### P-002 Docker Compose PostgreSQL + 설정 로딩
- **상태:** DONE (2026-10-22, PR #4 squash merge)
- **담당 역할:** data-engineer
- **단계:** 0
- **의존:** P-001
- **목표:** 로컬에서 `docker compose up`으로 PostgreSQL이 뜨고, 파이썬 코드가 `.env`의 설정으로 DB에 연결된다.
- **범위:**
  - `docker-compose.yml` (postgres 16 이상, 볼륨, 헬스체크)
  - `.env.example` (가짜 값만)
  - `src/presagio/config.py` (pydantic-settings 또는 동등한 방식으로 `.env` 로딩)
  - `src/presagio/db/session.py` (엔진, 세션 팩토리)
  - `tests/db/test_connection.py` (DB가 없으면 skip 되는 연결 테스트)
- **범위 밖:** 테이블 정의, 마이그레이션.
- **읽어야 할 파일:** `CLAUDE.md`, P-001의 결과물
- **완료 기준:**
  1. `docker compose up -d` 후 `docker compose ps`에서 postgres가 healthy다.
  2. `.env.example`을 `.env`로 복사하면 `uv run python -c "from presagio.db.session import check_connection; check_connection()"` 같은 한 줄 명령으로 연결이 확인된다(명령 이름은 구현 시 정한다).
  3. `.env`가 `.gitignore`에 있고, `.env.example`에는 실제 비밀값이 없다.
  4. 테스트가 DB 없이도 통과한다(연결 테스트는 skip).
- **확인 방법:** 위 명령.
- **메모:**
  - 2026-10-22 PM: 이 맥에 `docker` 명령 없음(PM 확인). Docker 런타임 선택과 설치는 사람(decisions.md Q-003). 설치 전에는 세션을 띄우지 않는다. 브리핑은 PM 공유 노트 `P-002-briefing.md`에 준비됨.
  - 2026-10-22 PM: 사람이 Docker Desktop 설치 완료(D-016, hello-world 확인). data-engineer 세션 시작(Opus 5.5, 노력 높음, D-011). P-003과 병렬.
  - 2026-10-22 PM: PR #4(https://github.com/oghdy/Presagio/pull/4) 도착. README에서 PR #2와 충돌 예정(해결법은 PR 본문). reviewer 카드 올림. 머지 순서: #2 먼저, 그 다음 #4 충돌 해결 후.
  - 2026-10-22 PM: 리뷰 판정 "수락 가능", 완료 기준 1~4 전부 O. 발견 [중간] 1건(비밀번호 틀려도 조용히 skip, 사유 오표시), [낮음] 4건. PM이 작업자에게 [중간]과 [낮음] 2건(make_engine 타임아웃 함정, healthy 대기 안내) 수정 요청. 나머지는 단계 1로. 결정 D-019 기록.
  - 2026-10-22 PM: 수정 커밋 3개(HEAD `1b5539c`, 테스트 15→19) 도착. 리뷰어에게 재리뷰 요청. README 충돌은 PR #2 머지 후 해결.
  - 2026-10-22 PM: 재리뷰 판정 "수정 후 재리뷰"(반려 1회차). 1차 발견 1~3은 고쳐짐, 완료 기준 전부 O 유지. 새 발견 [중간]: fail 경로의 pytest traceback에 `DATABASE_URL` 비밀번호 평문 노출(`grep -c WRONGPW` → 4). 작업자에게 그것만 수정 요청(`__tracebackhide__` 또는 URL 객체). fixture session 범위는 단계 1로.
  - 2026-10-22 PM: 수정 커밋 `91bc7df`, 리뷰어 재확인 판정 "수락 가능"(6가지 traceback 옵션에서 비밀번호 0회, 변이 검사로 누출 테스트가 실제 누출을 잡음 확인). 결정 D-020 기록. 남은 것: PR #2 머지 후 README 충돌 해결 → PM 확인 → 사람 머지.
  - 2026-10-22 PM: PR #2 머지됨. 작업자에게 README 충돌 해결 + PR #4 CI 확인 요청.
  - 2026-10-22 PM: 충돌 해결 머지 커밋 `c6b08d3`. PM이 README diff(충돌 표식 없음, P-003 한 줄 유지, 자리표시 제거, 5번 섹션 추가)와 PR #4 CI(lint-and-test, secret-scan 초록, CLEAN)를 직접 확인. PM 수락. 사람 머지 대기.
  - 2026-10-22 PM: 사람 지시로 PM이 squash merge(D-005 보완). 후속(단계 1): fixture session 범위, `check_connection`의 print 위치, CI postgres service container + `PRESAGIO_REQUIRE_DB=1`(Q-005).

### P-003 GitHub Actions CI (lint + test)
- **상태:** DONE (2026-10-22, PR #2 squash merge → main `84c46ec`)
- **담당 역할:** platform-engineer (단계 0 예외 활성)
- **단계:** 0
- **의존:** P-001
- **목표:** PR을 열면 자동으로 ruff와 pytest가 돌아 결과가 PR에 표시된다.
- **범위:**
  - `.github/workflows/ci.yml`
  - `README.md`의 "4. 커밋 전 검사" 섹션에 한 줄 추가: gitleaks 훅은 스테이징된 변경만 보고, 기록 전체 검사는 CI에서 한다 (P-001 리뷰 발견 사항, 사람 승인으로 이 티켓에 포함. README의 다른 부분은 건드리지 않는다)
- **범위 밖:** 배포, Docker 이미지 빌드, DB가 필요한 테스트(단계 1에서 service container로 추가).
- **읽어야 할 파일:** `CLAUDE.md`, `pyproject.toml`, `.pre-commit-config.yaml`, `docs/decisions.md` D-012
- **완료 기준:**
  1. PR에서 CI가 실행되고 ruff check, ruff format --check, pytest가 통과한다.
  2. uv 캐시를 써서 2회차 실행이 1분 이내다(증거: Actions 로그 링크).
  3. main 브랜치 보호 규칙 제안(필수 체크)을 PR 본문에 적는다. 적용은 사람이 한다.
  4. CI에서 `gitleaks git`(커밋 기록 전체) 검사가 돌고 통과한다. 가짜 키를 커밋한 테스트 브랜치에서 CI가 실패하는 것을 Actions 로그 링크로 증명한다. (2026-10-22 사람 승인으로 추가, decisions.md D-015)
- **확인 방법:** PR의 Checks 탭.
- **메모:**
  - 2026-10-21 PM: P-001 리뷰어가 완료 기준 추가를 제안함(`gitleaks git` 기록 전체 검사).
  - 2026-10-22 PM: 사람이 승인. 완료 기준 4와 README 한 줄을 범위에 추가했다(D-015).
  - 2026-10-22 PM: 사람 확인 후 platform-engineer 세션 시작(Opus 5.5, 노력 높음, D-011). P-002는 Docker 설치 대기.
  - 2026-10-22 PM: PR #2(https://github.com/oghdy/Presagio/pull/2) 도착. CI 2개 잡 초록, 카나리 PR #3으로 실패 증명 후 삭제. 브랜치 보호는 비공개 무료 레포라 적용 불가(403) → 사람 결정 Q-004. reviewer 카드 올림.
  - 2026-10-22 PM: 리뷰 판정 "수락 가능", 완료 기준 1~4 전부 O. 발견 [중간] 1건(PR 본문이 gitleaks 검사 범위를 반대로 설명. 실제는 모든 브랜치 검사). PM 결정: 동작 유지, 주석·PR 본문 정정 요청. [낮음] concurrency는 단계 1 CI 티켓으로. 결정 D-018 기록. 레포 공개 전환(D-017)으로 브랜치 보호는 머지 후 사람이 켠다.
  - 2026-10-22 PM: 정정 커밋 `20b59e7`(주석만 변경, PM이 diff 확인) CI 초록. PM 수락. 사람 머지 대기.
  - 2026-10-22 PM: 사람이 "머지ㄱㄱ"라고 지시해 PM이 `gh pr merge --squash`로 머지(계정 oghdy). 브랜치 보호 적용은 사람 몫으로 남음. 후속: concurrency cancel-in-progress가 main push에도 적용되는 문제는 단계 1 CI 수정 시 처리.

---

## 단계 1. 데이터 파이프라인

### P-010 nba_api 엔드포인트 조사 (스파이크)
- **상태:** DONE (2026-10-22, PR #6 squash merge)
- **담당 역할:** data-engineer
- **단계:** 1
- **의존:** P-001
- **목표:** 추측 없이, 실제 호출로 "어떤 엔드포인트가 일정/결과/박스스코어를 주는지, 응답 형식이 뭔지, 얼마나 빨리 불러도 되는지"를 확인하고 문서로 남긴다. 코드는 짜지 않는다.
- **범위:**
  - `docs/research/nba_api.md` (조사 보고서)
  - `tests/fixtures/nba_api/*.json` (엔드포인트별 응답 샘플 1~2개, 작게)
  - `scripts/explore_nba_api.py` (조사에 쓴 일회성 스크립트, 재실행 가능)
- **범위 밖:** 어댑터 구현, DB 저장.
- **읽어야 할 파일:** `CLAUDE.md`, `PRESAGIO_OVERVIEW.md` 5장, `docs/team/risks.md` B1
- **완료 기준:**
  1. 보고서에 다음이 표로 있다: 용도(팀 목록 / 시즌 일정과 결과 / 팀 박스스코어)별 엔드포인트 이름, 필수 파라미터, 응답의 주요 컬럼, 한 시즌을 다 받는 데 필요한 호출 수 추정.
  2. 2023-24, 2024-25, 2025-26 정규시즌과 플레이오프, 그리고 2026-27 현재 시즌 데이터가 실제로 조회됨을 샘플로 증명한다.
  3. 경기 ID 체계, 시즌 구분 방법, 경기 유형(정규/플레이오프) 구분 방법, 경기 시각의 시간대(UTC 변환 방법)를 설명한다.
  4. 요청 간 딜레이 권장값과 근거(실제로 몇 번 호출해서 차단/지연이 있었는지)를 적는다.
  5. nba_api가 막혔을 때의 대체 소스 후보를 2개 이상 이름만이라도 적는다.
- **확인 방법:** 보고서 읽기, 픽스처 존재 확인.
- **메모:**
  - 2026-10-22 PM: 사람 확인 후 data-engineer 세션 시작(Opus 5.5, xhigh, D-011). P-018과 병렬(파일 겹침 없음). 브리핑에서 `nba_api`는 dev 그룹에만 추가, `scripts/explore_nba_api.py` 1개만 예외 허용.
  - 2026-10-22 PM: PR #6(https://github.com/oghdy/Presagio/pull/6) 도착. 실제 호출 40번, 차단 0, 픽스처 21개(328KB), 결정 제안 3개(백필 소스 조합, 간격 1.0초, 경기 유형은 ID 3번째 자리). CI 초록. reviewer 카드 올림(호출 최소화 지시).
  - 2026-10-22 PM: 리뷰 판정 "수락 가능", 완료 기준 1~5 전부 O(리뷰어가 실제 호출 5번과 픽스처로 재현, 5,470경기 ID·시간대 규칙 위반 0). 발견 [중간] 1건: 조사 스크립트가 HTML 403에서 즉시 멈추지 않음(JSON 파싱 후 검사) → 제품 코드가 아니므로 수정 없이 수락, P-013 기준 보강으로 반영(Q-008, D-024). [낮음] 2건(호출 수 표기, 픽스처 단계 경로)은 일회성 스크립트라 무시. 결정 D-023~D-025 기록. 사람 머지 대기.

### P-011 🧑 raw/clean 스키마 설계안 v1
- **상태:** DONE (2026-10-22, PR #7 squash merge. 사람 승인 A-1~A-11 전부 추천안대로, D-028)
- **담당 역할:** data-engineer (설계안 작성) → PM → 사람 승인
- **단계:** 1
- **의존:** P-010
- **목표:** `PRESAGIO_OVERVIEW.md` 6장의 테이블 개요를 실제 컬럼, 타입, 키, 인덱스, 제약까지 구체화한 설계안을 쓴다. **사람 승인 전에 마이그레이션을 만들지 않는다.**
- **범위:** `docs/design/schema-v1.md`
- **범위 밖:** 코드, 마이그레이션. 모델링 레이어 테이블(features, model_versions, predictions, evaluations)은 이번에 "초안"으로만 포함하고 단계 2에서 다시 승인받는다.
- **읽어야 할 파일:** `PRESAGIO_OVERVIEW.md` 6장, `docs/research/nba_api.md`, `docs/team/risks.md` B1·B3·B5
- **완료 기준:**
  1. 테이블마다: 컬럼 이름, 타입, NULL 허용 여부, 기본키, 유니크 제약(멱등 upsert의 근거), 인덱스, 외래키, 각 컬럼의 뜻 한 줄.
  2. 모든 clean 테이블에 `fetched_at`(수집 시각, UTC)과 `source`가 있다. `raw_api_responses`에는 요청 파라미터 해시로 중복을 막는 방법이 있다.
  3. `odds_snapshots`에 "예측 시점 배당"과 "마감 배당"을 구분할 수 있는 설계가 있다(예: `snapshot_type` 또는 `captured_at` 기준 규칙).
  4. 경기 시각은 UTC로 저장하고, 한국시간/미국 동부시간 변환은 조회 시 한다는 규칙이 적혀 있다.
  5. "다른 선택지와 안 고른 이유"가 최소 3개 있다(예: JSONB vs 정규화 컬럼, 자연키 vs 대리키, 팀 ID 체계).
  6. 사람이 승인하면 PM이 `docs/decisions.md`에 D-번호로 기록한다.
- **확인 방법:** 사람이 설계안을 읽고 승인/수정 요청.
- **메모:**
  - 2026-10-22 PM: 사람 확인 후 data-engineer 세션 시작(Opus 5.5, xhigh, D-011). 브리핑에 P-010 6장 관찰과 P-018 응답 구조, D-023~D-026을 반영. 결과물은 설계안 + "승인 요청 사항" 번호 목록.
  - 2026-10-22 PM: PR #7(https://github.com/oghdy/Presagio/pull/7) 도착. 표 8개(raw_api_responses, ingestion_runs, teams, team_aliases, games, team_game_stats, odds_events, odds_snapshots) + 뷰 2개 + 승인 요청 A-1~A-11. 부록 DDL은 작성자가 임시 Postgres 17에서 검증. CI 초록. reviewer 카드 올림(픽스처 대조, DDL 실행, 매일 흐름 시뮬레이션 지시).
  - 2026-10-22 PM: 리뷰 판정 "수정 후 재리뷰"(반려 1회차). DDL 통과, 완료 기준 1~5 O. [중간] 3건을 임시 DB로 재현: (1) 연기 경기에서 `odds_closing_lines`가 오래된 배당을 마감으로 반환, (2) `games`를 scoreboard가 먼저 적재하면 `season` NOT NULL 위반·`postponed_status` 소실(소스별 갱신 컬럼 규칙 없음), (3) `team_game_stats_primary`가 TOV 정의를 섞음. [낮음] 4건. 작성자에게 문서 수정 요청(표 구조 변경 금지). 리뷰어 지적한 기준 6 문제(PM 행동이라 PR로 검증 불가)는 다음 설계 티켓부터 문구 수정(D-021 적용).
  - 2026-10-22 PM: v1.1(`35b97b9`) 재리뷰 "수락 가능". [중간] 1~3은 리뷰어가 시나리오(S3 연기, C5/S2 scoreboard 선적재, S6 TOV)를 임시 DB에서 다시 돌려 해결 확인. 표 구조·키 변경 없음, 뷰 1개만 변경. PM 수락. **사람 승인 대기: A-1~A-11**(decisions.md Q-009). 승인되면 PR #7 머지 + D-번호 기록 + P-012/P-013/P-023 시작.

### P-012 SQLAlchemy 모델 + Alembic 초기 마이그레이션
- **상태:** DONE (2026-10-22, PR #9 squash merge)
- **담당 역할:** data-engineer
- **단계:** 1
- **의존:** P-002(DONE), P-011 승인(D-028, 2026-10-22)
- **목표:** 승인된 스키마 v1을 코드와 마이그레이션으로 만든다.
- **범위:** `src/presagio/db/models.py`(또는 패키지), `alembic/`, `alembic.ini`, `tests/db/test_migrations.py`
- **범위 밖:** 데이터 넣는 코드.
- **읽어야 할 파일:** `docs/design/schema-v1.md`, `docs/decisions.md`
- **완료 기준:**
  1. `uv run alembic upgrade head`가 빈 DB에서 성공하고, `downgrade base`도 성공한다.
  2. 모델과 마이그레이션이 일치한다(`alembic check` 또는 autogenerate 결과가 비어 있음).
  3. 설계안의 유니크 제약과 인덱스가 실제 DB에 존재한다(테스트로 확인).
  4. DB 설정이 있는데 틀린 경우(인증 실패, DB 없음)는 테스트가 skip이 아니라 실패한다(D-020, D-022). `PRESAGIO_REQUIRE_DB=1`로 로컬 실제 DB에서 전체 테스트가 통과한다.
  5. CI에서 실제 PostgreSQL로 마이그레이션 테스트가 돈다. 워크플로 수정은 P-023(platform-engineer)에서 하고, 이 티켓은 P-023과 함께 머지되거나 P-023 머지 후 CI 초록으로 증명한다(D-022).
- **확인 방법:** 위 명령.
- **메모:**
  - 2026-10-22 PM: 사람 지시(D-028 승인)로 data-engineer 세션 시작(Opus 5.5 높음). 설계안 부록 DDL이 기준. P-023과 병렬. P-013은 이 티켓 뒤에.
  - 2026-10-22 PM: PR #9(https://github.com/oghdy/Presagio/pull/9) 도착. 모델 8표, 마이그레이션 0001(CHECK 60개), 테스트 18개 추가(로컬 REQUIRE_DB=1로 33 passed). 설계 DDL과 마이그레이션 카탈로그 비교 테스트 포함. 기준 5는 P-023 머지 후 CI로 증명 예정. 결정 제안 4건(제약 이름 규칙, CHECK는 표 단위, 임시 테스트 DB, DDL 일치 테스트). reviewer 카드 올림.
  - 2026-10-22 PM: 리뷰 판정 "수정 후 재리뷰"(반려 1회차). 스키마는 설계안과 정확히 일치(리뷰어 독립 pg_dump diff, 제약 이름 외 차이 0). [심각] `test_one_running_row_per_work_unit`이 파이썬 시각 vs DB `now()` 비교로 불안정(로컬 42회 중 3회 실패, CI에서는 시계 공유로 거의 매번 실패 예상). [중간] 기준 5의 로컬 흉내는 동등 증명 불가 → P-023 머지 후 CI로만. [낮음] 모델↔마이그레이션 CHECK 드리프트를 아무도 못 잡음(`checkconstraint_byname` 플러그인 + 비교 테스트 제안). 작성자에게 수정 요청. TOAST 압축률 측정은 P-013으로 이동(PM 결정).
  - 2026-10-22 PM: 수정 커밋 `1307317`(시계 독립 테스트, `checkconstraint_byname` 플러그인, CHECK 드리프트 테스트) + main 병합 `a867ad4`. CI(실제 DB) `34 passed`, skipped 0 → 기준 5 증명. 작성자에게 재실행 1회 + PR 본문 갱신, 리뷰어에게 수정분 재확인 요청.
  - 2026-10-22 PM: 재리뷰 "수락 가능". CI 실제 DB 2회(같은 커밋 attempt 1·2) 모두 34 passed, skipped 0. 시계 의존 제거·CHECK 드리프트 감지 확인. 결정 D-030 기록. PM 수락. 사람 머지 대기.

### P-013 공통 수집 레이어: 재시도/딜레이/타임아웃/raw 저장/ingestion_runs
- **상태:** DONE (2026-10-22, PR #10 squash merge)
- **담당 역할:** data-engineer
- **단계:** 1
- **의존:** P-012
- **목표:** 모든 외부 호출이 거치는 한 곳을 만든다. 여기서 외부 서비스 존중 원칙(딜레이, 재시도, 타임아웃)과 raw 보관, 실행 기록을 강제한다.
- **범위:** `src/presagio/ingestion/client.py`, `src/presagio/ingestion/raw_store.py`, `src/presagio/ingestion/runs.py`, `tests/ingestion/`
- **범위 밖:** nba_api 특화 로직.
- **읽어야 할 파일:** `docs/research/nba_api.md`(딜레이 권장값), `docs/team/risks.md` B1
- **완료 기준:**
  1. 요청 간 최소 딜레이, 지수 백오프 재시도 횟수, 타임아웃이 설정값(`config.py`)이고 기본값이 문서화되어 있다.
  2. 재시도는 일시적 오류(타임아웃, 5xx)에만 하고, 403/429를 포함한 4xx는 재시도 없이 즉시 실패한다. HTTP 상태 코드는 **JSON 파싱보다 먼저** 검사한다(nba_api는 4xx에서 예외를 던지지 않고 HTML 본문을 돌려준다). 비JSON 차단 응답(HTML 403)을 mock으로 테스트한다. (2026-10-22 D-024, Q-008 승인으로 보강)
  3. 모든 응답이 `raw_api_responses`에 저장되고, 같은 요청을 다시 했을 때 **내용(바이트 sha256)이 같으면** 새 행이 생기지 않고 `last_fetched_at`·`fetch_count`만 갱신된다. 내용이 다르면 새 버전 행이 생긴다(테스트, D-028 A-1/A-2).
  4. `ingestion_runs`에 작업 종류, 시작/종료, 상태, 처리 건수, 에러 메시지가 기록된다(테스트).
  5. 테스트는 네트워크를 타지 않는다(httpx MockTransport 또는 동등한 방식).
- **확인 방법:** `uv run pytest tests/ingestion -q`
- **메모:**
  - 2026-10-22 PM: 설계안 3장의 "실제 일정 응답(4MB)으로 jsonb TOAST 압축률을 재본다"는 raw 저장이 처음 생기는 이 티켓에서 한다(P-012 리뷰에서 범위 조정). 결과를 PR에 적고 용량 추정을 갱신.
  - 2026-10-22 PM: 사람 지시로 data-engineer 세션 시작(Opus 5.5 높음). 브리핑은 PM 공유 노트 `P-013-briefing.md`. nba_api(requests 내부)와 The Odds API(httpx) 두 소스를 한 레이어로, 상태 코드를 파싱 전에 검사, raw 버전 규칙(D-028 A-1).
  - 2026-10-22 PM: PR #10(https://github.com/oghdy/Presagio/pull/10) 도착. 테스트 43개 추가(DB 켜면 77 passed), CI 초록. 결정 제안 7건(nba_api는 요청 설명만, 전송은 httpx / nba_api 런타임 이동 / Accept-Encoding gzip / 모든 응답 raw 저장 / 시계 분리 / lz4 검토 / job_type은 P-016). 작성자 질문: 403/429는 브리핑의 `failed`가 아니라 설계안 4장의 `aborted`로 닫음 → PM 판단: 승인된 설계안(D-028)이 브리핑보다 우선, `aborted`가 맞다. TOAST: pglz 780KB vs lz4 630KB(4MB 일정 응답). reviewer 카드 올림.
  - 2026-10-22 PM: 리뷰 판정 "수락 가능", 완료 기준 1~5 전부 O(네트워크 차단 상태에서 77 passed, 순서 강제·raw 버전·비밀값 누출 0 재현). [낮음] 4건: `STALE_RUN_HOURS` 미사용, 같은 바이트·다른 상태 코드 병합, `finish()`가 닫힌 행 덮어씀, `DecodingError` 미포장 → (a)(b)(c)는 P-016 완료 기준으로, 병합 한계는 D-032에 기록. 결정 D-031, D-032 기록, lz4는 Q-010 보류 추천. PM 수락. 사람 머지 대기.

### P-014 nba_api 어댑터: 팀, 일정, 결과 → teams, games
- **상태:** DONE (2026-10-22, PR #12 squash merge)
- **담당 역할:** data-engineer
- **단계:** 1
- **의존:** P-010, P-013
- **목표:** nba_api에서 팀 목록과 시즌별 경기 일정/결과를 받아 clean 테이블에 멱등하게 넣는다.
- **범위:** `src/presagio/ingestion/sources/nba_api/` (어댑터), `src/presagio/ingestion/loaders/games.py`, `tests/ingestion/`
- **범위 밖:** 박스스코어, CLI.
- **읽어야 할 파일:** `docs/research/nba_api.md`, `docs/design/schema-v1.md`
- **완료 기준:**
  1. 어댑터는 추상 인터페이스(`ScheduleSource` 같은 프로토콜)를 구현한다. 나중에 소스를 바꿀 수 있도록.
  2. 한 시즌(픽스처 기반)을 두 번 로드해도 `games` 행 수가 같다(테스트).
  3. 경기 시각이 UTC로 저장되고, 정규/플레이오프 구분이 맞다(픽스처로 검증).
  4. 상태가 "예정"인 경기와 "종료"인 경기를 모두 처리한다(점수 NULL 허용).
- **확인 방법:** `uv run pytest tests/ingestion -q`
- **메모:**
  - 2026-10-22 PM: 사람 지시로 data-engineer 세션 시작(Opus 5.5 높음). 브리핑은 PM 공유 노트 `P-014-briefing.md`(어댑터/로더 분리, D-023·D-025·D-028 규칙, raw는 2xx만 읽기).
  - 2026-10-22 PM: PR #12(https://github.com/oghdy/Presagio/pull/12) 도착. DB 켜면 110 passed, 실제 호출 3회로 1,322경기 적재. reviewer 카드 올림.
  - 2026-10-22 PM: 리뷰 판정 "수락 가능"(기준 1~4 O, 실제 2,529경기로 UTC·유형·season 일치, 홈/원정 불일치·stale 가드·TBD 재현 확인). [중간] 2건: 한 경기의 CHECK 위반이 시즌 전체 적재를 실패시킴(J1 한 팀만 미정, J2 홈=원정, J3 종료인데 점수 없음, ISO 아닌 시각), 저장 못 한 경기가 있어도 `succeeded`(P-016 재개가 영구 누락). [낮음] 3건(게임로그 미적용 결과를 성공으로 셈, 올스타 skip 테스트 없음, 설계안 6-1 표에 `leaguegamelog` 열 없음). PM 판단: P-016 매일 운영 전에 고쳐야 하므로 **머지 전 수정 요청**(조건부 수락). 재확인은 같은 리뷰어.
  - 2026-10-22 PM: 수정 커밋 `c23ed6b`(7파일, 스키마 변경 없음, 설계안 v1.2). 재리뷰 "수락 가능", 1차 발견 1~5 전부 해결, 124 passed, 멱등성 재확인. 결정 D-034 기록. PM 수락. 사람 머지 대기.

### P-015 nba_api 어댑터: 팀 박스스코어 → team_game_stats
- **상태:** DONE (2026-10-22, PR #13 squash merge)
- **담당 역할:** data-engineer
- **단계:** 1
- **의존:** P-014
- **목표:** 종료된 경기의 팀 단위 박스스코어를 받아 저장한다. 포제션 추정치도 계산해 넣는다.
- **범위:** 어댑터 확장, `src/presagio/ingestion/loaders/team_stats.py`, `tests/ingestion/`
- **범위 밖:** 선수 단위 스탯(단계 7).
- **읽어야 할 파일:** `docs/research/nba_api.md`, `docs/design/schema-v1.md`
- **완료 기준:**
  1. 경기당 정확히 두 팀 레코드가 생긴다(테스트).
  2. 두 팀 득점 합이 `games`의 최종 점수와 일치한다(테스트).
  3. 포제션 추정 공식이 코드 주석과 `docs/decisions.md`에 적혀 있다.
  4. 멱등: 두 번 로드해도 행 수 동일.
- **확인 방법:** `uv run pytest tests/ingestion -q`
- **메모:**
  - 2026-10-22 PM: 사람 지시로 data-engineer 세션 시작(Opus 5.5 높음). 브리핑 `P-015-briefing.md`. P-019와 병렬(파일 겹침 없음, `sources/base.py`는 P-015만 수정).
  - 2026-10-22 PM: PR #13(https://github.com/oghdy/Presagio/pull/13) 도착. 테스트 56개, 실제 호출 3회(85경기 불일치 0), 한 호출로 games+team_stats 두 표 적재. 포제션은 설계안 6-2대로 저장 안 함(PM 판단 D-035). CI 초록. reviewer 카드 올림.
  - 2026-10-22 PM: 리뷰 판정 "수락 가능", 기준 1~4 O(리뷰어 SQL 독립 확인, 실제 85경기 불일치 0). [낮음] 3건: 정수 상한 미검사(비현실적), 매일 흐름에서 득점 검증이 자기 자신과 비교됨 → P-020 체크 항목으로, 게임로그 응답의 동점 정렬 순서가 호출마다 바뀌어 raw 새 버전이 생길 수 있음 → P-017 참고. PM 수락. 사람 머지 대기.

### P-016 백필 CLI (시즌/날짜 범위, 재개 가능, 멱등)
- **상태:** DONE (2026-10-22, PR #15 squash merge)
- **담당 역할:** data-engineer
- **단계:** 1
- **의존:** P-014, P-015
- **목표:** `presagio backfill --season 2023-24` 또는 `--from 2024-01-01 --to 2024-01-31`로 과거 데이터를 채운다. 중간에 끊겨도 다시 돌리면 이어서 한다.
- **범위:** `src/presagio/cli.py`(backfill 명령), `src/presagio/ingestion/backfill.py`, `tests/`
- **범위 밖:** 증분 수집, 배당.
- **완료 기준:**
  1. 시즌 지정과 날짜 범위 지정 둘 다 동작한다.
  2. `--dry-run`이 호출 수와 예상 시간을 출력하고 DB를 건드리지 않는다.
  3. 날짜 단위로 진행 상황이 `ingestion_runs`에 남고, 재실행 시 이미 완료된 날짜는 건너뛰거나 멱등하게 다시 처리한다(둘 중 무엇을 택했는지 decisions 제안).
  4. 일부 날짜가 실패해도 전체가 멈추지 않고 실패 목록을 마지막에 출력한다.
  5. (2026-10-22 추가, D-032 후속) `track_run`에 `stale_after_hours`를 설정값(`STALE_RUN_HOURS`)으로 넘겨 죽은 프로세스의 `running` 행이 이후 실행을 영구 거부하지 않는다(테스트). `RunTracker.finish()`는 `WHERE status='running'`으로만 갱신한다(테스트). `httpx.DecodingError` 등 `TransportError` 밖의 httpx 예외도 `IngestionError`로 감싼다. `job_type` 값 목록과 `code_version`(git 해시) 자동 기록을 확정한다. CLI 로깅 설정이 `httpx` 로거를 INFO로 내리지 않는다(쿼리스트링에 키가 찍힘, D-031).
- **확인 방법:** 픽스처 기반 테스트 + `--dry-run` 실행.
- **메모:**
  - 2026-10-22 PM: 사람 지시로 data-engineer 세션 시작(Opus 5.5 높음). 브리핑 `P-016-briefing.md`. 호출 단위는 시즌×시즌 유형, 재개는 scope_key + has_succeeded, 차단은 즉시 전체 중단.
  - 2026-10-22 PM: PR #15(https://github.com/oghdy/Presagio/pull/15) 도착. 로컬 299 passed, 실제 2호출 1회 실행(임시 DB). reviewer 카드 올림.
  - 2026-10-22 PM: 리뷰 판정 "수락 가능", 기준 1~5 O(임시 DB 실제 2호출, 재개·차단·후속 4건 재현, 소켓 차단 299 passed). [낮음] 3건: 재개 판정이 "하나라도 succeeded"라 --force 후 partial이 영구 skip → **최신 run 기준으로 수정 요청**, `--stale-after-hours` 음수 검사 없음 → 수정 요청, README 소유권 → PM 예외 허용. 결정 D-037 기록. 조건부 수락, 재확인은 같은 리뷰어.
  - 2026-10-22 PM: 수정 커밋 `759f3b1`(재개는 최신 run 기준, 옵션 양수 검사, README). 재리뷰 "수락 가능", 새 발견 0, 305 passed. PM 수락. 사람 머지 대기.

### P-017 증분 수집 CLI (전날 결과 + 오늘 일정)
- **상태:** DONE (2026-10-22, PR #17 squash merge. 야간 위임 D-041로 PM이 머지)
- **담당 역할:** data-engineer
- **단계:** 1
- **의존:** P-016
- **목표:** 매일 아침 돌릴 한 명령 `presagio ingest-daily`. 전날 경기 결과와 박스스코어를 갱신하고 오늘 일정을 넣는다.
- **범위:** `src/presagio/cli.py`(ingest-daily), `src/presagio/ingestion/incremental.py`, `tests/`
- **완료 기준:**
  1. 기준 날짜를 인자로 받고 기본값은 "한국시간 오늘"이다. 시간대 처리가 테스트되어 있다.
  2. 어제 "예정"이던 경기가 "종료"로 갱신되고 점수와 박스스코어가 채워진다(픽스처 테스트).
  3. 같은 날 두 번 돌려도 결과가 같다.
- **확인 방법:** 테스트 + 수동 1회 실행 로그.
- **메모:**
  - 2026-10-22 PM(P-015·P-019 리뷰 이관): (1) D-027 배당 하루 최대 4회의 정확한 시각(예측 시점 1회 + tip-off 시간대별 마감)을 여기서 정한다. (2) 배당 응답에 중복 `event id`가 있으면 그 이벤트만 제외(지금은 전체 실패). (3) 게임로그는 동점 날짜 정렬 순서가 호출마다 바뀌어 raw 새 버전이 생길 수 있다(A-1 보장 안 됨, clean은 멱등). 매일 게임로그 재수집 비용에 참고. (4) 스코어보드(`scoreboardv3`) 갱신 경로는 설계안 6-1 표대로 여기서 구현.
  - 2026-10-22 PM: 사람 지시로 data-engineer 세션 시작(Opus 5.5 높음). 브리핑 `P-017-briefing.md`. P-020과 병렬(`cli.py`는 서브커맨드 등록 줄만). The Odds API 실제 호출 0회(필요 시 PM 경유).
  - 2026-10-22 PM: PR #17(https://github.com/oghdy/Presagio/pull/17) 도착. 실제 실행 1회(nba_api 3회, 배당 skipped, exit 0). reviewer 카드 올림.
  - 2026-10-22 PM: 리뷰 판정 "수락 가능", 기준 1~3 O(서머타임 경계·자정·예정→종료·멱등·재시도 창 재현, 실제 3호출). [낮음] 3건(README 호출 수, get_settings 위치, 테스트 주석) + 팀 갱신 20h → **7일**로 결정 + P-020 체크 연결 요청. 수집 시각 D-038 승인. 결정 D-039 기록. 조건부 수락: #16 머지 후 main 합치고 재확인.
  - 2026-10-22 PM: 수정 `f482fb3` 1차 재확인 "수락 가능"(팀 7일, 설정 오류 한 줄, 테스트 값, 운영 메모. 365 passed). 남은 것: #16 머지 후 main 합치기 + 품질 체크 연결 → 2차 재확인.
  - 2026-10-22 PM: main 병합 `4c9e4ce` + 품질 체크 연결(HEAD `ed69d35`). 2차 재확인 "수락 가능"(fail→1, warn→0, dry-run 호출 0, CI 초록). [낮음] 1건은 단계 6 알림 설계 참고. PM 머지(D-041).

### P-018 🧑 배당 API 후보 조사 (스파이크)
- **상태:** DONE (2026-10-22, PR #5 squash merge. 사람 결정 D-026: The Odds API 무료 플랜, 과거 백필 보류. 무료 키 발급 완료, `.env`에 보관)
- **담당 역할:** data-engineer (조사) → PM → 사람 결정
- **단계:** 1
- **의존:** P-001
- **목표:** 배당 데이터 소스를 고르기 위한 비교표를 만든다. 돈이 들 수 있으므로 사람이 결정한다.
- **범위:** `docs/research/odds_api.md`
- **범위 밖:** 코드, 키 발급(사람이 한다).
- **완료 기준:**
  1. 후보 3개 이상(The Odds API 포함)에 대해: 무료 한도, 유료 가격, NBA 머니라인/스프레드/토탈 제공 여부, 과거 데이터 제공 여부와 범위, 북메이커 수, 약관상 저장/분석 허용 여부, 응답 샘플 링크.
  2. 하루 2회 수집(예측 시점, 마감 직전) 기준으로 한 시즌에 필요한 호출 수와 비용 추정.
  3. 추천안 1개와 이유, 차선책 1개.
- **확인 방법:** 사람이 표를 보고 결정.
- **메모:**
  - 2026-10-22 PM: 사람 확인 후 data-engineer 세션 시작(Opus 5.5, xhigh, D-011). P-010과 병렬. 실제 API 호출·키 발급 금지, 가격·약관은 공식 URL과 확인 날짜 필수.
  - 2026-10-22 PM: PR #5(https://github.com/oghdy/Presagio/pull/5) 도착. 후보 5개, 추천 The Odds API(매일 무료 + 과거 3시즌 백필에 $59 한 달). CI 초록. reviewer 세션 시작(Opus 5.5 높음, 출처·계산 검증 중심).
  - 2026-10-22 PM: 리뷰 판정 "수락 가능"(출처 표본 25개 중 오류 0, 계산 전부 일치). 발견 [중간] 1건(100K 플랜의 하루 스냅숏 상한 5회가 안 적힘), [낮음] 3건. 작성자에게 문서 보완 요청. 보완 후 사람에게 결정 질문(The Odds API, $59 vs $0) 올림. 리뷰어 지적: 마감 스냅숏을 하루 3~4회로 하면 risks B2의 "하루 2회"를 고치는 결정 필요.
  - 2026-10-22 PM: 보완 커밋 `ecd418d`(문서만, PM이 diff 확인: 스냅숏 상한 5회, Odds-API.io 약관 채움, 되돌리기·시간 민감성 추가). CI 초록. PM 수락. 사람 결정 대기(decisions.md Q-006, Q-007).

### P-019 배당 어댑터 인터페이스 + odds_snapshots 저장
- **상태:** DONE (2026-10-22, PR #14 squash merge)
- **담당 역할:** data-engineer
- **단계:** 1
- **의존:** P-012, P-018 승인(D-026으로 완료: The Odds API 무료 플랜)
- **목표:** 배당 소스가 무엇이든 같은 모양으로 `odds_snapshots`에 넣는 인터페이스와, 선택된 소스의 어댑터(키가 있으면 실제 연결, 없으면 픽스처 기반)를 만든다.
- **범위:** `src/presagio/ingestion/sources/odds/`, `src/presagio/ingestion/loaders/odds.py`, `tests/`
- **완료 기준:**
  1. 인터페이스가 소스 독립적이다(프로토콜 + 픽스처 구현 + 실제 구현).
  2. 같은 경기의 스냅샷이 시각별로 여러 개 저장되고, "예측 시점"과 "마감" 구분이 된다.
  3. 미국식/소수식 배당을 소수식으로 통일해 저장하고 변환 테스트가 있다.
  4. 멱등: 같은 스냅샷 재저장 시 중복 없음.
- **확인 방법:** 테스트.
- **메모:**
  - 2026-10-22 PM: 사람 지시로 data-engineer 세션 시작(Opus 5.5 높음). 브리핑 `P-019-briefing.md`. 실제 호출 최대 2회, 키는 사람 `.env`에만 → 호출 필요 시 PM 경유로 사람 확인. P-015와 병렬.
  - 2026-10-22 PM: PR #14(https://github.com/oghdy/Presagio/pull/14) 도착. 테스트 76개(합성 픽스처), 실제 호출 0회(브리핑대로 멈춤). 작성자가 `/events`(무료) 1회 + `/odds`(3크레딧) 1회 요청 → 사람 승인 대기. CI 초록. reviewer 카드 올림(픽스처 기반 먼저, 실제 응답 픽스처는 승인 후 추가).
  - 2026-10-22 PM: 사람 승인으로 실제 호출 2회(`/events` 무료, `/odds` 3크레딧, 잔여 497). 커밋 `d4b2704`: 실제 픽스처 2개(경기 3개로 자름, 키 없음), 팀 이름 시드 30개 확인, 41/41 매핑(시각 차이 최대 600초). 리뷰 판정 "수락 가능", 기준 1~4 O(가짜 키 누출 0회를 pg_dump 10.5MB까지 grep). [낮음] 4건(중복 event id 시 전체 실패 → P-017, 쿼터는 성공 run에만 기록, D-027 4회 시각 미정 → P-017). 결정 D-036 기록. PM 수락. 사람 머지 대기.

### P-020 데이터 품질 체크 모듈 + CLI
- **상태:** DONE (2026-10-22, PR #16 squash merge)
- **담당 역할:** data-engineer
- **단계:** 1
- **의존:** P-015
- **목표:** `presagio quality-check --season 2023-24`가 데이터가 상식에 맞는지 검사하고 결과를 표로 출력한다.
- **범위:** `src/presagio/quality/`, `src/presagio/cli.py`(quality-check), `tests/quality/`
- **완료 기준:**
  1. 체크 항목 최소 5개: 경기당 팀 레코드 2개, 팀 득점 합 = 최종 점수, 종료 경기의 점수 NULL 없음, 시즌별 정규시즌 경기 수가 1,230(30팀 × 82 ÷ 2) 근처, 같은 팀이 같은 날 두 경기 없음.
  2. 각 체크는 통과/실패 건수와 실패 예시 ID를 출력한다.
  3. 실패해도 예외로 죽지 않고 종료 코드로 알린다(0 통과, 1 실패).
- **확인 방법:** 픽스처 테스트 + 실제 데이터 실행.
- **메모:**
  - 2026-10-22 PM(P-015 리뷰 이관): 체크 항목에 "`games` 점수 출처가 `leaguegamelog`인 경기는 일정/스코어보드 재수집 값과 대조"를 넣는다(매일 흐름에서는 득점 검증이 자기 자신과 비교되기 때문). 배당 쪽: `odds_events.game_id` NULL 비율과 사유별 개수.
  - 2026-10-22 PM: 사람 지시로 data-engineer 세션 시작(Opus 5.5 높음). 브리핑 `P-020-briefing.md`. P-017과 병렬(`cli.py`는 서브커맨드 등록 줄만, `loaders/`·`sources/` 금지).
  - 2026-10-22 PM: PR #16(https://github.com/oghdy/Presagio/pull/16) 도착. 읽기 전용 체크 14개, 355 passed. reviewer 카드 올림.
  - 2026-10-22 PM: 리뷰 판정 "수락 가능", 기준 1~3 O(읽기 전용·14개 정상/고장·종료 코드 재현, 심각·중간 0). [낮음] 3건 중 L1(raw 없으면 조용히 PASS → skip/warn), L2(이벤트 시작 시각 기준 warn 추가) 머지 전 수정 요청. 결정 D-040 기록. 조건부 수락, 재확인 후 **#16 먼저 머지**.
  - 2026-10-22 PM: 수정 `4435e21` 재리뷰 "수락 가능"(L1 skip/warn, L2 이벤트 기준 warn 재현, 357 passed). PM 수락. 사람 머지 대기.

### P-021 3시즌 + 현재 시즌 실제 백필 + 확인 쿼리 보고
- **상태:** DONE (2026-10-23, PR #19 squash merge `4c6e547`. 야간 위임 D-041로 PM 머지)
- **담당 역할:** data-engineer
- **단계:** 1
- **의존:** P-016, P-020
- **목표:** 실제 네트워크로 2023-24, 2024-25, 2025-26(정규+플레이오프)과 2026-27 현재까지를 채우고, 단계 1 완료 기준의 증거를 만든다.
- **범위:** `docs/research/backfill-report-stage1.md`, 필요한 버그 수정(별도 fix 티켓으로 분리 가능)
- **완료 기준:**
  1. 시즌별 경기 수, 홈팀 승률, 평균 총점 표가 보고서에 있다.
  2. 백필을 한 번 더 돌린 뒤 행 수가 변하지 않았다는 증거(전후 count).
  3. 품질 체크 전부 통과. 통과 못 한 항목은 원인과 처리 방법을 적는다.
  4. 전체 소요 시간과 호출 수, 차단/재시도 발생 횟수를 적는다.
- **확인 방법:** 사람이 로컬 DB에서 보고서의 쿼리를 직접 실행.
- **메모:**
  - 2026-10-22 PM: 야간 위임(D-041)으로 data-engineer 세션 시작(Opus 5.5 높음). 브리핑 `P-021-briefing.md`. 개발 DB(5433)를 실제로 채운다. 차단(exit 2)이면 멈추고 보고.
  - 2026-10-22 PM: 자동 이벤트 턴이라 세션 시작이 거부됨(사람 클릭 필요) → CLAUDE.md 허용 범위대로 data-engineer **서브에이전트**(Opus, 전용 worktree `.claude/worktrees/p-021-backfill-report`)로 수행.
  - 2026-10-23 PM: PR #19(https://github.com/oghdy/Presagio/pull/19) 도착. 68호출, 차단·재시도 0, games 5,169·team_game_stats 7,946, 품질 체크 14 pass, 멱등 증명(`--force` 전체 재실행 후 행 수 불변). 시즌별 경기 수가 조사 보고서 표와 일치. **코드 수정 1건**(중립 경기장: 게임로그가 두 팀 모두 "@"로 줘서 10경기 거부되던 것 → 일정 기준으로 처리, 21줄 + 테스트 3개). 결정 제안 3건(중립 경기 규칙, 로더 의미 변경 시 --force 운영, `games.is_neutral` 컬럼은 스키마 변경 → 사람). reviewer 서브에이전트로 독립 리뷰 중.
  - 2026-10-23 PM: 리뷰 판정 "수락 가능", 기준 1~4 전부 O(리뷰어가 읽기 전용 연결로 보고서 SQL 전부 재현, raw 14행에서 중립 10경기 직접 확인, 수정 되돌리면 테스트 3개 실패, 422 passed). [낮음] 3건은 보고서 문장(raw 행 증가 설명, 2023-24 중립 경기 목록, 추정 표시) → 작성자에게 수정 요청. 결정 D-042 기록, `is_neutral` 컬럼은 Q-013으로 사람에게.
  - 2026-10-23 PM: 보고서 문장 수정 `bad4900`(문서만) 확인 후 머지. 개발 DB: games 5,169, team_game_stats 7,946, raw 24행(4.7MB), DB 21MB. **단계 1 완료 기준 전부 충족.**

### P-023 CI에 postgres service container + REQUIRE_DB (+ concurrency 수정)
- **상태:** DONE (2026-10-22, PR #8 squash merge)
- **담당 역할:** platform-engineer (단계 1 예외 활성, D-022)
- **단계:** 1
- **의존:** P-002 (DONE). P-012와 같은 시기에 진행.
- **목표:** CI가 실제 PostgreSQL로 DB 테스트를 돌려 "DB 테스트 전부 skip"인 지금 상태를 없앤다. P-003 리뷰에서 나온 concurrency 문제도 같이 고친다.
- **범위:** `.github/workflows/ci.yml`
- **범위 밖:** 테스트 코드, 마이그레이션(P-012).
- **읽어야 할 파일:** `CLAUDE.md`, `.github/workflows/ci.yml`, `docker-compose.yml`, `.env.example`, `tests/conftest.py`, `docs/decisions.md` D-018, D-020, D-022
- **완료 기준:**
  1. `lint-and-test` 잡(또는 별도 `db-test` 잡)에 `services: postgres`(17)를 붙이고 `DATABASE_URL`과 `PRESAGIO_REQUIRE_DB=1`을 설정해, PR에서 DB 테스트가 skip 없이 통과한다(Actions 로그에 skipped 0 증거).
  2. 비밀번호는 CI 전용 가짜 값이고 워크플로 파일에만 있다(GitHub Secrets 불필요). gitleaks에 걸리지 않는다.
  3. `concurrency.cancel-in-progress`가 `pull_request`에서만 true이고 `push: main`에서는 취소되지 않는다(연속 머지 2회로 증명하거나 표현식 동작을 문서로 확인).
  4. 2회차 실행이 여전히 캐시를 복원하고 2분 이내다.
- **확인 방법:** PR의 Checks 탭, Actions 로그.
- **메모:**
  - 2026-10-22 PM: D-022(Q-005 승인)와 P-003 리뷰어 [낮음] 발견으로 생성.
  - 2026-10-22 PM: 사람 지시로 platform-engineer 세션 시작(Opus 5.5 높음). P-012와 병렬(파일 겹침 없음: ci.yml만).
  - 2026-10-22 PM: PR #8(https://github.com/oghdy/Presagio/pull/8) 도착. CI에서 21 passed, skipped 0(DB 테스트 6개 실제 실행), 2회차 31초 캐시 복원. concurrency는 PR만 취소(증명), main은 run_id 그룹(문서 근거, 머지 후 확인 필요). reviewer 카드 올림. 머지 순서: #8 먼저.
  - 2026-10-22 PM: 리뷰 판정 "수락 가능", 완료 기준 1~4 전부 O(3(b) main 미취소는 문서 근거 + 논리, 머지 후 확인). 리뷰어가 P-012 브랜치 테스트 33개를 같은 CI 조건에서 교차 확인(통과, 임시 DB 정리됨). [낮음] 3건(PR 본문 설명 1곳 부정확, squash 필수, 포트 0.0.0.0). PM 수락, squash 머지 조건. 결정 D-029 기록. 사람 머지 대기.
  - 2026-10-22 PM: 사람 지시로 squash merge. 후속 확인: main 연속 머지 2회에서 두 실행 모두 완료되는지(다음 머지 때 PM이 본다).

### P-022 단계 0~1 학습 노트 + 회고
- **상태:** DONE (2026-10-23, `docs/learning/stage-0-1-data-pipeline.md` + 용어집 12항목, main 직접 커밋)
- **담당 역할:** pm
- **단계:** 1
- **의존:** P-021
- **목표:** 사람이 면접에서 데이터 파이프라인을 설명할 수 있도록 쉬운 말로 정리한다.
- **범위:** `docs/learning/stage-0-1-data-pipeline.md`, `docs/learning/glossary.md` 갱신, `README.md` 결과 요약 갱신
- **완료 기준:** `docs/team/handoff-templates.md` F 양식을 모두 채운다. 면접 예상 질문 3개 포함.

---

## 단계 2. 피처 생성 + 베이스라인 모델 (2026-10-23 사람 승인, D-045)

> 사람이 승인한 계획(D-045). 새 PM 세션이 P-030 브리핑부터 쓴다. 상세 완료 기준은 P-030 승인 후 확정(아래 요지가 초안).
> 모델·노력(D-043): 설계·해석·누수 감사 = Fable 5.1 xhigh, 구현·리뷰 = Opus 5.5 높음, 실험 실행은 스크립트. `games.is_neutral` 컬럼은 이미 있다(D-046, D-044 정정). 2023-24 값 보완은 P-025.
> 단계 2 완료 기준(OVERVIEW): 과거 시즌에 대해 Elo 예측과 기준선 예측이 생성되고, 기본 지표가 계산된다.
> 방향(D-033 + 사람 메모): 시장을 기준선으로, 독립 모델은 척도. 실험은 가설 등록 → walk-forward → 실패도 기록. 보류 시즌은 끝까지 안 봄.

### 보드

| 티켓 | 제목 | 역할 | 상태 | 의존 |
| --- | --- | --- | --- | --- |
| P-024 | 맥 launchd 임시 스케줄 (단계 6 전까지 매일 수집 + 배당 스냅숏, D-038 시각) | platform-engineer | REVIEW (PR #23, 기준 5 대기) | P-017 |
| P-025 | 2023-24 중립 경기장 `is_neutral` 값 보완 (컬럼 추가 아님) + 2023-24 일정 `--force` 재수집 | data-engineer | DONE | P-021 |
| P-029 | 운영 문서를 단계 1 결정(D-005 보완·D-011/D-043·D-014·D-027)과 맞춘다 | pm | DONE | - |
| P-030 | 🧑 모델링 설계안 v1 + 실험 계획 (예측 대상, 피처, 시장 기준선, 가설 목록, 지표, 보류 시즌) | ml-engineer | REVIEW (PR #22) | P-021 |
| P-031 | 🧑 모델링 레이어 스키마 v1 (features, model_versions, predictions as_of 스냅숏, 참조 배당) → 마이그레이션 0002 (`is_neutral` 컬럼 추가 없음, D-046) | data-engineer | TODO | P-030 승인 |
| P-032 | walk-forward 평가 틀 + 지표(log loss, Brier, 보정 구간, MAE/RMSE) + 시장 확률·CLV 계산 | ml-engineer | TODO | P-030 승인 |
| P-033 | 기준선 3종: 시장 암시 확률(마진 제거), 항상 홈팀, 시즌 승률 + 결과 저장 | ml-engineer | TODO | P-031, P-032 |
| P-034 | Elo 레이팅(홈 어드밴티지, 시즌 간 평균 회귀, 중립 경기 처리) + 총점 기준선(최근 N경기 평균) | ml-engineer | TODO | P-033 |
| P-035 | as_of 피처 모듈 v1 (최근 5/10/20 공격·수비 효율·순득실·페이스, 휴식일·백투백, 홈/원정, 시즌 누적) + 누수 테스트 | ml-engineer | TODO | P-031 |
| P-036 | 누수 감사 1차 (P-032~P-035 대상: as_of 강제, 시간 여행 테스트, 미래 주입 테스트) | leakage-auditor | TODO | P-035 |
| P-037 | 실험 1차: 가설 등록 → 기준선 vs Elo vs 시장, 시즌별 walk-forward 지표 표 → `docs/experiments.md` | ml-engineer | TODO | P-034, P-035, P-036 |
| P-038 | 단계 2 학습 노트 + 단계 3 계획 초안 | pm | TODO | P-037 |

병렬: **P-024, P-025, P-029, P-030**은 서로 파일이 겹치지 않는다(`scripts/`+README / `ingestion/`+tests / `docs/team/`+CLAUDE.md / `docs/design/modeling-v1.md`). 사람 승인이 나오는 대로 동시에 띄울 수 있다. P-031 제목은 Q-015 승인(D-046)대로 고쳤다: `is_neutral` 컬럼 추가 없음, 마이그레이션 0002는 모델링 레이어 표만.

### 티켓 상세 (2026-10-23 새 PM이 확정. 완료 기준은 사람만 바꾼다, D-008)

### P-030 🧑 모델링 설계안 v1 + 실험 계획
- **상태:** REVIEW (2026-10-23 PR #22 https://github.com/oghdy/Presagio/pull/22. 1차 "수정 후 재리뷰" → v1.1 `5dc1a9c` → 2차 "수정 후 재리뷰"(**반려 2회차**, 기준 6만 X, N1 한 줄 + N2) → 작성자 수정 중 → 3차 재리뷰는 N1·N2 중심. 통과하면 사람 승인 M-1~M-11. 3차도 X면 D-008대로 사람에게)
- **담당 역할:** ml-engineer (설계안 작성. Fable 5.1, 노력 xhigh, D-043) → PM → 사람 승인
- **단계:** 2
- **의존:** P-021 (DONE)
- **목표:** 단계 2~4에서 "무엇을 어떤 데이터로 예측하고, 시장(배당)과 어떻게 비교·결합하고, 어떤 가설을 어떤 순서로 어떻게 검증할지"를 한 문서로 정해 사람 승인을 받는다. 이 문서가 P-031(모델링 스키마), P-032(walk-forward 틀), P-033~P-035(기준선·Elo·피처), P-037(실험 1차)의 입력이다. **사람 승인 전에 구현 티켓은 시작되지 않는다.** 코드는 쓰지 않는다.
- **범위(건드릴 파일):** `docs/design/modeling-v1.md` 하나(그림은 mermaid로 문서 안에).
- **범위 밖:** 코드, 마이그레이션·DDL(P-031이 설계안의 "요구 사항"을 받아 만든다), 실험 실행, `docs/experiments.md` 기록(P-037), 배당 결제 결정(사람, P-033 때), `docs/backlog.md`·`docs/decisions.md`.
- **읽어야 할 파일:** `CLAUDE.md`, `PRESAGIO_OVERVIEW.md` 1·2·3·7장, `docs/backlog.md` 단계 2 절(사람의 방향 메모 포함), `docs/decisions.md` D-004·D-023·D-025~D-028·D-033·D-035·D-036·D-038·D-039·D-042~D-045, `docs/design/schema-v1.md` 1-5·6·7·8·9장, `docs/research/backfill-report-stage1.md` 3·10장, `docs/research/nba_api.md` 3·6장, `docs/research/odds_api.md` 2.1·3·4장, `docs/experiments.md`, `docs/learning/glossary.md`, `.claude/agents/leakage-auditor.md`, `src/presagio/ingestion/possessions.py`, `src/presagio/db/models.py`(읽기만)
- **완료 기준 (전부 만족해야 DONE):**
  1. **예측 대상 표.** 승패(홈팀 승 확률)와 총점(예상 총점, 그리고 토탈 라인 대비 오버 확률) 각각에 대해 입력·출력·단위, 대상 경기 필터(`game_type` regular/playin/playoffs 기본. `cup_final`·플레이인 포함 여부는 M-항목), 예측 기준 시각(`as_of`) 후보와 D-038 수집 시각(16:00 KST 본 실행, 동부 18:45·19:45·21:45 마감 스냅숏)과의 관계가 표로 적혀 있다.
  2. **피처 후보 표.** 피처마다 이름, 정의(수식 또는 SQL 의사코드), 읽는 테이블·뷰(`games`, `team_game_stats_primary`, `odds_snapshots`/`odds_closing_lines`만), as_of 규칙(어느 시각 이전 행만 쓰는지), 버전 전략, 근거(출처 또는 사람 메모의 대리 지표). 최소 포함: 최근 5/10/20경기 공격·수비 효율·순득실·페이스, 휴식일·백투백, 홈/원정·중립, 시즌 누적, Elo, 시장 암시 확률. D-035 포제션 공식과 대안(basketball-reference식) 비교 실험 계획이 있다. 현재 스키마로 계산할 수 없는 대리 지표는 "데이터 요구"로 따로 적는다.
  3. **시장 기준선 정의.** 마진 제거 방식(D-036 비례 배분을 출발값으로, 대안 1개 이상과 비교), 북메이커 선택 규칙(특정 북메이커 vs 합의 확률), "예측 시점 배당"(as_of 이전 마지막 스냅숏)과 "마감 배당"(`odds_closing_lines`, `lead_time` 허용치)의 정의, CLV 정의, 배당이 없는 경기·시즌(2023-24~2025-26은 $59 백필 전까지)의 처리 규칙이 있다.
  4. **지표와 walk-forward 프로토콜.** 지표(정확도, log loss, Brier, 보정 구간표, 총점 MAE/RMSE, 시장 대비 log loss·Brier 차이, CLV)의 정의와 계산 단위, walk-forward 단위(재학습 주기)와 이유, 학습 시작·콜드 스타트 규칙, 시즌 경계 처리(Elo 평균 회귀 포함), 보류(holdout) 시즌 규칙(제안: 2025-26을 P-037까지 보지 않음. M-항목)이 있다.
  5. **가설 목록 8~12개.** 번호(H-01…), 한 문장 가설, 비교 대상, 지표, **사전 등록된 성공 판정 기준(숫자)**, 시장 포함/미포함 실험군, 배정 단계(2/3/4). 사람 방향 메모의 항목(시뮬레이션 모델, 감독·전술 대리 지표, 전략 토너먼트, 다폴더)이 어느 가설·단계에 들어가는지 또는 왜 지금 범위 밖인지 적혀 있다. `docs/experiments.md` 양식에 추가할 항목 제안(가설 번호, 사전 판정 기준, 보류 시즌 미접촉 선언)이 있다.
  6. **P-031에 넘길 모델링 레이어 요구 사항.** `features`, `model_versions`, `predictions`, `evaluations`에 필요한 컬럼·키·as_of 규칙을 요구 형태(DDL 아님)로. D-033(경기당 여러 as_of 스냅숏 + 참조 배당 스냅숏), schema-v1 9장 초안, D-030 교훈(features는 계산 시 저장, 재계산 덮어쓰기 금지)이 반영돼 있다.
  7. **누수 자기 점검 절.** 피처·기준선·평가마다 "as_of 이후 정보가 섞일 수 있는 경로"와 방어책(시간 여행 테스트·미래 주입 테스트 아이디어). 브리핑의 leakage-auditor 관점 질문 8개에 전부 답한다. "누수 없음" 선언은 하지 않는다(판정은 감사관, D-006).
  8. **"사람이 결정할 지점" 절.** M-1부터 번호, 각각 추천안·대안·영향. 최소 포함: 예측 기준 시각, 보류 시즌, `cup_final`·플레이인 포함 여부, 북메이커 선택, 마진 제거 방식, walk-forward 단위, Elo 출발 상수(K·홈 어드밴티지·회귀율)와 출처, 시장 비교 불가 기간의 처리.
  9. **문서 품질.** 맨 앞에 사람용 쉬운 말 요약(10문장 이내)과 새 용어 풀이. 문서의 모든 숫자는 출처(개발 DB 쿼리 원문 또는 보고서 절)가 있고, 문헌 값은 출처가 있으며, 확인 못 한 것은 "(미확인)"으로 표시한다. 사람이 승인하면 PM이 D-번호로 기록한다.
- **확인 방법:** 사람이 설계안을 읽고 M-번호에 답한다. reviewer(Opus 5.5 높음)가 문서 리뷰(숫자를 개발 DB에서 재현, 수식 검토, 누수 경로 점검)를 하고 PM이 기준 1~9를 대조한다.
- **메모:**
  - 2026-10-23 PM: 브리핑 초안 `P-030-briefing.md`(공유 노트) 작성. 사람 확인 후 ml-engineer 세션(Fable 5.1 xhigh, 새 worktree) 시작. 브랜치 `docs/P-030-modeling-design`. 검증 노트 F-5의 데이터 사실(cup_final 별도 타입, `team_game_stats_primary`, `odds_closing_lines`, 배당은 live 1회분)을 브리핑에 넣었다.
  - 2026-10-23 PM: 사람이 브리핑 확정 → 세션 시작. P-024·P-025 세션과 동시(파일 겹침 없음). PR이 오면 reviewer(Opus 높음)에게 문서 리뷰(숫자 재현·수식·누수 경로)를 맡기고, 누수 관점은 리뷰 브리핑에 체크리스트로 넣는다.
  - 2026-10-23 PM: ml-engineer 세션(Fable xhigh) 끝 → PR #22 도착(파일 1, +779, CI 초록. 결정 지점 M-1~M-11, 가설 H-01~H-12). 자동 턴이라 reviewer(Opus 5.5 높음) **서브에이전트**가 detached worktree에서 리뷰: 기준 1~9 대조, 12-1 쿼리 재현, 수식 검토, 스키마 대조, 누수 질문 8개.
  - 2026-10-23 PM: 리뷰 판정 **수정 후 재리뷰**(반려 1회차). 기준 1·2·3·4·6·7·8 O, **5·9 X**. 12-1 쿼리 19개 전부 재현(2건은 P-025 실행으로 값이 바뀜), 수식(암시 확률·마진 제거·Elo·log loss·Brier·ECE·CLV·포제션·페이스) 전부 맞음, 문서가 전제한 컬럼·뷰 전부 존재. [심각] 2: (1) 학습 행 피처의 as_of 규칙 부재 → 라벨 누수 경로, (2) M-9 영향 숫자 오류(주말 11:45 캡처로 메워지는 낮 경기 492 중 60 → 커버리지 89.1%, "약 100%" 아님). [중간] 8: H-05 포제션 비교가 설계상 약분돼 구분력 없음, H-04 carry가 DR-1 없이 선택 불가, H-10이 2024-25에서 계산 불가, 9-3 T1·T2 절차가 결정성 확인에 그침, T1·T2·T6 빈 곳, T3이 불가능한 경우 시험, 과거 백필 lead_time 10분 주장이 $59 상한과 불일치, M-10 대안 Δ=24h 오류. [낮음] 10(출처·숫자 7건 포함). 누수 관점: "누수 없음" 선언 없음(정상), 누수 의심 경로 1·테스트 결함 4. 작성자(Fable xhigh)에게 수정 요청, 같은 리뷰어 재리뷰 예정. 기준 자체 메모(PM): 기준 2 읽는 표 목록에 `odds_events`가 빠짐(D-036 매핑에 필수, 위반으로 안 셈), 기준 5 하위 변형 셈 방식 미정, 기준 6의 "D-030 교훈"은 실제로는 schema-v1 9장 출처, 기준 9가 사실 숫자와 제안값을 구분 안 함, 읽을 파일 `models.py`는 실제로 패키지 `models/`.
  - 2026-10-23 PM: 작성자 v1.1 커밋 `5dc1a9c`("apply P-030 review round 1") push, PR 본문 갱신. 작성자 보고: 필수 6묶음 전부 반영, 낮음·참고 반영, 12-1 쿼리 19개 실행 시각과 함께 재실행. 사람 휴식 지시로 대기 → 12:45 사람 복귀, 같은 reviewer 서브에이전트가 재리뷰 시작(발견 1~20 처리 여부, 기준 1~9 재대조, 쿼리 재실행).
  - 2026-10-23 PM 13:1x: **2차 재리뷰 "수정 후 재리뷰"**(`P-030-review-2.md`, 반려 2회차). 1차 발견 20건 중 18 해결·2 부분(12 LightGBM CV grep, 15 시장 비교 집합). 12-1 쿼리 26개 전부 재현, Q-U 커버리지 스크립트 재현(D-038 그대로 87.6%, 주말 +2회 97.0%, +3회 98.5%). 기준 6만 X: **N1** 새로 넣은 `features` CHECK `computed_at − as_of ≤ 2h`가 origin을 안 가려 백테스트 피처 저장 불가(6-2·8-1·T14와 모순, 한 줄 수정 `origin <> 'live' OR ...`). **N2** 시장 비교 집합이 4-6(available ∪ pred_only)과 6-1·H-03(available)에서 다름. [낮음] N3~N9(M-9와 D-027·P-024 충돌 표기, T9 grep에 lightgbm.cv 등, H-05(b) 숫자 기준, 옮겨 적기 오류 5건, T14 라벨 제외, market_status 목록 누락, "(제안)" 표시 누락). 누수 관점: 1차 의심 경로는 규칙·경로 행·T14로 닫혔으나 저장 경로가 N1에 막힘, "누수 없음" 선언 없음. 작성자에게 수정 요청. **PM 결정(M-9 vs P-024):** P-024는 현행 기준(하루 정확히 4회)으로 끝내고, M-9(주말 추가 캡처)가 승인되면 D-027 개정 + 후속 티켓(DR-8)으로 처리한다.
  - 2026-10-23 PM 13:08: 작성자 v1.2 커밋 `afe7b63`(924줄, CI 초록. 작성자 보고: N1 CHECK를 live 행에만, N2 시장 비교 집합 통일, N3~N9·참고·P-025 머지 반영). 같은 reviewer 서브에이전트에게 **3차 재리뷰**(마지막 회차) 요청 → `P-030-review-3.md`. 재리뷰 방법: 같은 reviewer 서브에이전트(이 PM 세션 안) 또는 새 서브에이전트가 `~/.claude/worktrees/presagio-review-p-030`에서 `git fetch origin docs/P-030-modeling-design && git checkout --detach origin/docs/P-030-modeling-design` 후 1차 보고서 `P-030-review.md`의 발견 1~20 처리 여부 + 기준 5·9 재대조.

### P-024 맥 launchd 임시 스케줄 (단계 6 전까지 매일 수집 + 배당 스냅숏)
- **상태:** REVIEW (2026-10-23 PR #23 https://github.com/oghdy/Presagio/pull/23, `126d857`. 1차 리뷰 "수정 후 재리뷰"(기준 1~4 O, 차단 경로 결함 2건) → 작성자 수정 중(15:30 KST 전 커밋 또는 16:05 후, 16:00 실행 보호). **기준 5는 16:00 KST launchd 실행 후 PM 확인**)
- **담당 역할:** platform-engineer (단계 6 역할의 선행 예외 활성. D-002 예외, P-003·P-023과 같은 방식)
- **단계:** 2 (단계 6 선행)
- **의존:** P-017 (DONE), Q-017 승인
- **목표:** 단계 6 배포 전까지 올 시즌 배당 스냅숏과 매일 결과 수집이 끊기지 않게, 이 맥에서 launchd로 `presagio ingest-daily`를 D-038 시각에 돌린다. 임시 장치이며 단계 6에서 서버 스케줄로 대체하고 제거한다.
- **범위(건드릴 파일):** `scripts/launchd/`(plist 템플릿, 설치·제거 스크립트, 실행 래퍼), `README.md` 8절 아래 운영 소절.
- **범위 밖:** 스케줄러 도구 선택(Prefect 등), 클라우드 배포, 알림, 신선도 체크(전부 단계 6), 제품 코드(`src/`), 스키마, `.env` 내용 변경.
- **읽어야 할 파일:** `CLAUDE.md`, `README.md` 8·9절, `docs/decisions.md` D-027·D-037(종료 코드)·D-038·D-039, `src/presagio/cli.py`(`ingest-daily` 옵션), `.env.example`
- **완료 기준 (전부 만족해야 DONE):**
  1. `scripts/launchd/`에 plist와 설치·제거 명령이 있고, README에 "켜는 법 / 끄는 법 / 로그 보는 법 / 맥이 꺼져 있던 날을 확인하는 쿼리(`ingestion_runs`)"가 있다. 새 클론에서 설치 명령을 실행하면 `launchctl list | grep presagio`에 잡이 보인다(D-021: 명령 + 예상 결과).
  2. 하루 **정확히 4회**만 실행된다: 16:00 KST 본 실행 1회, 동부 18:45·19:45·21:45 `--odds-only` 3회. 미국 서머타임 종료(2026-11-01) 전후 모두 **동부 시각 기준**으로 맞는다(테스트 또는 dry-run 로그로 증명). 중복 호출은 없다(월 최대 372크레딧, D-027).
  3. 실행 결과(종료 코드, 표준 출력·오류)가 날짜별 로그 파일로 남고, 종료 코드 2(차단)·1(실패/partial)·0이 로그에서 구분된다.
  4. 레포 경로·uv 경로·`.env` 위치가 plist에 사람 손으로 박히지 않고 설치 스크립트가 채운다. 루트 `.env`의 키는 읽기만 하고 복사·출력하지 않는다.
  5. 실제 설치 후 본 실행 1회의 `ingestion_runs` 결과(run_id, status, 배당 스냅숏 행 수)를 PR에 적는다. 외부 호출은 그 1회(nba_api 2~3회 + 배당 3크레딧) 이내. Q-017 승인이 이 호출의 승인이다.
- **확인 방법:** `launchctl list | grep presagio`, 로그 파일, `SELECT job_type, scope_key, status, started_at FROM ingestion_runs ORDER BY run_id DESC LIMIT 10`.
- **메모:**
  - 2026-10-23 PM: 창업 세션 검증 노트 F-3로 생성. 맥·Docker Desktop이 꺼진 날은 결손이고 `ingestion_runs`에 run이 없는 것으로 드러난다(정직한 결손). 브랜치 `feat/P-024-launchd-schedule`.
  - 2026-10-23 PM: platform-engineer 세션(Opus 5.5 높음) 시작. 세션이 10:57 KST 타이머를 기다리며 PR 본문을 준비해 둔 상태(10:17 기준 launchd 잡 미설치).
  - 2026-10-23 PM 12:40: PR #23 도착(`scripts/launchd/` plist 템플릿·`install.sh`·`uninstall.sh`·`presagio_launchd.py` 래퍼, `tests/scripts/test_launchd_schedule.py`, README 운영 소절). 작성자 보고: 래퍼가 매 기동마다 실제 시계로 동부 시각을 판단해 하루 4회만 실행(서머타임 전환 두 번을 포함한 7개월 모의 테스트), `--odds-only`는 CLI가 시각을 판단하지 않아 래퍼가 전부 결정, 로그는 날짜별 OK/FAILED/BLOCKED. 12:36 KST에 잡 `com.presagio.ingest-daily` 설치(공유 체크아웃 대상, 래퍼 파일은 작업자 worktree 경로) 후 `kickstart` 1회 → **`uv` 프로세스(PID 82025)가 macOS 개인정보 보호(TCC) 창 "uv가 데스크탑 폴더에 접근"에 막혀 멈춤.** 사람이 Allow를 눌러야 풀리고, 그 전까지 16:00 본 실행 포함 어떤 예약 실행도 안 돌아간다. 외부 호출 0, run 95 그대로, odds 796 그대로. 미확인: 맥 완전 종료 중 예약 실행 처리(Apple 문서는 sleep만: 깨어나면 1회 실행). 작성자 인계 노트 `P-024-handover.md`. 머지 후 PM이 공유 체크아웃에서 `scripts/launchd/install.sh` 재실행(worktree 정리 전에).
  - 2026-10-23 PM 12:44: 사람이 uv의 데스크탑 폴더 접근을 허용 → 멈춰 있던 실행이 12:42 `skip: no slot now (23:42 ET, 12:42 KST)` 로그를 남기고 정상 종료, 잡 로드 상태(마지막 종료 코드 0). reviewer(Opus 5.5 높음) 서브에이전트가 detached worktree에서 리뷰 시작(설치된 잡은 건드리지 않음, 기준 5는 "대기"로 표시). **16:00 KST 뒤 PM이 `ingestion_runs`·`~/Library/Logs/Presagio/ingest-2026-10-23.log` 확인.**
  - 2026-10-23 PM 13:2x: **1차 리뷰 "수정 후 재리뷰"**(`P-024-review.md`). 기준 1~4 O(1의 "새 클론 설치"는 실행 금지라 스크립트 논리로 판정), 기준 5 대기. 리뷰어가 2026-10~2027-05 깨어남 852개를 `zoneinfo`로 독립 계산: KST 날짜당 정확히 4회, 동부 날짜당 마감 3회, 중복 0, 1분 간격 스윕에서도 최대 4회. 461 passed(네트워크 차단), 변이 6종 잡힘. 설치된 래퍼 해시 = 커밋. [중간] 2: (1) 차단 뒤 24h 정지의 경계가 시작 시각 초 단위 오차에 좌우돼 다음 날 같은 슬롯을 반반 확률로 놓침(본 실행이면 그날 예측 시점 배당 영구 결손), (2) uv 자체 종료 코드 2를 "BLOCKED(403/429)"로 오진하고 24h 정지. [중간~낮음] install.sh에 다른 레포를 인자로 주면 래퍼 경로가 스크립트가 든 체크아웃을 가리킴(지금 설치 상태). [낮음] 자식 타임아웃 없음, README `.env` 문구, 비밀값 숨김·오류 경로 테스트 부재, man 페이지 인용 오류, 시각 변경 시 재설치 필요 미기재. 작성자에게 수정 요청(16:00 실행 보호 규칙 포함). 기준 자체 메모(PM): CLAUDE.md 4장 "파이썬은 src/ 아래에만"과 `scripts/` 파이썬 래퍼 충돌 → Q-019; 기준 1의 "새 클론 설치"는 기계 상태 명령이라 리뷰어가 실행 불가(D-021) → 다음부터 "스크립트 논리 + 작성자 설치 출력"으로. **PM 운영 조치:** 머지 후 재설치 전까지 작업자 worktree 삭제 금지, 재설치 뒤 plist의 래퍼 경로에 `.claude/worktrees` 없는지 확인.
  - 2026-10-23 PM 13:08: 작성자 수정 커밋 `3a4dddd`("base block pause on slot times and trust only ingest-daily exit codes", 파일 7, +1187, 래퍼 테스트 50, CI 초록). 작업자 worktree 깨끗(래퍼 해시 `baf87eed…`), 설치된 잡은 그 파일을 16:00에 실행. 같은 reviewer 서브에이전트에게 재리뷰(차단 경로 1~8, 평상시 경로 회귀 확인) 요청 → `P-024-review-2.md`. 기준 5는 16:00 뒤 PM. PM 지시 변경: 16:00 launchd 실행을 기다리지 말고 10:57 확인 뒤 PR을 먼저 열고, 기준 5(실제 본 실행 1회)는 16:00 KST launchd 실행 결과로 채우되 확인 쿼리를 PR에 적기. 설치한 잡은 제거하지 말 것(머지 후 PM이 공유 체크아웃에서 재설치). **16:00 이후 PM이 `ingestion_runs`로 본 실행 결과를 확인해야 한다.**

### P-025 2023-24 중립 경기장 `is_neutral` 값 보완 (컬럼 추가 아님)
- **상태:** DONE (2026-10-23, PR #21 squash merge `f77b691`. 사람 지시("그래 그러면 머지해줘", 추천대로 테스트 한 줄은 후속으로)로 PM이 머지. 머지 후 main에서 `backfill --season 2023-24 --force` 1회: run 96(자식 97~101) 5호출 succeeded, code_version `f77b691`, 행 수·중립 5경기 불변, 품질 체크 14 pass)
- **담당 역할:** data-engineer
- **단계:** 2 (단계 1 보수)
- **의존:** P-021 (DONE), Q-015 (a) 승인
- **목표:** `games.is_neutral`은 schema v1에 이미 있지만 2023-24 시즌은 NBA 일정 응답의 `isNeutral`이 전부 false라 중립 경기 5개(멕시코시티 `0022300172`, 파리 `0022300527`, 라스베이거스 인시즌 토너먼트 준결승 `0022301229`·`0022301230`, 결승 `0062300001`)가 false로 저장돼 있다. 일정 파서에 보완 규칙을 넣고 2023-24 일정을 다시 받아 값을 채운다. Elo 홈 어드밴티지와 홈/원정 피처(P-034·P-035)가 이 값을 쓴다.
- **범위(건드릴 파일):** `src/presagio/ingestion/sources/nba_api/schedule.py`(보완 규칙), 필요 시 `src/presagio/ingestion/loaders/games.py`, `tests/ingestion/`, `tests/fixtures/nba_api/`(2023-24 중립 경기 1~2개가 든 작은 일정 샘플), `docs/design/schema-v1.md` 6-1 적재 규칙 표 한 줄(스키마 아님).
- **범위 밖:** 스키마·마이그레이션(컬럼은 이미 있음), 다른 시즌 값 변경, 모델링 레이어(P-031), `docs/backlog.md`·`docs/decisions.md`.
- **읽어야 할 파일:** `CLAUDE.md`, `docs/decisions.md` D-034·D-042·D-044(Q-015로 정정 예정), `docs/design/schema-v1.md` 6-1, `docs/research/backfill-report-stage1.md` 4-1·10장, `tests/fixtures/nba_api/scheduleleaguev2__2023-24.json`
- **완료 기준 (전부 만족해야 DONE):**
  1. 보완 규칙이 코드와 schema-v1 6-1 적재 규칙 표에 적혀 있다. 규칙 후보 둘 중 하나를 raw 응답 근거로 고르고 PR에 이유를 적는다: (a) `isNeutral` OR `arenaCity`가 중립 개최지 목록(Mexico City, Paris, Las Vegas, London, Berlin, Manchester 등)에 있음, (b) game_id 고정 목록. 어느 쪽이든 `isNeutral=true`인 경기는 그대로 true다.
  2. 테스트: 2023-24 일정 픽스처로 파서가 중립 경기를 true, 일반 경기를 false로 낸다. `PRESAGIO_REQUIRE_DB=1 uv run pytest -q` 전부 통과.
  3. 개발 DB에서 `uv run presagio backfill --season 2023-24 --force` 후 `SELECT game_id FROM games WHERE season='2023-24' AND is_neutral ORDER BY 1`이 위 5경기 정확히 그 집합을 돌려주고, 다른 시즌의 true 수(2024-25 6, 2025-26 6, 2026-27 4)와 `games` 5,169·`team_game_stats` 7,946 행 수가 변하지 않는다(전후 count를 PR에).
  4. D-042 2항대로 PR 본문에 "영향받는 단위: 2023-24 일정"과 실제 `--force` 실행 결과(호출 수, 종료 코드)를 적는다. 외부 호출은 최대 5회(teams 1 + 일정 1 + 게임로그 3) 이내.
- **확인 방법:** 기준 3의 쿼리, `uv run presagio quality-check --season 2023-24` 14 pass.
- **메모:**
  - 2026-10-23 PM: 창업 세션 검증 노트 F-1로 생성. D-044의 "컬럼 추가"는 틀린 전제였다(컬럼은 마이그레이션 0001에 있음) → Q-015로 정정 요청. 브랜치 `fix/P-025-neutral-2023-24`.
  - 2026-10-23 PM: data-engineer 세션(Opus 5.5 높음) 시작 → 같은 날 PR #21 도착(+493/-4, 파일 4, CI 초록). reviewer(Opus 5.5 높음) 서브에이전트가 detached worktree에서 독립 리뷰(D-006, D-014).
  - 2026-10-23 PM: 작성자 수정 커밋 `f2bfce3`(파일 6, +778/-5: 픽스처에 파리·서부 준결승 경기 추가 + 집합 단언, `scoreboard.py:103`도 같은 `is_neutral_game` 규칙 + 테스트, PR 본문 code_version 설명, 6-1 스코어보드 칸 정정). **재리뷰 "수락 가능"**(`P-025-review-2.md`): 1차 [낮음] 1~3 전부 해결, 425 passed(네트워크 차단), 변이 검사 12종(1차에 살아남은 오타 변이 2개가 이제 실패), raw 7/11/2/17 재파싱 = DB, 스코어보드 raw 72·73 영향 없음. 새 [낮음] 1: 스코어보드 테스트가 `isNeutral=true` 유지를 안 검사(코드는 맞음, 한 줄 보강 가능). **PM 수락.** 수락 메시지 `P-025-acceptance.md`. 사람 머지 대기. 머지 후 PM: main에서 `backfill --season 2023-24 --force` 1회(5호출)로 run `code_version` 맞춤, 리뷰 worktree 제거.
  - 2026-10-23 PM: 1차 리뷰 판정 **수락 가능**, 기준 1~4 전부 O(리뷰어가 424 passed·네트워크 차단 상태 재현, 개발 DB 쿼리로 5경기 집합 확인, 커밋된 파서를 raw 7에 돌려 DB와 일치, 2023-24 전 경기를 훑어 놓친 중립 후보 0, 변이 검사 6종). 규칙은 (b) game_id 고정 목록(작성자 근거: 라스베이거스 프리시즌은 비중립, 2026-27 컵 결승은 인디애나폴리스인데 중립, 스코어보드에 arenaCity 없음 → 도시 목록으로 표현 불가). [낮음] 3건: (1) 파리·서부 준결승 ID가 테스트와 묶여 있지 않아 오타가 살아남음 → 픽스처 2경기 추가 + 전체 집합 단언, (2) 스코어보드 경로(`scoreboard.py:101`)가 과거 날짜 `ingest-daily`에서 값을 되돌릴 수 있음 → **PM이 범위 추가 승인**, 같은 규칙 적용 + 테스트 + 6-1 문장 정정, (3) run 84~95 `code_version`이 미커밋 상태(`01af36d`)로 기록됨 → PR 본문에 한 줄, 머지 후 PM이 main에서 `--force` 1회로 provenance 맞춤. 작성자에게 수정 요청(반려 아님, 재리뷰 1회차). 기준 자체 메모: 기준 4 "최대 5회"는 브리핑의 멱등 재실행과 충돌 → 다음부터 "실행당 N회, 합계 M회"로 쓴다. 후속(범위 밖, PM 문서 정리 때): `nba_api.md` 7장 픽스처 표, `backfill-report-stage1.md` 10장의 "중립 여부가 DB에 없다" 문장, `code_version` dirty 표시(단계 6 후보).

### P-029 운영 문서를 단계 1 결정(D-005 보완·D-011/D-043·D-014·D-027)과 맞춘다
- **상태:** DONE (2026-10-23, PR #20 squash merge `d631cee`. 사람 지시로 PM이 머지, D-005 보완)
- **담당 역할:** pm
- **단계:** 2
- **의존:** 없음 (Q-016 승인 → D-047도 같은 PR에)
- **목표:** 단계 1에서 내린 운영 결정이 운영 문서(`docs/team/*`, `CLAUDE.md`)에 반영되지 않아 새 세션이 옛 규칙을 읽는다(검증 노트 F-4). 문서를 결정과 맞춘다. 규칙을 새로 만들지 않는다.
- **범위(건드릴 파일):** `docs/team/risks.md`, `docs/team/workflow.md`, `docs/team/organization.md`, `CLAUDE.md`
- **범위 밖:** 결정 변경, 역할 파일(`.claude/agents/`), 제품 코드, `PRESAGIO_OVERVIEW.md`(3장은 이미 D-038대로임).
- **완료 기준 (전부 만족해야 DONE):**
  1. `risks.md` B2가 "하루 최대 4회(D-027, 시각은 D-038)"이고, B8의 "uv 미설치"가 현재 상태(uv 설치됨, D-013 `only-managed`)로 갱신된다.
  2. `workflow.md` 리뷰어 시작 프롬프트에 D-014의 `git fetch origin <브랜치> && git checkout --detach origin/<브랜치>`와 `git diff main...HEAD --stat` 확인 단계가 있고, 7장(머지)에 D-005 보완("사람이 명시하면 PM이 `gh pr merge --squash`")이 있고, "세션 시작은 사람 턴에서만, 자동 턴은 카드 또는 서브에이전트(전용 worktree)" 한 줄이 있다.
  3. `organization.md`에 D-011·D-043 모델·노력 표가 있다.
  4. `CLAUDE.md` 7장(또는 1장)에 D-005 보완 한 줄이 있다. Q-016이 승인되면 "PM 문서 변경도 브랜치 + PR"(D-047)로 바뀐다.
  5. `git grep -n "하루 2회" docs/team CLAUDE.md`가 0줄이고, 바뀐 문장마다 D-번호가 붙어 있다. PR은 문서만(`src/`·`tests/` 변경 0).
- **확인 방법:** PR diff, 기준 5의 grep.
- **메모:**
  - 2026-10-23 PM: 검증 노트 F-4로 생성. P-030과 파일이 겹치지 않아 병렬. 브랜치 `docs/P-029-ops-docs-sync`. PM이 직접 쓰고 사람이 머지한다(리뷰어 없음, 문서 정합성 작업).
  - 2026-10-23 PM: PR #20. 문서 4개, +43/-8줄. `git grep "하루 2회" docs/team CLAUDE.md` 0줄 확인. D-047 문구 포함. 사람 머지 대기.
  - 2026-10-23 PM: 사람 지시("PR #20 머지해줘. squash로")로 PM이 squash 머지 `d631cee`. DONE.

### 티켓 초안 요지 (상세 완료 기준은 P-030 승인 후 새 PM이 확정)

- **P-030 (🧑):** 산출물 `docs/design/modeling-v1.md`. 예측 대상(홈 승률, 예상 총점 → 언더/오버), 피처 후보와 근거(사람 메모의 감독·전술 대리 지표 포함), 시장 기준선 정의(마진 제거 방식, 어느 스냅숏을 "예측 시점"으로 볼지), 예측·추천 시각 제안(D-038과 연결), 가설 10개 내외, 지표, 보류 시즌(제안: 2025-26을 끝까지 안 봄), 실험 등록 양식, 포제션 공식 비교 계획(D-035). "사람이 결정할 지점" 번호 목록 필수(P-011 방식).
- **P-031 (🧑):** 설계안 9장 초안을 확정. `predictions`는 `(game_id, model_version_id, as_of)` 유니크로 경기당 여러 스냅숏, 참조한 `odds_snapshots` 집합(또는 시각) 기록. `features`는 `(game_id, as_of, feature_version)`에 값 저장(재계산 금지, D-030 교훈). `games.is_neutral`은 이미 있으므로 다루지 않는다(D-046. 2023-24 값은 P-025). 마이그레이션 0002 + DDL 일치 테스트 확장.
- **P-032:** 한 명령으로 "날짜 순으로 이전 데이터만 학습 → 예측 → 지표" 실행기. 입력은 "예측 함수", 출력은 지표 표 + 보정 표. 시장 확률 변환과 CLV(예측 시점 vs 마감, 뷰 `odds_closing_lines`)도 여기. 이 틀이 없으면 이후 실험 비교가 불공정하므로 선행.
- **P-033/P-034:** 기준선과 Elo는 P-032 틀 위에서 돌아 `predictions`에 저장. Elo 상수·홈 어드밴티지·시즌 회귀율은 실험으로 정하되 첫 값은 문헌 기본값(설계안에 출처).
- **P-035:** 피처 함수는 전부 `as_of` 입력. `team_game_stats_primary`(D-028 A-6)만 읽음. 누수 테스트: as_of 이후 행을 바꿔도 피처가 변하지 않음, 미래 경기를 주입해도 불변.
- **P-036:** 작성자와 다른 세션. 피처·평가 코드가 as_of를 어기는 경로가 없는지, walk-forward가 지켜지는지. 보고서는 리뷰어 양식.
- **P-037:** 실험마다 번호·가설·설정·결과·판정을 `docs/experiments.md`에. 시장 포함/미포함 실험군 분리. 보류 시즌은 건드리지 않음.

### 단계 2 시작 전 결정된 것 (2026-10-23)
- D-043 모델·노력 배치, D-044 `is_neutral` 컬럼(P-031), D-045 계획 승인. 과거 배당 $59 백필은 **P-033 시장 기준선 만들 때** 사람이 결제 → PM이 백필 티켓(P-039 후보) 생성.
- 2026-10-23 새 PM: 창업 세션 검증 노트(`stage-1-verification-2026-10-23.md`, F-1~F-5)로 세 가지를 질문으로 올렸다. **Q-015** D-044 정정(`is_neutral` 컬럼은 이미 있음, 2023-24 값만 보완 → P-025), **Q-016** 브랜치 보호, **Q-017** 임시 스케줄(P-024). F-4 문서 드리프트는 P-029. P-031 보드 제목의 "`games.is_neutral`(Q-013) → 마이그레이션 0002"는 Q-015 결과대로 고친다.
- 2026-10-23 사람 승인(한 메시지): Q-015 (a) → D-046, Q-016 → D-047(사람이 GitHub에서 켠다), Q-017 (a) → D-048, P-029 진행, P-030 브리핑 확정. PM이 Q-018(PM 문서 PR 자율 머지)을 추가로 올림. 같은 날 P-030(ml-engineer, Fable xhigh)·P-024(platform-engineer, Opus 높음)·P-025(data-engineer, Opus 높음) 세션 시작, P-029는 PM.

## 나중에 (단계 2 이후, 티켓화 전 메모)

단계 1이 끝나면 PM이 사람 확인을 받고 티켓으로 만든다. 지금은 제목만.

- 단계 2: 피처 모듈(as_of), 롤링 효율/페이스, 휴식일/백투백, Elo, 단순 기준선, 누수 테스트, features 테이블 승인
  - (D-033) predictions는 경기당 여러 `as_of` 스냅숏 허용 + 참조 배당 스냅숏. 전 경기 승패 확률과 총점(언더/오버) 둘 다. 예측 시각 결정.
- 단계 4 (D-033 추가): edge 계산, 스테이킹(정액 vs 분수 Kelly), 가상 자금 시뮬레이터, ROI·CLV·낙폭 백테스트, `recommendations`·`bankroll_ledger` 표 승인, 과거 배당 백필($59) 재결정
- 단계 5 (D-033 추가): `GET /recommendations/today`, 대시보드에 오늘의 추천과 가상 자금 곡선
- **사람의 방향 메모(2026-10-22, 단계 2 계획에 반영할 것):**
  - 시장(배당)을 기준선으로 두고 "시장이 체계적으로 틀리는 지점"을 찾는 구조. 시장 암시 확률을 가장 강한 피처로, 독립 모델(Elo 등)은 척도·보정용. 정직한 지표는 CLV.
  - **시뮬레이션 모델:** 포제션 기반(페이스 × 공격·수비 효율 + 분산)으로 경기를 수천 번 돌려 점수 차·총점 분포 → 승률·언더/오버 확률. D-035 포제션 함수가 입력. 단계 3 후보.
  - **감독·전술·매치업:** 공개 데이터로 잡히는 대리 지표(페이스, 3점 시도 비율, 림 근처 비율, 로테이션 깊이, 상대 성향별 성적)부터. 라인업·플레이바이플레이 단위 피처는 단계 7(데이터 양이 큼).
  - **전략 토너먼트:** 가상 베터 여러 명 = (모델 변형 × 우위 임계값 × 스테이킹 규칙 × 마켓 종류 × 다폴더 정책). walk-forward로 ROI·CLV·최대 낙폭 비교. 과적합 방지: 전략을 미리 등록하고 보류 시즌으로 검증, "최고 전략"을 사후 선택하지 않음. 단계 4. `recommendations`·`bankroll_ledger`에 `strategy_id` 필요.
  - **다폴더(parlay):** 독립 가정이면 확률 곱, 같은 경기 조합은 상관관계 반영. 우위가 있는 레그만 묶어도 분산이 폭증하므로 시뮬로 비교. 단계 4.
  - 시장 행동을 배우려면 과거 배당 표본이 필요 → $59 백필(D-026 보류)을 단계 2 시작 전에 재결정.
  - **모델·노력 배치(Q-012, 사람 승인 대기):** 설계·가설·해석·누수 감사 = Fable xhigh, 구현·리뷰 = Opus 높음, 실험 실행 = 스크립트. 단계 2 순서: (1) 🧑 모델링 설계안 + 실험 계획, (2) walk-forward 틀 + 기준선(시장 암시 확률·홈팀·Elo), (3) 피처 파이프라인(as_of), (4) 가설 단위 실험(실패도 기록, 보류 시즌은 끝까지 안 봄), (5) 시장 포함/미포함 실험군 분리.
- 단계 3: 로지스틱 회귀, LightGBM, 총점 회귀, model_versions, 보정
- 단계 4: walk-forward 백테스트 명령, 지표, reliability diagram, 시장 확률 변환과 비교, experiments.md
- 단계 5: FastAPI 5개 엔드포인트, 테스트, 대시보드 기술 결정
- 단계 6: 매일 흐름 묶기, Docker 이미지, 배포 비용 비교, 알림, 신선도 체크, 7일 연속 운영
  - (P-025 리뷰, 2026-10-23) `ingestion_runs.code_version`에 작업 트리 dirty 표시가 없어 미커밋 코드로 돌린 run이 커밋 해시로만 남는다 → 로더 버전 기록(D-042 2항)과 함께 처리.
  - (P-025 리뷰) 진행 중 시즌에서 스코어보드가 보완 목록 밖 중립 경기에 `isNeutral=false`를 주면 일정의 true를 덮을 수 있다(스코어보드 raw 72·73에서는 미관찰). 2026-27 중립 경기(파리·맨체스터·멕시코시티·컵 결승) 날짜의 스코어보드 raw로 확인 후 필요하면 `is_neutral`을 일정만 갱신하게. 같은 티켓에서 `tests/ingestion/sources/test_nba_api_scoreboard.py`의 중립 테스트에 "`isNeutral=true`는 그대로 true" 단언 한 줄 추가(P-025 재리뷰 [낮음] 1, 머지 시 후속으로 넘김).
- 문서 정리(PM, P-038 또는 docs 티켓): `docs/research/nba_api.md` 7장 픽스처 표에 `scheduleleaguev2__2023-24_neutral.json` 추가, `docs/research/backfill-report-stage1.md` 10장 "중립 경기 여부가 DB에 없다" 문장을 D-046·P-025 이후로 갱신.
- 단계 7(선택): 부상자, 선수 피처, MLflow, 드리프트

## 아이디어 주차장 (범위 밖 아이디어는 여기에만 적는다)

- (비어 있음)
