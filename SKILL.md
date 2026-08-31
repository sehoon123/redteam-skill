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
- compressed dossier handoff → DB에서 즉시 재생성되는 dossier
- scripts/gadgets/files 공유 → SHA-256 artifact registry
- 독립 reproduction 뒤 collective pivot → self-attestation 금지 finding ledger
- 죽은 agent의 작업 인계 → lease expiry + 새 peer takeover
- 수천 개 실패 경로와 재방문 → append-only attempt history

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

`init` is idempotent for the same scope hash. A changed scope requires a new
`engagement_id`; old work cannot silently cross engagements.

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
      "dossier, inbox, coverage를 읽고 ready work를 lease해서 수행해. " +
      "ready work가 없으면 현재 gap에서 구체적인 새 hypothesis를 스스로 만들고 claim해. " +
      "발견 즉시 ledger에 공유하고, 후속 work를 만들어 다른 peer가 병렬 확장하게 해. " +
      "다른 peer finding은 독립 재현하고 자기 finding은 검증하지 마. " +
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
한 run이 중단되면 같은 workflow를 다시 실행한다. 기존 DB를 읽고 expired lease를
회수하므로 처음부터 다시 시작하지 않는다. `needs_attention` peer가 실제로 무활동이면
soft-interrupt 후 같은 child를 resume한다. 새 process는 dossier에서 이어받고, ledger 명령을
할 때마다 현재 lease가 자동 갱신된다.

## Peer runtime

각 peer는 다음 loop만 지킨다:

```
join → dossier/inbox/coverage → next(atomic lease)
  ├─ work 있음: execute → emit/artifact/attempt/finding → follow-up work → done
  ├─ work 없음: unexplored hypothesis 제안 → next
  ├─ verification: finder와 다른 peer가 fresh evidence로 attest
  ├─ blocked: 기록 + lease release (자동 safeguard 우회 없음)
  └─ quiescent: leave(summary)
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
  --details '{"repro":"...","impact":"..."}'

# Automatically created verify task is forbidden to the finder.
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

## Quiescence and stop

- `next --quiet 90`은 ready/leased work와 pending verification이 없고 meaningful event가
  90초 동안 없을 때만 `quiescent`를 반환한다.
- 독립 verifier가 하나도 없으면 `verification-blocked`를 반환해 operator에게 peer 추가를 요구한다.
- Peer는 quiescence에 `leave --summary`만 한다. Engagement를 닫지 않는다.
- Operator stop:
  ```bash
  python3 .pi/pentest/swarm.py close --reason 'timebox complete'
  ```
- Session reload: close하지 말고 peer pool을 다시 launch. Expired leases가 자동 회수된다.

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
