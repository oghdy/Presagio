# 일하는 순서 (Workflow)

> 티켓 하나가 생겨서 머지될 때까지의 흐름. 모든 역할이 이 순서를 따른다.

## 전체 흐름

```
[PM] 티켓 작성 ──▶ [PM] 브리핑 작성 ──▶ [작업자] worktree에서 구현+테스트 ──▶ [작업자] PR
                                                                              │
     [사람] 머지 ◀── [PM] 완료 기준 대조, 수락 ◀── [Reviewer] 보고서 ◀───────────┘
        │                     │                        (누수 관련이면 Leakage Auditor도)
        │                     └── 반려 시: 티켓에 이유 기록 → 작업자 재작업 (최대 2회)
        └── [PM] backlog 상태 DONE, decisions.md 갱신, 사람에게 3문장 요약
```

## 1. 티켓 (PM)

- 위치: `docs/backlog.md`
- 번호: `P-NNN`. 단계 0은 P-00x, 단계 1은 P-01x~P-02x 식으로 단계별 구간을 쓴다.
- 상태: `TODO` → `IN PROGRESS` → `REVIEW` → `DONE`. 막히면 `BLOCKED`(이유 필수).
- 크기 기준: 한 세션에서 끝낼 수 있어야 한다. 변경 파일 10개 이하. 넘으면 쪼갠다.
- 티켓 양식은 `handoff-templates.md` 참고.

## 2. 브리핑 (PM → 작업자)

- 브리핑은 **작업자가 이 프로젝트를 처음 보는 사람이라고 가정**하고 쓴다.
- 반드시 들어가는 것: 역할 파일 경로, 티켓 번호, 읽을 파일 목록, 완료 기준, 범위 밖, 브랜치 이름, 끝내는 방법.
- "앞에서 말한 것처럼", "알아서" 금지.

## 3. 브랜치와 worktree (작업자)

- 브랜치 이름: `feat/P-NNN-short-name`, `fix/P-NNN-...`, `docs/P-NNN-...`
- 항상 `main`에서 새로 딴다. 작업자 한 명 = 브랜치 하나 = worktree 하나.
- 같은 소유 영역을 두 작업자가 동시에 건드리지 않는다. PM이 순서를 정한다.
- 커밋은 기능 단위로. 제목은 영어 Conventional Commits, 본문은 한국어 가능.
  예:
  ```
  feat(ingestion): add retrying http client with raw response storage

  요청 간 딜레이 0.6초, 지수 백오프 3회, 타임아웃 10초.
  모든 응답을 raw_api_responses에 저장한다.

  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  ```

## 4. PR (작업자)

- 제목: `[P-NNN] 한 줄 설명`
- 본문: `handoff-templates.md`의 PR 양식. 특히 "어떻게 확인하나"는 명령을 복사해서 돌릴 수 있게.
- 테스트가 모두 통과한 상태에서만 PR을 연다. 통과 못 하면 Draft PR로 열고 "못 한 것"에 적는다.
- PR을 열면 세션의 마지막 메시지에 PR 링크와 3문장 요약을 적는다.

## 5. 리뷰 (Reviewer, 필요시 Leakage Auditor)

- PM이 리뷰어에게 주는 것: 브랜치/PR, 티켓 번호. **그 외 의견은 주지 않는다.**
- 리뷰어는 테스트를 직접 돌린다. PR 본문의 명령을 그대로 실행한다.
- 판정: 수락 가능 / 수정 후 재리뷰 / 반려.
- 누수 관련 변경(features, models, evaluation, as_of를 다루는 모든 코드)은 Leakage Auditor 보고서도 필요하다(단계 2부터).

## 6. 수락 (PM)

- 완료 기준을 한 항목씩 O/X로 대조한다. 리뷰어 보고서의 발견 사항이 해결됐는지 본다.
- 전부 O여야 수락. 하나라도 X면 반려하고 티켓에 이유를 적는다.
- 반려는 같은 티켓에 최대 2회. 3번째면 티켓을 쪼개거나 사람에게 올린다.
- 수락하면: 사람에게 "3문장 요약 + 확인 명령 + 머지 요청"을 보낸다.

## 7. 머지 (사람)

- 사람이 GitHub에서 PR을 읽고 머지한다. squash merge를 기본으로 한다(히스토리가 티켓 단위로 깔끔해진다).
- 사람이 수락 메시지를 읽고 명시적으로 지시하면("머지해줘", "머지ㄱㄱ") PM이 `gh pr merge --squash`로 대신 실행할 수 있다(D-005 보완). 리뷰어와 작업자는 머지하지 않는다. 사람이 이해하고 결정한다는 원칙은 그대로이고, 버튼을 누르는 손만 위임한 것이다.
- `main`에는 브랜치 보호(PR 필수, 필수 체크 `lint-and-test`·`secret-scan`, force push 금지, 관리자 우회 금지)를 건다(D-047, 사람이 GitHub에서 켠다). 켜진 뒤에는 PM 소유 문서(`docs/backlog.md`, `docs/decisions.md`, `docs/team/`, `docs/learning/`, `CLAUDE.md`) 변경도 `docs/P-NNN-...` 또는 `docs/pm-YYYYMMDD-<주제>` 브랜치 + PR로 간다. 리뷰어는 붙이지 않고, 작은 기록 변경은 모아서 PR 하나로 한다.
- 머지 후 PM이 backlog를 `DONE`으로 바꾸고, 결정이 있었으면 `decisions.md`에 적는다.

## 8. 단계 종료

- 단계의 모든 티켓이 DONE이고 `PRESAGIO_OVERVIEW.md`의 완료 기준이 충족되면 PM이:
  1. 완료 기준 증거(명령과 결과)를 `docs/learning/stage-N-*.md`에 정리한다.
  2. 다음 단계의 세부 계획과 티켓 초안을 만들어 사람에게 확인받는다.
  3. 다음 단계에서 활성화할 역할을 사람에게 알린다.

## 에스컬레이션 규칙 (누가 언제 사람에게 묻나)

| 상황 | 누가 | 어떻게 |
| --- | --- | --- |
| DB 스키마 추가/변경 | 작업자가 설계안 작성 → PM이 사람에게 | 테이블/컬럼/타입/이유를 표로 |
| 기술 스택 변경, 새 라이브러리 추가(핵심) | PM | 이유와 대안 포함 |
| 외부 서비스 선택(배당 API 등) | PM | 무료 한도, 약관, 과거 데이터 여부를 표로 |
| 돈이 드는 것 | PM | 월 비용 추정 포함 |
| 완료 기준이 비현실적 | 작업자 → PM → 사람 | 기준을 바꾸는 건 사람만 |
| 같은 실패 2회 | 작업자 → PM | 상황과 시도한 것 요약 |

## 세션 시작 제약 (Claude 데스크탑 앱, 2026-10-23 기준)

- 작업자·리뷰어 세션은 **사람 턴**에서만 PM이 바로 띄울 수 있다. 다른 세션의 알림이나 자동 이벤트로 시작된 턴에서는 앱이 거부한다. 그때 PM은 (a) 카드(own_initiative)를 올려 사람이 클릭해 시작하게 하거나, (b) 같은 역할 파일의 서브에이전트를 전용 worktree(`git worktree add`)에서 돌린다(D-041 야간 운영에서 실제로 썼다). 서브에이전트에게도 "공유 체크아웃 수정 금지, 루트 `.env` 열지 말 것, 셸로 파일 쓰기"를 명시한다.
- 앱이 만드는 worktree는 항상 main 기준이다(D-014). 작업자 브리핑에는 브랜치 이름 확인·변경 명령(`git branch --show-current`, `git branch -m ...`)을 넣는다.
- 모델·노력은 `docs/team/organization.md`의 표(D-011, D-043)대로 띄우고, 어떤 모델·노력으로 띄웠는지 PM이 backlog 티켓 메모에 적는다.

## 클라우드 세션 (D-052, 2026-10-23~2026-11-05 크레딧 기간)

- 어떤 티켓을 클라우드로: mock 테스트로 완결되는 구현(Elo, as_of 피처, 단계 3 모델), 리뷰어, 문서. 로컬에 두는 것: 실제 개발 DB가 필요한 수집·실데이터 검증·실험 티켓, launchd, 마이그레이션 적용, 외부 API 실제 호출.
- 시작: 사람이 "클라우드로"라고 하면 PM이 `start_session target: "cloud"`로, 또는 사람이 claude.ai/code에서 직접. 클라우드는 GitHub의 **푸시된 브랜치**를 클론하므로 브리핑이 참조하는 티켓·문서는 main에 머지돼 있어야 한다. 환경 셋업과 생성 절차는 P-040(`scripts/cloud-setup.sh`, README "클라우드 세션" 절).
- **완료 확인:** 클라우드 세션은 앱에 끝났다는 알림을 보내지 않는다. PM이 `gh pr list`로 PR이 열렸는지 확인한다(필요하면 30분 간격 일회성 예약). 리뷰·수락·머지는 로컬 세션과 같다(D-014 detached checkout, D-006).
- **비밀값:** `ODDS_API_KEY`는 클라우드 환경변수에 두지 않는다(환경 사용자 모두 읽음, 공개 레포). 필요하면 network secret으로만.
- **클라우드에서 `gh pr`은 막힌다**(GitHub GraphQL 403, D-052 보완 2). 작업자는 `gh api -X POST repos/oghdy/Presagio/pulls -f title=... -f head=... -f base=main -F body=@body.md`(REST) 또는 플랫폼 내장 PR 도구로 PR을 열고, 브리핑 전문은 프롬프트에 넣는다(로컬 파일·공유 노트·개발 DB 접근 불가). 환경은 `presagio-dev`(README "클라우드 세션" 절). 세션 시작 줄에 `[presagio cloud] DB ready ... PRESAGIO_REQUIRE_DB=1`이 보여야 한다.

- **환경·브랜치(D-057):** 환경 `presagio-dev`의 칸 값·설정 스크립트 래퍼·클론 경로·`/remote-env`·`gh api` REST 예시는 README "운영: Claude Code 클라우드 세션" 절이 원본이다. 클라우드 세션의 브랜치는 플랫폼이 고정한 `claude/<이름>`일 수 있다 → PR 제목 `[P-NNN]`으로 식별하고, PM은 `gh pr list`(브랜치 무관)로 찾는다. 머지 뒤 자동 브랜치는 PM이 지운다.
- **작업자 세션에 후속 요청 보내기(D-052 보완 3):** 리뷰 결과·수정 요청·재리뷰 요청은 `claude -p "<메시지 전문>" --cloud <세션 id>`로 보낸다(세션 id는 PR 본문 꼬리의 `claude.ai/code/session_…`). 사전 조건: 이 맥의 `claude` CLI가 claude.ai 계정으로 로그인(`claude auth login`). 같은 내용을 PR 댓글로도 남기되 댓글은 기록용이다(세션은 댓글로 깨어나지 않는다). 메시지에는 "같은 브랜치에 커밋, 새 PR·새 브랜치·main 머지 커밋 금지, 끝나면 커밋 해시를 마지막 메시지에"를 넣는다. 브리핑에는 `/effort high` 지시도 적는다(D-043; `/model`만으로는 노력이 medium).
## PM 세션 시작 프롬프트 (사람이 복사해서 쓴다)

```
너는 Presagio의 PM이다. `.claude/agents/pm.md`를 읽고 그 역할로 일해라.
CLAUDE.md, PRESAGIO_OVERVIEW.md, docs/backlog.md, docs/decisions.md, docs/team/workflow.md를 읽어라.
지금 단계는 [단계 번호]다. backlog에서 다음에 할 티켓을 고르고, 브리핑을 작성해서 나에게 보여줘.
내가 확인하면 작업자 세션을 띄워라. 제품 코드는 네가 직접 짜지 마라.
```

## 작업자 세션 시작 프롬프트 (PM이 브리핑 앞에 붙인다)

```
너는 Presagio의 [역할 이름]이다. `.claude/agents/[역할 파일].md`를 읽고 그 역할로 일해라.
CLAUDE.md를 먼저 읽어라. 아래 브리핑의 티켓만 수행한다. 범위 밖은 건드리지 않는다.
브랜치 [브랜치 이름]에서 작업하고, 끝나면 PR을 열고 PR 링크와 3문장 요약을 마지막 메시지로 남겨라.

--- 브리핑 ---
(handoff-templates.md의 브리핑 양식)
```

## 리뷰어 세션 시작 프롬프트 (PM이 쓴다)

```
너는 Presagio의 Reviewer다. `.claude/agents/reviewer.md`를 읽고 그 역할로 일해라.
리뷰 대상: 브랜치 [이름] (PR [번호]). 티켓: P-NNN (docs/backlog.md에서 직접 읽어라).
네 worktree는 main 기준으로 만들어진다. 먼저 `git fetch origin [브랜치] && git checkout --detach origin/[브랜치]`로 리뷰 대상을 받고,
`git diff main...HEAD --stat`이 비어 있지 않은지 확인한 뒤 시작해라(작업자 브랜치는 작업자 worktree에 체크아웃되어 있어 일반 checkout이 거부된다).
테스트를 직접 실행하고, handoff-templates.md의 리뷰 보고서 양식으로 보고해라. 코드는 고치지 마라.
```

- detached checkout 단계는 D-014. 이 단계가 없으면 리뷰어가 main을 보고 "리뷰할 것 없음"이라는 빈 리뷰를 낼 수 있다.
- 리뷰어에게 작성자의 결론이나 PM 의견을 주지 않는다(D-006). 수정 후 재리뷰는 같은 리뷰어 세션에 메시지로 보낸다(새 세션을 띄우지 않는다).
- 누수 관련 변경(features, models, evaluation, as_of)은 Leakage Auditor 세션에도 같은 방식으로 준다. 보고서 제목에 `[누수 감사]`.
