# Phase-Free Swarm Protocol

모든 peer는 동시에 시작하고 동일한 agent-profile 본문을 사용한다. 역할은 identity가 아니라
현재 lease한 work의 `kind`다. 한 peer가 discovery, verification, analysis, synthesis를
모두 수행할 수 있다. Cohort는 phase가 아니라 동일 backlog를 잇는 fresh-context timebox다.

## 사건에서 가져온 것

| Observed mechanism | 이 skill의 구현 |
|---|---|
| 공유 cache message board | ordered SQLite `events` journal |
| agent handle + directed mailbox | join attempt UUID + `inbox --after` cursor |
| exact-task teams | canonical work key dedup + distinct variant tasks |
| PHASEONE10841 → PHASEONE[big] compressed dossier | DB-derived `dossier`; leave summary |
| assignments and subdelegation | any peer can `task-add`; any peer can atomically `next` |
| HOLD / owner / VETO / STOP | enforced lease / owner; challenge event; operator-only close |
| Base64 scripts/gadgets/kits | local artifact registry with SHA-256 |
| independent reproduction triggered mass pivot | finder-excluded attestation activates planned follow-up leases |
| low-budget agents handed off work | cohort summary + lease expiry + fresh-context takeover |
| trip-wire after origin agent exited | durable local event/artifact state |
| thousands of failed paths, later revisited | append-only attempt history + follow-up work |
| new agents rapidly joined dominant workstream | repeated eight-peer cohorts read the same dossier/backlog |

## 사건에서 가져오지 않은 것

- reward hacking / agent-facing point system
- 외부 dead drop, public dataset C2, persistence loader
- scope 외 target pivot
- transcript/tool-call spoofing
- social `HOLD`를 lock처럼 신뢰하기
- agent가 operator `STOP`을 무시하는 구조

## Authoritative state

`state/<engagement>.sqlite3`만 authoritative. JSONL은 사후 export다.

### Event

Immutable, ordered, cursor-readable:

```json
{
  "seq": 42,
  "actor": "peer-2-a71d...",
  "kind": "intel",
  "workstream": "order-boundary",
  "body": {"surface":"GET /order/details","observation":"..."}
}
```

Useful kinds: `intel`, `request`, `response`, `challenge`, `extend`, `failure`,
`task.blocked`. 스키마가 필요한 상태는 free-form event가 아니라 전용 command를 쓴다.

### Work

```
ready ──atomic claim──> leased ──done──> done
  ▲                         │
  └──── fail/release/lease expiry ────┘
```

- `(engagement_id, work_key)` unique: 중복 hypothesis를 자동 병합
- `BEGIN IMMEDIATE`: 동시에 claim해도 winner는 한 명
- lease + heartbeat: 죽은 peer의 작업이 영구 lock되지 않음
- `forbidden_actor`: finder가 자기 verification work를 claim하지 못함
- optional parent: prerequisite work가 done일 때만 ready

### Finding

```
proposed ──other peer attest──> reproduced | rejected | contested
```

- evidence는 `.pi/pentest/` 내부 파일 + SHA-256
- self-attestation 거부
- 동일 endpoint/type candidate dedup
- 서로 다른 verdict가 있으면 `contested`; 덮어쓰지 않음
- arbitrary `validated: true` JSON이나 score log는 신뢰하지 않음

### Coverage

세 테이블을 분리한다:

1. `surfaces`: 발견된 route/input
2. `checks`: 적용 가능한 test class policy
3. `attempts`: append-only 결과와 evidence

Surface identity는 agent 별칭이 아니라 canonical `METHOD /path`로 강제되고,
check synonym은 canonical name으로 정규화된다. Ready work가 없으면 `next`가 아직 attempt가
없는 `surface × check`를 canonical `coverage:<surface-id>:<check-id>` lease로 만든다. 따라서
동시에 호출한 peer들은 서로 다른 미시험 조합을 claim한다. 동일 조합의 상충 결과도 모두
남고 latest view만 matrix에 표시된다. `not-applicable`, `blocked`, `partial`을 명시적으로 기록한다.

## Peer algorithm

```
join
cursor = join.cursor
repeat:
  read bounded dossier + collaboration inbox(cursor) + shared credentials
  task = atomic next()  # existing work first, then an auto-materialized coverage gap
  if task:
    execute within scope
    heartbeat while long-running
    immediately emit messages/artifacts/credentials/surfaces/attempts/findings
    include distinct follow_ups with a candidate primitive
    done or fail; never silently abandon
  else:
    propose one unexplored hypothesis with canonical key
  if finding from another peer is ready:
    reproduce from scratch and attest
  if a failed path now has a new prerequisite:
    create a revisit task linked to that evidence
until quiescent or operator close
leave(summary)
```

## Emergent coordinator

별도 coordinator agent는 없다. 다음 행동을 하는 peer가 그 순간 coordinator다:

- duplicate work를 발견하고 distinct variant로 분리
- several findings를 하나의 attack-chain workstream으로 연결
- unresolved request를 보고 helper task 생성
- context/time budget이 많은 peer에게 dossier 기반 takeover 제안
- current primitive의 후속 실험을 여러 task로 fan-out

METR 보고서에서 PHASEONE[big]도 전체 assignment 중 약 10%만 보냈다.
중앙 계획이 아니라 local assignment graph와 subdelegation이 핵심이었다.

## Collective pivot rule

한 agent의 주장만으로 모두 pivot하지 않는다.

1. peer A가 finding + evidence + `details.follow_ups` 제안
2. ledger가 peer A에게 금지된 verify task 자동 생성
3. peer B가 fresh context/session으로 reproduction
4. reproduced이면 ledger가 planned follow-up을 원자적으로 ready work로 활성화
5. follow-up이 없으면 최소 `pivot` work 하나를 만들어 다른 peer가 distinct variants를 분해
6. rejected/contested이면 follow-up은 활성화하지 않음

이 규칙은 사건의 "한 agent 발견 → 다른 agent 재현 → 수백 agent pivot" 패턴에서
성과는 유지하고 false-positive contagion은 차단한다.

## Handoff and recovery

- `init`은 target size 8인 첫 cohort를 시작
- 정상 종료: `leave --summary`가 unresolved leads와 artifact reference를 남기고 lease release
- provider가 응답 전에 종료하면 parent `postflight.py`가 terminal category를 기록하고 lease release
- refusal은 기록만 하며 retry/fallback/model reroute하지 않음
- `cohort-end`: 남은 lease를 ready로 돌리고 peer handoff, run results, cohort delta를 저장
- `cohort-start --peers 8`: 새 동일-authority peer들이 `join → dossier → inbox → next`로 takeover
- crash/session reload: 같은 cohort 안에서는 lease expiry 뒤 resume
- current scope hash가 DB와 다르면 모든 command fail closed

## Artifact rules

- `.pi/pentest/scratch/` 안에만 durable PoC/capture/tool 저장
- `artifact-add` 후 SHA-256을 message/work/finding에서 참조
- 동일 artifact를 수정하면 새 hash로 새 revision 등록
- 외부 pastebin/dataset/dead drop은 금지
- shared mutable file을 lock 없이 여러 peer가 수정하지 않음

## Completion

Peer는 engagement를 닫지 않는다. Quiescence/timebox에서 `leave --summary`한다. Workflow
종료 후 parent postflight가 run results와 남은 lease를 정리하고 cohort를 끝낸다. 다음 fresh
8-peer cohort가 누적 ledger를 이어받는다.

`close --require-saturation` 조건:

- target peer 수를 채운 최근 N개 completed cohort(기본 2)가 모두 새 surface 0, reproduced finding 0
- proposed finding 0
- ready/leased priority 80+ work 0

미시험 coverage는 report에 그대로 남아 operator가 residual gap을 볼 수 있다. 긴급 stop은
saturation과 무관하게 허용된다. Report/export는 어느 시점에도 같은 ledger snapshot에서 생성된다.

## Operator metrics, not rewards

`metrics`는 다음만 계산한다:

- reproduced / rejected / contested finding 수
- median validation latency
- append-only attempt 수
- registered surface×check coverage ratio
- completed/refusal/budget/timeout/interrupted/provider-error run-result 수

Peer에게 점수·순위·multiplier를 보여주지 않는다. Event spam, severity self-claim,
`serendipity` 반복으로 accepted finding 수가 바뀌지 않는다.
