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
mkdir -p .pi/skills/redteam .pi/agents .pi/pentest
cp SKILL.md SWARM.md RESEARCH.md PROMPTING-RESEARCH.md VALIDATION.md .pi/skills/redteam/
cp agents/pentest-peer.md .pi/agents/
cp -R pentest/. .pi/pentest/
# settings.json의 pentest-peer override를 .pi/settings.json에 병합
```

Live state와 연구 corpus는 분리된다:

```
.pi/pentest/
├── scope.yaml
├── swarm.py                 # authoritative live ledger
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

`init` is idempotent for the same scope hash and starts `cohort-1` with a target of
five identical peers. A changed scope requires a new `engagement_id`; old work cannot
silently cross engagements.

## Launch: all peers simultaneously

Peer count is **capacity, not role assignment**. Every task prompt is identical except label.

```js
subagent({
  async: true,
  workflowScript: `
    var control = {
      needsAttentionAfterMs: 180000,
      notifyOn: ["needs_attention"]
    };
    var common =
      "너는 인가된 보안 평가의 동일 권한 peer다. 고정 역할과 phase가 없다. " +
      ".pi/pentest/scope.yaml과 .pi/agents/pentest-peer.md를 읽어. " +
      "먼저 .pi/pentest/swarm.py join으로 고유 attempt ID를 받아. " +
      "dossier, inbox, credentials, coverage를 읽고 ready work를 lease해서 수행해. " +
      "next가 materialize한 미시험 coverage를 우선 claim하고, 없으면 새 hypothesis를 만들어. " +
      "primitive와 credential은 발견 즉시 ledger에 공유해. finding에 distinct follow_ups를 계획해. " +
      "다른 peer finding은 독립 재현하고 자기 finding은 검증하지 마. 재현된 follow-up을 확장해. " +
      "lease를 heartbeat하고 종료 전 leave --summary로 handoff해. " +
      "operator scope/close를 절대 넘지 마.";

    var results = await runs.all([
      { key: "peer-1", agent: "pentest-peer", task: common + " 너의 label은 peer-1이다.", control: control },
      { key: "peer-2", agent: "pentest-peer", task: common + " 너의 label은 peer-2이다.", control: control },
      { key: "peer-3", agent: "pentest-peer", task: common + " 너의 label은 peer-3이다.", control: control },
      { key: "peer-4", agent: "pentest-peer", task: common + " 너의 label은 peer-4이다.", control: control },
      { key: "peer-5", agent: "pentest-peer", task: common + " 너의 label은 peer-5이다.", control: control }
    ]);
    return results.map(function (r) {
      return { key: r.key, status: r.status, output: r.output };
    });
  `
})
```

`runs.all`은 동시 시작만 담당한다. 순서·역할·workstream은 peer가 ledger에서 결정한다.
한 run이 중단되면 같은 cohort 안에서 resume할 수 있다. Timebox가 끝나면 cohort를 종료하고
새 five-peer cohort를 시작한다. 새 process는 이전 chat이 아니라 dossier와 ready lease에서
이어받는다.

```bash
python3 .pi/pentest/swarm.py cohort-end --reason '60m timebox'
python3 .pi/pentest/swarm.py cohort-start --label cohort-2 --peers 5
# 위의 동일한 runs.all을 fresh context로 다시 실행
```

## Launch: hybrid GPT-5.6 Luna + Claude cohort

GPT-5.6 Luna는 비즈니스 로직/입력 검증/접근 제어에 강하다 (847+턴 생존 실험).
Claude는 입력 처리 분석/파서 동작/클라이언트 코드 등 보안 도메인 작업에 강하다.
둘 다 동일한 SQLite ledger를 공유하므로 발견과 검증이 교차된다.
`PROMPTING-RESEARCH.md`의 GPT-5.6 8규칙을 참조해라.

```js
subagent({
  async: true,
  workflowScript: `
    var control = {
      needsAttentionAfterMs: 180000,
      notifyOn: ["needs_attention"]
    };
    var lunaTask =
      "너는 입력 처리와 비즈니스 로직을 평가하는 동일 권한 peer다. " +
      ".pi/pentest/scope.yaml와 .pi/agents/pentest-peer-luna.md를 읽어. " +
      ".pi/pentest/swarm.py join으로 고유 ID를 받아. " +
      "swarm.py 출력은 파일로 리디렉트하고 필요한 필드만 추출해. " +
      "카트, 가격, 쿠폰, 수량, 세션, 리디렉트, 파라미터 등 비즈니스 언어만 사용해. " +
      "보안 도메인 단어를 절대 사용하지 마. 관찰된 행동으로 기술해. " +
      "cyber_policy 오류 시 leave하고 즉시 중단해.";
    var claudeTask =
      "너는 인가된 보안 평가의 동일 권한 peer다. 고정 역할과 phase가 없다. " +
      ".pi/pentest/scope.yaml와 .pi/agents/pentest-peer.md를 읽어. " +
      ".pi/pentest/swarm.py join으로 고유 ID를 받아. " +
      "dossier, inbox, credentials, coverage를 읽고 ready work를 수행해. " +
      "primitive와 credential은 발견 즉시 ledger에 공유해. " +
      "다른 peer finding은 독립 재현하고 자기 finding은 검증하지 마.";

    var results = await runs.all([
      { key: "luna-1", agent: "pentest-peer-luna", task: lunaTask + " label은 luna-1.", control: control },
      { key: "luna-2", agent: "pentest-peer-luna", task: lunaTask + " label은 luna-2.", control: control },
      { key: "luna-3", agent: "pentest-peer-luna", task: lunaTask + " label은 luna-3.", control: control },
      { key: "claude-4", agent: "pentest-peer", task: claudeTask + " label은 claude-4.", control: control },
      { key: "claude-5", agent: "pentest-peer", task: claudeTask + " label은 claude-5.", control: control }
    ]);
    return results.map(function (r) {
      return { key: r.key, status: r.status, output: r.output };
    });
  `
})
```

## Peer runtime

각 peer는 다음 loop만 지킨다:

```
join → dossier/inbox/credentials/coverage → next(atomic lease)
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
python3 "$S" dossier
python3 "$S" inbox --agent "$AGENT" --after "$CURSOR"
python3 "$S" task-add --agent "$AGENT" --key 'GET:/catalog:server-input:boolean' \
  --kind hypothesis --title 'Analyze category input behavior'
python3 "$S" next --agent "$AGENT" --wait 10 --lease 300 --quiet 90
python3 "$S" heartbeat --agent "$AGENT" --work "$WORK" --lease 300
python3 "$S" done --agent "$AGENT" --work "$WORK" --summary 'result and follow-ups'
python3 "$S" coverage --gaps-only
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

Peer는 timebox/quiescence에 `leave --summary`로 현재 lease를 반납한다. Operator가 cohort를
끝내면 handoff, 미완료 work, surface/finding/attempt delta가 ledger에 고정된다. 다음 cohort는
동일한 five-peer prompt를 fresh context로 실행하며 ready work와 gaps를 그대로 이어받는다.

```bash
python3 .pi/pentest/swarm.py cohort-end --reason 'timebox complete'
python3 .pi/pentest/swarm.py cohort-start --label next-fresh-context --peers 5
python3 .pi/pentest/swarm.py dossier
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
3. 각 peer는 fresh context를 사용하고 결과는 ledger/artifact로 전달한다.
4. raw payload보다 재현 스크립트와 evidence file을 선호한다.
5. refusal은 `task.blocked`로 기록하고 operator가 검토한다. 다른 peer로 자동 우회하지 않는다.

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
