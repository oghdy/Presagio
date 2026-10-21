# Presagio 백로그 (티켓 보드)

> 상태 변경은 **PM만** 한다. 작업자는 PR 본문에 결과를 적는다.
> 티켓 양식과 규칙: `docs/team/handoff-templates.md` A, `docs/team/workflow.md` 1장.
> 상태: `TODO` → `IN PROGRESS` → `REVIEW` → `DONE`. 막히면 `BLOCKED(이유)`.
> 🧑 표시는 "사람 승인 게이트"가 있는 티켓이다. 승인 전에는 구현을 시작하지 않는다.

## 현재 단계: 0 (프로젝트 셋업)

### 보드 한눈에 보기

| 티켓 | 제목 | 역할 | 상태 | 의존 |
| --- | --- | --- | --- | --- |
| P-000 | 회사 구조 셋업 | (창업 세션) | DONE | - |
| P-001 | uv 프로젝트 뼈대 + ruff + pytest + pre-commit | data-engineer | REVIEW | - |
| P-002 | Docker Compose PostgreSQL + 설정 로딩 | data-engineer | TODO | P-001 |
| P-003 | GitHub Actions CI (lint + test) | platform-engineer | TODO | P-001 |
| P-010 | nba_api 엔드포인트 조사 (스파이크) | data-engineer | TODO | P-001 |
| P-011 | 🧑 raw/clean 스키마 설계안 v1 | data-engineer | TODO | P-010 |
| P-012 | SQLAlchemy 모델 + Alembic 초기 마이그레이션 | data-engineer | TODO | P-002, P-011 승인 |
| P-013 | 공통 수집 레이어: 재시도/딜레이/타임아웃/raw 저장/ingestion_runs | data-engineer | TODO | P-012 |
| P-014 | nba_api 어댑터: 팀, 일정, 결과 → teams, games | data-engineer | TODO | P-010, P-013 |
| P-015 | nba_api 어댑터: 팀 박스스코어 → team_game_stats | data-engineer | TODO | P-014 |
| P-016 | 백필 CLI (시즌/날짜 범위, 재개 가능, 멱등) | data-engineer | TODO | P-014, P-015 |
| P-017 | 증분 수집 CLI (전날 결과 + 오늘 일정) | data-engineer | TODO | P-016 |
| P-018 | 🧑 배당 API 후보 조사 (스파이크) | data-engineer | TODO | P-001 |
| P-019 | 배당 어댑터 인터페이스 + odds_snapshots 저장 | data-engineer | TODO | P-012, P-018 승인 |
| P-020 | 데이터 품질 체크 모듈 + CLI | data-engineer | TODO | P-015 |
| P-021 | 3시즌 + 현재 시즌 실제 백필 + 확인 쿼리 보고 | data-engineer | TODO | P-016, P-020 |
| P-022 | 단계 0~1 학습 노트 + 회고 | pm | TODO | P-021 |

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
- **상태:** REVIEW (2026-10-21 PR #1 열림, 브랜치 `feat/P-001-project-skeleton`)
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
  - 2026-10-21 PM: 리뷰 판정 "수락 가능", 완료 기준 1~5 전부 O. 발견 사항 [낮음] 1건(README가 `pre-commit run --all-files`로 비밀값 전체 검사가 되는 것처럼 읽힘. 실제 gitleaks 훅은 스테이징 변경만 검사). 결정 D-012, D-013, D-014 기록. 사람 머지 대기. 브리핑에서 런타임 의존성(SQLAlchemy 등) 추가를 범위 밖으로 명시했고, `uv.lock`은 커밋 대상에 포함했다.

### P-002 Docker Compose PostgreSQL + 설정 로딩
- **상태:** TODO
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

### P-003 GitHub Actions CI (lint + test)
- **상태:** TODO
- **담당 역할:** platform-engineer (단계 0 예외 활성)
- **단계:** 0
- **의존:** P-001
- **목표:** PR을 열면 자동으로 ruff와 pytest가 돌아 결과가 PR에 표시된다.
- **범위:** `.github/workflows/ci.yml`
- **범위 밖:** 배포, Docker 이미지 빌드, DB가 필요한 테스트(단계 1에서 service container로 추가).
- **읽어야 할 파일:** `CLAUDE.md`, `pyproject.toml`
- **완료 기준:**
  1. PR에서 CI가 실행되고 ruff check, ruff format --check, pytest가 통과한다.
  2. uv 캐시를 써서 2회차 실행이 1분 이내다(증거: Actions 로그 링크).
  3. main 브랜치 보호 규칙 제안(필수 체크)을 PR 본문에 적는다. 적용은 사람이 한다.
- **확인 방법:** PR의 Checks 탭.
- **메모:** 2026-10-21 PM: P-001 리뷰어가 완료 기준 추가를 제안함(decisions.md Q-001: `gitleaks git` 기록 전체 검사). 기준 변경은 사람 승인 후 반영. README에 "gitleaks 훅은 스테이징 변경만 본다, 기록 전체는 CI에서" 한 줄 추가도 이 티켓에서 같이 처리 가능(사람 확인 필요).

---

## 단계 1. 데이터 파이프라인

### P-010 nba_api 엔드포인트 조사 (스파이크)
- **상태:** TODO
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

### P-011 🧑 raw/clean 스키마 설계안 v1
- **상태:** TODO
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

### P-012 SQLAlchemy 모델 + Alembic 초기 마이그레이션
- **상태:** TODO
- **담당 역할:** data-engineer
- **단계:** 1
- **의존:** P-002, P-011 승인
- **목표:** 승인된 스키마 v1을 코드와 마이그레이션으로 만든다.
- **범위:** `src/presagio/db/models.py`(또는 패키지), `alembic/`, `alembic.ini`, `tests/db/test_migrations.py`
- **범위 밖:** 데이터 넣는 코드.
- **읽어야 할 파일:** `docs/design/schema-v1.md`, `docs/decisions.md`
- **완료 기준:**
  1. `uv run alembic upgrade head`가 빈 DB에서 성공하고, `downgrade base`도 성공한다.
  2. 모델과 마이그레이션이 일치한다(`alembic check` 또는 autogenerate 결과가 비어 있음).
  3. 설계안의 유니크 제약과 인덱스가 실제 DB에 존재한다(테스트로 확인).
  4. CI에서 postgres service container로 마이그레이션 테스트가 돈다(P-003 워크플로우 수정은 platform-engineer에게 요청, 이 티켓에서는 로컬 증명으로 충분).
- **확인 방법:** 위 명령.

### P-013 공통 수집 레이어: 재시도/딜레이/타임아웃/raw 저장/ingestion_runs
- **상태:** TODO
- **담당 역할:** data-engineer
- **단계:** 1
- **의존:** P-012
- **목표:** 모든 외부 호출이 거치는 한 곳을 만든다. 여기서 외부 서비스 존중 원칙(딜레이, 재시도, 타임아웃)과 raw 보관, 실행 기록을 강제한다.
- **범위:** `src/presagio/ingestion/client.py`, `src/presagio/ingestion/raw_store.py`, `src/presagio/ingestion/runs.py`, `tests/ingestion/`
- **범위 밖:** nba_api 특화 로직.
- **읽어야 할 파일:** `docs/research/nba_api.md`(딜레이 권장값), `docs/team/risks.md` B1
- **완료 기준:**
  1. 요청 간 최소 딜레이, 지수 백오프 재시도 횟수, 타임아웃이 설정값(`config.py`)이고 기본값이 문서화되어 있다.
  2. 재시도는 일시적 오류(타임아웃, 429, 5xx)에만 하고, 4xx는 즉시 실패한다. 테스트로 증명.
  3. 모든 응답이 `raw_api_responses`에 저장되고, 같은 요청을 다시 해도 중복 행이 생기지 않는다(테스트).
  4. `ingestion_runs`에 작업 종류, 시작/종료, 상태, 처리 건수, 에러 메시지가 기록된다(테스트).
  5. 테스트는 네트워크를 타지 않는다(httpx MockTransport 또는 동등한 방식).
- **확인 방법:** `uv run pytest tests/ingestion -q`

### P-014 nba_api 어댑터: 팀, 일정, 결과 → teams, games
- **상태:** TODO
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

### P-015 nba_api 어댑터: 팀 박스스코어 → team_game_stats
- **상태:** TODO
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

### P-016 백필 CLI (시즌/날짜 범위, 재개 가능, 멱등)
- **상태:** TODO
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
- **확인 방법:** 픽스처 기반 테스트 + `--dry-run` 실행.

### P-017 증분 수집 CLI (전날 결과 + 오늘 일정)
- **상태:** TODO
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

### P-018 🧑 배당 API 후보 조사 (스파이크)
- **상태:** TODO
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

### P-019 배당 어댑터 인터페이스 + odds_snapshots 저장
- **상태:** TODO
- **담당 역할:** data-engineer
- **단계:** 1
- **의존:** P-012, P-018 승인
- **목표:** 배당 소스가 무엇이든 같은 모양으로 `odds_snapshots`에 넣는 인터페이스와, 선택된 소스의 어댑터(키가 있으면 실제 연결, 없으면 픽스처 기반)를 만든다.
- **범위:** `src/presagio/ingestion/sources/odds/`, `src/presagio/ingestion/loaders/odds.py`, `tests/`
- **완료 기준:**
  1. 인터페이스가 소스 독립적이다(프로토콜 + 픽스처 구현 + 실제 구현).
  2. 같은 경기의 스냅샷이 시각별로 여러 개 저장되고, "예측 시점"과 "마감" 구분이 된다.
  3. 미국식/소수식 배당을 소수식으로 통일해 저장하고 변환 테스트가 있다.
  4. 멱등: 같은 스냅샷 재저장 시 중복 없음.
- **확인 방법:** 테스트.

### P-020 데이터 품질 체크 모듈 + CLI
- **상태:** TODO
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

### P-021 3시즌 + 현재 시즌 실제 백필 + 확인 쿼리 보고
- **상태:** TODO
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

### P-022 단계 0~1 학습 노트 + 회고
- **상태:** TODO
- **담당 역할:** pm
- **단계:** 1
- **의존:** P-021
- **목표:** 사람이 면접에서 데이터 파이프라인을 설명할 수 있도록 쉬운 말로 정리한다.
- **범위:** `docs/learning/stage-0-1-data-pipeline.md`, `docs/learning/glossary.md` 갱신, `README.md` 결과 요약 갱신
- **완료 기준:** `docs/team/handoff-templates.md` F 양식을 모두 채운다. 면접 예상 질문 3개 포함.

---

## 나중에 (단계 2 이후, 티켓화 전 메모)

단계 1이 끝나면 PM이 사람 확인을 받고 티켓으로 만든다. 지금은 제목만.

- 단계 2: 피처 모듈(as_of), 롤링 효율/페이스, 휴식일/백투백, Elo, 단순 기준선, 누수 테스트, features 테이블 승인
- 단계 3: 로지스틱 회귀, LightGBM, 총점 회귀, model_versions, 보정
- 단계 4: walk-forward 백테스트 명령, 지표, reliability diagram, 시장 확률 변환과 비교, experiments.md
- 단계 5: FastAPI 5개 엔드포인트, 테스트, 대시보드 기술 결정
- 단계 6: 매일 흐름 묶기, Docker 이미지, 배포 비용 비교, 알림, 신선도 체크, 7일 연속 운영
- 단계 7(선택): 부상자, 선수 피처, MLflow, 드리프트

## 아이디어 주차장 (범위 밖 아이디어는 여기에만 적는다)

- (비어 있음)
