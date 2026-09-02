# Redteam Skill

Pi/Herdr용 phase-free authorized penetration-testing skill. 여러 agent를 먼저 늘리는 대신
**Reliable Atomic Execution Plane**을 유지하고, fresh Pi peer lifecycle은 Herdr가 관리한다. Host-owned `exec-http`가 target request와 durable
artifact/attempt를 묶고, `checkpoint`가 semantic assertion·follow-up·completion을 한 transaction으로
처리한다.

## 핵심

- **Equal authority** — 고정 recon/worker/verifier role이나 phase 없음
- **SQLite authority** — claim generation, evidence, attempt, finding, provider circuit, replay
- **Atomic HTTP assertion** — scope/proxy/fencing/timeout/artifact/partial attempt를 runner가 소유
- **Atomic checkpoint** — typed interpretation, next work, work completion을 한 commit으로 처리
- **Grounded bootstrap** — empty engagement는 reachability/root-fetch부터 시작; 근거 없는 hypothesis 거부
- **Partial takeover** — response를 재전송하지 않고 이전 experiment를 다음 generation이 checkpoint
- **Progress-aware lease** — bookkeeping renewal 제거, `expired`와 `stalled` 분리
- **Herdr elastic cohort** — fresh Pi process 1개에서 시작해 evidence/backlog에 따라 최대 3 peer
- **Intercom control plane** — bounded lifecycle/reference message만 전달; assessment authority는 SQLite
- **Provider-wide 429 circuit** — same-provider launch storm 방지, fallback/reroute 없음
- **Independent findings** — SHA-256 evidence와 finder가 아닌 peer의 attestation
- **Task-local causal handoff** — unrelated transcript 대신 workstream provenance만 전달
- **Deterministic replay/SwarmBench** — nullable telemetry와 strict experiment provenance
- **Deferred complexity** — Evidence Graph와 adaptive scheduler는 controlled benchmark 전까지 미구현

설계 근거: `RESEARCH.md`, runtime: `SKILL.md`/`SWARM.md`, proxy: `PROXY.md`, workspace:
`WORKSPACES.md`, causal protocol: `CAUSAL-PROTOCOL.md`, benchmark: `SWARMBENCH.md`, 실제 검증:
`VALIDATION.md`.

## 구조

```text
├── SKILL.md / SWARM.md / CAUSAL-PROTOCOL.md / SWARMBENCH.md
├── PROXY.md / WORKSPACES.md / VALIDATION.md
├── agents/
│   └── pentest-peer.md / pentest-peer-sonnet.md / pentest-peer-luna.md
├── pentest/
│   ├── herdr_cohort.py          # Herdr lifecycle + event-driven replacement
│   ├── swarm.py                 # ledger + peer-start + exec-http + checkpoint
│   ├── replay.py / benchmark.py / scheduler.py / protocol.py
│   ├── postflight.py / workspace.py / kb.py
│   └── engagements/<id>/        # isolated scope/state/scratch/findings/board
└── tests/test_swarm.py
```

## 설치

Herdr 0.8+와 pi-intercom을 먼저 설치한다.

```bash
mkdir -p .pi/skills/redteam .pi/agents .pi/pentest
cp SKILL.md SWARM.md RESEARCH.md PROMPTING-RESEARCH.md PROXY.md WORKSPACES.md \
  CAUSAL-PROTOCOL.md SWARMBENCH.md VALIDATION.md .pi/skills/redteam/
cp agents/pentest-peer.md agents/pentest-peer-sonnet.md agents/pentest-peer-luna.md \
  agents/luna-probe.md .pi/agents/
cp -R pentest/. .pi/pentest/
```

Cohort 1은 Sonnet 5, cohort 2+는 Opus 4.8이고 Luna는 explicit one-lease option이다. 각 Pi process는
한 model만 scope하며 fallback/resume을 사용하지 않는다.

## 사용

```text
/redteam "https://example.com"에 대한 모의해킹을 진행해줘
```

명시적 평가 요청은 해당 target에 대한 authorization assertion이다. 불명확하다고 사용자가 말한
경우에만 traffic 전에 확인한다. Skill이 다음을 자동 수행한다.

```bash
python3 .pi/pentest/workspace.py ensure --target "$TARGET" \
  --authorization "Operator explicitly requested and asserted authorization for this target."
python3 .pi/pentest/swarm.py init
python3 .pi/pentest/kb.py index
```

사용자는 `create/use`를 직접 다루지 않는다. 여러 site는 site별 Pi process와
`PENTEST_ENGAGEMENT=<id>`를 사용한다.

Canonical launcher는 `pentest/herdr_cohort.py`다. Coordinator는 Herdr에서 peer pane과 watcher pane을
만들고, pi-intercom stable name을 전달한다. 첫 peer만 시작한 뒤 기존 `ramp-status` gate가 ready일 때
slot 2/3을 추가한다.

```bash
python3 .pi/pentest/herdr_cohort.py launch \
  --pane '<peer-pane-1>' --slot 1 --coordinator '<coordinator-name>'
herdr pane run '<watcher-pane>' \
  'python3 .pi/pentest/herdr_cohort.py watch --interval 2'
```

Watcher는 `pane.agent_detected`/`pane.exited` event를 구독한다. Exact Pi session이 실제로 사라졌을
때 transcript를 분류하고, `run-result`와 `run-result-get` receipt 뒤에만 fresh generation을 시작한다.
Refusal/429/budget, missing·ambiguous transcript, pane exit, Herdr/socket loss는 replacement 없이 fail closed한다.

Agent의 정상 HTTP path:

```bash
. .pi/pentest/engagement_env.sh
python3 .pi/pentest/swarm.py peer-start --label peer-1.gen-1 --continuous \
  --proxy-policy auto --lease 180 --no-progress 120
python3 .pi/pentest/swarm.py exec-http --agent "$AGENT" --work "$WORK" \
  --method GET --url 'https://example.com/' --check baseline-reachability --timeout 30
python3 .pi/pentest/swarm.py checkpoint --agent "$AGENT" --work "$WORK" \
  --experiment "$EXPERIMENT" --message-type observation \
  --claim 'bounded interpretation' --finish done
```

직접 curl/requests/browser target traffic은 허용하지 않는다. `exec-http`가 ledger의 trusted proxy
결정을 적용한다. Reachable proxy rejection은 차단하고, auto mode의 connection unavailability만
direct/offline으로 진행한다.

Cohort 후:

```bash
python3 .pi/pentest/herdr_cohort.py disable --slot 1
python3 .pi/pentest/swarm.py cohort-end --reason 'Herdr cohort complete'
python3 .pi/pentest/swarm.py cohort-start --peers 3
python3 .pi/pentest/swarm.py metrics
python3 .pi/pentest/swarm.py replay-export --strict
python3 .pi/pentest/swarm.py report
```

## 테스트

```bash
python3 -m unittest discover -s tests -v
```

현재 deterministic suite는 loopback HTTP 10/10 durable assertion, duplicate-request suppression,
partial takeover, stale claim fencing, redirect/scope/proxy behavior, stalled claims, provider 429 circuit,
migration, strict replay, benchmark NULL semantics을 포함한다. Live target traffic은 테스트에서 사용하지 않는다.

## License

MIT. 인가된 보안 평가 전용. Infrastructure allowlist, rate limit, kill switch는 operator 책임이다.
