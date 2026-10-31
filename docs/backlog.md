# Presagio 백로그 (티켓 보드)

> 상태 변경은 **PM만** 한다. 작업자는 PR 본문에 결과를 적는다.
> 티켓 양식과 규칙: `docs/team/handoff-templates.md` A, `docs/team/workflow.md` 1장.
> 상태: `TODO` → `IN PROGRESS` → `REVIEW` → `DONE`. 막히면 `BLOCKED(이유)`.
> 🧑 표시는 "사람 승인 게이트"가 있는 티켓이다. 승인 전에는 구현을 시작하지 않는다.

## 현재 단계: 2 (피처 생성 + 베이스라인 모델). 2026-10-23 D-045 승인 → 10-23 P-024·P-025·P-029·P-030·P-031·P-032·P-026 DONE(설계안 D-049, launchd 가동, 마이그레이션 0002 적용, 평가 틀, 2022-23 시즌 적재), 10-24 **P-033·P-040·P-041·P-042** DONE(기준선 3종 + 저장 경로 #41 `625edfa`, 클라우드 환경 #31 `24abf4b` + `presagio-dev` 생성(Q-023 (a), PM이 크롬으로; 기준 4는 P-033 클라우드 세션이 증명), 모델링 품질 체크 #49 `d7a0e01`, 클라우드 셋업 후속 #48 `b2d0ec1`). 개발 DB에 기준선 예측 5,288행 저장. 열린 PR 0. 10-28 **P-028** DONE(#61 `9a3073b`: 주말 마감 2회·DB 대기·RunAtLoad, D-059; 새 plist 설치 23:12). 10-29 **P-044** DONE(#63 `c63fb3c`: 마감 배당 캡처 + 개발 DB를 수집 VM으로, 하이브리드 D-058·D-060; 전환 ①~③ 10-29 완료, ④ 10-30 첫 VM 캡처 3회 OK + 맥 16:00 본 실행 VM DB 쓰기 OK). **개발 DB 기준은 이제 VM**(맥 로컬 컨테이너는 사본·테스트용). **다음: P-043(브리핑 완료 10-31, 사람 "띄워" 대기, 클라우드) → P-027 → P-034(Elo, 클라우드) → P-035 → P-036 → P-037 → P-038. Q-025 대기.** **main 브랜치 보호 적용(D-047): 모든 변경은 PR, 브랜치 최신화 필수.**

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
| P-024 | 맥 launchd 임시 스케줄 (단계 6 전까지 매일 수집 + 배당 스냅숏, D-038 시각) | platform-engineer | DONE | P-017 |
| P-025 | 2023-24 중립 경기장 `is_neutral` 값 보완 (컬럼 추가 아님) + 2023-24 일정 `--force` 재수집 | data-engineer | DONE | P-021 |
| P-029 | 운영 문서를 단계 1 결정(D-005 보완·D-011/D-043·D-014·D-027)과 맞춘다 | pm | DONE | - |
| P-030 | 🧑 모델링 설계안 v1 + 실험 계획 (예측 대상, 피처, 시장 기준선, 가설 목록, 지표, 보류 시즌) | ml-engineer | DONE | P-021 |
| P-026 | 2022-23 시즌 추가 수집 (Elo 워밍업·carry 탐색용, D-049 M-11) | data-engineer | DONE (PR #34 `4f0d9af`) | P-031 DONE ✓ |
| P-027 | 스탯 정정 빈도 품질 체크 (raw 버전 간 team_game_stats 변경 수, DR-2) | data-engineer | TODO | P-031 |
| P-028 | 주말 낮 경기 배당 캡처 2회 추가 (토·일 14:45·16:45 ET, D-027 개정, D-049 M-9) + 운영 관찰 ⑤⑥ 대응·23h 문구 | platform-engineer | DONE (10-28, #61 `9a3073b`) | P-024 DONE ✓ |
| P-043 | odds 단위 partial 판정·종료 코드 분리(거부 가격 비율 임계, warn/fail) + `ingestion_runs.started_at` 호스트 시계 기록 (P-028 운영 관찰 ②③) | data-engineer | TODO | P-028 관찰 |
| P-044 | 🧑 매일 배당 수집 + 개발 DB를 소형 클라우드 VM(DigitalOcean $6, 싱가포르)으로 이전 — 하이브리드(nba_api는 맥 16:00 본 실행), Tailscale, 백업, 맥 launchd 축소 (D-058, 단계 6 선행) | platform-engineer | DONE (10-29, #63 `c63fb3c`; 전환 완료, 10-30 첫 VM 캡처 3/3 OK) | P-028, 사람 준비 |
| P-040 | 🧑 Claude Code 클라우드 세션 환경 셋업 (셋업 스크립트, SessionStart 훅, 환경 생성 절차) — 크레딧 US$250, 2026-11-05 만료 | platform-engineer | DONE (PR #31 `24abf4b`, 기준 4 = P-033 세션 증거) | P-024 DONE |
| P-042 | 클라우드 셋업 후속 (셋업 칸 cwd 대처, 캐시 낡음 경고, /remote-env 안내, 성공 경로 테스트, 훅 출력 한 줄) | platform-engineer | DONE (PR #48 `b2d0ec1`) | P-040 DONE |
| P-041 | 모델링 스키마 후속 보강 (P-031 리뷰 낮음 10건: feature_id 정합 품질 체크, 인덱스 정의 비교 테스트, feature_drift SKIP, 9장 문구, README 14→17) | data-engineer | DONE (PR #49 `d7a0e01`) | P-031 DONE, Q-022 |
| P-031 | 🧑 모델링 레이어 스키마 v1 (설계안 8장 → DDL) → 마이그레이션 0002 + 품질 체크 3개 + 뷰 1개 | data-engineer | DONE | P-030 DONE |
| P-032 | walk-forward 평가 틀 + 지표 + 시장 확률(비례·Shin·power, 합의) + CLV + 보류 시즌 가드 + 누수 테스트 틀 | ml-engineer | DONE | P-030 DONE |
| P-033 | 승패 기준선 3종(B0 항상 홈, B1 시즌 승률, B3 시장) + 결과 저장 경로(model_versions·predictions·evaluations, 덮어쓰기 금지) | ml-engineer | DONE (PR #41 `625edfa`) | P-031·P-032 DONE ✓ |
| P-034 | Elo 레이팅(홈 어드밴티지, 시즌 간 평균 회귀, 중립 경기 처리) + 총점 기준선(최근 N경기 평균) | ml-engineer | TODO | P-033 |
| P-035 | as_of 피처 모듈 v1 (최근 5/10/20 공격·수비 효율·순득실·페이스, 휴식일·백투백, 홈/원정, 시즌 누적) + 누수 테스트 | ml-engineer | TODO | P-031 |
| P-036 | 누수 감사 1차 (P-032~P-035 대상: as_of 강제, 시간 여행 테스트, 미래 주입 테스트) | leakage-auditor | TODO | P-035 |
| P-037 | 실험 1차: 가설 등록 → 기준선 vs Elo vs 시장, 시즌별 walk-forward 지표 표 → `docs/experiments.md` | ml-engineer | TODO | P-034, P-035, P-036 |
| P-038 | 단계 2 학습 노트 + 단계 3 계획 초안 | pm | TODO | P-037 |

병렬: **P-024, P-025, P-029, P-030**은 서로 파일이 겹치지 않는다(`scripts/`+README / `ingestion/`+tests / `docs/team/`+CLAUDE.md / `docs/design/modeling-v1.md`). 사람 승인이 나오는 대로 동시에 띄울 수 있다. P-031 제목은 Q-015 승인(D-046)대로 고쳤다: `is_neutral` 컬럼 추가 없음, 마이그레이션 0002는 모델링 레이어 표만. 2026-10-23 D-049로 P-026·P-027·P-028 추가, P-031·P-032 상세 확정.

### 티켓 상세 (2026-10-23 새 PM이 확정. 완료 기준은 사람만 바꾼다, D-008)

### P-030 🧑 모델링 설계안 v1 + 실험 계획
- **상태:** DONE (2026-10-23, 사람 승인 Q-020 "추천대로" → D-049. PR #22 squash merge `482a449`)
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
  - 2026-10-23 PM 13:08: 작성자 v1.2 커밋 `afe7b63`(924줄, CI 초록. 작성자 보고: N1 CHECK를 live 행에만, N2 시장 비교 집합 통일, N3~N9·참고·P-025 머지 반영). 같은 reviewer 서브에이전트에게 **3차 재리뷰**(마지막 회차) 요청 → `P-030-review-3.md`.
  - 2026-10-23 PM 13:3x: **3차 재리뷰 "수락 가능 + 후속 메모"**: N1·N2 해결(CHECK는 live 행에만, 시장 비교 집합 통일), N3~N9 전부 반영, 기준 1~9 전부 O, 12-1 쿼리 28개 재현, 새 모순·숫자 오류·누수 경로 없음. 누수 관점: 1차 의심 경로 닫힘(6-2 규칙, live-only CHECK, live 우선, T14), "누수 없음" 선언 없음, 최종 판정은 P-036. [낮음] 후속 2: L1 T9의 `.sample(frac=1)` 금지가 평가 모듈의 경기 단위 부트스트랩(`replace=True`)과 충돌 가능 → P-032 때 "replace 없는 셔플만 금지"로, L2 M-9 추천안 칸에 D-027 표기. 참고: 결손 날 보충 행의 origin은 사실상 `backtest`(한 줄 명시 권장). **PM이 0·1·3-2·3-3·6-3·6-4·7·8·10·11·12-3장을 직접 읽고 기준 1~9 대조 → 수락.** 수락 메시지 `P-030-acceptance.md`. 사람 승인 질문 **Q-020(M-1~M-11)** 올림. 승인되면 PR #22 머지 + D-049 기록 + 후속 티켓(DR-1 2022-23 수집, DR-2 스탯 정정 품질 체크, DR-8은 M-9 결과대로) + P-031·P-032 브리핑. 재리뷰 방법: 같은 reviewer 서브에이전트(이 PM 세션 안) 또는 새 서브에이전트가 `~/.claude/worktrees/presagio-review-p-030`에서 `git fetch origin docs/P-030-modeling-design && git checkout --detach origin/docs/P-030-modeling-design` 후 1차 보고서 `P-030-review.md`의 발견 1~20 처리 여부 + 기준 5·9 재대조.

### P-031 🧑 모델링 레이어 스키마 v1 → 마이그레이션 0002
- **상태:** DONE (2026-10-23, 사람 지시로 PM이 squash 머지 `b84acb6`(main 최신화 뒤 CI 재통과). PM이 공유 체크아웃에서 개발 DB `alembic upgrade head` 0001→0002 적용, 표 4개 생성 확인, 품질 체크 16 pass + 1 skip(feature_drift: 재계산 함수 없음, P-035) exit 0)
- **담당 역할:** data-engineer (Opus 5.5 높음, D-043 구현형)
- **단계:** 2
- **의존:** P-030 DONE (D-049). P-032와 병렬(파일 겹침 없음: `db/`·`alembic/`·`quality/` vs `evaluation/`).
- **목표:** 승인된 설계안 `docs/design/modeling-v1.md` 8장(모델링 레이어 요구 사항)을 표 4개(`features`, `model_versions`, `predictions`, `evaluations`)의 DDL로 구체화해 `docs/design/schema-v1.md` 9장을 확정하고, 마이그레이션 0002·SQLAlchemy 모델·DDL 일치 테스트·품질 체크 3개·뷰 1개를 만든다. 🧑 게이트: 요구 사항은 D-049로 이미 승인됐으므로 **DDL 승인은 사람이 PR의 9장을 읽고 머지하는 것**으로 한다(PM 결정, D-010 취지 유지). 개발 DB 적용(`alembic upgrade head`)은 머지 후 PM이 한다.
- **범위(건드릴 파일):** `docs/design/schema-v1.md`(9장 확정 + 부록 DDL + 13장 변경 기록 v1.4), `src/presagio/db/models/`(모델링 표 4개), `alembic/versions/0002_*.py`, `tests/db/`(DDL 일치 테스트 확장, 마이그레이션 왕복), `src/presagio/quality/checks.py` + `tests/quality/`(체크 3개).
- **범위 밖:** `src/presagio/features|models|evaluation`(ML), `games.is_neutral`(D-046, 변경 없음), 데이터 적재·실험, 단계 4 표(`recommendations`, `bankroll_ledger`는 자리만 문서에), 개발 DB에 직접 `alembic upgrade`(임시 DB만).
- **읽어야 할 파일:** `CLAUDE.md`, `docs/design/modeling-v1.md` 8장 전체·1-3·2-2·4-6(market_status 값)·6-2(학습 행 규칙)·9-3(T11·T12·T14가 전제하는 저장 규칙), `docs/design/schema-v1.md` 1·9장·부록 A, `docs/decisions.md` D-019·D-028·D-030·D-033·D-046·D-049, `alembic/versions/0001_schema_v1.py`, `src/presagio/db/models/`, `tests/db/`, `src/presagio/quality/checks.py`
- **완료 기준 (전부 만족해야 DONE):**
  1. `schema-v1.md` 9장이 초안에서 **확정 DDL**(컬럼·타입·NULL·기본값·키·CHECK·인덱스·뷰)로 바뀌고, 설계안 8-1~8-4의 **요구 항목마다** 어느 컬럼·제약으로 구현했는지 대응표가 있다. 빠지면 안 되는 것: `features` `UNIQUE (game_id, as_of, feature_version)`, `values jsonb`, `as_of`/`computed_at`/`snapshot_kind`/`origin`(`live`·`backtest`), `input_fingerprint jsonb`, `CHECK (as_of <= computed_at)`, `CHECK (origin <> 'live' OR computed_at - as_of <= interval '2 hours')`; `model_versions` `UNIQUE (model_name, version)`, `model_family`, `feature_version`, `hyperparameters jsonb`, `seed`, `code_version`, `training_window jsonb`, `tuning_data`, `experiment_id`, `holdout_seasons text[]`, `holdout_unlocked_reason`, `artifact_path`, `is_recommendation_model`, `retired_at`; `predictions` `UNIQUE (game_id, model_version_id, as_of)`, `p_home numeric(6,4)` CHECK (0,1), `total_mean`, `total_sd`, `over_prob`, `reference_line`, `reference_bookmaker`, `snapshot_kind`, `origin`, `train_cutoff` CHECK `<= as_of`, `scheduled_start_utc`, `feature_id` FK, `market_reference jsonb`, `market_status`(4-6의 6값 CHECK), `is_recommendation_basis` + 부분 유니크 `(game_id, model_version_id) WHERE is_recommendation_basis`; `evaluations` `UNIQUE (prediction_id, metric_version)`, 결과 컬럼(`home_won`, `actual_total`, `actual_margin`, `actual_start_utc`, `rescheduled`), 모델 지표 컬럼, 시장 지표 컬럼(NULL 허용), CLV 컬럼, `closing_lead_time interval`, `line_move`, `evaluated_at`, `source_fetched_at`. 결손 보충 행의 `origin = backtest` 규칙 한 줄(D-049 후속 메모).
  2. 마이그레이션 0002는 표 4개 + 인덱스 + CHECK + 뷰 `predictions_recommendation_basis`만 만들고, 임시 DB에서 `alembic upgrade head` → `downgrade -1` → `upgrade head`가 왕복한다. `is_neutral`·기존 표 변경 없음.
  3. D-030 규칙 준수: 제약 이름 규칙(`pk_`/`fk_`/`uq_`/`ck_`), CHECK는 `__table_args__`, `uv run alembic check` 통과, 설계 DDL 일치 테스트가 0002까지 확장되어 "설계 9장 = 마이그레이션" 비교가 통과한다.
  4. 품질 체크 3개 추가: `features_after_tipoff`(`as_of >= games.start_time_utc` 행 수 = 0, fail), `predictions_after_tipoff`(같음, fail), `feature_drift`(재계산 불일치 보고형. 이 티켓에서는 체크 틀만, 비교 대상이 없으면 skip). 빈 표에서 pass 또는 skip. 테스트 포함.
  5. `PRESAGIO_REQUIRE_DB=1 uv run pytest -q` 전부 통과, 네트워크 0.
  6. PR 본문 "결정 제안"에 설계안 8장과 다르게 한 점(있으면)과 이유, 그리고 사람이 9장을 읽을 때 봐야 할 지점 3개.
- **확인 방법:** `uv run alembic check`, `PRESAGIO_REQUIRE_DB=1 uv run pytest tests/db tests/quality -q`, PR의 대응표.
- **메모:**
  - 2026-10-23 PM: D-049로 생성 확정. 브랜치 `feat/P-031-modeling-schema`. P-032와 병렬.
  - 2026-10-23 PM 17:1x: **리뷰 "수락 가능"**(`P-031-review.md`): 530 passed(네트워크 차단), 임시 DB 왕복 뒤 카탈로그 691줄 동일, 0001 변경 0, 제약 61개 이름 규칙 준수, 9장 컬럼 표 85/85·CHECK 48/48 카탈로그 일치, 제약 동작(live 2h 초과 거부·backtest 통과·p_home 경계·train_cutoff·market_status·basis 중복) 직접 재현, 품질 체크 3개 FAIL/PASS/SKIP 재현, D-030 변이 13종 중 12 잡힘(M5 모델 전용 부분 유니크 WHERE 변경만 미탐지). 개발 DB는 0001 그대로. [낮음] 10: feature_id 같은 경기·as_of 순서 미검증(누수 경로, P-033/P-035 전 품질 체크), predictions market_status 6값 vs 9-4 문구, basis origin 무관, 0002 이후 표 없을 때 SKIP, M5, feature_drift compared=0 PASS, 9장 밖 `predicted_at` 잔존·13장 오기, README 14→17, 범위 밖 2파일(체크 수 반영만 — PM 사후 인정), artifact_path 규칙 없음. → **P-041**(data-engineer 후속) + **Q-022**(basis origin, market_status 뜻). 리뷰어가 "사람이 9장에서 볼 지점 3개"를 적음(`P-031-acceptance.md`에 옮김). 범위 밖 파일 변경(`tests/ingestion/test_incremental.py` 2줄, `quality/__init__.py` export)은 체크 수 반영에 그쳐 PM 인정.

### P-032 walk-forward 평가 틀 + 지표 + 시장 확률·CLV + 보류 시즌 가드 + 누수 테스트 틀
- **상태:** DONE (2026-10-23, 사람 지시로 PM이 squash 머지 `6ecbe22`(main 최신화 뒤 CI 재통과). main에서 `backtest run --experiment H-00 --seasons 2023-24,2024-25 --dry-run` 정상: 예측 2,638·채점 2,158·워밍업 480, 전체 log loss 0.6931, 시장 있음 0)
- **담당 역할:** ml-engineer (Opus 5.5 높음, D-043 구현형. 설계는 D-049로 끝남)
- **단계:** 2
- **의존:** P-030 DONE (D-049). P-031과 병렬. `predictions`·`evaluations` 저장은 P-033(P-031 뒤)에서 붙인다.
- **목표:** 설계안 6장(지표, walk-forward, 보류 시즌, 네스티드)·4장(시장 확률, 마감, CLV, market_status)을 코드로 만든다. 입력은 "예측 함수", 출력은 지표 표(markdown). 이 틀이 있어야 이후 기준선·Elo·모델 비교가 공정하다(D-045).
- **범위(건드릴 파일):** `src/presagio/evaluation/`(예: `metrics.py`, `market.py`, `clv.py`, `walkforward.py`, `holdout.py`, `report.py`), `tests/evaluation/`, `src/presagio/cli.py`의 `backtest` 서브커맨드(ML 몫 부분만), `pyproject.toml`에 의존성 추가가 필요하면 PM에게 먼저(scipy/numpy 등은 결정 제안으로).
- **범위 밖:** 피처 함수(P-035), Elo·기준선 구현(P-033·P-034), 모델링 표 쓰기(P-031·P-033), `docs/experiments.md`, 설계 변경(설계안과 다르게 하려면 PR "결정 제안"으로).
- **읽어야 할 파일:** `CLAUDE.md`, `docs/design/modeling-v1.md` 전체(특히 1-2·1-3·2-2·2-3·4-2~4-6·5-1·6-1~6-4·9-1~9-3·12-1), `docs/decisions.md` D-033·D-035·D-036·D-043·D-049, `docs/design/schema-v1.md` 6·7·8장(읽는 표·뷰), `src/presagio/ingestion/odds_math.py`(기존 마진 제거 함수 재사용), `src/presagio/ingestion/possessions.py`, `src/presagio/db/models/`(읽기만), `.claude/agents/leakage-auditor.md`
- **완료 기준 (전부 만족해야 DONE):**
  1. **지표**(설계안 6-1 식 그대로): 정확도, log loss(p를 `[1e-6, 1−1e-6]`로 자름, 자연로그), Brier, 보정 구간표(10 등간격) + ECE, 총점 MAE/RMSE, 오버 확률 log loss/Brier(`T = L` push 제외), 같은 경기 집합의 Δlog loss·ΔBrier, Brier 스킬 점수, 경기 단위 재표집 부트스트랩(기본 1,000회, 시드 고정) 90% 구간. 각 함수에 손계산 기대값 단위 테스트.
  2. **시장 확률**(4-2·4-3·4-6): 암시 확률, 비례 배분(기존 함수 재사용), Shin, power 구현; 합의 중앙값(같은 `captured_at`에 양쪽 있는 북메이커 ≥ 3곳), 기준 북메이커 폴백(`draftkings` → `fanduel` → `betmgm`), 예측 시점 배당 R3(`captured_at ≤ as_of`, 24시간 안), 토탈 기준 라인 L, `market_status` 6값 판정. 합성 스냅숏으로 분기마다 테스트. 2-outcome에서 Shin·power가 Σp = 1을 만족하고 비례 배분과 롱샷 방향이 맞는지 테스트.
  3. **마감과 CLV**(4-4·4-5): 마감 = 뷰 `odds_closing_lines` + `lead_time ≤ 2h`(설정값), `clv_prob`, `clv_return`, `beat_close`, `line_move`(라인이 다르면 `clv_return` NULL). `odds_closing_lines`를 읽는 코드는 `evaluation/`에만 있다(`features/`·`models/`에 그 뷰 이름이 나오면 실패하는 테스트, 9-3 Q4).
  4. **walk-forward 실행기**(6-2·2-2·2-3·6-4): 동부 날짜 단위, as_of = 백테스트 07:00 UTC, 입력은 예측 함수 `predict(as_of, games) → p_home/total_mean[/over_prob]`, 학습 집합 규칙 `D(h) < fit_cutoff` + 가용 지연 Δ(기본 3시간, 설정값), 재학습 주기(기본 7일), 확장 창, 최소 학습 규칙(학습 모델은 2023-24 전체 뒤, 비학습 기준선은 2024-01-01 이후 보고), 보고 구간(시즌 × 정규/포스트시즌 × `market_status` × 전체/시장 있음 두 줄, n 표기, n < 100 참고 표시). 피처 함수에 재학습 시점 as_of를 넘길 수 없는 인터페이스(`training_matrix(feature_version, fit_cutoff)`만 노출, 6-2) — 이 티켓에서는 인터페이스와 더미 구현.
  5. **보류 시즌 가드**(6-3): `--seasons`에 2025-26이 있으면 `--unlock-holdout "<가설 번호와 사유>"` 없이는 거부(테스트). 해제 사유를 반환값·로그에 남겨 P-033이 `model_versions.holdout_unlocked_reason`에 쓸 수 있게.
  6. **누수 테스트 틀**(9-3의 T1·T2·T4·T9·T10 해당분): 더미 예측 함수와 임시 DB 복제본으로 시간 여행 테스트(as_of 이후 행 변경 → 예측 불변), 미래 주입 테스트(미래 결과 극단값 → 과거 예측 불변), Δ 경계 테스트, 보류 거부 테스트가 **실행기 수준**에서 통과. `KFold|cross_val_score|GridSearchCV|lightgbm.cv|xgboost.cv` import 금지 테스트(T9. `.sample(frac=1)`은 `replace=True` 없는 경우만 금지, D-049 후속 메모).
  7. **CLI 골격**: `uv run presagio backtest run --experiment H-00 --seasons 2023-24,2024-25 [--dry-run]`이 더미 예측 함수(예: 항상 0.5)로 돌아 지표 표(markdown)를 출력한다. DB는 `games`·`team_game_stats_primary`·`odds_*`·`odds_closing_lines` **읽기만**. 저장 없음(P-033).
  8. `PRESAGIO_REQUIRE_DB=1 uv run pytest -q` 전부 통과, 네트워크 0, 외부 호출 0. 테스트는 임시 DB(conftest)만 쓴다.
- **확인 방법:** 테스트, CLI 더미 실행 출력, `git grep odds_closing_lines src/presagio` 결과가 `evaluation/`만.
- **메모:**
  - 2026-10-23 PM: D-049로 생성 확정. 브랜치 `feat/P-032-walkforward-eval`. P-031과 병렬. 리뷰는 reviewer + leakage-auditor 관점(단계 2 첫 코드).
  - 2026-10-23 PM 18:1x: ml-engineer 세션(Opus 높음) → PR #26(파일 26, +4748, 테스트 121 추가, 598 passed). 리뷰+누수 감사 겸임 서브에이전트(`P-032-review.md`, 261줄. 중간에 사람 플랜 세션 한도로 중단 후 재개): **기준 1~8 전부 O** — 지표 손계산 전부 일치, Shin·power Σp=1·롱샷 방향 확인, 합의·폴백 분기 6종, R3 경계(= 포함, +1µs 제외, 24h), CLV 손계산, 보류 가드 exit 64(사유 없는 "H-08"도 거부), CLI SQL 424개 전부 SELECT·READ ONLY, 598 passed(루프백 밖 연결 0), 변이 32종 중 29 잡힘. **누수 감사: 복제본에서 시간 여행(기준일 뒤 2,071경기 300-0 + 삭제·삽입)·미래 주입·배당 시간 여행 모두 기준일까지 예측 불변(검정력 확인). 단, [중간][누수 의심 — 인터페이스] `PredictionContext._games`가 미래 점수 포함 전체 경기 목록을 품어 예측 함수가 읽으면 정확도 1.000(재현)** → context를 R1·R2 결과+시장만 담는 frozen dataclass로, 점수 없는 일정 뷰(cup_final 포함) 별도, 재귀 단언 테스트. [낮음] 실행기 가드 3곳 테스트 미고정(변이 생존), 실행기 수준 배당 시간 여행 테스트 없음, `_check_training` 자기 보고 신뢰, 상태별 지표 행 없음(4-6 두 줄은 충족), 학습 창 시즌 하한 없음(P-026 뒤 2022-23 섞임 위험). 작성자에게 필수 1~3(context 경계, 가드 테스트, 배당 시간 여행 테스트) 수정 요청, 나머지는 P-033 후속. 기준 자체 메모(PM): 기준 3의 grep 문구는 main에서 이미 거짓(`VIEW_NAMES`·docstring·quality SQL에 뷰 이름 존재) → 의도(features/·models/ 0줄)는 테스트로 고정됨, 다음 티켓부터 문구 정확히; 기준 7 읽는 표에 `team_aliases`(unmatched_event 판정용) 누락. 리뷰어 간 scratchpad 디렉터리 충돌 1회(둘 다 고유 디렉터리에서 재실행해 결과 동일).
  - 2026-10-23 PM 18:2x: 작성자 수정 `81fca24`("score-free prediction context, pin every runner guard": `PredictionContext`를 R1·R2 결과 튜플 + 점수 없는 일정 뷰 `ScheduledGame`(cup_final 포함) + 읽기 전용 시장만 담는 frozen dataclass로, 전체 경기 목록은 실행기 내부 `_History`로, 가드 3곳 전용 테스트, 실행기 수준 배당 시간 여행 테스트, 테스트 121→133). **재리뷰 "수락 가능" + [누수 감사] "누수 없음"**(`P-032-review-2.md`): 1차 재현 코드가 `AttributeError`/`FrozenInstanceError`로 중단, gc 수준 객체 그래프 검사 위반 0(하루 최대 26,495객체), 1차 생존 변이 3개 전부 잡힘, 610 passed(네트워크 0), CLI 출력 1차와 바이트 동일·SQL 424개 전부 SELECT, 복제본 시간 여행·미래 주입·배당 시간 여행 불변(일정 뷰 포함 해시로도). 새 [낮음] 2: cup_final 제외 조건 변이가 살아남음(픽스처 cup final에 스탯 없어서 R2가 대신 막음 → P-033에서 픽스처 스탯 2행), PR 본문 옛 문구(작성자가 본문만 정리). 참고: 의도적 스택 프레임 우회(`sys._getframe`)는 파이썬 한계 → P-033/P-036 AST 금지 테스트, `ScheduledGame`에 상태 없음(P-035 설계 문제), `GameMarket` 내부 dict 가변(P-033 저장 시 복사). **PM 수락.** 사람 머지 대기 → 머지 후 P-033 브리핑(P-031도 머지돼야 함).

### P-033 승패 기준선 3종(B0·B1·B3) + 결과 저장 경로
- **상태:** DONE (2026-10-24 13:12 KST PR #41 squash merge `625edfa`, 사람 지시("머지해줘")로 PM 머지(update-branch → CI → squash). 기준 6 실데이터 실행 완료(아래 메모). 개발 DB: `model_versions` 3, `predictions` 5,288, `evaluations` 5,276)
- **담당 역할:** ml-engineer (Opus 5.5 높음)
- **단계:** 2
- **의존:** P-031 DONE, P-032 DONE (둘 다 2026-10-23 머지, 개발 DB 0002 적용)
- **목표:** 설계안 5-1의 승패 기준선 B0(항상 홈), B1(시즌 승률), B3(예측 시점 시장 확률)을 P-032 실행기의 예측 함수로 구현하고, P-032가 미뤄 둔 저장 경로(`model_versions`·`predictions`·`evaluations` 쓰기, 덮어쓰기 금지)를 붙인다. Elo(B2)·총점 기준선은 P-034.
- **범위(건드릴 파일):** `src/presagio/models/`(기준선), `src/presagio/evaluation/`(저장 `store.py`, 실험 등록, 실행기 가드 보강, CLI `--store`·종료 코드), `src/presagio/cli.py`(backtest 부분), `tests/models/`, `tests/evaluation/`, `pyproject.toml`(numpy 직접 의존 한 줄 — PM 범위 추가 승인)
- **범위 밖:** Elo·총점 기준선(P-034), 피처 모듈·`features` 표(P-035), `docs/experiments.md`(P-037), 스키마 변경(안 맞으면 멈추고 PM에게, Q-022·P-041), 추천(`is_recommendation_basis` 전부 false), 수집 코드.
- **읽어야 할 파일:** `CLAUDE.md`, `docs/design/modeling-v1.md` 5-1·4-3·4-6·1-2·2-2·2-4·6-1~6-3·8-2~8-4·9-3, `docs/design/schema-v1.md` 9장, D-030·D-033·D-036·D-043·D-049·D-052, `src/presagio/evaluation/`(P-032 전부), `src/presagio/db/models/modeling.py`, `alembic/versions/0002_modeling_layer.py`, `tests/evaluation/`
- **완료 기준 (전부 만족해야 DONE):**
  1. **기준선 정의(5-1):** B0 `p_home` = as_of 이전 학습 기간 누적 홈 승률(시즌 초 표본 < 100경기(제안값)면 전 시즌 값, 없으면 0.5; 중립은 0.5) + "항상 홈 승" 정확도 변형. B1 시즌 누적 승률 차 `0.5 + 0.5 × (win%_h − win%_a)`를 [0.05, 0.95](제안값)로 자름, 동률·시즌 첫 경기 0.5. B3 예측 시점 합의 시장 확률(P-032 `market.py`), 없으면 예측하지 않고 `market_status` 사유. 셋 모두 context의 `results()`·`schedule()`·`markets`만 사용. 손계산 단위 테스트.
  2. **누수 테스트 파라미터화:** T1·T2가 등록된 모든 EXPERIMENTS(B0·B1·B3·더미)로 통과. B0·B1의 당일 경기 off-by-one 테스트(당일 결과를 바꿔도 예측 불변).
  3. **저장 경로:** `backtest run --experiment B0 --seasons 2023-24,2024-25 --store`가 `model_versions` 1행(재현 정보·보류 컬럼 포함), 예측마다 `predictions` 1행(`origin=backtest`, `snapshot_kind=daily`, `as_of`, `train_cutoff`, `scheduled_start_utc`, `feature_id` NULL, `p_home`, `total_mean` NULL, `market_reference`, `market_status` 4값, `reference_*`, `is_recommendation_basis=false`), 채점 뒤 `evaluations` 1행(`metric_version=1`)을 쓴다. 덮어쓰기 금지: `ON CONFLICT DO NOTHING` + 값 불일치 건수·예시 보고. 테스트: 임시 DB 두 번 저장 → 행 수 동일·불일치 0, 값 바꿔 재저장 → 불일치 보고.
  4. **보류 시즌:** `--store`로도 2025-26 행은 쓰지 않음(`--unlock-holdout` 시 `holdout_unlocked_reason` 기록). 테스트.
  5. **P-032 리뷰 후속:** (a) 학습 소스 자기 보고 값을 `game_id`로 대조해 다르면 `LeakageGuardError`, (b) 학습 창 시즌 하한(`--seasons` 밖 시즌·2022-23 제외), (c) 저장 시 `GameMarket` 복사, (d) `models`·`features`에서 `sys._getframe`·`inspect.currentframe/stack/getouterframes`·`gc.get_*`·`ctypes` 금지 AST 테스트, (e) 합성 리그 cup final 스탯 2행, (f) `cli.py` 가드 위반 종료 코드 분리(제안 3) + ERROR 로그, (g) `pyproject.toml` numpy 명시, (h) `evaluation/market.py` `NO_ODDS_SEASONS`에 2022-23 추가(P-026 리뷰 2026-10-23: 지금은 2022-23을 채점하면 `no_odds_season`이 아니라 `no_snapshot_at_as_of`로 나옴. M-11상 채점 대상 아니지만 사유 코드는 맞아야 함).
  6. **실데이터 실행 1회(개발 DB):** B0·B1·B3를 2023-24·2024-25에 `--store`로 돌려 `model_versions`·`predictions`·`evaluations` 행 수와 6-1 두 줄 표를 PR 본문에(B3는 n=0 `no_odds_season` 정상, 성능 숫자는 참고). 재실행 후 행 수 불변·불일치 0. 클라우드 세션이면 "머지 후 PM"으로 표시하고 PM이 수행.
  7. `PRESAGIO_REQUIRE_DB=1 uv run pytest -q` 전부 통과, 네트워크 0, 새 의존성은 numpy 명시만.
- **확인 방법:** CLI 명령 + `SELECT mv.model_family, count(p.*) FROM predictions p JOIN model_versions mv USING (model_version_id) GROUP BY 1`, 두 번 실행 전후 count, 테스트.
- **메모:**
  - 2026-10-24 PM 13:13: **기준 6 실데이터 실행(로컬 개발 DB, main `625edfa`, launchd 시각 밖):** B0·B1 `--seasons 2023-24,2024-25 --store` 각 2회, B3 `--seasons 2026-27 --store` 2회, 전부 exit 0. 1회차: B0 predictions +2,638 / evaluations +2,638, B1 +2,638 / +2,638, B3 predictions +12 / evaluations 0(10-23 ET 경기 결과가 아직 DB에 없음 — 16:00 수집 뒤 채점 가능). 2회차: 전부 inserted 0·already stored 2,638(B3 12)·differing 0·not written for differing 0. 6-1 (a) 전체 줄(n 2,158, 2024-01-01 이후, 부트스트랩 90%): B0 `| all | all | (a) all games | 2158 | 0.537 | 0.6915 [0.6875, 0.6958] | 0.2492 [0.2472, 0.2513] | 0.0184 | - | - |  |` / B1 `| all | all | (a) all games | 2158 | 0.633 | 0.6389 [0.6311, 0.6475] | 0.2234 [0.2199, 0.2272] | 0.0655 | - | - |  |` / B3 2026-27 표 없음(12 예측, 결과 미도착으로 채점 0). (b) 시장 있음 줄은 2023-24·2024-25 전부 n=0(`no_odds_season` 2,158) — 과거 배당 없음 그대로. 성능 숫자는 참고(가설 판정은 P-037). `quality-check` 17개: 15 pass·1 warn(14 `recent_failed_runs`: 오늘 launchd run 119/120 partial, P-028 메모)·1 skip(`feature_drift`)·fail 0, `predictions_after_tipoff` 5,288 pass. 로그 scratchpad `p033-store-1313.log`. **DONE.**
  - 2026-10-24 PM 02:3x: **재리뷰 "수락 가능"**(`P-033-review-2.md`): 1차 필수 4건(발견 1·2·4·6) 전부 해결, 새 발견 0. 범위 = 8파일(+219/−37)뿐, 의존성·마이그레이션 0. 775 passed·skipped 0(루프백 밖 0)·ruff·CI 통과. 복제본 재현 A1·A2·B에서 불일치 예측의 평가 미삽입 + 보고서 줄 `… evaluations inserted 0, already stored 2637, differing 0, not written for differing predictions 1 …`, 기존 평가 불변, exit 4. R1 `<`→`<=` 변이 잡힘, 미고정 3건 변이 잡힘, B0 자르기(4-0 → 0.95, 0-4 → 0.05, `hyperparameters.clip` 기록, 기존 값 불변·실데이터 경계값 0). 회귀 변이 34/34 잡힘. 미래 주입(300-0) 재실행에서 B0 더 이상 멈추지 않음. 참고: 일정이 바뀐 2026-27 경기는 재저장 때마다 exit 4 + 평가 비어 있음(D-055 2항 결과) → P-037 자동화에서 "일정 변경" vs "결정성 위반" 구분 필요(D-055 보완). AST 우회 4종은 P-035/P-036. **PM 수락**(`P-033-acceptance.md`), 사람 머지 대기.
  - 2026-10-24 PM 01:5x: **리뷰(Opus, 누수 감사 겸임) "수정 후 재리뷰"**. 직접 실행: 768 passed ×2(루프백 밖 0)·ruff·CI 통과, 불안정 테스트 13회+ 재현 안 됨, 개발 DB `--dry-run`(SQL 전수 로깅: 쓰기 0), **복제본(pg_dump)에서 `--store`** B0·B1 각 +2,638/+2,638 → 재실행 inserted 0·differing 0, B3 2023-24·2024-25 0행(no_odds_season 2,638)·2026-27 +12, 값 변경 → exit 4, StoreError exit 1, 보류 64, 해제 +1,321·사유 기록·다른 사유 거부, exit 3 확인. **누수: 복제본 시간 여행(기준일 2024-02-15 뒤 3,160경기 뒤집기·300-0·50경기 삭제)에서 기준일까지 예측 820개 불변·이후 B0 1,756/B1 1,730 변함(검정력), 당일 결과 뒤집기 불변, B3 배당 시간 여행(724행 극단값 + as_of+1µs 120행) 불변, 저장 `market_reference.captured_at > as_of` 0행 → 누수 없음.** 손계산: B0·B1 합성 리그 345/345 독립 SQL 일치, B3 중앙값 일치. 변이 54 중 44 잡힘. 실데이터 복제본 참고 수치(all/all, n 2,158): B0 acc 0.537·LL 0.6915·Brier 0.2492, B1 acc 0.633·LL 0.6389·Brier 0.2234(가설 판정은 P-037). **[중간] 1:** `store.py:434-440` 저장값과 다른(differing) 예측에 이번 실행 기준 평가가 붙어 삽입됨(저장 p 0.1234 vs 이번 p 0.5582로 계산한 log_loss 삽입; 같은 날짜 2시간 지연 재저장이면 `rescheduled=false`로 삽입) → 불일치 예측의 평가 미삽입 + 건수 보고(필수 1). **[낮음] 5:** 가드 전용 테스트가 교차 대조에 가려 R1 `<`→`<=` 변이 생존(필수 2), 테스트 미고정 3(NO_ODDS 2022-23·READ ONLY·StoreError 로그, 필수 3), B0 확률 자르기 없음 → 홈 승률 0/1이면 실행 중단(필수 4), B0 정확도 ≠ 항상 홈 승 정확도(2024-11-06 p=0.4907, 0.5373 vs 0.5410 → P-037 별도 줄), AST 우회 4종(→ P-035/P-036). 참고: 평가 log_loss는 자르기·양자화 전 p로 계산(최대 2.6e-4 차), **B3 2026-27 백테스트에서 A0 캡처(07:00:09.7)가 as_of 07:00:00보다 늦어 18시간 전 캡처를 씀(누수 아님, Q-025)**, `--seasons`는 DB에 안 남음(실행 범위는 predictions의 경기 시즌으로 복원), B0 분모에 중립 경기 포함. **P-040 기준 4 증거 검증:** 훅 줄 형식 일치, 684 = `a09f5ae` collect-only 일치, 0002 head 일치(VM 안 사실은 작성자 보고). PM 결정 D-055(제안 1~6 + 리뷰 항목), 필수 4건 `P-033-fix-request.md`.
  - 2026-10-24 PM 01:0x: PR #41 도착 확인(클라우드 세션은 완료 알림이 없어 `git branch -r`·`gh pr list`로 발견. 커밋 시각 10-23 15:47Z = 00:47 KST). CI: secret-scan 통과, lint-and-test 진행 중, main 대비 BEHIND(머지 전 update-branch 필요). 작성자 PR 본문의 결정 제안 6건(학습 창 하한 vs `context.results()` 상태 입력 분리(5(b) 해석), 불일치 exit 4, model_versions 설정 충돌 거부, 워밍업 예측 행 저장, `experiment_id`·`metric_version`·`seed` 규칙, 예측 시점 market_status 뜻)은 리뷰 뒤 PM이 판단해 D-054로 정리 예정. 알려진 한계: 학습 창 = 요청 시즌만이라 단계 3의 "2025-26 학습만·2026-27 예측"은 `--train-from` 옵션 필요(단계 3 티켓 후보), `test_odds_time_travel_at_runner_level[B3]` 1회 ERROR 미재현(리뷰어가 재현 시도).
  - 2026-10-24 PM 00:1x: 사람이 Q-024 (a) 승인 → PM이 Claude in Chrome으로 `presagio-dev` 편집(환경 선택기 → 클라우드 → 행 hover → 톱니 → 설정 스크립트) 칸을 래퍼로 교체, 사람 확인 뒤 "변경사항 저장"(토스트 "환경 업데이트됨"). 같은 브리핑(+Q-024 사실 수집 지시)으로 2차 세션 요청(task_385637e4).
  - 2026-10-23 PM 21:5x: 첫 시도 실패(위). 공식 문서(`cloud-environments.md` Setup scripts 절)는 "runs as root on Ubuntu 24.04, before Claude Code launches"만 말하고 작업 디렉터리·클론 경로는 적지 않는다. `scripts/cloud-setup.sh` 자체는 `$BASH_SOURCE` 기준으로 레포를 찾으므로 절대 경로로 부르면 된다 — 클론 경로를 모르니 `find`로 찾는 한 줄 래퍼를 셋업 칸에 넣는 수정안(Q-024). P-040 리뷰·첫 클라우드 세션 실측에도 클론 경로(`pwd`) 기록이 없었다(교훈: 셋업 칸 문구는 실제 플랫폼 실행 1회로 검증해야 했다 = 기준 4가 바로 그것).
  - 2026-10-23 PM 21:4x: 환경 `presagio-dev` 생성 직후 `start_session target: cloud`로 세션 요청(task_3915f1ce). 앱 환경 목록이 생성 뒤 약 1분 늦게 갱신돼 1회 거부("No cloud environment named") 후 재시도 성공. 기준 5에 (h) `NO_ODDS_SEASONS` 2022-23 추가(P-026 리뷰). 클라우드 세션은 완료 알림이 없으므로 PM이 `gh pr list --head feat/P-033-baselines-store`로 확인.
  - 2026-10-23 PM: P-031·P-032 머지 뒤 상세 확정. P-032 리뷰(1·2회차)의 P-033 후속을 기준 5에 전부 넣음. 브랜치 `feat/P-033-baselines-store`.

### P-042 클라우드 셋업 후속 (P-040 리뷰 [낮음] 5건)
- **상태:** DONE (2026-10-24 16:4x PR #48 squash merge `b2d0ec1`, 사람 지시로 PM 머지. 머지 뒤 로컬 훅 무동작 확인(출력 0·exit 0), launchd 16:00 본 실행 정상(run OK 5.3 s). D-057. workflow.md 클라우드 절 갱신)
- **담당 역할:** platform-engineer (Opus 5.5 높음). D-052상 클라우드에서 수행 가능.
- **단계:** 2 (단계 6 선행)
- **의존:** P-040 DONE(머지 + 첫 환경 세션 확인)
- **목표:** P-040 리뷰(`P-040-review.md`)의 [낮음] 5건을 한 PR로. (1) README 셋업 칸 안내: 플랫폼이 레포 루트에서 셋업을 돈다는 보장이 없으면 rc 127 → 셋업 칸을 `bash scripts/cloud-setup.sh || true` 또는 절대 경로 폴백으로, 실패 시 세션 안 수동 실행 안내. (2) 훅: `alembic current`가 head와 다르면 "캐시가 낡음, `bash scripts/cloud-setup.sh` 실행" 경고 + README에 "셋업 칸 주석을 바꿔 캐시 재생성". (3) README에 `claude --cloud` 전 `/remote-env`로 `presagio-dev` 선택 안내, Default 환경으로 뜬 경우의 훅 메시지. (4) 셋업 성공 경로 테스트(스텁 `runuser`·`psql`·`uv`로 CREATEDB·alembic·`--locked` 단언, 변이 5 생존 해소). (5) 훅 실패 출력 한 줄로. 함께: README 예시 수치 683/0002로 갱신, 클라우드에서 `gh pr` 불가(GraphQL 403) 안내와 `gh api` REST 예시.
- **메모:**
  - 2026-10-24 PM 16:4x: 머지 `b2d0ec1`. 자동 브랜치 `claude/sweet-lovelace-vpl75t`·(P-033의) `claude/cool-brown-n2ynso` 삭제. **DONE.** 후속: 셋업 단계의 실제 작업 디렉터리 값(래퍼 첫 줄 출력)은 사람이 세션 시작 화면에서 보면 README에 추가; 훅 uv 폴백 경로·`PG_WAIT_SECONDS` 테스트(선택).
  - 2026-10-24 PM 16:5x: **2차 리뷰 "수락 가능"**: 782 passed·skipped 0, scripts 79, ruff·`bash -n` 파일별·CI 통과, 범위 밖 diff 0, main·#49 충돌 없음. README 변경은 350·385·403행뿐(1(c) `/model`+`/effort high`+`/effort status`, 관찰 vs Auto-fix 문서 보장 구분, Trusted 범위). 훅 `9e089a5`: stderr를 mktemp로 받아 `^INFO `·`^(Background on this error` 제외 마지막 줄을 꼬리에 보존, 임시 파일 삭제, stdout 1줄·exit 0 유지(진짜 alembic 종단 실행 2회로 확인), 변이 N1·N2·N5·N7 잡힘. [낮음] 2: grep 필터(N3·N4)·임시 파일 삭제(N8) 변이 생존(선택). 1차 생존 변이 3 그대로(선택). **PM 수락.**
  - 2026-10-24 PM 16:3x: PR 댓글(15:2x)로는 안 깨어남 → `claude -p --cloud session_01A2Mi74nuJBsvQPBuPS6SrE`(16:1x) → 16:29 확인 시 `e4ebc5d`·`9e089a5` 도착(필수 3건 + 선택 2건 반영, 작성자 댓글로 보고). 재리뷰 요청.
  - 2026-10-24 PM 15:2x: **리뷰(Opus) "수정 후 재리뷰"**: 782 passed·skipped 0(루프백 밖 0), ruff·`bash -n`·CI 통과, 범위 밖 diff 0, main·PR #49와 충돌 없음(병합 트리에서 scripts 테스트 27 passed). 변이 33 중 30 잡힘(P-040 생존 변이 5 중 4 해소, `PG_WAIT_SECONDS=0`만 남음), 스텁 PATH 격리 확인, alembic 호출 0.87초(훅 타임아웃 300초 대비 충분), `-q`가 출력을 지우는 것·`python -m alembic` 동작·실패 출력 여러 줄 재현, 공식 문서 인용 4건 원문 일치. **[중간]** README:385 `/model claude-opus-5-5`만 있고 `/effort high`가 없어 D-043 "높음" 미보장(Opus 기본 노력 medium) → 필수 1. **[낮음]** README:403 "GitHub 이벤트로 깨어나 main을 읽는다"는 문서 근거 없음(관찰로 고쳐 적기, 필수 2), `bash -n a b`는 첫 파일만 검사(필수 3), `session-start.sh:105,109` alembic stderr 버림(선택), 생존 변이 3(PG 대기·alembic_out 접기·uv 폴백 경로). 참고: README:350 Trusted 범위 좁게 적힘, Q-024 본문에 래퍼 주석 줄 없음(PM 기록 보완). PM에게: 브랜치명 플랫폼 고정, 기준 2 줄 번호는 2행·4~11행, 다음 브리핑에 `/effort high` 명시, 브리핑 양식의 `bash -n a b` 고치기.
  - 2026-10-24 PM 14:4x: PR #48 도착(세션 시작 약 40분 뒤). 작성자 결정 제안 4건(래퍼 확정·절대 경로 단순화 안 함, 캐시 경고는 stdout만, 클라우드 브랜치명은 플랫폼 고정 허용 + PR 제목으로 식별, 환경 설정 변경 없음). 셋업 단계의 실제 작업 디렉터리 값은 여전히 미확인(래퍼 첫 줄 출력을 사람이 세션 시작 화면에서 볼 수 있으면 README에 추가).
  - 2026-10-24 PM 13:5x: 완료 기준 1~6 확정(브리핑에 전문): README 클라우드 절(UI 칸 이름·네트워크 표기·래퍼·클론 경로·/remote-env·모델 상속·gh api REST·수치), `cloud-setup.sh` 머리말, 훅 캐시 낡음 경고 한 줄 + 실패 한 줄 + 로컬 무동작, 셋업 성공 경로 스텁 테스트, 범위 밖 0, 클라우드 실측(훅 직접 실행·downgrade/upgrade로 경고 재현). `docs/team/workflow.md` 클라우드 절은 PM이 PR 뒤 갱신.
  - 2026-10-24 PM 01:0x: 클론 경로 실측 `/home/user/Presagio`(P-033 PR #41). 셋업 칸은 지금 래퍼(Q-024)가 동작 확인됨 → README에 (a) 래퍼 그대로 적거나 (b) `bash /home/user/Presagio/scripts/cloud-setup.sh` 절대 경로로 단순화. PM 추천 (a): 경로가 플랫폼 내부 규칙이라 바뀔 수 있고 래퍼는 `CLAUDE_PROJECT_DIR`가 생기면 그것을 우선 쓴다. 훅 쪽은 `$CLAUDE_PROJECT_DIR` 빈 값일 때 `$BASH_SOURCE` 폴백이 실제로 쓰였음을 README에 적기.
  - 2026-10-23 PM 21:5x(Q-024): README 셋업 스크립트 칸 문구를 경로 무관 래퍼(아래 Q-024 본문)로 교체하고, `scripts/cloud-setup.sh` 5~7행 주석("Paste one line … `bash scripts/cloud-setup.sh`")도 같이. P-033 클라우드 세션 PR의 "P-040 기준 4 증거" 절에서 실제 `pwd`·클론 경로·`CLAUDE_PROJECT_DIR` 값을 받아 README에 적는다(그 뒤 래퍼를 절대 경로로 단순화할지 결정).
  - 2026-10-23 PM: P-040 리뷰로 생성.
  - 2026-10-23 PM 21:3x(P-040 환경 생성 실측): README 클라우드 절의 "네트워크 접근 Trusted(기본값)"를 실제 UI 표기로 보정 — 선택지는 없음/제한됨/전체이고, "제한됨 + 허용 도메인 비움 + 패키지 관리자 허용 켬"이 공식 문서의 Trusted다. 환경 폼 칸 이름도 UI 그대로(이름 / 네트워크 액세스 / 네트워크 시크릿 / 설정 스크립트 / 환경 변수).

### P-026 2022-23 시즌 추가 수집 (D-049 M-11, DR-1)
- **상태:** DONE (2026-10-23 PR #34 https://github.com/oghdy/Presagio/pull/34 squash merge `4f0d9af`. 리뷰 "수락 가능" → PM 수락(`P-026-acceptance.md`) → 사람 지시("PR #34 squash 머지해줘")로 PM이 머지, D-005 보완. 개발 DB에는 2022-23 1,320경기·중립 2가 이미 들어 있음(이 PR 코드 `fe1f2db`로 수집, HEAD의 src·tests와 동일 → 머지 뒤 재수집 불필요))
- **담당 역할:** data-engineer (Opus 5.5 높음)
- **단계:** 2
- **의존:** P-031 뒤(같은 역할이 동시에 두 티켓을 하지 않음). P-034(Elo) 전에 끝나야 함.
- **목표:** Elo 워밍업·`prev_season_*` 피처·carry 탐색용으로 2022-23 시즌(정규·플레이인·플레이오프)을 개발 DB에 넣는다. 학습·평가 대상이 아니다(배당 없음).
- **범위:** 실행(`uv run presagio backfill --season 2022-23`), `docs/research/backfill-report-stage1.md`에 2022-23 절 추가 **또는** 새 파일 `backfill-report-2022-23.md` 하나(브리핑에서 허용. PR은 새 파일을 택함), 필요 시 최소 코드 수정(2022-23 응답 형식 차이, 중립 경기 보완 목록 확장 — 2022-23에도 멕시코시티·파리 경기가 있고 `isNeutral`이 false일 가능성).
- **범위 밖:** 배당, 스키마, 2022-23을 학습 대상에 넣는 것.
- **완료 기준:**
  1. `backfill --season 2022-23`이 종료 코드 0, nba_api 호출 ≤ 4(일정 1 + 게임로그 3; 재실행 포함 합계 ≤ 8).
  2. 2022-23 `games` 수 = 일정 응답의 002·004·005 수, `team_game_stats` = 종료 경기 × 2. 품질 체크 `--season 2022-23` fail 0·error 0(D-040의 14개 pass. P-031 뒤 17개가 되어 모델링 3개는 pass/skip. 원문 "14 pass"를 2026-10-23 보정, D-053 4항).
  3. 다른 시즌 행 수 불변, 같은 명령 재실행 후 불변(전후 count).
  4. 2022-23 중립 경기를 raw(`arenaCity`·`gameLabel`)로 확인해 `is_neutral`이 맞다(필요하면 P-025 목록 확장 + 테스트). 보고서에 표(경기 수·홈 승률·평균 총점·중립 수).
- **메모:**
  - 2026-10-23 PM: D-049 M-11로 생성.
  - 2026-10-23 PM 20:00: 사람 지시로 data-engineer 로컬 세션 `p-026-season`(local_8d439251…, Opus 5.5 높음, 브랜치 `feat/P-026-season-2022-23`) 시작. 브리핑 `P-026-briefing.md`(보고서는 stage1 절 추가 또는 새 파일 하나, 개발 DB 쓰기는 이 세션만).
  - 2026-10-23 PM 20:1x: **PR #34** 도착(`c5a2cb1`, 파일 4 +1067/−7: `docs/research/backfill-report-2022-23.md`, `schedule.py` 중립 목록 2건(멕시코시티 `0022200439`·파리 `0022200678`, 5 → 7), 픽스처 `scheduleleaguev2__2022-23_neutral.json`, 테스트). 작성자 보고: 1,320경기·2,640 스탯 행, nba_api 7호출(4 + 보완 뒤 정규 `--force` 3), 품질 fail 0. reviewer(Opus 5.5 높음) 서브에이전트가 detached worktree에서 개발 DB 읽기 전용(psql read-only 트랜잭션, backfill은 `--dry-run`만)으로 리뷰.
  - 2026-10-23 PM 20:3x: **리뷰 "수락 가능"**(`P-026-review.md`): **기준 1~4 전부 O** — `ingestion_runs` 107~116으로 호출 7(1차 4·teams skip, force 1회 3, 재실행 0; retry 0, 전부 succeeded), raw 94 유형별 수(002 1,230/004 84/005 6/006 0) = DB 1,320, 스탯 2,640(경기당 정확히 2행), 품질 17개 중 16 pass·1 skip·fail 0(전체도 같음), 다른 시즌·모델링 표 4개·odds 불변, `--force` inserted 0(upsert 멱등), 중립 전수 스캔(홈팀별 도시·경기장)으로 정확히 2경기·누락 0(Austin 2·Alamodome 1은 SAS 연고 안 → D-046과 같은 판단), 커밋된 파서를 raw에 돌려 DB 14컬럼 전부 일치, 변이 11/11 잡힘, 보고서 숫자 쿼리 12개 재현 일치, 664 passed(루프백 밖 연결 0), M-11 문구 명시. **[낮음] 3(전부 문서 문구):** 보고서 268행이 포스트시즌 90경기 점수 근거로 `gamelog_vs_schedule_score`를 댔으나 그 체크는 `source='leaguegamelog'`만 봄(값은 raw 게임로그와 90/90 일치, `points_match_score`가 보증), `schema-v1.md` 6-1·13장 "5경기" 미갱신, 오타 1 → **P-041**에 묶음. 참고: `status_text` 공백 15칸 1건(`0022200236`), `NO_ODDS_SEASONS`에 2022-23 없음(→ P-033 5(h)), 머지 커밋에 Co-Authored-By 없음(squash면 소멸). **기준 자체(PM 메모):** 기준 2 "14 pass"는 17개 시대에 맞지 않아 브리핑에서 보정, 범위 문구(stage1 절 vs 새 파일)를 브리핑과 맞춤, 호출 상한이 "보완 뒤 전체 재수집" 몫을 못 담아 포스트시즌 `games.source` 정렬이 막힘 → **D-053**. **PM 수락**(`P-026-acceptance.md`). 사람 머지 대기.
  - 2026-10-23 PM 20:39: 기록 PR #35 머지 뒤 `gh pr update-branch 34`(HEAD `5e74b79`, CI 초록) → 사람 지시로 PM이 squash 머지 **`4f0d9af`**. **DONE.** 개발 DB 재수집 불필요(DB 값은 `fe1f2db` 코드로 받았고 머지된 src·tests와 동일). 리뷰 worktree 제거. 후속은 P-041(문서 정정 3건)·P-033 5(h)·D-053.

### P-027 스탯 정정 빈도 품질 체크 (설계안 DR-2)
- **상태:** TODO (상세 완료 기준은 P-031 뒤 확정)
- **담당 역할:** data-engineer
- **단계:** 2~3
- **의존:** P-031
- **목표:** 같은 경기·팀의 `team_game_stats` 값이 raw 버전 사이에 바뀐 횟수를 세는 품질 체크(2026-27부터). 정정이 잦으면 실시간과 백테스트의 입력이 체계적으로 달라 재현성(누수 질문 5)에 영향.
- **메모:** 2026-10-23 PM: D-049(설계안 3-2 DR-2)로 생성. 우선순위 중간.

### P-028 주말 낮 경기 배당 캡처 2회 추가 (D-027 개정, D-049 M-9, DR-8)
- **상태:** DONE (2026-10-28, PR #61 → main `9a3073b`, D-059)
- **담당 역할:** platform-engineer (Opus 5.5 높음)
- **단계:** 2 (단계 6 선행)
- **의존:** P-024 DONE
- **목표:** 토·일 14:45·16:45 ET에 `ingest-daily --odds-only` 캡처 2회를 더해 낮 경기(12:00~18:00 ET 시작) 마감 배당 커버리지를 87.6% → 97.0%로 올린다. 평일은 4회 그대로. 월 최대 425크레딧.
- **범위:** `scripts/launchd/`(래퍼 슬롯 규칙·plist 깨어남), `tests/scripts/`, README 운영 소절, `docs/team/risks.md` B2 숫자(PM이 같은 PR에서 또는 별도). 설정값이 필요하면(`ODDS_WEEKEND_CLOSING_TIMES_ET` 등) `src/presagio/config.py` 변경은 data-engineer 영역이므로 PM이 범위를 정한다.
- **완료 기준(초안, P-024 머지 후 확정):** 토·일 6회·평일 4회를 7개월 모의로 증명, 중복 없음, 월 크레딧 계산 표(≤ 425), 실제 주말 1회 결과. 함께: P-024 리뷰 3회차 [낮음] 1(23h 정지라 19:45 차단 뒤 다음 날 18:45부터 재개 — 주석·README 문구 정정 또는 "같은 슬롯 벽시계 −10분"으로 변경 + 19:45 차단 테스트).
- **메모:**
  - 2026-10-23 PM: D-049 M-9로 생성.
  - 2026-10-24 PM 13:2x(운영 관찰 4건, 이 티켓 범위에 넣을 것): ① **07:45 KST 슬롯(odds-2026-10-23-1845) 누락** — 로그에 08:00:02 "skip: no slot now"만 있음(맥이 잠들어 launchd가 08:00에 깨운 것으로 보임; 1845 캡처 결손 = 정직한 결손이지만 B2 커버리지 손실). 맥 잠자기 중 깨우기(`pmset repeat wake`/`caffeinate`) 또는 슬롯 허용폭 검토. ② **10:45 슬롯(2145)이 exit 1 FAILED** — 실제로는 262행 저장 성공, FanDuel h2h Boston Celtics 가격 1.0(`bad_price`) 1건 거부로 단위 partial → 부모 failed → launchd FAILED. 거부 1건은 품질 이슈이지 실패가 아님 → partial 판정 기준(거부 outcome 비율 임계) 또는 warn/fail 분리(P-017 리뷰 후속)와 함께. ③ **Docker VM 시계 지연** — run 119 `started_at`(DB `now()`) 10:29 KST vs 실제 10:46(호스트·`captured_at`). 맥 잠자기 뒤 Docker Desktop VM 시계가 17분 늦었다가 13:14에는 재동기화됨. `captured_at`·`fetched_at`은 파이썬(호스트) 시계라 as_of 영향 없음, DB `now()` 기본값 컬럼(`ingestion_runs.started_at`, 각 표 `created_at`)만 어긋남 → `started_at`을 파이썬 시계로(단계 6 또는 이 티켓). ④ 09:45·11:48 KST에도 launchd가 깨어나 "no slot" skip(서머타임 양쪽 시각 등록, 정상).
  - 2026-10-26 PM 18:0x(운영 관찰 2건 추가, 이 티켓 범위에 넣을 것. 10-25·10-26 실측): ⑤ **재부팅 뒤 Docker Desktop이 자동 시작되지 않아 개발 DB가 내려가 있음** — 10-25 16:00 본 실행(맥 11:01 종료 → 14:17 재부팅)과 10-26 10:45 odds(09:15 재부팅)가 `connection refused 127.0.0.1:5433`으로 run 행도 없이 FAILED. Docker Desktop 설정 `AutoStart: false`, 로그인 항목에도 없음(10-26 확인). 두 번 모두 PM이 `open -a Docker`로 기동(데몬 약 5초, DB healthy). 대응 후보: 사람이 Docker Desktop 설정 "Start Docker Desktop when you sign in"을 켠다(사람 설정) + 래퍼가 DB 연결 실패를 감지하면 `open -a Docker` 뒤 최대 N초 대기 후 1회 재시도(한 번 더 실패하면 지금처럼 FAILED). ⑥ **맥이 꺼져 있던 슬롯은 재부팅 뒤 보충 실행되지 않는다** — plist `RunAtLoad=false`. 10-26에 07:45·08:45 KST(09:15 부팅 전)와 16:00 본 실행(11:25 종료 ~ 17:42 부팅)이 로그 한 줄 없이 빠졌다. 잠자기 중 놓친 슬롯은 깨어나면 한 번 실행되지만(launchd.plist 문서; 10-23~10-25 08:00 wake 실측 = 관찰 ①), 전원이 꺼진 동안의 슬롯은 보충이 없다(10-26 17:42 부팅 뒤 실행 없음 실측). 결과: 10-25 ET 경기 7개의 마감 배당 3회분은 영구 결손(정직한 결손), 10-26 본 실행(10-25 결과·10-26 일정·예측 시점 배당)은 사람 지시로 수동 재실행해야 함. 대응 후보: `RunAtLoad=true` + 래퍼가 부팅/로드 시 "오늘 16:00 KST가 지났는데 오늘 본 실행 run이 없으면" 본 실행을 돈다(멱등이라 중복 무해). 놓친 odds 슬롯은 보충하지 않는다(마감 뒤 캡처는 뜻이 달라 `pre_tip`으로 기록하면 안 됨). 참고: 10-26 09:45 KST wake는 관찰 ④(DST 양쪽 시각)대로 정상 skip.
  - 2026-10-26 PM 19:4x: **브리핑 작성** `P-028-briefing.md`(공유 노트, B 양식; 사람 "써줘" 지시). PM 범위 결정: ②③은 수집 코드(data-engineer)라 **P-043**으로 분리; ①(잠자기)은 README 문서·검토만(근본 해법은 **Q-026** 상시 기기/서버); 이 티켓 = 주말 2회(래퍼 상수, `config.py` 변경 없음) + ⑤ DB 다운 시 `open -a Docker` 기동·최대 90초 대기 + ⑥ `RunAtLoad=true`(로그인 시 그날 본 실행만 보충, 마감 슬롯은 보충 안 함) + 23h **문구** 정정(동작 유지) + README 8절. 완료 기준 1~7 확정안은 브리핑에(크레딧 표: 최악의 달 PM 계산 432 < 500, 작업자 검증). 초안 기준 "실제 주말 1회 결과"는 **머지 뒤 PM이 `install.sh`로 설치한 다음 첫 주말(ET 10-31·11-01, 가을 DST 전환일)**에 확인해 여기에 기록. 작업자는 이 맥의 launchd·Docker·`.env`를 건드리지 않는다. 세션은 사람 "띄워" 뒤(로컬, Opus 5.5 높음).
  - 2026-10-28 23:1x PM: **DONE.** 22:2x 사람 "띄워" → 로컬 platform-engineer 세션 → PR #61(기준 1~7 O) → 리뷰 1회차(중간 1: 포트 열림≠Postgres 준비 → 그 몇 초 창에서 슬롯 결손 가능; 낮음 3) → 수정 커밋 `49a7076`(psycopg `SELECT 1` 실접속 프로브, 루프백일 때만 Docker 기동, 차단 재개 문구 정정, 미실측 표시) → 리뷰 2회차(높음 0·중간 0·낮음 1, 회귀 0) → 수락 → 사람 "머지해줘" → squash `9a3073b`. 테스트 841 passed·skipped 0. 크레딧 표: 2026-11~2027-06 최악 **432**(2027-01·05), 평균 414.75. **설치 실측 23:12:12:** `install.sh` → RunAtLoad로 즉시 한 번 깨어남 → `skip full … this slot already ran (marker full-2026-10-28-1600)`(설계대로), TCC 창 없음(이미 허용), `launchctl list` `- 0`, 깨어남 10개. **남은 확인:** 첫 주말 ET 10-31(토)·11-01(일) = KST 11-01 03:45·05:45(EDT), 11-02 04:45·06:45(EST) 캡처 결과와 재부팅 뒤 로그인 시 RunAtLoad 동작(미실측) → 여기 메모. 리뷰 2회차 낮음 1(비밀번호·DB 이름 오류도 "다운"으로 봐 90초 대기)은 P-044 브리핑 메모로.
  - 2026-10-29 PM **운영 실측:** 10:50 KST(21:45 ET 슬롯) 맥 재부팅 뒤 Docker 꺼진 상태에서 래퍼가 `db: down → launched Docker → up after 4 s` 뒤 캡처 OK(run 162~163) — ⑤ 실전 증명. 15:45:06 로그인 시 RunAtLoad 발화 → `skip`(본 실행 미도래) — ⑥ 실측. 07:45·08:45 KST 슬롯은 맥이 꺼져 있어 결손(사람 결정: 하루 감수, P-044 전환이 해법). 16:00 본 실행 OK(run 164~168, 5.1 s). **10-29 19:14 P-044 ③ 전환 뒤 맥 깨어남은 `16:00` 하나**(`ODDS_CLOSING_TIMES_ET=` 빈 값으로 주말 2회도 함께 꺼짐, D-059 1항 실측) → 첫 주말 ET 10-31·11-01 캡처 확인은 **VM 타이머**(`presagio-odds.timer`, 토·일 14:45·16:45 ET 포함) 결과로 P-044 메모에 적는다. 맥 쪽 RunAtLoad 로그인 보충은 본 실행만 해당.

### P-043 odds 단위 partial 판정·종료 코드 분리 + `ingestion_runs.started_at` 호스트 시계 (P-028 운영 관찰 ②③)
- **상태:** TODO — **브리핑 완료(2026-10-31, 공유 노트 `P-043-briefing.md`, 완료 기준 1~9 확정)**. 사람 "띄워" 대기(클라우드 data-engineer).
- **담당 역할:** data-engineer (Opus 5.5 높음, 클라우드 — mock으로 완결, D-052)
- **단계:** 2 (운영 품질)
- **의존:** 없음 (P-028과 파일이 겹치지 않음: `src/presagio/ingestion/` vs `scripts/launchd/`)
- **목표:** ② 10-24 10:46 odds 캡처가 262행을 저장했는데 FanDuel h2h 가격 1.0(`bad_price`) 1건 거부로 단위 partial → 부모 failed → exit 1 → launchd FAILED가 된 것을 고친다: 거부 outcome이 소수(임계 비율·건수)면 단위는 `succeeded` + 보고(warn), 임계를 넘으면 partial. 종료 코드는 "데이터는 들어갔고 품질 경고만"과 "실패"를 구분한다(P-017 리뷰 후속 warn/fail 분리와 함께). ③ `ingestion_runs.started_at`(DB `now()` 기본값)이 Docker VM 시계 지연(잠자기 뒤 17분)으로 어긋난 것을 파이썬(호스트) 시계로 기록하도록 바꾼다(`captured_at`·`fetched_at`과 같은 시계). 스키마 변경은 없다(기본값만 코드에서 채움).
- **범위:** `src/presagio/ingestion/`(odds 단위 판정, run 기록), `tests/ingestion/`, README 8절 결과 표의 exit 뜻(필요 시). 임계값은 설정으로 두면 `src/presagio/config.py`(같은 역할 소유).
- **범위 밖:** 래퍼·plist(P-028), 알림 채널(단계 6), 스키마.
- **메모:**
  - 2026-10-26 PM: P-028 브리핑에서 분리해 생성(관찰 ②③ 원문은 P-028 메모 10-24). 우선순위 중간 — 지금은 FAILED 표시만 틀리고 데이터는 들어간다.
  - 2026-10-31 PM: 재발 — VM `odds-2026-10-30-2145`(run 184/185) 가격 1개 거부로 자식 `partial`·부모 `failed`·래퍼 `exit=1 FAILED`, 340행은 저장됨. 전환 뒤 VM에서도 같은 증상이므로 `quality-check` 14 `recent_failed_runs` warn이 매번 쌓인다(119·184). **우선순위 유지(다음 티켓).** 브리핑 때 실제 사례 2건(119, 184/185)을 재현 자료로.
  - 2026-10-31 17:2x PM: **브리핑 작성**(사람 "써줘"; `P-043-briefing.md`, B 양식, 클라우드 세션용으로 실사례 run 119/120·184/185 행과 시계 어긋남(run 119 `started_at` 01:29:10 vs `captured_at` 01:46:25 UTC, 17분 15초) 수치를 전부 포함). PM이 확정한 범위·기준: ① 설정 `ODDS_REJECTED_PRICE_MAX_RATIO`(기본 0.02, 0이면 현행) — 거부 가격 비율이 임계 이하면 단위 `succeeded` + 경고(log·`error_detail.warnings`·quality_issues 유지, D-034 3항의 "크게 말하기"는 상태만 바꿈), 초과면 `partial`; 이벤트 거부는 현행 유지 ② 보고서에 "Warnings (N):" 절, 종료 코드는 D-037 그대로(경고만 0, partial/failed 1; 새 코드 없음 — 래퍼 `EXIT_MEANING`·README 표 호환) ③ 자식 `partial` → 부모 `partial`(지금은 한 단계 실행에서 부모 `failed`) ④ `started_at`·`finished_at` 호스트 시계(`server_default` 유지, 스키마 diff 0; `abort_stale_runs`의 시계 혼용은 주석) ⑤ 테스트 (a)~(g) ⑥ README 8절 표·236행, schema-v1 4장 ⑦ 실데이터는 2단계(D-060 2항: 머지·VM 배포 뒤 PM이 다음 자연 캡처에서 상태·`started_at`−`captured_at` < 5초 확인). 머지 뒤 PM 할 일: 공유 체크아웃 ff + VM `deploy.sh`(슬롯 창 밖).

### P-044 🧑 매일 배당 수집 + 개발 DB를 소형 클라우드 VM으로 이전 (D-058, 단계 6 일부 선행)
- **상태:** DONE (2026-10-29, PR #63 squash `c63fb3c`; 리뷰 2회, 기준 1~9 O — 3(c)·7(c)는 사람 승인 개정). 전환 ①~③ 10-29 완료, ④ 10-30 첫 자연 발화 3/3 OK(메모). 남은 ⑤·첫 주말은 메모에 추가.
- **담당 역할:** platform-engineer (Opus 5.5 높음). **로컬 세션** — VM에 SSH로 붙어야 하고 개발 DB 복사·맥 `.env`·launchd 전환이 있다(D-052 로컬 조건).
- **단계:** 2 (운영) — 단계 6 배포의 절반을 앞당김
- **의존:** P-028(슬롯 로직 재사용; 머지 뒤), **사람 준비 4가지**: ① DigitalOcean 가입 + 결제 수단 ② Droplet 생성(Basic $6, 1 GB, SGP1, Ubuntu 24.04, SSH 키) ③ Tailscale 계정 + 맥에 설치 ④ `ODDS_API_KEY`를 VM `.env`에 직접 넣기(PM이 명령을 주고 사람이 실행, 값은 채팅에 안 적음).
- **목표:** 배당 캡처(예측 시점 1 + 마감 3~5)와 개발 DB를 VM으로 옮겨 맥이 꺼져 있어도 마감 배당이 빠지지 않게 한다. 맥은 16:00 본 실행(nba_api, `ODDS_API_KEY` 없음 → 배당 단계 자동 skip)만 VM DB에 대고 돈다. 결손 확인 쿼리(README 8절)가 평일 `odds_only_runs = 3`, 주말 5를 VM 기준으로 보여 준다.
- **범위(초안):** `scripts/vm/`(셋업 스크립트: docker compose Postgres, uv, 레포 클론, systemd timer + 래퍼 재사용, 일일 `pg_dump`), `scripts/launchd/`(맥은 본 실행만 남기는 옵션), README 운영 절(VM 절 신설, launchd 절 축소), `tests/scripts/`. VM 쪽 실측(`stats.nba.com` 도달 여부 1회, 첫 캡처)은 PR 본문에.
- **범위 밖:** `src/`(코드 변경 없이 되는 설계 — 안 되면 멈추고 PM에게), 알림(단계 6), 대시보드, 과거 배당 백필.
- **메모:**
  - 2026-10-28 PM: Q-026 (b) 승인으로 생성. 사람용 준비 안내는 PM이 채팅으로 줬다(인계 노트 참조). 작업자 세션은 사람 준비 ①~③이 끝나고 IP·Tailscale 이름을 받은 뒤, 사람 "띄워"로.
  - 2026-10-28 21:5x PM 실측(D-058 보완): VM 생성됨(Ubuntu 24.04.5, x86_64, 1 vCPU/961 MB/24 GB, UTC, 스왑 없음, docker·uv 없음). VM에서 The Odds API 도달 OK, **stats.nba.com은 nba_api 헤더로도 403(Akamai WAF)** → 본 실행은 맥 고정. `--odds-only`는 항상 `pre_tip` 라벨이라 예측 시점 캡처도 맥 본 실행에 남고 키는 양쪽에. 맥 마감 슬롯은 `ODDS_CLOSING_TIMES_ET=` 빈 값(허용 실측)으로 제거. 맥 DB 28 MB. 레포 PUBLIC → IP는 README에 안 적음.
  - 2026-10-28 23:2x PM: 선행 P-028 머지됨(`9a3073b`) — 래퍼에 `connect_probe`·`LOOPBACK_HOSTS`·`--no-start-docker`·`WEEKEND_CLOSING_ET`·`plan --summary` 있음. 사람 준비 4단계 전부 완료(VM, SSH 키, Tailscale 맥 설치, 주간 백업). 브리핑 `P-044-briefing.md`에 P-028 리뷰 메모 3건 추가(실접속 프로브는 자격 증명 오류도 다운으로 봄 → VM `.env`·compose 값 일치 필수; `--no-start-docker`는 대기는 함; 주말 2회는 마감 시각 빈 값이면 함께 꺼짐). 남은 것: 사람 "띄워".
  - 2026-10-29 PM: 10-28 밤 사람 "띄워" → 로컬 platform-engineer 세션(`local_08dde69d`) → 10-29 14:0x PR #63(기준 1~9, VM 상태 보고·타이머 22회 = 래퍼 `plan` 1:1). 작업자가 Tailscale 로그인 URL 등 사람 행동이 필요할 때 PM 세션에 SendMessage(브리핑 지시대로 동작). 리뷰 1회차(`P-044-review.md`: 수정 후 재리뷰, 중간 2·낮음 5 — db-copy 가드가 `max(captured_at)`라 구멍, 복원 비단일 트랜잭션, deploy.sh 오류 숨김, README 수치 불일치 등) → 수정 요청 A~E → 커밋 `0575fbd`(집합 가드 + `--force`, `psql -1`, 오류 노출·슬롯 창·`apt --no-upgrade`, README 사실 정정·가벼운 되돌리기, 테스트 상수) → 리뷰 2회차(`P-044-review-2.md`: 수락 가능, 높음 0·중간 0·낮음 1, 회귀 0, 테스트 880 passed). **기준 개정(사람 승인 16:4x, D-008):** 3(c) "타이머 자연 발화 실제 슬롯"은 PR 시점에 구조적으로 불가(첫 슬롯 18시간 뒤) → 2단계(PR: 실제 시계·실제 유닛 명령 대체 시험 / 머지 뒤: PM이 첫 자연 발화 확인); 7(c) 전환 순서 = PR 결정 제안 1(복사 → 키 → 맥 전환 → 첫 캡처 확인 → 결손 쿼리). 수락 `P-044-acceptance.md` → 사람 "머지해줘" → 17:42 squash `c63fb3c`.
  - 2026-10-29 PM **전환 실행 기록(README 8절 체크리스트, D-060):** 17:4x VM `deploy.sh` → main `c63fb3c`(uv sync, alembic 0002 그대로, 타이머 2개 active) → ① 17:42 `db-copy-from-mac.sh`(가드: 캡처 시각 맥 19·VM 18·VM 전용 0 → 통과; 11초; 7개 표 행 수 일치: odds_snapshots 8,552·ingestion_runs 168·games 6,489·team_game_stats 10,694·predictions 5,288·evaluations 5,276·model_versions 3) → ② 19:1x 사람이 ssh 파이프 한 줄로 `ODDS_API_KEY` 투입(출력 `1`; PM `grep -c`로 1줄·600 확인, 값은 어디에도 안 남김) → ③ 19:14 맥 `.env` 전환(`.env.before-p044` 백업, `DATABASE_URL`→VM Tailscale 주소, `DB_CONNECT_TIMEOUT=5`, `ODDS_CLOSING_TIMES_ET=` 빈 줄 **추가** — 기존 `.env`에 그 키가 없어 README의 sed 치환만으로는 안 꺼짐, 추가로 해결) → `install.sh`: 깨어남 `16:00` 하나, RunAtLoad 즉시 1회 `skip full … already ran (marker full-2026-10-29-1600)`, `launchctl list` `- 0` → VM DB에 대고 `quality-check` 17 pass·0 fail·1 warn(14, 10-23 run 119 그대로)·1 skip, 행 수 VM 사본과 동일. **④ 실측(2026-10-30 17:5x PM):** VM 타이머 첫 자연 발화 3회 전부 성공 — `RESULT exit=0 OK` `odds-2026-10-29-1845`(07:45:06 KST, 5.4 s) · `-1945`(08:45:06, 4.8 s) · `-2145`(10:45:07, 5.8 s); journal 각 "Starting → Finished", 슬롯 마커 3개, 캡처 행 514·624·620(`capture_kind` pre_tip), run 169~174, 크레딧 440→437→434→431. 메모리 available 519 MB, OOM 0. **맥 쪽(전환 뒤 첫날):** 11:16 KST 로그인 RunAtLoad → `skip: no slot now`(마감 슬롯은 보충 안 함, 설계대로), 16:00 본 실행 `RESULT exit=0 OK [full-2026-10-30-1600] 25.9 s`(VM DB 원격 쓰기라 5 s → 26 s; nba_api 3 + 배당 1 = 4 call, 예측 시점 캡처 594행, run 175, 품질 16 pass·0 fail·1 warn(14, 10-23 run 119)·2 skip). VM DB `odds_snapshots` 8,552 → 10,904, 10-29 ET 날짜 캡처 = 맥 1(prediction) + VM 3(pre_tip), 중복 0. **기준 3(c) 2단계 완료 → P-044 전 기준 충족.** **⑤ 실측(2026-10-31 17:1x PM, 10-30 ET 날짜):** VM 마감 3슬롯 전부 발화·캡처 — `odds-2026-10-30-1845` OK 590행 · `-1945` OK 322행 · `-2145` **`exit=1 FAILED` 그러나 340행 저장**(run 184/185: 가격 1개가 못 쓰는 값 → 자식 `partial`(19/0/19 이벤트, 359 rows), 부모 `failed`, 종료 1; 10-23 run 119와 같은 증상 = **P-043 대상**, 데이터 결손 아님). 결손 0, 크레딧 431→422. 맥: 12:42 로그인 RunAtLoad skip, 16:00 본 실행 OK(launchd가 16:01:05에 깨움 — 허용폭 ±10분 안; 28.0 s, 414행 prediction, run 186, 품질 16 pass·0 fail·1 warn·2 skip). VM `odds_snapshots` 12,570, OOM 0, available 541 MB. **남은 확인:** 첫 주말 ET 10-31(토)·11-01(일, DST 종료) VM 캡처 5회씩(KST 11-01 03:45·05:45·07:45·08:45·10:45, 11-02 04:45·06:45·08:45·09:45·11:45) → 메모 뒤 P-044 메모 종료. 후속(작음): 리뷰 2회차 낮음 1(README `psql -1` "원래 그대로" 문구 완화), 결정 제안 6(기존 스크립트 shellcheck 3건·CI 도입 여부), Tailscale 주소로 ssh 할 때 known_hosts 지문 대조.

### P-041 모델링 스키마 후속 보강 (P-031 리뷰 [낮음] 10건)
- **상태:** DONE (2026-10-24 16:4x PR #49 squash merge `d7a0e01`, 사람 지시("둘 다 머지해줘")로 PM 머지(update-branch → CI → squash). 기준 5 실데이터: 머지 뒤 개발 DB `quality-check` 19개 중 17 pass·1 warn(14번, run 119/120)·1 skip(17번)·fail 0·error 0, 18 PASS checked 0·19 PASS 5,288. D-056)
- **담당 역할:** data-engineer (Opus 5.5 높음). D-052상 클라우드 후보(mock 테스트로 완결).
- **단계:** 2
- **의존:** P-031 DONE(머지), Q-022.
- **목표:** P-031 리뷰(`P-031-review.md`)의 [낮음] 중 코드·문서로 고칠 것을 한 PR로. 스키마(DDL) 변경 없음.
- **범위(초안):** `src/presagio/quality/checks.py`(+tests): (a) `predictions.feature_id`가 같은 `game_id`이고 피처 `as_of ≤ 예측 as_of`인지 검사하는 체크(fail), (b) `feature_drift` compared=0이면 SKIP, (c) 모델링 표가 없을 때 `alembic_version` < 0002면 SKIP·그 외 error, (d) 예측 행 `market_status`가 4값 밖이면 fail(Q-022 (2)(a)); `tests/db/test_migrations.py`에 `pg_indexes.indexdef` 비교 추가(M5); `docs/design/schema-v1.md` 9장 머리말 메모·3장 Q7·7-2의 `predicted_at`→`as_of`·13장 1-1 오기·`artifact_path` 규칙("디렉터리 + `train_cutoff` 기준 파일명") 한 줄·9-4 두 표의 `market_status` 뜻 차이 명시; `README.md` 품질 체크 14→17 표.
- **완료 기준 (전부 만족해야 DONE):**
  1. `src/presagio/quality/checks.py`에 (a)·(b)·(c)·(d) 네 가지가 들어가고 각각 임시 DB 테스트가 있다: (a) `predictions.feature_id`가 가리키는 피처가 같은 `game_id`이고 피처 `as_of` ≤ 예측 `as_of`가 아니면 **fail**(feature_id NULL은 대상 밖), (b) `feature_drift`는 compared=0이면 **skip**, (c) 모델링 표가 없을 때 `alembic_version` < 0002면 **skip**, 그 외(표가 있어야 하는데 없음)는 **error**, (d) `predictions.market_status`가 예측 시점 4값(available / no_snapshot_at_as_of / no_odds_season / unmatched_event) 밖이면 **fail**(D-054 2항. `evaluations`는 6값 그대로). 전체 `quality-check` 출력에서 D-040 14개의 pass/fail 수가 변하지 않는다(개수 기준 금지, D-053 4항).
  2. `tests/db/test_migrations.py`에 모델링 표 부분 유니크 인덱스의 `pg_indexes.indexdef`를 설계안 9장 정의와 비교하는 테스트(M5)가 있고 통과한다.
  3. 문서 정정이 전부 들어간다(코드 아님): `docs/design/schema-v1.md` 9장 머리말 메모·3장 Q7·7-2 `predicted_at`→`as_of`·13장 1-1 오기·`artifact_path` 규칙("디렉터리 + `train_cutoff` 기준 파일명") 한 줄·9-4 두 표의 `market_status` 뜻 차이(예측 4값/평가 6값, D-054)·추천 기준 행 origin 규칙("origin별 하나, 단계 4 마이그레이션에서", D-054 1항)·6-1·13장 "2022-23 2 + 2023-24 5 = 7경기"; `docs/research/backfill-report-2022-23.md` 198·268행 근거 교체 + 199행 오타; `README.md` 품질 체크 표 14 → 17(이 티켓으로 늘어나는 수까지).
  4. 스키마·마이그레이션 변경 0: `git diff main...HEAD -- alembic src/presagio/db`가 비어 있다. `PRESAGIO_REQUIRE_DB=1 uv run pytest -q` 전부 통과, skipped 0, 외부 호출 0.
  5. 실데이터 확인: `uv run presagio quality-check`가 개발 DB에서 fail 0·error 0(클라우드 세션이면 "머지 후 PM"으로 표시하고 임시 DB 결과만).
- **확인 방법:** 기준 4의 diff, 기준 5의 명령, 새 체크의 임시 DB 테스트(fail·skip·error 각 1회 재현).
- **추가 범위(P-026 리뷰, 2026-10-23, D-053):** 소형 문서 정정 3건 — (e) `docs/design/schema-v1.md` 6-1·13장 "2023-24 보완 목록 5경기" → "2022-23 2 + 2023-24 5 = 7경기"(305·329·1086행 근처), (f) `docs/research/backfill-report-2022-23.md` 198·268행: 포스트시즌 90경기 점수 근거를 `gamelog_vs_schedule_score`(대상 밖)에서 `points_match_score`·raw 게임로그 대조로 바꾸고 "이 90경기는 `gamelog_vs_schedule_score` 대상 밖" 한 줄, (g) 같은 파일 199행 오타("1행 는 것은" → "1행 늘어난 것은"). 코드 변경 없음.
- **메모:**
  - 2026-10-24 PM 16:4x: 머지 `d7a0e01`. 개발 DB quality-check 19개 fail 0(기준 5 O). 자동 브랜치 `claude/happy-hypatia-o2xt9a` 삭제. **DONE.** 후속("나중에"): 4값 ⊂ 6값 고정 테스트, M5 정규식은 단계 4 origin 때, `schema-v1.md:3` 버전 나열.
  - 2026-10-24 PM 16:3x: PR 댓글(15:0x)로는 세션이 안 깨어남 → 사람 `claude auth login` 뒤 `claude -p --cloud session_01AUNBZPfERi7WrcPoVp9F7C`로 전송(16:1x) → 16:29 확인 시 `682a967` 도착. **PM 수락**, 사람 머지 대기.
  - 2026-10-24 PM 15:2x: 리뷰어가 PR #49 head와 PR #48 head의 병합 트리에서 충돌 없음 확인(README 절 분리 성공).
  - 2026-10-24 PM 15:0x: **리뷰(Opus) "수락 가능"**: 779 passed·skipped 0(루프백 밖 0), ruff·CI 통과, `alembic`·`db`·`evaluation`·`models`·`scripts`·의존성 diff 0, main·P-042 head와 충돌 없음(`git merge-tree`). 변이 20종(요청 (i)~(v), M5a~f, 추가 9) 중 결과가 사실상 같은 1개만 생존. 체크 1~16 함수 본문 불변(`inspect.getsource` 대조), 바뀐 건 feature_drift·`_rows_after_tipoff` 표 없음 분기뿐. 문서 11항목 전부 들어감(Q7은 2장에만 있어 작성자 해석 합리, 13장 1-1은 P-031 리뷰 원문과 같은 뜻). 개발 DB 실측: 18 `prediction_feature_link` PASS checked 0, 19 `predictions_market_status` PASS 5,288, `--season 2024-25`도 fail 0. **[낮음] 2:** `schema-v1.md:561` "이 티켓의 체크 3개"가 P-041로 읽힘(→ "P-031의"), `backfill-report-2022-23.md:265` "아직 5경기라고 적혀 있다"가 PR 뒤 거짓(→ "(P-041에서 정정)"). 참고: `alembic_version` 없음/빈 DB → skip(D-040 보완에 한 줄), 리비전 문자열 비교(0001·0002 0채움 전제), 4값 ⊂ 6값 고정 테스트 없음, M5 정규식은 단계 4 origin 추가 때 손질, `schema-v1.md:3` 버전 나열 v1.4에서 멈춤, 자동 브랜치 `claude/happy-hypatia-o2xt9a` 머지 뒤 정리. 작성자 결정 제안 1~5 → PM이 D-056으로.
  - 2026-10-24 PM 14:4x: PR #49 도착(세션 시작 약 40분 뒤). 작성자 결정 제안 5건(표 없음 규칙 D-040 보완, 4값 상수 독립 정의, FK 소실 시 fail, README 체크 수 표기, 13장 v1.5 기록)과 해석 2곳(2장 Q7 행, 13장 1-1 오기)은 리뷰 뒤 PM 판단. 클라우드에서 pre-commit 훅 다운로드가 이번엔 됐다고 함(네트워크 허용 범위 변동 가능성 메모).
  - 2026-10-24 PM 13:5x: 클라우드 세션 시작. P-042와 동시 진행(README는 9절 vs 클라우드 절로 분리, 나중 머지 쪽은 PM이 `gh pr update-branch`).
  - 2026-10-24 PM 01:2x: 사람 "Q-022는 추천대로" → D-054. 완료 기준 1~5 확정. P-033 PR #41의 저장 경로가 이미 예측 행 4값 규칙을 지키므로 (d) 체크는 그 뒤에 자연스럽게 pass.
  - 2026-10-23 PM: P-031 리뷰로 생성. PR #25 머지를 막지 않음.
  - 2026-10-23 PM: P-026 리뷰 [낮음] 3건을 문서 정정으로 추가(위 추가 범위).

### P-040 🧑 Claude Code 클라우드 세션 환경 셋업
- **상태:** DONE (2026-10-24. PR #31 `24abf4b` 머지 + 기준 4 = `presagio-dev` 첫 세션(P-033 2차)의 PR #41 증거: 훅 줄 정상, main 684 passed·skipped 0, `alembic current` 0002 head, 셋업 단계에서 uv sync 완료. 리뷰어가 검증 가능한 3개 일치. 환경은 Q-023·Q-024로 PM이 생성·수정. README 보정은 P-042)
- **담당 역할:** platform-engineer (Opus 5.5 높음). 환경 생성(claude.ai/code 입력)은 사람.
- **단계:** 2 (단계 6 선행). **시간 민감:** 클라우드 크레딧 US$250이 2026-11-05 16:59 KST에 만료된다.
- **의존:** P-024 DONE. Q-021 승인.
- **목표:** 구현·리뷰 티켓을 Claude Code 클라우드 세션(Ubuntu 24.04 VM, 약 4 vCPU·16GB)에서 돌릴 수 있게 레포 쪽 준비물(셋업 스크립트, SessionStart 훅, 운영 절차)을 만들고, 사람이 환경을 만든 뒤 첫 클라우드 세션 1회로 검증한다. 로컬 플랜 한도를 아끼고 만료 전 크레딧을 쓴다.
- **범위(건드릴 파일):** `scripts/cloud-setup.sh`, `.claude/settings.json`(SessionStart 훅. `CLAUDE_CODE_REMOTE`가 설정된 클라우드 세션에서만 동작), `README.md` 운영 소절("클라우드 세션"). `docs/team/workflow.md`의 운영 규칙은 PM(D-052 예정).
- **범위 밖:** 제품 코드, 스키마, 로컬 compose·launchd 변경, 실제 nba_api·The Odds API 호출(클라우드 네트워크 Trusted 목록에 stats.nba.com이 없고 구현 티켓은 전부 mock), `ODDS_API_KEY`를 환경변수로 두는 것(금지. 필요해지면 network secret).
- **읽어야 할 파일:** `CLAUDE.md`, `README.md` 1~6절, `docker-compose.yml`(DB 사용자·DB 이름·포트 규칙), `.env.example`, `tests/conftest.py`(`PRESAGIO_REQUIRE_DB`, 임시 DB 생성에 `CREATEDB` 권한 필요), `alembic.ini`, D-019·D-020·D-022·D-029(CI의 postgres 설정과 같은 모양으로), 공식 문서 `code.claude.com/docs/en/cloud-environments.md`·`claude-code-on-the-web.md`(창업 세션이 2026-10-23 확인한 사실은 메모 참고. **작업자는 다시 읽고 바뀐 것이 있으면 PR에 적는다** — 추측 금지)
- **완료 기준 (전부 만족해야 DONE):**
  1. `scripts/cloud-setup.sh`(루트로 실행됨): uv 설치, `uv sync`, PostgreSQL 16 시작(`service postgresql start`), `presagio` 롤(CREATEDB 포함, 클라우드 전용 비밀번호)·`presagio` DB 생성, `uv run alembic upgrade head`. **멱등**(두 번 실행해도 성공), **5분 이내**(실측 시간을 PR에), 비밀값 없음, 실패 시 0이 아닌 종료 코드와 원인 한 줄.
  2. `.claude/settings.json` SessionStart 훅: `CLAUDE_CODE_REMOTE`가 설정된 경우에만 Postgres가 떠 있는지 확인·시작하고 `DATABASE_URL` 연결을 1회 검사한다. **로컬 세션에서는 아무 동작도 하지 않는다**(테스트: 로컬에서 훅 스크립트를 직접 실행하면 출력 없이 종료 0). 훅 스크립트는 `scripts/` 아래에 두고 settings.json은 그것을 부른다(D-051).
  3. README 운영 소절: (a) 사람이 claude.ai/code 환경 선택기에서 **복사해 붙일 값** — 환경 이름(제안 `presagio-dev`), 네트워크 수준(Trusted. stats.nba.com 미포함을 명시), 환경변수 두 개(`DATABASE_URL=postgresql+psycopg://presagio:<클라우드 전용 비번>@localhost:5432/presagio`, `PRESAGIO_REQUIRE_DB=1`. 둘 다 비밀 아님), 셋업 스크립트 내용(`scripts/cloud-setup.sh` 호출 한 줄 또는 본문). (b) 세션 시작법(`claude --cloud`, claude.ai/code, 데스크탑 앱 환경 선택기). (c) **클라우드 세션은 앱에 완료 알림을 보내지 않으므로 PM이 `gh pr list`로 확인**한다는 한 줄. (d) 클라우드 DB는 비어 있으므로 실데이터가 필요한 티켓은 로컬. (e) `ODDS_API_KEY`는 환경변수 금지, 필요 시 network secret.
  4. **클라우드 세션 1회 실제 검증:** 사람이 환경을 만든 뒤 PM이 띄운 첫 클라우드 세션에서 `PRESAGIO_REQUIRE_DB=1 uv run pytest -q`가 main 기준 전체 통과(skipped 0), `uv run alembic current`가 head, 그리고 그 세션이 PR을 하나 연다(이 티켓의 README 보완이나 검증 로그 추가). 통과 수·소요 시간·셋업 캐시 적중 여부를 PR 본문에.
  5. 로컬 영향 0: 로컬에서 `PRESAGIO_REQUIRE_DB=1 uv run pytest -q` 전부 통과, launchd 잡과 compose 설정 변경 없음, `git diff main...HEAD -- src alembic docker-compose.yml`이 비어 있음.
- **확인 방법:** 기준 4의 클라우드 세션 PR 본문, 로컬에서 훅 스크립트 무동작 실행, `bash -n scripts/cloud-setup.sh`.
- **메모:**
  - 2026-10-24 PM 02:3x: P-033 PR #41 PM 수락 → **PR #41 머지 시 기준 4 O, P-040 DONE**(증거는 PR #41 본문 절, 리뷰어 검증 3건 일치). 환경 모델 관찰: `start_session target: cloud` 세션이 PM 모델(Fable xhigh)로 뜸 → 다음 클라우드 브리핑 첫 줄 `/model claude-opus-5-5`·`/effort high`(D-043), P-042 README에 한 줄.
  - 2026-10-24 PM 01:5x: 리뷰어가 기준 4 증거 중 검증 가능한 3개(훅 줄 형식 = `session-start.sh` 출력, main `a09f5ae` 테스트 684개, alembic 0002 head) 일치 확인. VM 안 사실(pwd·CLAUDE_PROJECT_DIR·캐시)은 작성자 보고. **P-033 머지와 함께 기준 4 O → DONE 예정.**
  - 2026-10-24 PM 01:0x: **기준 4 증거 도착**(P-033 PR #41 본문 "P-040 기준 4 증거" 절, 작성자 보고): 훅 줄 `[presagio cloud] DB ready (PostgreSQL already running). OK … PostgreSQL 16.15 … PRESAGIO_REQUIRE_DB=1`, main `a09f5ae` 기준 `PRESAGIO_REQUIRE_DB=1 uv run pytest -q` **684 passed, skipped 0**, `alembic current` `0002 (head)`, uv 0.11.32, 셋업 단계에서 `uv sync` 완료(.venv mtime 15:19Z, 첫 명령 15:20Z). **Q-024 사실:** `pwd` = `/home/user/Presagio`, `CLAUDE_PROJECT_DIR` 빈 값(세션 셸), `/workspace*` 없음 → 클론 경로는 `/home/user/<레포>`. 리뷰어가 검증 가능한 부분(훅 문자열 형식, main 테스트 수, alembic head) 확인 후 PM이 기준 4 O 판정 → DONE. README 절대 경로 단순화 여부는 P-042에서.
  - 2026-10-24 PM 00:1x: Q-024 (a) 적용 — `presagio-dev` 셋업 칸을 경로 무관 래퍼로 교체(저장 확인). 기준 4는 P-033 2차 세션이 다시 본다. 래퍼 첫 줄 출력(`pwd`·script 경로)과 세션 안 `pwd`·`CLAUDE_PROJECT_DIR`가 README 수정(P-042)의 근거.
  - 2026-10-23 PM 21:5x: **기준 4 첫 실측 실패(수정 필요).** `presagio-dev`의 첫 세션(P-033)이 셋업 단계에서 `bash: scripts/cloud-setup.sh: No such file or directory`(exit 127). 플랫폼의 셋업 스크립트 작업 디렉터리 ≠ 레포 루트(공식 문서에 명시 없음, 실측으로 확인). README 345~365행의 "셋업 스크립트 칸: `bash scripts/cloud-setup.sh`"는 틀린 안내 → Q-024(환경 칸 교체)·P-042(README·스크립트 머리말 수정, 실제 `pwd`·클론 경로 기록). 훅(`$CLAUDE_PROJECT_DIR` 기준)은 영향 없음. 기준 4는 수정 뒤 새 세션으로 다시 본다.
  - 2026-10-23 PM 21:3x: **환경 `presagio-dev` 생성(Q-023 (a) 승인)** — 사람이 Claude in Chrome 확장을 연결, PM이 claude.ai/code 환경 선택기 → 새 환경 폼을 README 345~365행 값으로 채우고 사람 확인 뒤 "환경 추가". 실제 UI 표기: 네트워크 액세스 선택지는 없음/제한됨/전체 셋이고 "Trusted" 라벨이 없다 → "제한됨 · 도메인 없음 · 패키지 관리자 켜짐"이 문서의 Trusted(허용 목록만)와 같은 뜻(README 문구 보정은 P-042). 시크릿 없음, 셋업 `bash scripts/cloud-setup.sh`, 환경변수 2개. 환경 id `env_01UoBDS4VzgW1DDEPHrA7rLy`. 기준 4는 P-033 클라우드 세션의 PR이 증명.
  - 2026-10-23 PM 20:5x: 사람 지시로 `gh pr update-branch 31`(BEHIND → HEAD `589ba07`, CI 초록) → **squash 머지 `24abf4b`**. 로컬 훅 무동작 확인(공유 체크아웃에서 `bash scripts/cloud/session-start.sh` 출력 없이 exit 0, launchd 영향 없음). 리뷰 worktree 제거. 사람 질문 "환경은 내가 만들어야 하나, 네가 못 만드나" → 공식 문서로 확인해 **Q-023**(UI에서만 추가 가능. 선택지: PM이 네 크롬으로 / 네가 직접 / Default 환경 우회).
  - 2026-10-23 PM 20:1x: **리뷰 "수락 가능, 기준 4 머지 후 확인"**(`P-040-review.md`): 기준 1·2·3·5 O(맥 497 passed·훅 무동작 exit 0 직접 확인; ubuntu:24.04 + PG 16.15 컨테이너에서 셋업 콜드 10.4초·재실행 0.6초·멱등 5경우·실패 경로 3종 재현; PR+main 병합 트리 683 passed·0002 head; 공식 문서 인용 전부 원문 일치). 기준 4는 작성자가 셋업 칸·환경변수 없는 Default 환경에서 확인한 것이라 "사람이 환경을 만든 뒤 첫 세션" 문구대로는 미충족 → **PM 결정: 머지 → 사람이 `presagio-dev` 생성 → 첫 세션(P-033 클라우드)이 훅 줄·683 passed·0002 head를 PR에 기록하는 것으로 기준 4 충족.** 기준 3(a) `localhost`는 PR의 `127.0.0.1` 인정. [낮음] 5(셋업 칸이 cwd에 기댐 → rc 127 위험, 캐시 낡아도 훅 "DB ready", `/remote-env` 환경 선택 안내 없음, 셋업 성공 경로 테스트 없음(변이 5 생존), 훅 실패 출력 여러 줄) → **P-042**. 변이 14 중 9 잡힘. 리뷰어가 VM에서 새로 확인한 사실(uv 0.11.32 설치됨, astral.sh·api.the-odds-api.com 막힘, docker 데몬 꺼짐, python3 3.13, `CLAUDE_CODE_REMOTE="true"`, **GitHub GraphQL 403 → 클라우드에서 `gh pr` 불가, `gh api` REST 사용**, idle 시 VM 정지·재개) → D-052 보완, workflow.md, P-033 브리핑 보강.
  - 2026-10-23 PM: 창업 세션 요청(사람이 claude.ai GitHub 커넥터 연결·Claude GitHub App을 `oghdy`에 설치·Presagio 선택 완료, 크레딧 US$250 잔액 확인). 창업 세션이 공식 문서로 확인한 사실: VM은 Ubuntu 24.04·약 4 vCPU·16GB·30GB, PostgreSQL 16과 docker compose 설치됨(Postgres는 꺼져 있음), 프로세스는 세션 간 비유지(파일·패키지·이미지 캐시만 유지) → DB 시작은 SessionStart 훅으로, 셋업 스크립트는 5분 안에 끝나야 캐시(약 7일), 환경변수는 환경 사용자 모두 읽음 → 비밀값은 network secret, 기본 Trusted 네트워크에 PyPI·GitHub·Docker Hub 있음·stats.nba.com 없음, 클라우드 세션은 레포의 CLAUDE.md·.claude/settings.json·.claude/agents를 읽고 사용자 ~/.claude는 없음, 시작 시 푸시된 브랜치를 클론, **완료 알림 없음**. 진행 중인 P-031·P-032는 로컬에서 끝낸다. 2단계 진행: ① 로컬 platform-engineer가 1·2·3·5 → PR(사람 머지) → ② 사람이 환경 생성 → PM이 첫 클라우드 세션으로 4 검증.

### P-024 맥 launchd 임시 스케줄 (단계 6 전까지 매일 수집 + 배당 스냅숏)
- **상태:** REVIEW (2026-10-23 PR #23 https://github.com/oghdy/Presagio/pull/23, `3a4dddd`. 재리뷰 "수락 가능(기준 5 대기)" → **16:00 KST 첫 실제 실행 성공(run 102, PM 확인 16:04)** → 리뷰 3회차 "수락 가능" → PM 수락 → **사람 지시로 PM이 squash 머지 `5035231`, 공유 체크아웃에서 `install.sh` 재설치 완료(plist 래퍼 경로 = 공유 체크아웃, `--uv` 없음). DONE**)
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
  - 2026-10-23 PM 13:08: 작성자 수정 커밋 `3a4dddd`("base block pause on slot times and trust only ingest-daily exit codes", 파일 7, +1187, 래퍼 테스트 50, CI 초록). 작업자 worktree 깨끗(래퍼 해시 `baf87eed…`), 설치된 잡은 그 파일을 16:00에 실행. 같은 reviewer 서브에이전트에게 재리뷰(차단 경로 1~8, 평상시 경로 회귀 확인) 요청 → `P-024-review-2.md`. 기준 5는 16:00 뒤 PM.
  - 2026-10-23 PM 13:4x: **재리뷰 "수락 가능(기준 5 대기)"**(`P-024-review-2.md`). 1차 발견 1~8 전부 해결(정지 기준을 슬롯 예정 시각으로 + 23시간 50분, 자식을 venv `presagio` 콘솔 스크립트로 직접 호출 + 차단 줄이 있을 때만 BLOCKED, install.sh가 `$REPO/scripts/launchd` 래퍼만 허용, 30분 타임아웃 `exit=124 TIMEOUT`, README·테스트 보강). 전에 살아남은 변이 3개 이제 잡힘. 평상시 경로 회귀 없음(852 슬롯 재계산 동일, 설치된 plist 인자로 dry-run 5회 exit 0). 472 passed(네트워크 차단). 새 [낮음] 2: (1) 봄 서머타임 시작 다음 날 하루만 차단 다음 날 같은 마감 슬롯을 건너뜀(`BLOCK_PAUSE = 23h − 허용치`로 줄이면 해결), (2) `presagio_script()`를 고정하는 테스트 없음(한 줄). → 16:05 이후 기준 5 증거와 함께 한 커밋으로 반영 요청 예정(그 전엔 래퍼 파일 불변). 참고: 타임아웃으로 죽은 run은 `running`으로 남아 같은 scope가 6시간(`STALE_RUN_HOURS`) 동안 exit 1 → 운영 메모. **머지 후 순서:** `git pull` → 공유 체크아웃에서 `scripts/launchd/install.sh` → plist 래퍼 경로에 `.claude/worktrees`·`--uv` 없는지 확인 → 그 뒤에만 작업자 worktree 정리.
  - 2026-10-23 PM 16:04: **기준 5 충족.** launchd 16:00:03 시작 → 16:00:10 `RESULT exit=0 OK [full-2026-10-23-1600] 7.1 s`, 슬롯 표시 생성, blocked 없음, err 로그 0바이트, `launchctl` 종료 0. DB run 102(`incremental date_kst=2026-10-23`, succeeded) + 자식 103 schedule 10-22(1호출·2행), 104 gamelog(1호출·48행), 105 schedule 10-23 upcoming(1호출·12행), 106 odds(1호출·741행). 외부 호출 nba_api 3 + 배당 1(3크레딧) = 계획과 동일. `odds_snapshots` +724행(`prediction`, 이벤트 17, 07:00:09 UTC) — **올 시즌 예측 시점 배당이 처음으로 자동 수집됨.** 작성자에게 PR 기준 5 기록 + [낮음] 2건 한 커밋 요청. 밤 마감 3회는 10-24 07:45·08:45·10:45 KST.
  - 2026-10-23 PM 16:1x: 작성자 커밋 `527b717`("shorten block pause to 23 h for the spring DST day" + `presagio_script` 테스트 + PR 본문 기준 5 기록, 474 passed). 작업자 worktree 깨끗(설치된 래퍼 해시 `54793f84…` = 이 커밋). reviewer 서브에이전트에게 최종 확인(diff·테스트·기준 5 대조) 요청 → `P-024-review-3.md`.
  - 2026-10-23 PM 16:2x: **3회차 "수락 가능"**: 기준 1~5 전부 O(5는 로그·슬롯 폴더·DB·raw 응답으로 PR 기록과 전부 일치: run 102~106, nba_api 3 + 배당 1, 잔여 크레딧 494, odds 796→1,520). 낮음 2건 해결(정지 길이·테스트 변이로 확인), 평상시 경로 회귀 없음(852 슬롯, 200일 정지 경계 프로브에서 데이터 손실 0). 16:00 실행은 `3a4dddd` 래퍼로 돌았고 차이는 `BLOCK_PAUSE`뿐이라 영향 없음. 새 [낮음] 1: 정지 23h라 19:45 ET 차단 뒤 다음 날 18:45부터 재개(주석·README의 "같은 슬롯 전까지"보다 한 슬롯 이름. 손실 없음, 스냅숏 1개 추가) → **P-028 범위에 문구·테스트 정리 포함**(재리뷰 불필요). **PM 수락.** 수락 메시지 `P-024-acceptance.md`. 사람 머지 대기. **머지 후 PM:** `git pull` → `scripts/launchd/install.sh` → `plutil -p ~/Library/LaunchAgents/com.presagio.ingest-daily.plist | grep -n "presagio_launchd\|--uv"`에 `.claude/worktrees`·`--uv` 없음 확인 → 리뷰 worktree 제거 → 그 뒤 작업자 worktree 정리 가능. 내일 아침 `ingest-2026-10-24.log`에 `odds-2026-10-23-1845/-1945/-2145` RESULT 3줄 확인(기준 2 실물 증거). PM 지시 변경: 16:00 launchd 실행을 기다리지 말고 10:57 확인 뒤 PR을 먼저 열고, 기준 5(실제 본 실행 1회)는 16:00 KST launchd 실행 결과로 채우되 확인 쿼리를 PR에 적기. 설치한 잡은 제거하지 말 것(머지 후 PM이 공유 체크아웃에서 재설치). **16:00 이후 PM이 `ingestion_runs`로 본 실행 결과를 확인해야 한다.**

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
- **P-033/P-034:** 기준선과 Elo는 P-032 틀 위에서 돌아 `predictions`에 저장. Elo 상수·홈 어드밴티지·시즌 회귀율은 실험으로 정하되 첫 값은 문헌 기본값(설계안에 출처). **P-033에 넘기는 P-032 리뷰 후속(2026-10-23):** 학습 소스 자기 보고 값(`et_date`·`available_at`)을 `game_id`로 실행기가 대조, 학습 창 시즌 하한(2022-23 제외, M-6·M-11), 상태별(`market_status`) 모델 지표 행, 픽스처 cup final에 스탯 2행(제외 조건 변이 고정), `src/presagio/models`·`features`에서 `sys._getframe`·`inspect.currentframe/stack`·`gc.get_*`·`ctypes` 금지 AST 테스트(T9 확장), `predictions` 저장 시 `GameMarket` 복사, T1/T2를 등록된 모든 EXPERIMENTS 예측 함수로 파라미터화, `cli.py` 가드 위반과 DB 장애의 종료 코드 구분, numpy를 pyproject 직접 의존으로 명시, `predictions.feature_id` 같은 경기·as_of 순서 품질 체크(P-041과 조정).
- **P-035:** 피처 함수는 전부 `as_of` 입력. `team_game_stats_primary`(D-028 A-6)만 읽음. 누수 테스트: as_of 이후 행을 바꿔도 피처가 변하지 않음, 미래 경기를 주입해도 불변.
- **P-036:** 작성자와 다른 세션. 피처·평가 코드가 as_of를 어기는 경로가 없는지, walk-forward가 지켜지는지. 보고서는 리뷰어 양식. **참고(P-032 감사):** 평가 틀은 "누수 없음"(인터페이스·복제본 시간 여행 기준). 남은 위험은 의도적 스택 프레임 우회 → P-036에서 AST 금지 범위(`sys._getframe`, `inspect`, `gc`, `ctypes`, `sklearn.utils.shuffle`, `np.random.permutation/shuffle`, `random.shuffle`)를 확정. **(P-033 리뷰 2026-10-24)** 5(d) AST 검사 우회 4종을 금지 목록에 추가: `getattr(sys, "_getframe")`, `e.__traceback__.tb_frame.f_back`, `sys._current_frames()`, `sys.modules["gc"].get_objects()` — P-035 피처 작성 전에 검사기 보강 권고.
- **P-037:** 실험마다 번호·가설·설정·결과·판정을 `docs/experiments.md`에. 시장 포함/미포함 실험군 분리. 보류 시즌은 건드리지 않음. **(P-033 리뷰 2026-10-24)** B0 정확도(p≥0.5 규칙, 시즌 초 p<0.5면 원정 승 예측)와 "항상 홈 승" 정확도(= 홈 승률)를 별도 줄로. 집계는 `as_of >= 2024-01-01`(워밍업 행도 저장됨, D-055 4항). **(P-033 재리뷰)** 재저장 자동화는 exit 4를 "일정 변경(`scheduled_start_utc` 불일치, 평가 비어 있음)"과 "결정성 위반(`p_home` 등 값 불일치)"으로 구분해 처리(D-055 보완). 저장된 예측으로 재채점하는 경로는 단계 3 후보.

### 단계 2 시작 전 결정된 것 (2026-10-23)
- D-043 모델·노력 배치, D-044 `is_neutral` 컬럼(P-031), D-045 계획 승인. 과거 배당 $59 백필은 **P-033 시장 기준선 만들 때** 사람이 결제 → PM이 백필 티켓(P-039 후보) 생성.
- 2026-10-23 새 PM: 창업 세션 검증 노트(`stage-1-verification-2026-10-23.md`, F-1~F-5)로 세 가지를 질문으로 올렸다. **Q-015** D-044 정정(`is_neutral` 컬럼은 이미 있음, 2023-24 값만 보완 → P-025), **Q-016** 브랜치 보호, **Q-017** 임시 스케줄(P-024). F-4 문서 드리프트는 P-029. P-031 보드 제목의 "`games.is_neutral`(Q-013) → 마이그레이션 0002"는 Q-015 결과대로 고친다.
- 2026-10-23 사람 승인(한 메시지): Q-015 (a) → D-046, Q-016 → D-047(사람이 GitHub에서 켠다), Q-017 (a) → D-048, P-029 진행, P-030 브리핑 확정. PM이 Q-018(PM 문서 PR 자율 머지)을 추가로 올림. 같은 날 P-030(ml-engineer, Fable xhigh)·P-024(platform-engineer, Opus 높음)·P-025(data-engineer, Opus 높음) 세션 시작, P-029는 PM.

## 나중에 (단계 2 이후, 티켓화 전 메모)

단계 1이 끝나면 PM이 사람 확인을 받고 티켓으로 만든다. 지금은 제목만.

- 단계 2: 피처 모듈(as_of), 롤링 효율/페이스, 휴식일/백투백, Elo, 단순 기준선, 누수 테스트, features 테이블 승인
  - (D-033) predictions는 경기당 여러 `as_of` 스냅숏 허용 + 참조 배당 스냅숏. 전 경기 승패 확률과 총점(언더/오버) 둘 다. 예측 시각 결정.
  - (P-026 리뷰, 2026-10-23) **P-035 피처 작성자용:** `games.status_text`에 소스 그대로 공백 15칸이 붙은 경기 1건(2022-23 `0022200236`, `"Final" + 공백`. 다른 시즌 없음). 종료 판정은 `status_code`만 쓰므로 지금 영향 없지만 문자열을 비교하면 `trim` 필요. P-035 브리핑에 한 줄.
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
  - (P-026 리뷰, 2026-10-23, D-053) 백필 CLI에 "일정만 force" 옵션이 없어 중립 보완 뒤 일정만 다시 받으려면 시즌 유형 단위 전체(teams+일정+게임로그 3호출)를 force해야 한다. 수집 CLI를 손볼 때 후보(D-042 2항). 호출 상한 기준에는 "보완 뒤 전체 재수집" 몫을 포함.
  - (P-026 리뷰, D-053) 2022-23 플레이인 6·플레이오프 84경기 `games.source`가 `scheduleleaguev2`로 남아 `gamelog_vs_schedule_score`(`source='leaguegamelog'`만 검사) 대상 밖인 유일한 포스트시즌. 값은 게임로그와 같음(90/90). 다음 2022-23 재수집(`--season 2022-23 --force`, 5호출) 때 정렬.
- 문서 정리(PM, P-038 또는 docs 티켓): `docs/research/nba_api.md` 7장 픽스처 표에 `scheduleleaguev2__2023-24_neutral.json`·`scheduleleaguev2__2022-23_neutral.json` 추가, `docs/research/backfill-report-stage1.md` 10장 "중립 경기 여부가 DB에 없다" 문장을 D-046·P-025 이후로 갱신.
- 단계 7(선택): 부상자, 선수 피처, MLflow, 드리프트

## 아이디어 주차장 (범위 밖 아이디어는 여기에만 적는다)

- (비어 있음)
