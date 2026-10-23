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

테스트를 강제로 죽이면(`kill -9` 등) 임시 DB가 남을 수 있다. 남은 `presagio_test_*`를 한 번에 지운다(개발 DB `presagio`는 이름이 달라 지워지지 않는다).

```bash
echo "SELECT format('DROP DATABASE %I WITH (FORCE)', datname) FROM pg_database WHERE datname LIKE 'presagio\_test\_%' \gexec" | docker compose exec -T postgres psql -U presagio -d postgres
```

### 7. 백필 (과거 시즌 채우기)

`presagio backfill`은 nba_api에서 과거 시즌의 팀, 경기 일정·결과, 팀 박스스코어를 받아 DB에 넣는다. 5~6단계(DB와 마이그레이션)를 먼저 해 둔다.

**먼저 `--dry-run`으로 무엇을 할지 본다.** 할 일 목록, 호출 수, 예상 시간을 표로 보여 주고 DB에는 아무것도 쓰지 않는다(외부 호출도 0번).

```bash
uv run presagio backfill --season 2023-24 --dry-run
```

실제로 받는다. 시즌 하나에 호출은 일정 1번 + 게임로그 3번(정규, 플레이인, 플레이오프)이고, 처음 한 번은 팀 목록 1번이 더 붙는다. 호출 사이에는 1초씩 쉰다(D-024).

```bash
uv run presagio backfill --season 2023-24
```

여러 시즌은 `--season`을 여러 번 쓴다. 게임로그 종류를 줄이려면 `--season-type`(`regular`, `playin`, `playoffs`)을 쓴다.

```bash
uv run presagio backfill --season 2023-24 --season 2024-25 --season-type playoffs
```

날짜 범위(미국 동부 날짜)도 된다. 다만 nba_api는 시즌 단위로만 주므로, **범위가 걸친 시즌을 통째로 받는다**(호출 수는 시즌 지정과 같다). 끝나면 그 범위의 경기 수를 보여 준다.

```bash
uv run presagio backfill --from 2024-01-01 --to 2024-01-31
```

알아 두면 좋은 것:

- **끊겨도 다시 돌리면 이어서 한다.** 끝난 단위(시즌의 일정 1개, 게임로그 1개)는 건너뛴다. 판단은 그 단위의 **가장 최근 실행** 하나로 한다. 최근 실행이 실패(`failed`), 일부 실패(`partial`), 차단(`aborted`)이면 예전에 성공한 적이 있어도 다시 받는다. 아직 끝나지 않은 시즌(10월 15일 기준)은 데이터가 바뀌므로 매번 다시 받는다. 전부 다시 받으려면 `--force`. 다시 받아도 행 수는 같다(upsert).
- **일부가 실패해도 끝까지 간다.** 마지막에 단위별 결과 표와 실패 목록을 출력한다. 종료 코드: 전부 성공 `0`, 하나라도 실패 `1`, 403/429 차단으로 즉시 멈춤 `2`, 인자 오류 `64`. 차단되면 바로 다시 돌리지 말고 기다린다.
- 진행 기록은 `ingestion_runs` 표에 남는다. 실행 1번이 부모 행(`job_type = 'backfill'`) 1개, 단위마다 자식 행 1개다.

```bash
docker compose exec -T postgres psql -U presagio -d presagio -c "SELECT run_id, parent_run_id, job_type, scope_key, status, api_calls FROM ingestion_runs ORDER BY run_id DESC LIMIT 10"
```

- 프로세스가 죽어 `running`으로 남은 행은 `STALE_RUN_HOURS`(기본 6시간)가 지나면 다음 실행이 `aborted`로 닫고 진행한다. 그 전에 다시 돌리면 "already running"으로 거부된다. 확실히 죽었으면 `--stale-after-hours 0.1`처럼 줄여서 돌린다.
- `-v`를 붙이면 우리 로그만 자세히 나온다. `-v`는 `presagio -v backfill --season 2023-24`처럼 **`backfill` 앞에** 쓴다(뒤에 쓰면 인자 오류, 종료 코드 64). `httpx` 로그는 요청 주소(배당 API 키 포함)를 찍기 때문에 항상 꺼 둔다(D-031).

### 8. 매일 수집 (어제 결과 + 오늘 일정 + 배당)

`presagio ingest-daily`는 매일 한 번 돌리는 명령이다. 7단계 백필로 시즌 일정이 들어 있다고 가정한다.

**날짜를 어떻게 정하나.** 기준 날짜 D는 **한국 날짜**다(기본값: 지금 한국 날짜). NBA는 미국 동부 날짜로 경기를 묶으므로,
- 미국 동부 날짜 **D-1**에 시작한 경기 = "어제 경기" → 결과·점수·팀 박스스코어를 채운다.
- 미국 동부 날짜 **D**에 시작하는 경기 = "오늘 경기" → 일정을 넣고 배당을 받는다.

예: D = 2026-10-23(한국)이면 어제 경기는 동부 10-22(한국 10-23 아침에 열린 경기), 오늘 경기는 동부 10-23(한국 10-24 아침에 열릴 경기)이다.

먼저 `--dry-run`으로 무엇을 할지 본다(DB에 쓰지 않고 외부 호출 0번).

```bash
uv run presagio ingest-daily --date 2026-10-22 --dry-run
```

실제로 받는다. 보통 nba_api 3번(어제 스코어보드 1 + 게임로그 1 + 오늘 스코어보드 1)과 배당 1번이다. 팀 목록은 일주일에 한 번만 다시 받는다(`TEAMS_REFRESH_HOURS`, 기본 168시간). 그날은 nba_api가 4번이다. `.env`에 `ODDS_API_KEY`가 없으면 배당 단계는 `skipped`로 기록하고 넘어간다.

```bash
uv run presagio ingest-daily
```

배당만 받는 모드(경기 직전 "마감" 배당용, nba_api 호출 0번):

```bash
uv run presagio ingest-daily --odds-only
```

알아 두면 좋은 것:

- **언제 돌리나(D-027, 결정 제안).** 본 실행은 한국 16:00(`INGEST_DAILY_TIME_KST`) = 미국 동부 새벽 3시(여름)/2시(겨울)로, 어제 경기가 다 끝난 뒤다. 배당만 받는 실행은 동부 18:45, 19:45, 21:45(`ODDS_CLOSING_TIMES_ET`). 합쳐서 하루 최대 4번이다. `--dry-run`이 그날의 시각을 한국·동부·UTC로 보여 준다. 실제 예약(스케줄러)은 단계 6에서 한다.
- **어제 경기가 아직 안 끝났으면** 그 날짜는 `partial`로 남고, 다음 실행이 그 날짜를 다시 받는다(최근 3일까지). 이미 다 끝난 날짜는 다시 받지 않는다. 억지로 다시 받으려면 `--force`.
- **같은 날 두 번 돌려도 된다.** 경기·팀 스탯 행 수는 그대로다. 배당은 "스냅숏"이라 받을 때마다 값이 바뀌었으면 새 행이 쌓이는 것이 정상이다(값이 같으면 안 쌓인다).
- **마지막 단계는 품질 체크다**(9단계의 체크를 어제 동부 날짜 범위로, 읽기만 함). `FAIL`이나 `ERROR`가 하나라도 있으면 종료 코드 `1`, `WARN`만 있으면 `0`이다. 결과 요약(상태별 개수, 실패·경고 체크 이름)은 부모 행 `error_detail.quality`에 남는다. 끄는 옵션은 없다. `--dry-run`에서는 돌리지 않는다.
- 종료 코드는 백필과 같다: 전부 성공 `0`, 하나라도 성공 아님(`partial` 포함) `1`, 403/429 차단으로 즉시 멈춤 `2`, 인자 오류 `64`.
- 기록은 `ingestion_runs`에 부모 행(`job_type = 'incremental'`, `scope_key = 'date_kst=<D>'`) 1개와 단계별 자식 행으로 남는다. 어제 날짜의 결과 단위는 `job_type = 'schedule'`, `scope_key = 'date_et=<D-1>'`이다.

#### 운영: 임시 스케줄(launchd)

단계 6(서버 스케줄) 전까지 **이 맥의 launchd**가 `presagio ingest-daily`를 하루 4번 돌린다(P-024, D-048). 배당은 그 시각에 받지 않으면 영영 없기 때문이다. 단계 6에서 서버 스케줄로 바꾸면 이 잡은 끈다(아래 "끄는 법").

**전제.** 맥이 켜져 있고(잠자기는 괜찮다, 아래 참고), Docker Desktop이 켜져 DB 컨테이너가 떠 있고, 실행할 레포 루트에 `.env`(`DATABASE_URL`, `ODDS_API_KEY`)가 있어야 한다. 맥 시간대는 서울이어야 한다(설치 스크립트가 확인한다).

**macOS 개인정보 보호(TCC) 권한.** 레포가 `~/Desktop`·`~/Documents`·`~/Downloads` 아래에 있으면, launchd가 띄운 `uv`가 레포를 처음 열 때 macOS가 "uv이(가) 데스크탑 폴더의 파일에 접근하려고 합니다" 창을 띄우고 **사람이 답할 때까지 실행이 멈춘다**(2026-10-23 설치 시험에서 확인: `uv` 프로세스가 `open()`에서 대기, 로그 0줄). 터미널에서 직접 돌릴 때는 터미널 앱이 이미 권한을 가져서 안 보인다. 설치 직후 한 번 `launchctl kickstart gui/$(id -u)/com.presagio.ingest-daily`로 깨워 창이 뜨면 **허용**을 누른다(시스템 설정 → 개인정보 보호 및 보안 → 파일 및 폴더에서 `uv`의 "데스크탑 폴더"가 켜져 있으면 된다). 거부하면 `launchd.err.log`에 권한 오류가 남고 아무것도 돌지 않는다. 멈춘 채로 두면 그동안의 예약도 놓친다(잡이 도는 중에 온 예약은 건너뛴다, `man launchd.plist`). 확인: kickstart 뒤 몇 초 안에 오늘 로그에 `skip` 또는 `start` 줄이 생기고 `launchctl list | grep presagio` 첫 칸이 `-`로 돌아오면 정상이다.

**언제 도나.** 시각은 `.env`의 `INGEST_DAILY_TIME_KST`, `ODDS_CLOSING_TIMES_ET`를 그대로 읽는다(D-038).

| 실행 | 기준 시각 | 한국 시각: 서머타임(EDT, ~10-31) | 한국 시각: 겨울(EST, 11-01~) |
| --- | --- | --- | --- |
| 본 실행 `ingest-daily --date <한국 날짜>` | 한국 16:00 | 16:00 | 16:00 |
| 마감 배당 `ingest-daily --date <동부 날짜> --odds-only` | 동부 18:45 | 다음날 07:45 | 다음날 08:45 |
| 〃 | 동부 19:45 | 다음날 08:45 | 다음날 09:45 |
| 〃 | 동부 21:45 | 다음날 10:45 | 다음날 11:45 |

launchd는 맥의 한국 시각으로만 예약할 수 있어서, 잡은 여름·겨울 후보 시각을 모두 합친 **하루 6번**(07:45, 08:45, 09:45, 10:45, 11:45, 16:00) 깨어난다. 깰 때마다 래퍼(`scripts/launchd/presagio_launchd.py`)가 실제 시계로 "지금이 동부 기준 마감 시각 ±10분인가 / 한국 16:00 이후인가"를 판단해서, 아니면 로그에 `skip` 한 줄만 남기고 끝낸다. 그래서 여름이든 겨울이든 **실제 호출은 하루 정확히 4번**이다(월 최대 372크레딧, D-027). 같은 시각이 두 번 깨도(수동 실행, 잠자기 후) 시각마다 표시 파일이 있어 두 번째는 호출하지 않는다.

`ingest-daily --odds-only`는 스스로 시각을 확인하지 않고 부를 때마다 배당을 받는다(`src/presagio/ingestion/incremental.py`의 `plan_daily`). 시각 판단은 래퍼만 한다. 마감 배당에는 `--date <그 경기의 동부 날짜>`를 넘긴다. `ingest-daily --dry-run`이 보여 주는 "planned runs"(기준 날짜 D의 마감 배당은 동부 날짜 D 저녁)와 같은 규칙이다.

앞으로 며칠의 판단을 미리 보려면(호출 0번, DB도 안 씀):

```bash
uv run python scripts/launchd/presagio_launchd.py plan --from 2026-10-30 --days 4
```

예상 결과(일부): `2026-11-01 10:45 KST = 10-31 21:45 EDT  ->  odds ...`, `2026-11-02 07:45 KST = 11-01 17:45 EST  ->  skip`, `2026-11-02 08:45 KST = 11-01 18:45 EST  ->  odds ...`.

**켜는 법.** 매일 수집을 돌릴 체크아웃(루트에 `.env`가 있는 곳)에서:

```bash
scripts/launchd/install.sh
```

인자를 주지 않으면 스크립트가 들어 있는 레포에서 돈다. 다른 체크아웃에서 돌리려면 `scripts/launchd/install.sh /path/to/Presagio`. 설치 스크립트가 `uv` 절대 경로(`command -v uv`), 레포 경로, 로그 폴더를 채워 `~/Library/LaunchAgents/com.presagio.ingest-daily.plist`를 만들고 `launchctl bootstrap`으로 올린다(사용자 단위 LaunchAgent. Docker Desktop이 사용자 세션에서 돌기 때문). `.env`는 있는지만 확인하고 열지 않는다. 다시 실행하면 기존 잡을 내리고 새로 올린다. 마지막 줄에 예상 결과:

```
-	0	com.presagio.ingest-daily
```

(칸: 실행 중이면 PID, 아니면 `-` / 마지막 종료 코드 / 이름.) 언제든 `launchctl list | grep presagio`로 같은 줄을 볼 수 있다.

**끄는 법.**

```bash
scripts/launchd/uninstall.sh
```

`launchctl list | grep presagio`가 아무것도 출력하지 않으면 꺼진 것이다. 로그와 표시 파일은 남는다.

**로그 보는 법.** 한국 날짜별 파일 하나: `~/Library/Logs/Presagio/ingest-YYYY-MM-DD.log`. 실행마다 `start ...` 줄, `ingest-daily`의 표준 출력·오류 전부, 그리고 `RESULT` 줄이 남는다.

```bash
tail -n 80 ~/Library/Logs/Presagio/ingest-$(date +%F).log
grep RESULT ~/Library/Logs/Presagio/ingest-*.log
```

| `RESULT` 줄 | 뜻 | 할 일 |
| --- | --- | --- |
| `exit=0 OK` | 전부 성공 | 없음 |
| `exit=1 FAILED` | 실패 또는 `partial`(어제 경기가 덜 끝남, 품질 체크 FAIL, DB 연결 실패 등) | 위 출력의 보고서를 본다. `partial`은 다음 실행이 3일 안에서 다시 받는다(D-039) |
| `exit=2 BLOCKED` | 403/429 차단으로 즉시 멈춤(D-037) | **사람에게 알린다**(자동 알림은 단계 6). 래퍼는 24시간 동안 모든 실행을 건너뛴다. 원인을 확인한 뒤 일찍 재개하려면 `rm ~/Library/Application\ Support/Presagio/launchd/blocked` |
| `exit=64 USAGE` | 인자 오류(래퍼 버그) | 티켓을 연다 |

`launchd.out.log`/`launchd.err.log`(같은 폴더)에는 래퍼 자체가 못 뜬 경우(예: `uv`를 못 찾음)만 남는다. 시각이 아닌 깨어남은 `skip: no slot now (20:45 ET, 09:45 KST)`처럼 한 줄이다.

**맥이 잠들어 있거나 꺼져 있던 날.** Apple 문서(`man launchd.plist`의 `StartCalendarInterval`)에 따르면 cron과 달리 잠든 동안 지난 예약은 **깨어난 뒤 한 번** 실행되고, 여러 개가 지났으면 하나로 합쳐진다. 래퍼는 그 늦은 실행을 이렇게 다룬다: 마감 배당은 ±10분을 넘겼으면 건너뛴다(늦은 배당은 "마감"이 아니다). 본 실행은 같은 한국 날짜 안이면 늦게라도 돌고, 자정을 넘기면 건너뛴다(다음 날 본 실행이 어제 결과를 다시 받는다). 맥이 **꺼져** 있던 동안의 예약에 대해서는 문서에 언급이 없고 실제로 꺼 보고 확인하지 않았다(미확인). 어느 경우든 래퍼가 시각을 다시 판단하므로 하루 4번을 넘지 않는다. Docker Desktop이 꺼져 있으면 DB 연결 실패로 `exit=1`이고 그 시각의 데이터는 없다. **빠진 배당은 메우지 않는다**(정직한 결손, D-048).

**빠진 날 확인 쿼리.** 정상인 날은 `full_runs = 1`, `odds_only_runs = 3`이다. 마감 배당은 동부 날짜로 묶이므로 가장 최근 날짜는 한국 다음날 오전까지 3이 안 될 수 있다. 0인 날이 맥·Docker가 꺼져 있던 날이다.

```bash
docker compose exec -T postgres psql -U presagio -d presagio -c "
SELECT d::date AS day,
       count(*) FILTER (WHERE r.scope_key = 'date_kst=' || d::date) AS full_runs,
       count(*) FILTER (WHERE r.scope_key = 'date_kst=' || d::date || ';odds_only') AS odds_only_runs,
       string_agg(r.status, ',' ORDER BY r.run_id) AS statuses
FROM generate_series(current_date - 13, current_date, interval '1 day') AS d
LEFT JOIN ingestion_runs r
       ON r.job_type = 'incremental' AND r.parent_run_id IS NULL
      AND r.scope_key IN ('date_kst=' || d::date, 'date_kst=' || d::date || ';odds_only')
GROUP BY d ORDER BY d"
```

`scope_key`는 본 실행이 `date_kst=<한국 날짜>`, 마감 배당이 `date_kst=<동부 날짜>;odds_only`다(래퍼가 `--date`로 동부 날짜를 넘기기 때문. 이름의 `date_kst`는 CLI의 기준 날짜 D라는 뜻이다).

**래퍼만 시험해 보기(호출 0번).** `--dry-run`은 `ingest-daily --dry-run`을 부르고 표시 파일을 남기지 않는다. `--now`로 시각을 흉내 낸다.

```bash
uv run python scripts/launchd/presagio_launchd.py run --dry-run --now 2026-11-02T08:45+09:00 \
  --repo "$PWD" --uv "$(command -v uv)" --log-dir /tmp/presagio-dry --state-dir /tmp/presagio-dry
```

### 9. 품질 체크 (데이터가 상식에 맞는지 보기)

`presagio quality-check`는 DB에 들어 있는 데이터가 말이 되는지 검사하고 체크마다 한 줄씩 표로 보여 준다. **읽기만 한다**(DB를 고치지 않고, 외부 호출도 0번). 체크는 읽기 전용 트랜잭션 안에서 돌아서, 실수로 쓰려고 해도 DB가 거부한다.

```bash
uv run presagio quality-check --season 2023-24
```

표의 칸: 체크 이름, 대상 범위, 상태, 통과 수, 실패 수, 실패 예시 ID(최대 5개), 한 줄 설명. 표 아래 "Notes"에 시즌별 경기 수 같은 보충 설명이 나온다.

무엇을 보나(전부 14개):

| 체크 | 실패하면 무슨 뜻인가 |
| --- | --- |
| `team_rows_per_game` | 끝난 경기(정규·플레이인·플레이오프)에 팀 스탯 행이 홈 1 + 원정 1이 아니다. NBA컵 결승은 박스스코어를 필요할 때만 받으므로 빠진다 |
| `points_match_score` | 두 팀 득점 합이 최종 점수와 다르거나, 홈 팀 득점이 홈 점수와 다르다 |
| `stat_arithmetic` | 팀 스탯 한 행 안에서 `reb = oreb + dreb` 또는 `pts = 2×fgm + fg3m + ftm`이 안 맞는다(설계안 6-2에서 품질 체크로 넘긴 산술 관계) |
| `final_scores_present` | 끝난 경기인데 점수가 비어 있다(DB 제약이 막지만 혹시 대비) |
| `regular_season_games` | 끝난 시즌의 정규시즌 경기 수가 1,230(30팀 × 82 ÷ 2)이 아니다. **진행 중 시즌**은 예정 경기까지 센 수를 보여 주고 실패로 치지 않는다(30경기 넘게 모자라면 경고만) |
| `one_game_per_team_day` | 같은 팀이 같은 미국 동부 날짜에 두 경기를 한다(배당 매핑의 전제, D-036) |
| `gamelog_vs_schedule_score` | 게임로그로 넣은 점수가 일정·스코어보드 원본(raw)의 최종 점수와 다르다(매일 흐름에서 득점 검증이 자기 자신과 비교되는 것을 보완). 원본에 아직 종료 점수가 없어 비교 못 한 경기는 경고, 하나도 비교 못 하면 `SKIP` |
| `primary_stats_source` | NBA컵 결승이 아닌 경기의 대표 스탯이 게임로그가 아니라 박스스코어다(D-028 A-6) |
| `teams_and_aliases` | 팀이 30개가 아니거나, 어떤 팀에 `nba_api`·`the_odds_api` 별칭이 없다 |
| `odds_after_start` | 실패: 스냅숏이 **자기에게 적힌** 시작 시각 이후에 잡혔거나(DB 제약이 막으므로 제약이 사라졌을 때만), 마감 배당 뷰가 이벤트 시작 후 배당을 내준다. 경고: 이벤트의 **최신** 시작 시각(예: 경기가 앞당겨짐) 이후에 잡힌 스냅숏 |
| `odds_unmatched_events` (경고) | 우리 경기와 이어지지 않은 배당 이벤트 비율과 사유별 개수 |
| `odds_events_per_game` (경고) | 한 경기에 배당 이벤트가 2개 이상 붙었다 |
| `past_games_not_final` (경고) | 시작한 지 하루가 지났는데 아직 "종료"가 아닌 경기 |
| `recent_failed_runs` (경고) | 최근 7일 수집 실행 중 `partial`/`failed`/`aborted`로 끝났고 나중에 다시 성공하지 않은 것 |

상태는 `PASS`(통과), `FAIL`(데이터가 틀림), `WARN`(볼 만한 것, 종료 코드에 영향 없음), `ERROR`(체크 자체가 못 돎), `SKIP`(이 범위에서는 의미 없음) 다섯 가지다. **종료 코드: `FAIL`이나 `ERROR`가 하나도 없으면 `0`, 있으면 `1`, 인자 오류는 `64`.** 체크 하나가 오류로 죽어도 나머지는 끝까지 돈다.

범위와 옵션:

```bash
uv run presagio quality-check                                  # 전체 시즌
uv run presagio quality-check --date 2026-10-22                # 그 미국 동부 날짜의 경기만
uv run presagio quality-check --check points_match_score --check team_rows_per_game
uv run presagio quality-check --season 2025-26 --json          # 기계가 읽는 JSON (단계 6 알림용)
```

- `--games-tolerance N`: 끝난 시즌의 경기 수가 1,230에서 N경기까지 달라도 통과(기본 0). 단축 시즌을 다룰 때 쓴다.
- `--recent-days N`: 수집 실행을 며칠치 볼지(기본 7).
- 코드에서 부를 때는 `presagio.quality.run_checks(engine, season=..., date_et=...)`가 결과 객체(`QualityReport`)를 돌려준다. `report.exit_code`, `report.as_dict()`를 쓴다.

## 기술 스택 (기본안)

Python 3.12 · uv · PostgreSQL · SQLAlchemy 2 + Alembic · nba_api + httpx · scikit-learn + LightGBM · FastAPI · pytest + ruff

## 결과 요약

(단계 4 이후 채워진다. 지표 표, 보정 그래프, 모델 vs 시장 비교.)

## 한계

(단계 4 이후 채워진다. 숨기지 않는다.)
