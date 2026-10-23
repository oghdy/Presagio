# Presagio 회사 헌법 (CLAUDE.md)

이 파일은 Presagio 프로젝트에서 일하는 **모든 에이전트 세션이 가장 먼저 읽는 규칙**이다.
짧게 유지한다. 자세한 내용은 링크된 문서에 있다.

## 0. 이 프로젝트가 무엇인지

Presagio는 매일 자동으로 NBA 경기 데이터를 모으고, 승률과 총점을 예측하고, 경기 후 실제 결과와 시장 배당에 비교해 예측을 검증하는 시스템이다.
전체 그림은 [PRESAGIO_OVERVIEW.md](PRESAGIO_OVERVIEW.md)에 있다. 단계를 시작할 때마다 다시 읽는다.

## 1. 우리는 "회사"처럼 일한다

- 사람(Founder)이 최종 결정권자다. 스키마, 기술 스택 변경, 외부 서비스 선택, 비용이 드는 결정은 반드시 사람에게 묻는다.
- 역할은 `.claude/agents/*.md`에 정의되어 있다. 세션을 시작하면 자기 역할 파일을 먼저 읽는다.
- 조직도와 누가 무엇을 하는지: [docs/team/organization.md](docs/team/organization.md)
- 일이 흘러가는 순서(티켓 → 브랜치 → PR → 리뷰 → 수락): [docs/team/workflow.md](docs/team/workflow.md)
- 브리핑, 티켓, 리뷰 보고서 양식: [docs/team/handoff-templates.md](docs/team/handoff-templates.md)
- 알려진 위험과 대응책: [docs/team/risks.md](docs/team/risks.md)
- 세션의 모델·노력 배치(D-011, D-043): [docs/team/organization.md](docs/team/organization.md)의 표

## 2. 공유 기억은 문서다 (에이전트는 세션이 끝나면 기억을 잃는다)

| 무엇 | 어디 | 누가 고치나 |
| --- | --- | --- |
| 할 일 목록(티켓 보드) | [docs/backlog.md](docs/backlog.md) | PM만 상태를 바꾼다. 작업자는 PR 본문에 결과를 적는다. |
| 설계 결정 기록 | [docs/decisions.md](docs/decisions.md) | PM이 쓴다. 작업자는 PR에 "결정 제안"으로 적는다. |
| 실험 기록(실패 포함) | [docs/experiments.md](docs/experiments.md) | ML Engineer가 쓰고 PM이 확인한다. |
| 사람을 위한 학습 노트 | [docs/learning/](docs/learning/) | PM 또는 Scribe. 쉽게 풀어서 쓴다. |

작업을 시작할 때는 반드시 다음 순서로 읽는다: 이 파일 → 자기 역할 파일 → 자기 티켓(backlog) → 티켓에 적힌 "읽어야 할 파일".

## 3. 절대 원칙 (PRESAGIO_OVERVIEW 2장 요약)

1. **시점 관리와 데이터 누수 방지.** 어떤 경기의 예측에는 그 경기 시작 전에 알 수 있었던 정보만 쓴다. 모든 데이터에 수집 시각을 남긴다. 피처 함수는 `as_of`를 입력받는다.
2. **멱등성과 재현성.** 같은 작업을 두 번 돌려도 결과가 같아야 한다(upsert). 원본 응답은 raw로 보관한다. 학습은 시드를 고정한다.
3. **정직한 평가.** walk-forward만 쓴다. 무작위 train/test 분할 금지. 실패한 실험도 기록한다.
4. **외부 서비스 존중.** 요청 간 딜레이, 지수 백오프 재시도, 타임아웃을 항상 건다. 비밀값은 `.env`에만 두고 절대 커밋하지 않는다.
5. **추측 금지.** 라이브러리 동작이나 API 응답 형식을 모르면 직접 확인한다. 확인한 방법을 PR에 적는다.

## 4. 코드와 문서 규칙

- 언어: 문서, 티켓, PR 본문, 커밋 본문은 **한국어**. 코드 식별자, 주석, 커밋 제목은 **영어**.
- 커밋 제목은 Conventional Commits 형식. 예: `feat(ingestion): add nba_api schedule adapter`
- 커밋 메시지 끝에는 항상 다음 줄을 붙인다: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`
- Python 3.12, uv, ruff, pytest. 외부 네트워크는 테스트에서 mock 처리한다.
- 제품 파이썬 코드는 `src/presagio/` 아래에만 둔다. 운영·조사용 스크립트(스케줄 래퍼, 일회성 조사)는 `scripts/` 아래에 두고 `src/`를 import할 수 있지만 그 반대는 금지다(D-051). 테스트는 `tests/` 아래에 같은 구조로 둔다(`scripts/`의 테스트는 `tests/scripts/`).
- 타입 힌트를 쓴다. 함수에는 한 줄 docstring을 단다.

## 5. 파일 소유권 (충돌을 막는 규칙)

한 번에 한 파일 묶음에는 작성자가 한 명이다. 자기 소유가 아닌 영역을 고쳐야 하면 PM에게 티켓을 요청한다.

| 영역 | 소유 역할 |
| --- | --- |
| `CLAUDE.md`, `docs/backlog.md`, `docs/decisions.md`, `docs/team/` | PM |
| `src/presagio/ingestion/`, `src/presagio/db/`, `src/presagio/quality/`, `alembic/`, `docker-compose.yml`, `pyproject.toml` | Data Engineer (단계 0~1) |
| `src/presagio/features/`, `src/presagio/models/`, `src/presagio/evaluation/`, `docs/experiments.md` | ML Engineer (단계 2~4) |
| `src/presagio/api/`, `dashboard/` | Backend Engineer (단계 5) |
| `.github/workflows/`, `Dockerfile`, `scripts/`, 배포 설정 | Platform Engineer (단계 6) |
| `README.md`, `docs/learning/` | Scribe (초기엔 PM 겸임) |

## 6. 작업자가 절대 하지 않는 것

- 티켓에 적힌 완료 기준을 스스로 느슨하게 해석하지 않는다. 안 되면 "안 된다"고 PR에 쓴다.
- 자기 티켓 범위 밖 파일을 고치지 않는다.
- `main`에 직접 커밋하지 않는다. 항상 브랜치 + PR. 브랜치 보호가 켜진 뒤에는 PM 문서도 같다(D-047).
- 스키마를 바꾸는 코드를 사람 승인 없이 넣지 않는다.
- `.env`, API 키, 토큰을 커밋하지 않는다.
- 같은 티켓에서 같은 실패를 세 번째 반복하지 않는다. 두 번 실패하면 PM에게 올린다.

## 7. 세션을 끝낼 때

- 작업자: PR을 열고, PR 본문에 "무엇을 했나 / 어떻게 확인하나 / 못 한 것 / 결정 제안"을 적는다.
- PM: backlog 상태를 갱신하고, 결정이 있었으면 decisions.md에 적고, 사람에게 3문장 요약을 준다.
- 머지는 사람이 한다. 사람이 수락 메시지를 읽고 "머지해줘"라고 명시하면 PM이 `gh pr merge --squash`를 대신 실행할 수 있다(D-005 보완). 리뷰어·작업자는 머지하지 않는다.
