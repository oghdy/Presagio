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

DB(`docker compose up`)와 설정 파일(`.env.example`)은 P-002에서 추가된다.

## 기술 스택 (기본안)

Python 3.12 · uv · PostgreSQL · SQLAlchemy 2 + Alembic · nba_api + httpx · scikit-learn + LightGBM · FastAPI · pytest + ruff

## 결과 요약

(단계 4 이후 채워진다. 지표 표, 보정 그래프, 모델 vs 시장 비교.)

## 한계

(단계 4 이후 채워진다. 숨기지 않는다.)
