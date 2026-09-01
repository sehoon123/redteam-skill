# Redteam Skill

Pi용 phase-free multi-agent penetration testing skill. 모든 peer가 동시에 시작해
transactional ledger에서 스스로 workstream을 만들고 claim·handoff·verify한다.

## 핵심

- **동일 peer, 고정 역할 없음** — recon/analysis/verification이 동시에 발생
- **SQLite event + lease ledger** — atomic claim, dead-agent takeover, session reload 복구
- **Evidence-first findings** — SHA-256 artifact + finder와 다른 peer의 reproduction
- **Append-only coverage** — 상충 결과와 실패 경로를 덮어쓰지 않음
- **Model-aware cohorts** — Claude 5개 autonomous slot + Luna 3개 fresh one-shot slot
- **Automatic collective pivot** — 미시험 coverage claim, 독립 재현 후 follow-up 활성화
- **Dossier handoff** — 새 peer가 이전 chat 없이 현재 상태를 즉시 복구
- **한국어 KB 검색** — FTS5 trigram + structured JSONL normalization
- **No live bounty** — reward hacking 대신 operator-only evidence metrics
- **Fresh-context Luna chain** — 3개 slot × 7개 one-lease child, artifact로만 handoff
- **Rolling replacement** — refusal 작업은 격리하고 같은 모델의 fresh child로 빈 slot 보충
- **Proxy auto policy** — reachable이면 강제 사용, unavailable이면 경고 후 direct/offline 진행
- **Site isolation** — scope/DB/evidence/report/KB를 engagement workspace별 분리
- **Zero-admin bootstrap** — `/redteam <URL>`이 site create/select/init를 자동 처리

설계 근거: `RESEARCH.md` (OpenAI technical report, METR, Hugging Face timeline,
Black Hat 발표). Runtime 규약: `SWARM.md`. 프롬프팅 실험: `PROMPTING-RESEARCH.md`.
실제 ginandjuice.shop 실행 기록: `VALIDATION.md`.

## 구조

```
├── SKILL.md
├── SWARM.md
├── RESEARCH.md
├── PROMPTING-RESEARCH.md
├── PROXY.md
├── WORKSPACES.md
├── VALIDATION.md
├── settings.json
├── agents/pentest-peer.md          # Opus autonomous loop
├── agents/pentest-peer-sonnet.md   # Sonnet autonomous loop
├── agents/pentest-peer-luna.md     # one lease, then exit
├── agents/pentest-cohort-selector.md # read-only cohort number, xhigh
├── agents/pentest-run-recorder.md  # host-verified terminal recording, xhigh
├── agents/luna-probe.md            # bounded proxy-auto Luna experiment
├── workflows/cohort.js             # canonical launch; cohort 1 Sonnet, 2+ Opus
├── pentest/
│   ├── swarm.py
│   ├── postflight.py          # parent-side terminal-result/lease recovery
│   ├── kb.py
│   ├── active-engagement
│   ├── engagements/<id>/
│   │   ├── scope.yaml
│   │   └── state/ scratch/ findings/ board/ memory/ cache/
│   ├── workspace.py / engagement_env.sh
│   └── research/board/      # shared read-only operational research
└── tests/test_swarm.py
```

## 설치

```bash
mkdir -p .pi/skills/redteam/workflows .pi/agents .pi/pentest
cp SKILL.md SWARM.md RESEARCH.md PROMPTING-RESEARCH.md PROXY.md WORKSPACES.md VALIDATION.md .pi/skills/redteam/
cp workflows/cohort.js .pi/skills/redteam/workflows/
cp agents/pentest-peer.md agents/pentest-peer-sonnet.md agents/pentest-peer-luna.md \
  agents/pentest-cohort-selector.md agents/pentest-run-recorder.md agents/luna-probe.md .pi/agents/
cp -R pentest/. .pi/pentest/
# settings.json의 override를 .pi/settings.json에 병합
```

### ⚠️ 모델명 설정

`settings.json`의 모델 ID는 예시이며, **각자의 provider 환경에 맞게 변경**해야 한다.
현재 예시는 IBM ICA Services 라우터를 사용한다:

```jsonc
// settings.json — 자신의 provider/model ID로 교체
{
  "subagents": {
    "agentOverrides": {
      "pentest-peer": {
        "model": "<your-provider>/claude-opus-4-8",  // Claude provider
        "thinking": "max",
        "fallbackModels": []
      },
      "pentest-peer-sonnet": {
        "model": "<your-provider>/claude-sonnet-5", // Claude provider
        "thinking": "xhigh",
        "fallbackModels": []
      },
      "pentest-peer-luna": {
        "model": "<your-provider>/gpt-5.6-luna",    // OpenAI provider
        "thinking": "xhigh",
        "fallbackModels": []
      }
    }
  }
}
```

Provider ID 확인 방법:
- Pi CLI: `/subagents-models pentest-peer` 또는 `/models`
- 설정 파일: `~/.pi/agent/models.json`의 `providers` 섹션
- 예: OpenAI 직접 API → `openai/gpt-5.6-luna`, AWS Bedrock → `bedrock/...`,
  Azure → `azure/...`, 로컬 라우터 → `ica-services-openai/...` 등

`fallbackModels`는 비워 둘 것을 권장한다. Refusal 시 다른 모델로 자동 우회하면
실험 결과가 오염되고 계정 수준 threshold가 누적된다.

## 사용

1. 평소처럼 target만 요청한다:
   ```text
   /redteam "https://example.com"에 대한 모의해킹을 진행해줘
   ```
   Skill이 `workspace.py ensure`로 host/port 기반 site ID, scope, workspace 선택을 만들거나 재사용하고
   `swarm.py init`과 `kb.py index`까지 자동 실행한다. 사용자는 `create/use`를 직접 다루지 않는다.
2. 명시적 평가 요청은 operator의 authorization assertion으로 기록한다. 인가가 불명확한 요청만
   target traffic 전에 한 번 확인한다.
3. 동시 site 운영은 site별 Pi process를 `PENTEST_ENGAGEMENT=SITE-A pi`로 시작한다.
4. `${PENTEST_PROXY:-http://127.0.0.1:8080}` proxy가 reachable이면 peer가 반드시 사용한다.
   꺼져 있으면 ledger warning 후 direct/offline mode로 계속한다. Strict 모드는
   `PENTEST_PROXY_POLICY=required`; Python/browser 설정은 `PROXY.md`를 따른다.
5. `SKILL.md`의 단일 `.pi/skills/redteam/workflows/cohort.js` entrypoint를 실행한다.
   Workflow가 cohort 1은 Sonnet, cohort 2+는 Opus로 자동 선택한다.
6. Luna를 별도 `runs.all` 장기 loop에 넣지 않는다. Canonical workflow가 3개 Luna slot을
   fresh one-shot child로 교체한다. `join`은 기본 one-shot이고 Claude만 `--continuous`를
   사용한다. Ledger는 Luna의 두 번째 lease와 artifact 없는 `done`을 거부한다. Terminal child는
   같은 profile의 fresh generation으로 보충되지만 refusal work 자체는 `failed`로 격리된다.
7. Workflow 완료 후 parent가 postflight와 다음 cohort를 자동 진행:
   ```bash
   python3 .pi/pentest/postflight.py <workflow-run-dir>/status.json --end-cohort
   python3 .pi/pentest/swarm.py cohort-start --peers 8
   python3 .pi/pentest/swarm.py dossier --recent 8 --gap-limit 5 --compact
   python3 .pi/pentest/swarm.py coverage --gaps-only
   python3 .pi/pentest/swarm.py saturation
   python3 .pi/pentest/swarm.py report
   ```

Agent file은 `tools`와 `model`을 고정하지 않는다. 기본 도구를 상속하고 model routing은
`.pi/settings.json`이 담당한다. Session이 끊기면 close하지 말고 peer pool을 다시 실행한다.
Expired lease가 자동 회수되어 새 peer가 이어서 수행한다.

## 테스트

```bash
python3 -m unittest -v tests/test_swarm.py
```

검증 범위: concurrent event writes, atomic coverage claims, lease/cohort takeover,
independent attestation-triggered pivots, saturation, scope hash fail-closed, Korean search.

## License

MIT. 인가된 보안 테스트 전용. 네트워크 allowlist, rate limit, kill switch는
operator infrastructure에서 강제해야 한다.
