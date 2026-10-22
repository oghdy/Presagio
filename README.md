# Presagio

> *Leyendo las señales del juego* — 경기의 신호를 읽다

매일 자동으로 NBA 경기 데이터를 수집하고, 승률과 총점을 예측하고, 경기 후 실제 결과와 시장 배당에 비교해 예측 성능을 정직하게 검증하는 시스템.

**현재 상태:** 단계 0 (프로젝트 셋업) 진행 중. 2026-10-21 시작.

## 이 프로젝트가 특이한 점

1. **시점 관리가 엄격하다.** 모든 예측은 경기 시작 전에 알 수 있던 정보만 쓴다. 누수를 잡는 테스트와 독립 감사 역할이 따로 있다.
2. **정직한 평가.** 무작위 분할 없이 walk-forward만. 시장 배당을 기준점으로 삼아 "시장보다 얼마나 못하는가"를 숫자로 측정한다. 베팅 기능은 없다.
3. **AI 에이전트를 팀처럼 운영한다.** PM, 엔지니어, 리뷰어 역할을 나누고 완료 기준과 PR 리뷰로 품질을 관리한다. 사람은 결정하고 머지한다.

## 문서 지도

| 알고 싶은 것 | 문서 |
| --- | --- |
| 전체 그림, 목표, 로드맵 | [PRESAGIO_OVERVIEW.md](PRESAGIO_OVERVIEW.md) |
| 모든 에이전트가 지키는 규칙 | [CLAUDE.md](CLAUDE.md) |
| 누가 무엇을 하나 (조직도) | [docs/team/organization.md](docs/team/organization.md) |
| 일이 흘러가는 순서 | [docs/team/workflow.md](docs/team/workflow.md) |
| 할 일 목록 (티켓 보드) | [docs/backlog.md](docs/backlog.md) |
| 설계 결정과 이유 | [docs/decisions.md](docs/decisions.md) |
| 실험 기록 (실패 포함) | [docs/experiments.md](docs/experiments.md) |
| 위험과 대응책 | [docs/team/risks.md](docs/team/risks.md) |
| 공부용 쉬운 설명, 용어집 | [docs/learning/](docs/learning/README.md) |

## 실행 방법

### 1. uv 설치 (처음 한 번)

[uv](https://docs.astral.sh/uv/)는 파이썬 버전, 가상환경, 의존성을 한 번에 관리하는 도구다. 둘 중 하나로 설치한다.

```bash
brew install uv
```

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

설치 후 `uv --version`이 나오면 된다. 안 나오면 새 터미널을 열거나 `~/.local/bin`이 `PATH`에 있는지 확인한다.

### 2. 의존성 설치

```bash
uv sync
```

`.python-version`(3.12)에 맞는 파이썬을 uv가 직접 내려받아 `.venv/`를 만든다. 시스템(anaconda 등) 파이썬은 쓰지 않는다(`pyproject.toml`의 `python-preference = "only-managed"`).

### 3. 테스트와 린트

```bash
uv run pytest -q
```

```bash
uv run ruff check .
```

```bash
uv run ruff format --check .
```

### 4. 커밋 전 검사(pre-commit) 켜기 (클론마다 한 번)

```bash
uv run pre-commit install
```

이후 `git commit`마다 ruff(린트, 포맷)와 gitleaks(비밀값 탐지)가 자동으로 돈다. 전체 파일에 한 번 돌려 보려면:

```bash
uv run pre-commit run --all-files
```

gitleaks 훅은 처음 실행할 때 Go 툴체인을 pre-commit이 알아서 내려받으므로 몇 분 걸릴 수 있다.

gitleaks 훅은 스테이징된 변경만 본다. 커밋 기록 전체 검사는 CI에서 한다.

### 5. 로컬 DB(PostgreSQL) 띄우기

DB는 [Docker Desktop](https://www.docker.com/products/docker-desktop/)으로 띄운다(D-016). Docker Desktop을 설치하고 실행한 뒤 `docker info`가 에러 없이 나오면 된다.

먼저 설정 파일을 만든다. `.env.example`에는 로컬 개발용 가짜 값만 있고, 복사본 `.env`는 `.gitignore`에 있어 커밋되지 않는다.

```bash
cp .env.example .env
```

PostgreSQL 컨테이너를 백그라운드로 띄운다. `--wait`는 헬스체크가 healthy가 될 때까지 기다렸다가 끝나고, healthy가 안 되면 실패(종료 코드 1)한다. 처음에는 이미지를 받느라 1분쯤 걸릴 수 있다.

```bash
docker compose up -d --wait
```

`--wait` 없이 `docker compose up -d`만 쓰면 바로 끝나지만, 직후 몇 초 동안은 `(health: starting)` 상태다. 상태를 본다. `STATUS`에 `(healthy)`가 보이면 준비 완료다.

```bash
docker compose ps
```

파이썬 코드가 `.env`의 `DATABASE_URL`로 DB에 붙는지 확인한다. 성공하면 `OK ... PostgreSQL 17...`이 찍히고, 실패하면 `FAILED: ...`와 함께 종료 코드 1로 끝난다.

```bash
uv run python -m presagio.db.session
```

이제 `uv run pytest -q`를 돌리면 DB 연결 테스트도 실제로 실행된다. DB가 꺼져 있거나 `.env`가 없으면 연결 테스트만 skip 되고 나머지는 그대로 통과한다.

멈출 때는 아래 명령을 쓴다. 데이터는 이름 있는 볼륨(`presagio_pgdata`)에 남아 다음 `up`에서 그대로 쓰인다. 데이터까지 지우려면 `docker compose down -v`.

```bash
docker compose down
```

호스트 포트는 5432가 아니라 **5433**이다. 맥에 Postgres.app이나 Homebrew PostgreSQL이 이미 5432를 쓰고 있는 경우가 많아서 충돌을 피했다. 바꾸려면 `.env`의 `POSTGRES_HOST_PORT`와 `DATABASE_URL`의 포트를 함께 고친다.

`DATABASE_URL`의 호스트는 `localhost`가 아니라 `127.0.0.1`이다. `localhost`는 IPv6 주소(`::1`)로도 풀리는데 컨테이너는 `127.0.0.1`에만 열려 있어서, 연결 실패 메시지의 첫 줄이 엉뚱한 `::1 ... Connection refused`가 되어 진짜 원인(비밀번호 틀림 등)을 가린다.

연결 테스트는 "DB 포트에 아무도 없음"이면 skip하고, "포트는 열려 있는데 접속이 거부됨"(비밀번호·DB 이름이 틀림)이면 **실패**한다. 설정이 틀렸는데 skip으로 조용히 넘어가지 않게 하기 위해서다.

### 6. 마이그레이션 (DB에 표 만들기)

표 구조는 [Alembic](https://alembic.sqlalchemy.org/) 마이그레이션으로 만든다. 설계는 [docs/design/schema-v1.md](docs/design/schema-v1.md)(D-028), 마이그레이션 파일은 `alembic/versions/`에 있다. 접속 정보는 5단계의 `.env`의 `DATABASE_URL`을 그대로 쓴다(`alembic.ini`에는 URL을 적지 않는다).

DB를 띄운 상태에서 최신 구조로 올린다. 표 8개와 뷰 2개가 생긴다. 두 번 실행해도 이미 최신이면 아무것도 바뀌지 않는다.

```bash
uv run alembic upgrade head
```

지금 DB가 어느 버전인지 본다. `0001 (head)`가 나오면 최신이다.

```bash
uv run alembic current
```

코드의 모델(`src/presagio/db/models/`)과 DB 구조가 같은지 확인한다. 같으면 `No new upgrade operations detected.`가 나온다. 모델을 고쳤는데 마이그레이션을 안 만들었으면 실패한다.

```bash
uv run alembic check
```

전부 지우고 처음 상태로 되돌릴 때는 아래 명령을 쓴다. **표와 데이터가 모두 지워진다.**

```bash
uv run alembic downgrade base
```

`uv run pytest -q`의 마이그레이션 테스트는 `.env`의 DB를 건드리지 않는다. 같은 서버에 `presagio_test_xxxxxxxx` 같은 임시 DB를 만들어 올리고 내린 뒤 테스트가 끝나면 지운다.

## 기술 스택 (기본안)

Python 3.12 · uv · PostgreSQL · SQLAlchemy 2 + Alembic · nba_api + httpx · scikit-learn + LightGBM · FastAPI · pytest + ruff

## 결과 요약

(단계 4 이후 채워진다. 지표 표, 보정 그래프, 모델 vs 시장 비교.)

## 한계

(단계 4 이후 채워진다. 숨기지 않는다.)
