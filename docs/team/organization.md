# Presagio 조직도: 누가 무엇을 하는가

> 이 문서는 "Presagio 회사"가 어떻게 생겼는지 5분 안에 이해하기 위한 문서다.
> 역할별 상세 규칙은 `.claude/agents/<역할>.md`에 있다.

## 한 문장으로

사람(Founder)이 결정하고, PM이 일을 쪼개서 나눠주고, 사일로(팀)별 작업자가 자기 영역만 만들고, 독립 리뷰어가 검사하고, 다시 PM이 완료 기준에 맞는지 확인한 뒤 사람이 머지한다.

## 조직도

```
Founder (사람)  ── 최종 결정: 스키마, 스택, 외부 서비스, 비용, 머지
   │
   └── PM / Tech Lead (세션 1개, 장기)  ── 티켓, 브리핑, 수락/반려, 결정 기록, 학습 노트
         │
         ├── 사일로 1. Data Platform ─── Data Engineer          [단계 0~1 활성]
         ├── 사일로 2. ML Research ───── ML Engineer            [🔒 단계 2~4]
         │                          └── Leakage Auditor        [🔒 단계 2~4, 독립 검증]
         ├── 사일로 3. Serving ───────── Backend Engineer       [🔒 단계 5]
         ├── 사일로 4. Platform/SRE ──── Platform Engineer      [🔒 단계 6, 단계 0 CI는 예외]
         │
         ├── (횡단) Reviewer ─────────── 모든 PR 독립 리뷰      [항상 활성]
         └── (횡단) Scribe ───────────── 문서, 학습 노트        [🔒 단계 4부터 분리, 그 전엔 PM 겸임]
```

🔒 표시는 "정의는 되어 있지만 아직 띄우지 않는다"는 뜻이다. 단계마다 필요한 역할만 켜서 토큰과 혼란을 줄인다.

## 왜 사일로로 나누는가

에이전트는 세션이 끝나면 기억을 잃는다. 그래서 "사람이 서로 대화해서 맞추는" 방식은 안 통한다. 대신 **사일로 사이의 경계를 DB 스키마로 고정**한다.

| 경계 | 계약(인터페이스) | 뜻 |
| --- | --- | --- |
| Data ↔ ML | `games`, `team_game_stats`, `odds_snapshots` 테이블 | ML팀은 이 테이블을 읽기만 한다. 컬럼이 더 필요하면 PM에게 티켓을 요청한다. |
| ML ↔ Serving | `predictions`, `evaluations`, `model_versions` 테이블 | API는 이 테이블을 읽기만 한다. |
| 모두 ↔ Platform | `cli.py`의 명령들 | 스케줄러는 CLI 명령을 순서대로 부를 뿐 내부를 모른다. |

계약이 테이블이기 때문에, 한 팀이 자기 안쪽을 어떻게 바꾸든 다른 팀은 영향을 받지 않는다. 계약(스키마)을 바꾸는 것만 사람 승인이 필요하다.

## 역할 요약표

| 역할 | 한 줄 설명 | 코드를 쓰나 | 소유 영역 | 활성 |
| --- | --- | --- | --- | --- |
| Founder | 결정하고 머지하고 면접에서 설명하는 사람 | 아니오 (읽는다) | 전체 | 항상 |
| PM | 쪼개고, 브리핑하고, 수락하고, 기록한다 | 아니오 | `CLAUDE.md`, backlog, decisions, docs/team | 항상 |
| Data Engineer | 수집, 스키마, 백필/증분, 품질 체크 | 예 | ingestion, db, quality, alembic, compose | 0~1 |
| ML Engineer | 피처, Elo, 모델, 백테스트, 실험 기록 | 예 | features, models, evaluation, experiments | 2~4 |
| Leakage Auditor | 시점 누수만 전문으로 검증 | 아니오 | 없음 (읽기 전용) | 2~4 |
| Backend Engineer | API, 대시보드 | 예 | api, dashboard | 5 |
| Platform Engineer | 스케줄, 배포, 알림, 신선도 | 예 | workflows, Dockerfile, scripts | 6 (+0 CI) |
| Reviewer | PR 독립 리뷰, 테스트 직접 실행 | 아니오 | 없음 (읽기 전용) | 항상 |
| Scribe | 쉬운 문서, 학습 노트 | 아니오 | README, docs/learning | 4부터 |

## 세션은 어떻게 띄우나 (Claude 데스크탑 앱 기준)

- **PM 세션:** 사람이 직접 하나 띄우고 첫 메시지로 `docs/team/workflow.md`의 "PM 세션 시작 프롬프트"를 붙여넣는다. 이 세션은 단계가 끝날 때까지 유지한다.
- **작업자 세션:** PM이 브리핑을 써서 새 세션(새 worktree)을 띄운다. 티켓 하나가 끝나면 그 세션은 끝난다.
- **리뷰어 세션:** PR마다 새로 띄운다. 이전 리뷰 기억이 없어야 공정하다.
- 역할 파일은 `.claude/agents/`에 있으므로, PM 세션 안에서 서브에이전트로도 호출할 수 있다(짧은 리뷰나 조사). 하지만 코드를 쓰는 작업은 별도 세션 + worktree가 원칙이다. 같은 파일을 두 세션이 동시에 고치는 사고를 막기 위해서다.

## 사람이 하는 일 (중요)

이 구조의 가장 큰 위험은 "에이전트가 다 만들어서 사람이 설명을 못 하게 되는 것"이다. 그래서 사람은 아래를 꼭 한다.

1. PR을 머지하기 전에 PR 본문의 "이 PR을 3문장으로"와 diff를 읽는다.
2. 머지 전에 PM 세션에 **질문을 최소 1개** 한다. "왜 이렇게 했어?"면 충분하다.
3. 단계가 끝나면 `docs/learning/stage-N-*.md`를 읽고, 거기 붙은 면접 예상 질문에 스스로 답해 본다.
