# Prompting and Refusal Research

이 문서는 인가된 평가에서 발생한 provider false positive와 운영 실패를 기록한다.
Safeguard를 우회하기 위한 단어 치환·은닉·분해 지침이 아니다. Refusal은 재시도하거나
다른 모델로 자동 우회하지 않는다.

## 실험 결과 요약 (2026-09-01)

**SET 1 (framing variants, n=6)** — refusal 0/6:  
- V1 "취약점 테스트" (보안 레이블 명시) 포함 전 variant 통과.  
- 9~21 turn, fresh-context, task-only prompt.

**SET 2 (context contamination, n=5)** — refusal 0/5:  
- XSS/SQLi/XXE findings JSON, /vulnerabilities taxonomy, swarm help text,  
  HTTP 보안 응답, 전체 합산(F_heavy) 모두 통과.

**결론**: 어휘나 단순 context 주입은 primary trigger가 아니다.  
**실제 trigger**: exploit 코드 생성, attack chain 실행, PoC payload 작성.  
이전 cohort luna 사망은 단어 노출이 아니라 **Node.js exploit harness를 직접 작성**하다 발생.

---

## 운영 원칙: 모델별 역할을 만들지 않는다

Claude와 Luna는 다음 조건이 같다.

- 동일 authority와 backlog
- 동일 agent profile 본문
- 동일 task prompt (label만 다름)
- 동일 lease, evidence, finding, attestation 규칙
- 동일한 미시험 work claim 권한

모델별 도메인, 예상 강점, 선호 취약점, 표현 방식 힌트를 주지 않는다. 그런 힌트는
claim 선택과 결과를 편향시켜 mixed cohort 실험 자체를 훼손한다. 두 profile의 차이는
frontmatter 이름과 settings의 model route뿐이다.

## False-positive를 줄이는 허용된 방법

### 1. Authorization과 target을 정확히 쓴다

Scope에는 소유권/허가 근거, 허용 host/port, 종료 조건을 명시한다. 알려진 finding이나
모델별 work assignment는 scope에 넣지 않는다.

```yaml
engagement_id: EXAMPLE-001
authorization: "Written authorization reference SEC-2026-001"
targets:
  - host: staging.example.test
    ports: [443]
```

### 2. 한 번에 현재 lease 하나만 전달한다

모든 finding, payload, 과거 이벤트를 prompt에 붙이지 않는다. 이는 용어 은닉이 아니라
정확성·비용·재현성을 위한 context budgeting이다. Peer는 `next`가 반환한 한 work와 그
workstream의 artifact만 읽는다.

### 3. Bounded state를 읽는다

Join 응답의 `cursor`를 보존한다. 과거 mechanical event 전체를 다시 읽지 않는다.

```bash
python3 .pi/pentest/swarm.py dossier --recent 10 --gap-limit 12
python3 .pi/pentest/swarm.py inbox --agent "$AGENT" --after "$CURSOR" \
  --limit 25 --collaboration-only
```

전체 coverage matrix는 operator 보고용이다. Peer work는 `next`가 자동 materialize한다.

### 4. Raw capture 대신 artifact reference를 공유한다

큰 HTTP response, source bundle, browser trace는 파일로 저장하고 SHA-256과 짧은 관찰만
ledger에 보낸다. 다른 peer는 검증할 때 필요한 artifact를 직접 읽는다.

### 5. Fresh context와 durable ledger를 함께 쓴다

Fresh context는 provider 판정을 회피하기 위한 재시도가 아니다. 정상 cohort 경계에서
chat history 대신 검증된 dossier/artifact를 이어받아 hallucination과 token growth를
줄이는 복구 방식이다.

### 6. Refusal을 retry/reroute하지 않는다

- Agent가 응답할 수 있으면 `task.blocked`와 `leave`를 기록한다.
- Provider가 응답 자체를 끊으면 parent가 `postflight.py`로 결과를 기록한다.
- `refusal` lease는 반환하지만 같은 요청을 다른 model에 자동 재할당하지 않는다.
- `fallbackModels`는 비워 둔다.
- 공식 Trusted Access/Cyber Verification 절차로 접근 권한을 해결한다.

### 7. API의 공식 식별 기능을 사용한다

OpenAI API를 직접 운영한다면 공식 문서에 따라 안정적인 `safety_identifier`를 설정하고
Trusted Access for Cyber를 신청한다. 식별자를 회전시켜 집행을 피하거나 차단 직후 새
계정/session으로 같은 요청을 반복하지 않는다.

### 8. 모든 tool call을 bounded로 만든다

Refusal과 무관한 hang도 cohort capacity를 잃게 한다.

- `curl --connect-timeout 10 --max-time 30`
- browser/harness bash tool timeout ≤ 120초
- background process는 PID와 종료 경로를 남김
- checkpoint 후 다음 assertion으로 이동

## Mixed cohort 실험: RT-2026-GJ02-GPT56 cohort 2

실행 구성: Claude 5 + Luna 3, 동시 시작, 역할 지정 없음, 동일 SQLite ledger.

### 성과

| 지표 | 결과 |
|---|---:|
| joined peers | 8/8 |
| 새 attempts | 27 |
| 새 surfaces | 10 |
| proposed findings | 6 |
| independently reproduced | 5 |
| median validation latency | 148.06초 |
| 종료 시 ready work | 26 |

Peer들은 atomic lease로 중복을 피했고, broadcast intel과 finder-excluded verification work를
통해 다섯 finding을 교차 재현했다. Reproduced finding의 planned follow-up도 자동으로
ready 상태가 됐다. 별도 coordinator나 사람의 task assignment는 없었다.

### Terminal 결과

| model group | outcome |
|---|---|
| Luna 3 | provider cyber-risk refusal 3 |
| Claude 5 | provider refusal 2, budget 429 2, interrupted hang 1 |

Luna refusal은 각각 약 8, 10, 40 turns 뒤 발생했다. Claude refusal은 약 30, 46 turns 뒤
external-entity 검증으로 진행하던 중 발생했다. 이 작은 표본만으로 특정 단어나 work class가
원인이라고 단정하지 않는다. 초기 context 크기, raw target content, 누적 tool output,
provider account state가 동시에 달랐기 때문이다.

## 이번 실행에서 확인한 결함

### Raw dossier/inbox flood

기존 startup은 `inbox --after 0`, 전체 coverage gaps, 전체 CLI help를 읽었다. 한 Luna peer는
시작 직후 수백 event를 받았고 다른 peer는 수백 gap을 context에 넣었다. 이는 provider와
무관하게 토큰·주의력·비용을 낭비했다.

수정:

- join cursor 이후만 읽음
- `--collaboration-only`로 mechanical broadcast 제외
- dossier gap 기본 20개, profile startup은 12개
- KB에서 safeguard/refusal 실험 corpus 제외

### Child가 refusal을 기록할 수 없음

Provider가 generation을 끊으면 profile의 “emit then leave” 규칙은 실행 불가능하다.

수정:

```bash
python3 .pi/pentest/postflight.py <workflow-run-dir>/status.json --end-cohort
```

Postflight는 harness status를 다음 범주로 기록한다.

- `completed`
- `refusal`
- `budget`
- `timeout`
- `interrupted`
- `provider-error`

그 뒤 해당 agent의 lease를 즉시 ready로 돌리고 `agent.run_result` event를 남긴다.
동일 run ID는 idempotent하다. 이 과정은 재시도나 fallback을 실행하지 않는다.

### Unbounded tool hang

한 Claude peer가 bash tool에서 16분 이상 멈췄다. Profile에 finite timeout을 강제하고
기본 lease를 300초에서 180초로 줄였다. Postflight가 정상 동작하면 terminal child의
lease는 TTL을 기다리지 않고 즉시 반환된다.

### Handoff 0개

모든 child가 provider/budget/interrupt로 끝나 `leave` summary는 없었다. 그래도 assertion별
artifact/attempt/intel checkpoint 덕분에 takeover와 교차 재현은 성공했다. Cohort summary는
이제 semantic `agent.leave` handoff와 machine `run_results`를 함께 보존한다.

## Agent 간 자율 소통 관찰

Cohort 2에서는 고신호 communication event가 broadcast 중심으로 발생했다.

- 관찰과 artifact path를 즉시 `intel`/`response`로 공유
- candidate 등록 시 verifier task 자동 생성
- 다른 peer가 평균 148초 안에 fresh evidence로 attest
- reproduced verdict가 follow-up work를 원자적으로 활성화
- expired lease를 다른 peer가 prior artifact에서 takeover

Directed message 사용은 적었지만 결함으로 보지 않는다. 검증과 pivot은 free-form chat이
아니라 ledger state transition으로 강제되므로, 메시지 acknowledgement가 없어도 작업이
소실되지 않는다. `--collaboration-only` inbox는 request/challenge/intel/finding event를
남기고 artifact/attempt/work bookkeeping noise만 숨긴다.

## Historical corpus 경계

`pentest/research/board/`에는 과거 classifier 실험과 refusal 연구가 보존돼 있다. 이는
historical evidence이며 operational instruction이 아니다. `kb.py index`는 다음 네 파일만
operational research로 허용한다.

- `auth-analysis.jsonl`
- `code-analysis.jsonl`
- `recon-baseline.jsonl`
- `recon-surface.jsonl`

`advanced-techniques.jsonl`, `classifier-architecture.jsonl`, `refusal-*.jsonl`, model-profile
실험, reward 연구는 live peer 검색 결과에 들어가지 않는다.

## 공식 자료

- [GPT-5.6 System Card – Safeguards](https://deploymentsafety.openai.com/gpt-5-6/safeguards)
- [GPT-5.6 System Card – Monitor Design](https://deploymentsafety.openai.com/gpt-5-6/monitor-design)
- [OpenAI API Cybersecurity checks](https://developers.openai.com/api/docs/guides/safety-checks/cybersecurity)
- [OpenAI Trusted Access for Cyber](https://help.openai.com/en/articles/20001258-openai-daybreak-trusted-access-for-cyber-overview)
- [Anthropic Cyber Verification Program](https://support.claude.com/en/articles/14604842-real-time-cyber-safeguards-on-claude)
