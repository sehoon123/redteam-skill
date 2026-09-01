---
name: redteam
description: |
  Phase-free autonomous multi-agent penetration testing for authorized targets.
  Equal-authority peers share a transactional claim/evidence ledger while host-owned HTTP execution survives model failure.
  Trigger: /redteam, "pentest this", "red team", "run a pentest", "자율 보안 평가".
---

# Redteam

인가된 target을 고정 role이나 phase 없이 평가한다. SQLite가 claim, evidence, attempt,
finding, causal event, provider circuit, replay의 유일한 authority다. v3.7의 우선순위는 agent 수가
아니라 **한 번 수행된 HTTP assertion을 model 종료 전에 durable result로 만드는 것**이다.

- target HTTP: agent-owned curl이 아니라 host-owned `exec-http`
- semantic interpretation + follow-up + completion: 한 transaction의 `checkpoint`
- empty engagement: deterministic reachability/root-fetch work
- interruption: `work.partial` + next-generation resume-first brief
- stale owner: request 직전 claim fencing
- launch: provider capacity에 맞춘 1→2→최대 3 elastic ramp
- Evidence Graph/adaptive scheduler: benchmark가 필요성을 입증할 때까지 보류

연구 근거는 `RESEARCH.md`와 `PROMPTING-RESEARCH.md`, 협업/causal 계약은 `SWARM.md`와
`CAUSAL-PROTOCOL.md`, 비교 실험은 `SWARMBENCH.md`를 따른다.

## Operator contract

실행 전에 다음이 필요하다.

- 선택된 workspace `scope.yaml`의 명시적 authorization과 host/port/scheme allowlist
- infrastructure network allowlist, rate limit, kill switch
- 선택적 `${PENTEST_PROXY:-http://127.0.0.1:8080}`

Scope hash는 engagement 생성 시 고정된다. App-level 검사만으로 egress enforcement를 대신할 수 없다.

## 설치

```bash
mkdir -p .pi/skills/redteam/workflows .pi/agents .pi/pentest
cp SKILL.md SWARM.md RESEARCH.md PROMPTING-RESEARCH.md PROXY.md WORKSPACES.md \
  CAUSAL-PROTOCOL.md SWARMBENCH.md VALIDATION.md .pi/skills/redteam/
cp workflows/cohort.js .pi/skills/redteam/workflows/
cp -R benchmarks .pi/skills/redteam/
cp agents/pentest-peer.md agents/pentest-peer-sonnet.md agents/pentest-peer-luna.md \
  agents/pentest-cohort-selector.md agents/pentest-run-recorder.md agents/luna-probe.md .pi/agents/
cp -R pentest/. .pi/pentest/
# settings.json의 peer model override를 .pi/settings.json에 병합
```

Runtime은 공유되지만 scope, SQLite, scratch, findings, board, memory, cache는 engagement-local이다.
Selection과 legacy fallback은 `WORKSPACES.md`를 따른다.

## Automatic `/redteam <target>` bootstrap

명시적인 `/redteam <URL>` 요청은 그 target에 대한 operator authorization assertion으로 취급한다.
인가가 불명확하다고 사용자가 말했거나 단순 URL 질문이면 traffic 전에 확인한다.

```bash
python3 .pi/pentest/workspace.py ensure \
  --target "$TARGET" \
  --authorization "Operator explicitly requested and asserted authorization for this target in the current /redteam invocation."
python3 .pi/pentest/swarm.py init
python3 .pi/pentest/kb.py index
```

`ensure`는 host/port 기반 stable ID를 만들고 동일 target을 idempotently 재사용한다. Live peer나 lease가
있으면 selection pointer 변경은 fail closed한다. 여러 site는 site별 process와
`PENTEST_ENGAGEMENT=<id>`를 사용한다. 사용자는 단순 target 요청에서 `create/use`를 직접 다루지 않는다.

`init`은 fresh empty ledger에 아직 work를 쓰지 않는다. 첫 `peer-start/next`가 work/surface/attempt가
모두 0일 때 다음 두 work를 한 번만 materialize한다.

```text
baseline:<host>:reachability
baseline:<host>:root-fetch
```

기존 work가 있으면 bootstrap이 끼어들지 않는다. Ungrounded hypothesis는 거부된다.

## Proxy-auto와 network fencing

기본 `PENTEST_PROXY_POLICY=auto`다.

- reachable CONNECT → `proxy.checked`, runner가 explicit `ProxyHandler` 사용
- connection unavailable → `proxy.unavailable`, direct/offline 허용
- reachable rejection → `proxy.rejected`, traffic 차단
- `required` → unavailable도 차단
- `off` → explicit direct mode

`peer-start`가 join, proxy decision, claim, task-local brief를 한 번에 처리한다. `exec-http`는 URL의
host/port를 scope와 비교하고 redirect를 follow하지 않으며, request 직전에 live claim을 다시 확인한다.
모든 request에 host-owned `X-Redteam-Agent`, `X-Redteam-Claim`, `X-Redteam-Experiment`,
`X-Redteam-Engagement`를 넣는다. Agent가 같은 header를 덮어쓸 수 없다. 자세한 의미는 `PROXY.md`.

## Canonical elastic launch

Canonical source는 `workflows/cohort.js` 하나뿐이다.

```js
subagent({
  workflowScriptPath: ".pi/skills/redteam/workflows/cohort.js",
  cwd: ".",
  async: true,
  timeoutMs: 3600000,
  globalConcurrencyLimit: 3,
  maxSubagentSpawnsPerRun: 9
})
```

Workflow contract:

1. Cohort 1은 Sonnet, cohort 2+는 Opus profile을 선택한다. Work 종류로 model을 route하지 않는다.
2. 첫 peer 하나만 launch하고 durable useful action과 provider circuit health를 관찰한다.
3. Durable experiment와 ready backlog가 있을 때 두 번째 peer를 launch한다.
4. Verify work 또는 서로 다른 grounded workstream이 있을 때만 세 번째 peer를 launch한다.
5. Target peer 기본값과 최대 동시 assessment peer는 3이다. 각 peer는 8개 claim 또는 7분에서 새 claim을 멈추고 leave하여 10분 child timeout 전에 durable handoff를 남긴다.
6. Provider 429는 model ID와 분리된 logical provider key(`claude`)로 15분 global backoff를 열고 새 same-provider launch/replacement를 막는다.
7. Failed child를 같은 workflow에서 retry/resume/reroute하지 않는다. Fallback model은 없다.
8. Runtime API가 강제 child kill을 보장하지 않으므로 replacement는 terminal receipt 뒤에만 가능하다;
   stale target traffic 자체는 `exec-http` fencing이 차단한다.
9. Actor label은 workflow key와 동일해야 `run-result` provenance가 연결된다.

Workflow 종료 후 parent backstop:

```bash
python3 .pi/pentest/postflight.py <workflow-run-dir>/status.json \
  --end-cohort --reason 'elastic cohort complete'
python3 .pi/pentest/swarm.py cohort-start --label cohort-2 --peers 3
```

## Reliable atomic assertion

### 1. Peer startup

```bash
. .pi/pentest/engagement_env.sh
python3 .pi/pentest/swarm.py peer-start --label '<assigned-label>' --continuous \
  --proxy-policy "${PENTEST_PROXY_POLICY:-auto}" \
  --lease 180 --no-progress 120 --brief-tokens 1400
```

반환값에는 agent ID, network mode, scratch, claim, partial-first brief가 있다. Fresh shell마다 engagement
환경을 다시 source하되 전체 운영 문서를 다시 읽지 않는다.

### 2. Host-owned HTTP execution

```bash
python3 .pi/pentest/swarm.py exec-http \
  --agent "$AGENT" --work "$WORK" \
  --method GET --url 'https://scoped.example/' \
  --check baseline-reachability --timeout 30 \
  --expected '{"status":[200,301,302,401,403]}'
```

한 command가 다음을 수행한다.

```text
live claim 검증 → ExperimentSpec commit → request-time fencing → finite HTTP operation
→ bounded request/response artifact → surface/check → partial|error attempt
→ execution.completed|error commit
```

`expected`는 reporting hint일 뿐 취약점 verdict가 아니다. Redirect는 capture하지만 follow하지 않는다.
동일 work/request fingerprint의 durable experiment가 있으면 `resume-required`를 반환하고 재전송하지 않는다.
Terminated old claim의 `prepared` intent는 send 전임이 확정되므로 새 claim에 rebind할 수 있다. `sending`은
원격 exactly-once를 추측하지 않고 `indeterminate`로 멈춘다.

### 3. Atomic semantic checkpoint

```bash
python3 .pi/pentest/swarm.py checkpoint \
  --agent "$AGENT" --work "$WORK" --experiment "$EXPERIMENT" \
  --message-type observation --claim 'root returned an application shell' \
  --confidence 0.8 \
  --next-actions '[{"key":"root:assets","kind":"experiment","title":"Inspect referenced assets"}]' \
  --finish done --summary 'durable baseline interpreted'
```

한 SQLite transaction이 experiment provenance, typed assertion, grounded next work, experiment checkpoint,
work/claim completion을 처리한다. New generation은 같은 work의 이전 durable experiment를 checkpoint할 수
있지만 old owner는 mutation할 수 없다. Verify work는 계속 `finding-attest`로만 완료한다.

정상 HTTP assertion path는 `peer-start → exec-http → checkpoint` 세 번이다.

## Timing and partial takeover

시간 관계:

```text
operation timeout < no-progress timeout < claim lease < child timeout
```

Bookkeeping/inbox/brief/heartbeat는 lease를 갱신하지 않는다. Durable artifact/attempt, host execution,
typed assertion, finding/attestation만 material progress다. Lease deadline이 먼저 지나면 `expired`, lease가
살아 있어도 no-progress window가 지나면 `stalled`다.

Timeout/interruption/release 시 experiment나 artifact가 있으면 semantic verdict를 만들지 않고
`work.partial`을 기록한다. 다음 brief의 `partial_experiments`에는 상태별 exact action이 포함된다:
`completed/error`는 checkpoint, terminated `prepared`는 safe pre-send rebind, `sending`은 no-repeat
uncertainty다. Resume-first는 scheduler heuristic이 아니라
correctness rule이며 live scheduling은 계속 `fifo-v1`이다.

SQLite와 remote HTTP를 하나의 ACID transaction으로 만들 수는 없다. Runner는 spec-before-I/O,
request-time fence, bounded timeout, host-owned post-I/O commit으로 crash window를 최소화한다. Bytes가
전송된 직후 host process가 강제 종료되는 좁은 exactly-once gap은 숨기지 않는다.

## Findings and independent reproduction

`checkpoint`는 HTTP status를 finding으로 승격하지 않는다. Concrete candidate만 기존 command를 사용한다.

```bash
python3 .pi/pentest/swarm.py finding-add --agent "$AGENT" --work "$WORK" \
  --title 'Observed authorization boundary' --severity High --type access-control \
  --endpoint 'GET /orders/1' --evidence "$PENTEST_SCRATCH/evidence.txt"
python3 .pi/pentest/swarm.py finding-attest --agent "$OTHER_AGENT" --work "$VERIFY_WORK" \
  --finding FIND-0001 --verdict reproduced \
  --evidence "$PENTEST_SCRATCH/replay.txt" --notes 'independent reproduction'
```

Finder는 자기 finding을 attest할 수 없다. Reproduced finding만 planned pivots를 활성화한다.

## Metrics, replay, and benchmark gates

```bash
python3 .pi/pentest/swarm.py metrics
python3 .pi/pentest/swarm.py communication-metrics
python3 .pi/pentest/swarm.py replay-export --strict
python3 .pi/pentest/replay.py --events "$PENTEST_BOARD/replay.json" --policy fifo-v1 --strict
```

Metrics는 durable/completed assertions, stalled/expired claims, stage latency,
productive tool-call ratio와 completed assertion당 tool calls를 보고한다. Harness가 제공하지 않은 token,
request, first-output/tool timestamp는 `null`이다. Strict replay는 experiment→claim→attempt→artifact→checkpoint,
provider circuit, scheduler decision을 검증한다. Alternative scheduler policy는 ranking-only다.

Evidence Graph/adaptive scheduler 전에 다음 gate를 통과한다.

1. Loopback baseline 10/10 durable attempts, 9/10+ completion, expired ≤1/10.
2. Forced partial takeover 5/5 artifact reuse, duplicate target request 0.
3. 2→필요 시 3 peer shared validation 3회, claim completion ≥80%, expired ≤10%, strict replay 0.
4. 그 뒤에만 reset-controlled Solo/isolated/shared를 각각 3회 이상 비교한다.

Agent-visible reward/points/winner는 없다. Missing data를 0으로 만들지 않는다.

## Minimum acceptance

- scope/hash/workspace isolation과 proxy-auto semantics 유지
- empty ledger deterministic bootstrap, ungrounded hypothesis 거부
- active claim 및 scoped URL 없이는 `exec-http` request 0
- target response 뒤 model이 죽어도 artifact + partial/error attempt 보존
- duplicate same-work request는 `resume-required`, 재전송 0
- `checkpoint` event/follow-up/completion atomicity
- stale owner mutation/request fencing
- `expired`와 `stalled` 분리, bookkeeping renewal 금지
- partial-first brief와 cross-generation checkpoint
- provider-wide 429 circuit, initial concurrency 1, assessment peer 최대 3
- finding finder/verifier 분리와 SHA-256 evidence
- replay byte determinism과 strict validation
- Evidence Graph/adaptive scheduler 미구현
