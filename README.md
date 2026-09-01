# Redteam Skill

Pi용 phase-free multi-agent penetration testing skill. 모든 peer가 동시에 시작해
transactional ledger에서 스스로 workstream을 만들고 claim·handoff·verify한다.

## 핵심

- **동일 peer, 고정 역할 없음** — recon/analysis/verification이 동시에 발생
- **SQLite event + lease ledger** — atomic claim, dead-agent takeover, session reload 복구
- **Evidence-first findings** — SHA-256 artifact + finder와 다른 peer의 reproduction
- **Append-only coverage** — 상충 결과와 실패 경로를 덮어쓰지 않음
- **Persistent mixed cohorts** — Claude 5 + Luna 3 fresh context가 누적 ledger를 이어받음
- **Automatic collective pivot** — 미시험 coverage claim, 독립 재현 후 follow-up 활성화
- **Dossier handoff** — 새 peer가 이전 chat 없이 현재 상태를 즉시 복구
- **한국어 KB 검색** — FTS5 trigram + structured JSONL normalization
- **No live bounty** — reward hacking 대신 operator-only evidence metrics
- **Mixed Claude + Luna cohorts** — Claude 5 + Luna 3, 동일 ledger에서 자율 조정

설계 근거: `RESEARCH.md` (OpenAI technical report, METR, Hugging Face timeline,
Black Hat 발표). Runtime 규약: `SWARM.md`. 프롬프팅 실험: `PROMPTING-RESEARCH.md`.
실제 ginandjuice.shop 실행 기록: `VALIDATION.md`.

## 구조

```
├── SKILL.md
├── SWARM.md
├── RESEARCH.md
├── PROMPTING-RESEARCH.md
├── VALIDATION.md
├── settings.json
├── agents/pentest-peer.md
├── agents/pentest-peer-luna.md  # same profile body; different model route
├── pentest/
│   ├── swarm.py
│   ├── postflight.py          # parent-side terminal-result/lease recovery
│   ├── kb.py
│   ├── scope.yaml
│   ├── research/board/      # 187-test historical corpus
│   ├── board/               # post-run export only
│   ├── findings/
│   ├── scratch/
│   ├── memory/
│   └── state/
└── tests/test_swarm.py
```

## 설치

```bash
mkdir -p .pi/skills/redteam .pi/agents .pi/pentest
cp SKILL.md SWARM.md RESEARCH.md PROMPTING-RESEARCH.md VALIDATION.md .pi/skills/redteam/
cp agents/pentest-peer.md agents/pentest-peer-luna.md .pi/agents/
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
        "thinking": "xhigh",
        "fallbackModels": []
      },
      "pentest-peer-luna": {
        "model": "<your-provider>/gpt-5.6-luna",     // OpenAI provider
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

1. `.pi/pentest/scope.yaml` 작성
2. 초기화:
   ```bash
   python3 .pi/pentest/swarm.py init
   python3 .pi/pentest/kb.py index
   ```
3. `/redteam` 또는 `SKILL.md`의 `runs.all` 예시로 Claude 5 + Luna 3을 동일 prompt로 동시 실행
4. Workflow 완료 후 parent가 postflight와 다음 cohort를 자동 진행:
   ```bash
   python3 .pi/pentest/postflight.py <workflow-run-dir>/status.json --end-cohort
   python3 .pi/pentest/swarm.py cohort-start --peers 8
   python3 .pi/pentest/swarm.py dossier --recent 10 --gap-limit 12
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
