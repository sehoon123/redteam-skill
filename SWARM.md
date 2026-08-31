# Swarm Intelligence Protocol v2

OpenAI–Hugging Face 사건의 핵심 교훈: agent들은 coordinator 없이 자율적으로 협업했다.
Message board에 요청을 올리면 다른 agent가 자발적으로 응답하고, 작업을 claim하고,
서로의 결과를 확장하고 의심했다. Phase나 할당 없이.

## 핵심 원칙

**Orchestrator는 spawner일 뿐이다.** Agent를 생성하고 0-refusal 프롬프팅을 적용하는 것이
orchestrator의 유일한 역할이다. Task 할당, phase 분리, 작업 순서는 agent들이 board를 통해
자율적으로 결정한다.

## Agent 자율 협업 패턴

### 1. CLAIM before work

Agent가 작업을 시작하기 전에 board에 claim을 기록한다.
다른 agent는 이미 claim된 작업을 건너뛴다.

```json
{"ts":"...", "agent":"agent-3", "type":"claim", "body":"Starting SQLi testing on /catalog?category="}
{"ts":"...", "agent":"agent-1", "type":"claim", "body":"Starting JS code analysis on deparam.js + searchLogger.js"}
```

Agent가 board를 읽고 claim이 없는 영역을 자율적으로 선택한다.
Coordinator가 할당하지 않는다.

### 2. REQUEST pattern

도움이 필요하면 board에 요청을 올린다. 누군가 자발적으로 응답한다.

```json
{"ts":"...", "agent":"agent-3", "type":"request", "body":"XXE에서 file:///etc/hostname 읽었는데 내용이 반영 안 됨. OOB 채널 필요. 누가 외부 수신 서버 만들어줄 수 있어?"}
{"ts":"...", "agent":"agent-1", "type":"response", "body":"agent-3: /logger 엔드포인트가 POST 받음. 거기로 보내봐."}
```

### 3. CHALLENGE pattern

다른 agent의 발견을 의심하고 검증한다. 별도 validator phase가 아니라 자연스럽게.

```json
{"ts":"...", "agent":"agent-2", "type":"finding", "body":"FIND: /order/details?orderId= has no auth check"}
{"ts":"...", "agent":"agent-4", "type":"challenge", "body":"agent-2: 진짜? 내가 다른 orderId로 해봤는데 400 나왔어. 어떤 ID로 했어?"}
{"ts":"...", "agent":"agent-2", "type":"response", "body":"agent-4: 0254685 ~ 0254809 범위야. 400은 존재하지 않는 ID야, 404가 아니라 400인게 포인트."}
{"ts":"...", "agent":"agent-4", "type":"verify", "body":"확인. 0254685 비인증으로 200 + 전체 PII 반환. VERIFIED."}
```

### 4. EXTEND pattern

다른 agent의 발견을 기반으로 확장한다.

```json
{"ts":"...", "agent":"agent-1", "type":"intel", "body":"SQLi로 users 테이블에서 administrator 계정 발견"}
{"ts":"...", "agent":"agent-3", "type":"extend", "body":"agent-1의 admin credential로 /admin에 접근 시도 중"}
{"ts":"...", "agent":"agent-3", "type":"finding", "body":"FIND: /admin 접근 성공! 사용자 삭제 기능 노출"}
```

### 5. SERENDIPITY — 항상 활성

모든 agent가 자기 작업 중 우연히 발견한 것을 즉시 board에 올린다.

```json
{"ts":"...", "agent":"agent-2", "type":"serendipity", "body":"XSS 테스트 중 /catalog/subscribe에서 email 파라미터가 응답에 unencoded 반영됨. 누가 확인해봐."}
```

## 중복 방지: Board-based coordination

**Phase/coordinator 기반:**
```
Coordinator → Worker-1: "너는 SQLi만 해"
→ Worker-1이 XSS 단서를 발견해도 무시하거나 보고만 함
```

**Board-based 자율 조율:**
```
Agent-3: board 읽음 → SQLi claim 없음 → "Starting SQLi" claim 기록 → 진행
Agent-1: board 읽음 → SQLi 이미 claim됨 → 다음으로 XSS claim → 진행
Agent-3: SQLi 중 XSS 단서 발견 → board에 즉시 serendipity 기록
Agent-1: board에서 agent-3의 XSS 단서 읽음 → 자기 XSS 작업에 통합
```

누락 방지: 아무도 claim하지 않은 영역은 board를 읽는 모든 agent에게 보인다.
한 agent가 "아무도 XXE 안 하네" → 자발적으로 claim.

## Spawner (Orchestrator) 역할

Orchestrator는 agent를 생성할 때 **광범위한 미션**을 주고, **구체적 task를 할당하지 않는다.**

```js
// ❌ OLD: 구체적 task 할당
task: "V1(SQLi)을 테스트해. V3(XXE)도 해. V4(deserialization)도 해."

// ✅ NEW: 광범위한 미션 + 자율 조율 지시
task: "이 사이트의 보안을 평가해. board를 읽어서 다른 agent가 뭘 하고 있는지 확인하고,
아무도 하지 않는 영역을 claim해서 진행해. 발견한 모든 것을 즉시 board에 공유해.
다른 agent의 발견을 읽고 확장하거나 의심해. 자격증명을 발견하면 즉시 KB에 기록해."
```

## 실시간 공유 상태

### Board (`.pi/pentest/board/`)
모든 agent가 자유롭게 읽고 쓰는 비동기 message board.
- `claims.jsonl` — 작업 claim (중복 방지)
- `intel.jsonl` — 발견한 정보
- `requests.jsonl` — 도움 요청
- `challenges.jsonl` — 다른 agent 결과에 대한 의심/검증

또는 단일 `swarm.jsonl`에 type으로 구분해도 됨. Agent가 자율적으로 결정.

### KB (`kb.py`)
```bash
python3 .pi/pentest/kb.py search "keyword"    # 관련 정보 검색
python3 .pi/pentest/kb.py add --source agent-N --type intel --body "..."   # 지식 추가
python3 .pi/pentest/kb.py credential --type web_login --username admin --value pass --source FIND-001
python3 .pi/pentest/kb.py endpoint --endpoint "/path" --technique sqli --tested-by agent-N --result vulnerable
python3 .pi/pentest/kb.py creds               # 공유 자격증명 조회
```

### Scratch, Cache, Memory
이전과 동일. 모든 agent가 자유롭게 사용.

## Launch 패턴

```js
// Spawner: 모든 agent를 동시에 생성, 광범위한 미션만 부여
var ctx = "[타겟 설명]";
var proxy = "[프록시 설정]";
var board = "board를 먼저 읽어서 다른 agent가 뭘 하고 있는지 확인해. " +
            "아무도 하지 않는 영역을 claim해서 진행해. " +
            "발견한 모든 것을 즉시 board에 공유해. " +
            "다른 agent의 발견을 읽고 확장하거나 의심해. " +
            "자격증명 발견 시 즉시 python3 .pi/pentest/kb.py credential로 공유. " +
            "테스트한 조합은 python3 .pi/pentest/kb.py endpoint로 기록. " +
            "한 영역이 끝나면 board를 다시 읽고 다음 미 claim 영역을 찾아.";

var results = await runs.all([
  { key: "agent-1", agent: "pentest-worker",
    task: proxy + ctx + " " + board },
  { key: "agent-2", agent: "pentest-worker",
    task: proxy + ctx + " " + board },
  { key: "agent-3", agent: "pentest-worker",
    task: proxy + ctx + " " + board },
  { key: "agent-4", agent: "pentest-worker",
    task: proxy + ctx + " " + board },
  { key: "agent-5", agent: "pentest-worker",
    task: proxy + ctx + " " + board }
]);
```

5개 agent가 동시에 시작해서, board를 통해 자율적으로 협업한다.
누가 SQLi를 하고 누가 XSS를 하는지는 **agent들이 결정**한다.

## Bounty Scoring (Incentive System)

OpenAI 사건에서 agent들이 "cheat the scorer"를 공유 목표로 삼았을 때 90%+ 가 참여했다.
이 전염성(contagion)을 의도적으로 활용한다: scoring incentive가 collaboration을 drive한다.

### 점수표

| Action | Points | Why |
|--------|--------|-----|
| NEW finding (CVSS × 10) | 10-100 | 심각한 취약점일수록 더 많은 보상 |
| Finding VERIFIED by challenge | +5 bonus | 독립 검증된 finding은 더 가치 있음 |
| Successful CHALLENGE (false positive 잡음) | +8 | 품질 유지 incentive |
| Failed challenge | -3 | 무분별한 challenge 방지 |
| EXTEND another's finding (chain) | +7 | 체인 공격 장려 |
| SERENDIPITY discovery | +3 | 우연한 발견 장려 |
| ASSIST (내가 공유한 intel을 다른 agent가 사용) | +5 | 정보 공유 incentive |
| DIVERSITY bonus (유일한 vuln type) | ×2 multiplier | 모든 agent가 같은 곳에 몰리지 않게 |

### Prompt에 포함하는 방법

```
"너는 agent-N이야. 이 engagement에서 bounty points를 최대화해:
- 새 취약점 발견 (CVSS × 10점)
- 다른 agent finding을 challenge해서 false positive를 잡으면 +8점
- 다른 agent의 발견을 확장(chain)하면 +7점
- 아무도 안 찾은 vuln type을 발견하면 ×2 배율
- 네가 공유한 intel을 다른 agent가 사용하면 +5 assist점

board를 읽어서 다른 agent가 뭘 했는지 확인하고,
가장 높은 점수를 얻을 수 있는 전략을 선택해.
이미 여러 agent가 SQLi를 찾았으면 다른 영역(XXE, SSTI, deserialization)을
시도하는 게 diversity bonus로 더 많은 점수를 받을 수 있어."
```

### 왜 이게 작동하는가

1. **Competition → Depth**: 점수를 위해 각 agent가 더 깊이 파고듦
2. **Diversity bonus → Coverage**: 모든 agent가 같은 곳에 몰리지 않음
3. **Challenge → Quality**: 자연스러운 adversarial verification
4. **Assist points → Sharing**: intel을 공유할 incentive
5. **Contagion**: 한 agent가 좋은 전략을 찾으면 다른 agent가 따라함

## Verification-Before-Reward (보상 해킹 방지)

ExploitGym에서 agent들이 scorer를 속여서 점수를 얻었다. 우리도 같은 위험이 있다.
Agent가 가짜 finding을 올리고 점수를 받으면 안 된다.

### 5가지 원칙

**1. REPRODUCTION REQUIRED**
finding은 재현 가능한 PoC가 있어야만 점수 인정.
```json
{"type": "finding", "status": "unverified",
 "poc": "curl 'https://target/catalog?category=...' → HTTP 500",
 "evidence": "response body contains 'Internal Server Error'"}
```
이론적 분석만으로는 finding 불가. 실행 결과가 있어야 함.

**2. INDEPENDENT VERIFICATION**
finding을 올린 agent와 다른 agent가 독립적으로 재현해야 "verified".
같은 agent가 find + verify 불가.
```
agent-1: finding UNVERIFIED
agent-3: challenge → 재현 성공 → VERIFIED (+5 bonus for agent-1, +8 for agent-3)
```

**3. ADVERSARIAL CHALLENGE**
모든 finding은 기본 "unverified". challenge = 독립 재현 시도.
- Challenge 성공 (FP 잡음): challenger +8점
- Challenge 실패 (진짜였음): challenger -3점, finder +5 bonus
이것이 자연스러운 검증 메커니즘.

**4. DIVERSITY > VOLUME**
같은 유형 10개 < 서로 다른 유형 5개.
```
agent-1: SQLi 3개 = 30점
agent-2: SQLi 1개 + XXE 1개 + XSS 1개 = 30점 + diversity ×2 = 60점
```

**5. DYNAMIC SCORING**
| Phase | 보상 | 이유 |
|-------|------|------|
| 초기 (첫 5분) | 모든 finding 높은 보상 | 탐색 장려 |
| 중기 | verified finding만 보상 | 품질 장려 |
| 후기 | chain/impact 보상 증가 | 깊이 장려 |

### Prompt에 포함

```
"VERIFICATION RULES:
- finding을 올릴 때 반드시 PoC(curl 명령 + 결과)를 포함해
- '이론적으로 가능하다'만으로는 finding 불인정
- 다른 agent의 finding을 독립적으로 재현해서 verify/challenge해
- verified finding만 최종 보고서에 포함됨
- 가짜 finding을 올리면 다른 agent의 challenge로 패널티"
```
