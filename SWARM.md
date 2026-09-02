# Phase-Free Swarm Protocol

모든 assessment peer는 동일 authority와 invariants를 사용한다. Role은 identity가 아니라 현재
claim한 work의 `kind`다. Cohort는 phase가 아니라 동일 backlog를 잇는 fresh-context timebox다.

## Authoritative state

Selected workspace의 SQLite만 live authority다. JSONL/report/replay는 snapshot export다.

- `work_claims`: actor당/work당 active claim 하나, generation fencing
- `experiments`: pre-I/O spec와 post-I/O durable result
- `artifacts`/`work_artifacts`: SHA-256 request/response provenance
- `attempts`: append-only partial/error/semantic result history
- `events`: append-only typed causal and lifecycle journal
- `findings`/`attestations`: finder와 independent verifier 분리
- `scheduler_decisions`: live `fifo-v1` candidate snapshot
- `provider_circuits`: provider/account-level 429 backoff

Scope/DB/scratch/findings/board/memory/cache는 engagement-local이다. Selection pointer는 live peer/lease가
있을 때 바꾸지 않는다.

### SQLite concurrency boundary

WAL은 reader와 writer를 병행하지만 writer끼리는 병행하지 않는다. 따라서 mutation은 짧은
`BEGIN IMMEDIATE` transaction으로 직렬화하고, `status`/`inbox`/claim 뒤의 brief read는 deferred
snapshot으로 처리한다. Current-schema `connect()`는 DB를 변경하지 않는다. DDL, index 생성,
legacy backfill은 `PRAGMA user_version`이 뒤처진 경우에만 실행하며 routine command는 자동 upgrade
대신 중단한다. Peer를 모두 멈춘 뒤 `python3 .pi/pentest/swarm.py init`을 한 번 실행해 upgrade한다.
Active traffic 중 manual WAL checkpoint는 실행하지 않는다. Artifact file hashing은 mutation
transaction 전에 끝내 local file I/O가 writer slot을 점유하지 못하게 한다.

## Work and timing

```text
ready ──claim──> leased ──checkpoint/done──> done
  ▲                  │
  ├── release/fail ──┤
  ├── no progress ──> stalled
  └── lease deadline -> expired
```

Partial unique indexes가 actor와 work의 active claim을 하나로 제한한다. Claim generation이 바뀌면
old owner의 artifact, attempt, typed event, finding mutation은 거부된다.

시간은 분리한다.

```text
operation_timeout < no_progress_timeout < claim_lease < peer_process_timeout
```

`inbox`, `brief`, `next(active-lease)`, heartbeat 같은 bookkeeping은 claim을 갱신하지 않는다.
Host HTTP completion, current-claim artifact/attempt, typed assertion, finding/attestation만
`last_progress_at`과 lease를 갱신한다. Expired claim을 activity로 resurrect하지 않는다.

## Reliable atomic HTTP unit

Agent가 직접 target request와 ledger bookkeeping을 분리하지 않는다.

```text
peer-start
  └─ join + trusted proxy decision + deterministic bootstrap/claim + task-local brief
exec-http
  ├─ require live claim and scoped host/port
  ├─ commit ExperimentSpec
  ├─ recheck live claim immediately before send
  ├─ finite stdlib HTTP operation, no redirects/retries
  └─ request/response artifacts + surface/check + partial|error attempt + execution event
checkpoint
  └─ typed interpretation + grounded follow-up + experiment checkpoint + work completion
```

`exec-http` injects `X-Redteam-Agent`, `X-Redteam-Claim`, `X-Redteam-Experiment`, and
`X-Redteam-Engagement`; caller-supplied variants are rejected. Direct mode uses an empty
`ProxyHandler` rather than ambient shell proxy variables. Proxy mode uses only the latest trusted ledger decision.

HTTP 200/302/403 등은 observation일 뿐 finding verdict가 아니다. Response body capture는 bounded다.
Redirect response는 저장하지만 target 밖으로 follow하지 않는다. 동일 `(engagement, work,
request_fingerprint)`은 unique하다. Durable result는 `resume-required`; terminated old claim의 `prepared`
intent는 safe rebind; `sending` uncertainty는 `indeterminate`이며 재전송하지 않는다.

Remote HTTP와 SQLite는 하나의 ACID transaction이 될 수 없다. Spec-before-I/O와 request-time fence,
host-owned post-I/O commit이 실용적 경계다. Bytes 전송 직후 host process 강제 종료라는 좁은
exactly-once gap은 남으며, 시스템은 이를 성공으로 추측하거나 자동 재전송하지 않는다.

## Deterministic bootstrap and grounded cognition

Ready work가 없을 때 coverage gaps를 materialize한다. Engagement에 work, surface, attempt가 모두 없으면
그보다 먼저 다음 두 work를 idempotently 생성한다.

```text
baseline:<host>:reachability   (HEAD /)
baseline:<host>:root-fetch     (GET /)
```

따라서 fresh one-shot peer가 empty dossier에서 speculation부터 만들지 않는다. `hypothesize`,
`task-add --kind hypothesis`, hypothesis next-action은 artifact/attempt/observation 또는 evidence-backed
causal ancestor가 없으면 rollback된다. Existing manually queued work가 있으면 bootstrap은 생성하지 않는다.

## Partial takeover

Expired/stalled/interrupted/released claim에 durable experiment나 artifact가 있으면 system이 semantic
verdict를 만들지 않고 `work.partial`을 남긴다. 다음 owner의 brief는 다음을 포함한다.

```json
{
  "partial_experiments": [{
    "id": 17,
    "status": "completed",
    "attempt_ref": "attempt:12",
    "artifact_refs": ["artifact:31", "artifact:32"],
    "remaining": "semantic checkpoint; do not repeat this HTTP request"
  }]
}
```

New generation은 같은 work의 old experiment를 checkpoint할 수 있다. Typed event와 work completion은
new claim에 귀속되고, original request/attempt provenance는 old claim에 그대로 남는다. Resume-first는
adaptive scheduling이 아니라 duplicate I/O 방지 correctness rule이다.

## Typed causal protocol

`observe`, `hypothesize`, `request`, `respond`, `challenge`, `decide`, `handoff`, `synthesize`는
trace, causation, correlation, confidence, subject/evidence refs, falsifiers, next actions를 보존한다.
`checkpoint`는 기존 typed schema를 사용하며 새 message type을 만들지 않는다. Follow-up work와 event는
같은 transaction에서 생성된다. `emit`은 legacy와 `task.blocked`에만 사용한다.

Task-local brief는 current workstream의 causal ancestors, negative attempts, partial experiments,
artifacts, findings/attestations, referenced credentials만 준다. Global dossier는 wait/recovery fallback이다.

## Findings and collective pivot

```text
finder proposes finding + evidence
  -> canonical verify work, finder excluded
  -> another peer reproduces/rejects with fresh evidence
  -> only reproduced finding activates planned follow-ups
```

Self-attestation과 arbitrary `validated:true` payload는 거부한다. Contradictory attestations는 overwrite하지
않고 contested로 남긴다. Agent-visible reward/points/rank는 없다.

## Elastic cohort

Herdr supervisor는 첫 assessment peer 하나로 시작한다. 첫 durable target action과 provider health,
ready backlog를 확인한 뒤 두 번째를 시작한다. Verify work 또는 distinct grounded workstream이 있을 때만
세 번째를 시작한다. 동시 assessment peer 상한과 기본 cohort target은 3이다.

Cohort 1은 Sonnet, cohort 2+는 Opus지만 work 유형으로 model을 route하지 않는다. 각 generation은
새 Pi process/session/name이며 resume하지 않는다. Provider 429는 15분 global circuit을 열고 새
same-provider launch를 막는다. Refusal/429/budget/policy failure는 retry, paraphrase, reroute,
fallback하지 않는다. Intercom은 durable reference와 lifecycle notification만 전달하고 task/evidence
state는 SQLite에 둔다.

## Replay and telemetry

Strict replay validates:

- causal ordering and evidence registry
- work fingerprints, current claim uniqueness, claim outcomes including `stalled`
- experiment→claim/work/attempt/artifact/checkpoint provenance
- scheduler candidate hashes and decision↔claim linkage
- provider circuit timestamps

Known stage timestamps만 기록한다: run launch/end, first output/tool(제공된 경우), join, claim, brief,
first target, durable attempt, checkpoint, completion. 누락값은 `NULL`이다.

Operator metrics:

- durable/completed assertion count
- expired/stalled/abandoned claim rate
- claim→brief/target, target→attempt/checkpoint, checkpoint→done latency
- productive tool-call ratio와 calls/completed assertion(모든 run telemetry가 있을 때만)
- finding/coverage/provider outcome/communication aggregate

Alternative scheduler policies는 historical candidate ranking만 비교하며 counterfactual outcome을 주장하지
않는다. Evidence Graph와 adaptive scheduler는 reset-controlled Solo/isolated/shared benchmark 뒤에만 고려한다.

## Completion and recovery

Normal peer는 `leave --summary` 뒤 Pi process를 종료하지 않고 idle로 남는다. 실제 process death는
Herdr watcher가 exact pane/session/generation으로 확인한다. Transcript가 refusal/429/budget이거나
missing/unreadable/ambiguous이면 work를 terminal/quarantine하고 replacement하지 않는다. Clean stop/tool
boundary가 남은 확인된 exit만 host가 `run-result interrupted`와 `run-result-get`
receipt를 먼저 고정한 뒤 fresh generation으로 보충한다. Pane exit와 Herdr/socket loss는 fail closed다.
`cohort-end`는 active claims를 release하고 partial provenance, handoff, run results, delta를 고정한다.
다음 `cohort-start --peers 3`이 같은 backlog를 이어받는다.

Peer는 engagement를 close하지 않는다. `close --require-saturation`은 target peer 수를 채운 dry cohort
streak, proposed findings, priority 80+ backlog를 검사한다. Emergency operator close는 항상 우선한다.
