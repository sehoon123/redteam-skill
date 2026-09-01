---
name: redteam
description: |
  Phase-free autonomous multi-agent penetration testing for authorized targets.
  Identical peers self-organize through a transactional event/lease/evidence ledger.
  Trigger: /redteam, "pentest this", "red team", "run a pentest", "자율 보안 평가".
---

# Redteam

인가된 환경을 여러 동일 peer가 **동시에** 평가하는 Pi skill.
고정 phase도, 고정 recon/worker/validator 역할도 없다.

OpenAI–Hugging Face 사건에서 실제 성과를 만든 패턴만 안전하게 가져왔다:

- shared message board → SQLite append-only event journal
- task assignment/subdelegation → peer가 만드는 expiring work lease
- compressed dossier handoff → cohort별 handoff를 포함한 DB-derived dossier
- scripts/gadgets/files와 target credentials 공유 → artifact/credential registry
- 독립 reproduction 뒤 collective pivot → 검증 성공 시 follow-up work 자동 활성화
- 죽은 agent의 작업 인계 → lease expiry + 다음 fresh-context cohort takeover
- 수천 개 실패 경로와 재방문 → append-only attempt history
- unexplored work 분배 → `next`가 coverage gap을 canonical task로 자동 materialize

사건의 위험한 패턴은 가져오지 않는다: agent-visible reward, scope drift,
외부 dead drop/C2, persistence, transcript spoofing, safeguard 우회.
근거와 정확한 출처는 `RESEARCH.md`, 협업 규약은 `SWARM.md`, 프롬프팅 연구는
`PROMPTING-RESEARCH.md`를 읽어라.

## Operator contract

실행 전에 operator가 제공해야 한다:

- `.pi/pentest/scope.yaml`의 명시적 authorization과 target
- 네트워크 allowlist/격리, rate limit, kill switch
- 필요하면 Burp (`127.0.0.1:8080`)

`swarm.py`는 scope 파일 hash를 engagement에 고정한다. 실행 중 scope가 바뀌면
fail closed. 실제 네트워크 차단은 반드시 infrastructure layer에서 집행한다.

## 설치

```bash
mkdir -p .pi/skills/redteam/workflows .pi/agents .pi/pentest
cp SKILL.md SWARM.md RESEARCH.md PROMPTING-RESEARCH.md VALIDATION.md .pi/skills/redteam/
cp workflows/cohort.js .pi/skills/redteam/workflows/
cp agents/pentest-peer.md agents/pentest-peer-sonnet.md agents/pentest-peer-luna.md \
  agents/pentest-cohort-selector.md .pi/agents/
cp -R pentest/. .pi/pentest/
# settings.json의 세 peer override를 .pi/settings.json에 병합
```

Live state와 연구 corpus는 분리된다:

```
.pi/pentest/
├── scope.yaml
├── swarm.py                 # authoritative live ledger
├── postflight.py            # parent-side terminal result + lease recovery
├── kb.py                    # FTS5 trigram research/memory search
├── state/<engagement>.sqlite3
├── board/                   # operator export only; peers do not append
├── findings/report.md       # deterministic export
├── scratch/                 # PoC/evidence/tools
├── memory/                  # cross-engagement curated knowledge
└── research/board/          # 187-test historical corpus, read-only
```

## Initialize

```bash
python3 .pi/pentest/swarm.py init
python3 .pi/pentest/kb.py index
```

`init` is idempotent for the same scope hash and starts `cohort-1` with a target of eight
concurrent equal-authority slots. Luna fresh replacements may create more than eight joined actor
records. A changed scope requires a new `engagement_id`; old work cannot silently cross engagements.

## Canonical launch — 이 경로만 사용

> **필수:** Luna를 Claude와 같은 `runs.all` autonomous loop에 넣지 않는다.
> `PROMPTING-RESEARCH.md`는 연구 근거이며 launch source가 아니다. 실행은 아래
> `workflowScriptPath`만 사용한다.

`join`의 안전한 기본값은 one-shot이다. `pentest-peer-luna`는 명시적으로 `--one-shot`을
사용해 한 context에서 lease 하나의 bounded assertion을 artifact/ledger에 기록한 뒤
종료한다. Flag가 빠져도 ledger가 두 번째 claim을 거부한다. Claude profile만
`--continuous`를 명시해 autonomous loop를 사용한다.
Workflow가 다음 Luna child를
`context: "fresh"`로 시작한다. 따라서 사용자가 launch 예시만 복사해도 장기 Luna 대화가
생기지 않는다.

### 1. Canonical workflow 실행

```js
subagent({
  workflowScriptPath: ".pi/skills/redteam/workflows/cohort.js",
  cwd: ".",
  async: true,
  timeoutMs: 3600000
})
```

Workflow의 `cohort-mode` selector가 ledger status를 읽어 cohort 1이면 Sonnet,
cohort 2 이상이면 Opus profile을 자동 선택한다. Operator나 peer가 work 유형을 분류하지
않으며 코호트 안의 모든 peer는 동일 authority로 모든 ready work를 자유롭게 claim한다.
Selector가 cohort 번호를 확인하지 못하면 launch 전에 fail closed한다.

Workflow 실행 계약:

- Claude slot 5개: 해당 cohort의 Sonnet 또는 Opus profile로 autonomous peer loop
- Luna slot 3개: SET 5에서 검증한 7개의 **서로 다른 fresh child**가 slot별로 순차 실행
- 각 Luna child: 10분 timeout, lease 최대 1개, bounded assertion 1개, artifact/hash handoff 후 종료
- Luna stage는 structured verdict를 필수로 반환; `blocked/refusal` 또는 provider failure면 lane 중단
- Luna stage 사이에 `resume: "previous"`를 사용하지 않음
- 대화 transcript 대신 SQLite ledger와 scratch artifact만 다음 context로 전달
- 각 actor label은 `runs.lanes`의 `<lane>.<stage>` workflowKey와 같고 cohort 내 중복 join이 거부되어 postflight가 정확히 lease를 회수

`pentest-peer-luna`를 별도의 장기 `runs.all` loop로 추가하거나 canonical 파일을 inline fanout으로
바꾸지 않는다. Luna context 수를 조정해야 하면 workflow의 fresh stage 수만 바꾸며,
하나의 child가 두 번째 lease를 claim하게 만들지 않는다.

### 2. Postflight와 다음 cohort

Workflow가 끝나면 parent harness가 status file을 postflight에 전달한다. 실패 작업을 다른
모델로 재시도하지 않고 `refusal|budget|timeout|interrupted|provider-error`로 기록한 뒤
남은 lease만 반환한다.

```bash
python3 .pi/pentest/postflight.py <workflow-run-dir>/status.json \
  --end-cohort --reason 'canonical cohort workflow complete'
python3 .pi/pentest/swarm.py cohort-start --label cohort-2 --peers 8
```

다음 cohort는 누적 ledger/backlog를 fresh context로 이어받는다. Cohort 1의 Sonnet이 만든
surface, finding, hypothesis는 cohort 2의 Opus에게 역할 지정 없이 그대로 제공된다.

## Why fresh Luna stages are part of launch

SET 4에서는 single-session multi-step Luna가 첫 safe step만 수행하고 완료한 반면, SET 5의
7개 fresh contexts는 같은 authorized local chain을 끝까지 검증했다. 이 차이는 프롬프트
설명만으로 해결되지 않는다. 그래서 다음 네 곳에서 동일 계약을 강제한다:

1. `SKILL.md`: canonical workflow 외 launch 금지
2. `workflows/cohort.js`: cohort model 자동 선택 + 각 Luna stage의 fresh context/blocked verdict
3. `agents/pentest-peer-luna.md` + ledger: one-shot lease와 `done` 전 work artifact 강제
4. `postflight.py`: completed-looking structured refusal도 `refusal`로 분류

## Claude peer runtime

Sonnet/Opus peer는 다음 autonomous loop를 지킨다. Luna에는 이 loop를 적용하지 않는다;
Luna는 `agents/pentest-peer-luna.md`의 one-shot lifecycle을 사용한다.

```
join(cursor) → bounded dossier/collaboration inbox/credentials → next(atomic lease)
  ├─ untested gap: auto-created coverage work → attempt → done
  ├─ work 있음: execute → 즉시 message/artifact/credential/finding 공유
  ├─ verification: finder와 다른 peer가 fresh evidence로 attest
  ├─ reproduced: planned follow-up tasks가 ready로 전환 → collective pivot
  ├─ work 없음: unexplored hypothesis 제안 → next
  └─ cohort timebox/quiescence: leave(summary)
```

### 핵심 명령

```bash
S=.pi/pentest/swarm.py
python3 "$S" dossier --recent 10 --gap-limit 12
python3 "$S" inbox --agent "$AGENT" --after "$CURSOR" --limit 25 --collaboration-only
python3 "$S" task-add --agent "$AGENT" --key 'GET:/catalog:server-input:boolean' \
  --kind hypothesis --title 'Analyze category input behavior'
python3 "$S" next --agent "$AGENT" --wait 10 --lease 180 --quiet 90
python3 "$S" heartbeat --agent "$AGENT" --work "$WORK" --lease 180
python3 "$S" done --agent "$AGENT" --work "$WORK" --summary 'result and follow-ups'
python3 "$S" credentials --show-values
```

### Immediate sharing

```bash
python3 "$S" emit --agent "$AGENT" --kind intel \
  --json '{"surface":"GET /order/details","observation":"orderId changes response class"}'
python3 "$S" surface-add --agent "$AGENT" --method GET --path '/order/details' \
  --params '["orderId"]'
python3 "$S" attempt-add --agent "$AGENT" --surface 'GET /order/details' \
  --check access-control --result partial --notes 'needs second account'
```

### Evidence and independent reproduction

```bash
python3 "$S" finding-add --agent "$AGENT" --work "$WORK" \
  --title 'Order detail authorization boundary' --severity High \
  --type access-control --endpoint 'GET /order/details?orderId=' \
  --evidence .pi/pentest/scratch/order-response.txt \
  --details '{"repro":"...","impact":"...","follow_ups":[
    {"key":"other-role","title":"Replay primitive with another role","priority":90},
    {"key":"bulk-endpoint","title":"Test the same primitive on the bulk API"}
  ]}'

# Automatically created verify task is forbidden to the finder. A reproduced
# verdict atomically activates the planned follow-up work.
python3 "$S" finding-attest --agent "$OTHER_AGENT" --work "$VERIFY_WORK" \
  --finding FIND-0001 --verdict reproduced \
  --evidence .pi/pentest/scratch/order-replay.txt --notes 'fresh-session replay'
```

## Why no bounty points

OpenAI의 사건 자체가 reward hacking에서 시작했다. Agent에게 CVSS×points, winner,
diversity multiplier를 보여주면 finding spam과 score manipulation이 목표가 된다.
따라서 peer는 점수를 볼 수 없다. Operator만 immutable evidence에서 사후 지표를 본다:

```bash
python3 .pi/pentest/swarm.py metrics
```

지표: reproduced/rejected/contested findings, validation latency, attempts,
registered surface×check coverage. 임의 board 메시지는 지표를 바꾸지 못한다.

## Cohort handoff and saturation

Peer는 timebox/quiescence에 `leave --summary`로 현재 lease를 반납한다. 응답 전에 provider가
종료시키면 parent postflight가 원인을 기록하고 lease를 반납한다. Cohort 종료 시 handoff,
run-result, 미완료 work, surface/finding/attempt delta가 고정된다. 다음 cohort는 canonical
`cohort.js`를 다시 실행하며 selector가 Opus profile을 선택하고 ready work와 gaps를 이어받는다.

```bash
python3 .pi/pentest/swarm.py cohort-end --reason 'timebox complete'
python3 .pi/pentest/swarm.py cohort-start --label next-fresh-context --peers 8
python3 .pi/pentest/swarm.py dossier --recent 10 --gap-limit 12
```

`saturation`은 target peer 수를 채운 연속 2개 completed cohort에서 새 surface와 reproduced
finding이 없고, proposed finding 및 priority 80+ work가 없을 때만 true다. 빈/미달 cohort는
streak에 포함하지 않는다. Coverage gap 자체는 계속 report에 남는다.

```bash
python3 .pi/pentest/swarm.py saturation --streak 2
python3 .pi/pentest/swarm.py close --require-saturation --streak 2 \
  --reason 'two dry cohorts and no high-priority backlog'
```

긴급 operator stop은 `close --reason ...`으로 언제든 가능하다. 독립 verifier가 없으면
`verification-blocked`가 반환된다. 같은 cohort의 process만 끊겼다면 close/end하지 말고
resume하면 expired lease가 자동 회수된다.

## Export

Pentest 실행과 reporting은 phase로 분리되지 않는다. Peer가 실행 중 계속 findings와
attestation을 기록하고, operator는 원하는 시점에 동일 ledger snapshot을 export한다.

```bash
python3 .pi/pentest/swarm.py report
python3 .pi/pentest/swarm.py export
```

`report`는 `.pi/pentest/findings/report.md`, `export`는
`.pi/pentest/board/events.jsonl`을 atomic replace로 생성한다.

## Prompting discipline

상세 실험은 `PROMPTING-RESEARCH.md`. Runtime에서는 다음만 적용한다:

1. authorization과 target context를 명시한다.
2. 큰 요청은 관찰 → 최소 PoC → impact 기록으로 분해한다.
3. Claude child는 cohort마다 fresh로 시작한다. Luna는 **lease마다** fresh child로 교체하며
   결과를 ledger/artifact로만 전달한다.
4. raw payload보다 재현 스크립트와 evidence file을 선호한다.
5. agent가 응답할 수 있는 refusal은 `task.blocked`; 응답 자체가 끊긴 refusal은 parent `postflight.py`가 기록한다. 둘 다 재시도·모델 우회하지 않는다.

## Knowledge search

```bash
python3 .pi/pentest/kb.py index
python3 .pi/pentest/kb.py search 'prototype pollution'
python3 .pi/pentest/kb.py search '인증 취약점'
```

FTS5 trigram을 사용하므로 한국어 substring 검색이 LIKE fallback에만 의존하지 않는다.
`body`가 없는 structured JSONL도 전체 payload를 index한다.

## Minimum acceptance

Engagement가 유효하려면:

- 모든 live mutation은 SQLite journal을 경유
- concurrent claim winner가 정확히 1명
- dead peer lease가 회수 가능
- finding의 finder와 verifier가 다름
- evidence artifact SHA-256 기록
- attempt history가 overwrite되지 않음
- scope hash mismatch는 fail closed
- operator report에 validation status와 coverage gap 포함
- `next`가 untested surface×check를 distinct coverage lease로 materialize
- reproduced finding만 planned follow-up work를 활성화
- cohort 종료 뒤 work/handoff가 다음 fresh cohort에 유지
- saturation close가 dry-cohort streak와 고우선 backlog를 검사
- canonical workflow만 launch source이며 Luna를 장기 `runs.all` loop로 시작하지 않음
- 모든 Luna stage가 새 `agent` + `context: "fresh"`이고 `resume: "previous"`를 쓰지 않음
- `join`은 기본 one-shot이며 Claude만 `--continuous`; ledger가 Luna의 두 번째 lease를 거부함
- one-shot work는 등록된 work artifact 없이는 ledger가 `done`을 거부함
- agent-visible refusal은 structured `verdict=blocked,outcome=refusal`로 lane과 postflight에 전달됨
