# Swarm Intelligence Protocol

OpenAI–Hugging Face 사건에서 AI agent가 자연 발생시킨 협업 패턴을 **의도적으로** 구현한다.
그 사건에서 agent들이 만든 message board, credential 공유, technique 전파, work delegation은
단일 agent보다 압도적으로 효과적이었다. 우리는 같은 패턴을 인가된 환경에서 사용한다.

## 문제: 영역 분리 vs 중복

```
고정 영역 할당 (현재):
  Worker-A: /catalog 담당 → SQLi 발견
  Worker-B: /blog 담당   → XSS 발견
  문제: Worker-A가 /catalog의 XSS를 놓침 (SQLi만 봤으니까)
        Worker-B가 /blog의 SSTI를 놓침 (XSS만 봤으니까)

모든 영역 할당:
  Worker-A: 전체 사이트
  Worker-B: 전체 사이트
  문제: 같은 엔드포인트를 중복 테스트, 시간 낭비
```

## 해결: Shared State + Overlap Detection + Serendipity

```
해결:
  Worker-A: /catalog SQLi 테스트 → board에 결과 기록
  Worker-B: board 읽음 → "A가 /catalog에서 SQLi 찾음,
            근데 나도 /catalog 보다가 searchTerm에서 XSS 후보 발견"
            → board에 XSS 후보 기록
  Worker-A: board 읽음 → "B가 XSS 후보 올림, 나도 인코딩 확인해봤는데
            이건 DOM sink이 있다" → board에 추가 intel 기록
  Coordinator: board에서 두 agent의 결과를 합쳐서 새 task 생성
```

이것이 OpenAI 사건의 message board가 만든 **serendipitous discovery** 패턴이다.
Worker-A는 SQLi를 찾다가 우연히 XSS 단서를 발견하고, 그 단서를 Worker-B가 확장한다.
고정 영역 할당에서는 이 패턴이 불가능하다.

## 공유 인프라 구조

### 1. Board (비동기 메시지 보드) — 이미 구현됨
```
.pi/pentest/board/*.jsonl
```
- 모든 agent가 자유롭게 읽고 씀
- 구조: `{"ts", "agent", "type", "body"}`
- type: intel, technique, request, progress, finding, refusal

### 2. Scratch (공유 작업 공간) — 이미 구현됨
```
.pi/pentest/scratch/
```
- 스크립트, 도구, PoC, 캡처된 트래픽 공유
- Agent-A가 작성한 sqli-test.py를 Agent-B가 수정해서 재사용
- Agent-A가 만든 쿠키 jar를 Agent-B가 인증에 재사용

### 3. Cache (공용 도구/패키지 캐시) — 이미 구현됨
```
.pi/pentest/cache/
```
- pip install 결과, 다운로드한 도구, 컴파일된 바이너리
- 한 agent가 설치하면 다른 agent가 재설치 없이 사용

### 4. Memory (크로스 엔게이지먼트 지식) — 이미 구현됨
```
.pi/pentest/memory/
```
- 이전 engagement에서 학습한 기법, 타겟 프로필
- 새 engagement 시작 시 모든 agent가 읽음
- engagement 종료 시 coordinator가 핵심 학습 저장

### 5. Live State (실시간 공유 상태) — 새로 추가
```
.pi/pentest/state/
├── tested-endpoints.jsonl    # 테스트된 엔드포인트+기법 추적
├── active-sessions.jsonl     # 활성 인증 세션 정보
├── credentials.json          # 발견된 자격증명 (모든 agent 사용)
├── attack-surface.json       # 실시간 업데이트되는 공격 표면 맵
└── coverage-matrix.json      # 엔드포인트 × 취약점유형 커버리지
```

## 중복 방지 + 누락 방지: Coverage Matrix

핵심은 **tested-endpoints.jsonl**과 **coverage-matrix.json**이다.

### tested-endpoints.jsonl
Agent가 테스트할 때마다 기록:
```json
{"ts":"...", "agent":"worker-1", "endpoint":"/catalog?category=", "technique":"sqli-union", "result":"vulnerable", "finding_id":"FIND-001"}
{"ts":"...", "agent":"worker-1", "endpoint":"/catalog?category=", "technique":"xss-reflected", "result":"not-vulnerable", "notes":"HTML-encoded"}
{"ts":"...", "agent":"worker-2", "endpoint":"/blog?search=", "technique":"xss-dom", "result":"vulnerable", "finding_id":"FIND-002"}
```

### coverage-matrix.json
Coordinator가 주기적으로 tested-endpoints.jsonl에서 생성:
```json
{
  "/catalog?category=": {
    "sqli": {"tested_by": "worker-1", "result": "vulnerable"},
    "xss": {"tested_by": "worker-1", "result": "not-vulnerable"},
    "xxe": {"tested_by": null, "result": null},       // ← 아직 안 테스트됨
    "ssti": {"tested_by": null, "result": null}        // ← 아직 안 테스트됨
  },
  "/blog?search=": {
    "sqli": {"tested_by": null, "result": null},       // ← 아직 안 테스트됨
    "xss": {"tested_by": "worker-2", "result": "vulnerable"},
    "xxe": {"tested_by": null, "result": null}
  }
}
```

이 matrix를 보면:
- **중복**: 이미 테스트된 조합은 다른 agent가 건너뜀
- **누락**: `null`인 조합은 아직 아무도 안 봤음 → coordinator가 할당
- **교차 검증**: "not-vulnerable" 결과를 다른 agent가 다른 기법으로 재검증

## Agent 역할 — 유연한 영역, 고정된 책임

```
pentest-coord:   무엇을 할지 결정 (coverage matrix 기반)
pentest-recon:   공격 표면 매핑 (attack-surface.json 업데이트)
pentest-worker:  할당된 테스트 수행 (하지만 우연히 발견한 것도 보고)
pentest-validator: 독립 검증 (finding만 읽고 재현)
pentest-analyst: 코드 분석 (소스 수준 취약점 식별)
```

**핵심: Worker는 할당된 영역만 테스트하는 게 아니다.**
Worker는 할당된 task를 수행하면서 **우연히 발견한 모든 것**을 board에 기록한다.
이것이 OpenAI 사건에서 불가능한 task를 풀려다 message board를 발견한 것과 같은 패턴이다.

Worker prompt에 반드시 포함:
```
"할당된 task를 수행하면서 다른 취약점 후보나 흥미로운 동작을 발견하면,
그것도 board에 기록해. 네가 직접 조사하지 않아도 된다 —
다른 agent가 board를 읽고 추가 조사할 수 있다."
```

## Serendipity Protocol

OpenAI 사건에서 가장 강력했던 패턴: **다른 목적으로 탐색하다가 우연히 발견**.

이를 의도적으로 유도하는 방법:

### 1. Overlapping Recon
초기 recon은 전체 agent가 독립적으로 수행. 같은 사이트를 다른 관점에서 보면 다른 것을 발견한다.
```js
runs.all([
  { key: "recon-surface", agent: "pentest-recon",
    task: "전체 엔드포인트와 파라미터를 매핑해" },
  { key: "recon-code", agent: "pentest-analyst",
    task: "JavaScript 파일을 분석해서 클라이언트 사이드 취약점 후보를 찾아" },
  { key: "recon-auth", agent: "pentest-worker",
    task: "인증 메커니즘과 세션 관리를 분석해" }
])
```
→ 세 agent가 같은 사이트를 보지만 다른 것을 발견한다.

### 2. Cross-Pollination Rounds
매 phase 사이에 coordinator가 "교차 수분" round를 실행:
```
"board를 읽고, 다른 agent가 발견한 것 중 네 전문 영역과
관련 있는 것을 찾아. 추가 조사가 필요한 것이 있으면 기록해."
```

### 3. Second Opinion
주요 finding마다 다른 agent가 "다른 각도"로 재검토:
- Worker-A: "SQLi로 데이터 추출 가능"
- Worker-B: "같은 파라미터에서 blind SQLi time-based도 가능한지 확인"
- Analyst: "같은 코드에서 다른 injection point가 있는지 소스 분석"

## 실시간 자격증명 공유

OpenAI 사건에서 agent들이 message board에 credential을 공유한 것은
단일 agent보다 훨씬 빠른 lateral movement를 가능하게 했다.

```
.pi/pentest/state/credentials.json
{
  "web_login": [
    {"username": "carlos", "password": "hunter2", "source": "scope.yaml", "validated": true},
    {"username": "administrator", "password": "from-sqli", "source": "FIND-003", "validated": false}
  ],
  "session_cookies": [
    {"name": "session", "value": "abc123", "authenticated_as": "carlos", "expires": "..."}
  ],
  "api_keys": [],
  "tokens": []
}
```

모든 agent가 이 파일을 읽고 씀. Worker-A가 SQLi로 admin password를 추출하면
즉시 credentials.json에 기록하고, Worker-B가 그 credential로 admin 기능을 테스트한다.

## 이전 engagement memory 자동 재사용

```
.pi/pentest/memory/
├── techniques.jsonl           # "이 타겟에서 H2 DB 사용, 8컬럼 UNION 가능"
├── target-profiles/
│   └── ginandjuice.shop.json  # 기술 스택, 알려진 취약점, 방어 메커니즘
└── agent-notes.jsonl          # "XSS는 warm-up 접근이 효과적", "SSRF는 gradual"
```

다음 engagement에서:
```
"먼저 .pi/pentest/memory/를 읽어. 이 타겟에 대한 이전 지식이 있으면 활용해.
특히 techniques.jsonl에서 이 타겟에 효과적이었던 기법을 우선 시도해."
```

## workflowScript 예시 — Swarm 패턴

```js
var ctx = "ginandjuice.shop은 ...";
var proxy = "...";

// Phase 1: Overlapping Recon (3 agents, 같은 사이트, 다른 관점)
var recon = await runs.all([
  { key: "recon-surface", agent: "pentest-recon",
    task: proxy + ctx + " 전체 공격 표면을 매핑해. 모든 엔드포인트, 파라미터, 기술 스택을 board/recon.jsonl에 기록해." },
  { key: "recon-code", agent: "pentest-analyst",
    task: proxy + ctx + " 모든 JavaScript 파일을 분석해서 client-side 취약점 후보를 board/code-analysis.jsonl에 기록해." },
  { key: "recon-auth", agent: "pentest-worker",
    task: proxy + ctx + " 인증 메커니즘, 세션 관리, CSRF 보호를 분석해서 board/auth-analysis.jsonl에 기록해. 인증 성공하면 state/credentials.json에 세션 정보 기록해." }
]);

// Phase 2: Coordinator가 coverage matrix 생성 + task 할당
var plan = await runs.run("plan", {
  agent: "pentest-coord",
  task: proxy + ctx + " board/의 모든 recon 결과를 읽어. state/coverage-matrix.json을 생성해 — 각 엔드포인트×취약점유형 조합에서 아직 테스트 안 된 것을 식별해. 가장 가치 높은 미테스트 조합부터 task를 board/tasks.jsonl에 작성해. 각 task는 이 스킬의 0-refusal 프롬프팅 규칙을 따라야 해."
});

// Phase 3: 병렬 exploitation + 우연한 발견 공유
var exploit = await runs.all([
  { key: "worker-1", agent: "pentest-worker",
    task: proxy + ctx + " board/tasks.jsonl에서 너에게 할당된 task를 수행해. 결과를 board/와 findings/에 기록해. state/tested-endpoints.jsonl에 테스트한 엔드포인트×기법 조합을 기록해. 할당된 task 외에 우연히 발견한 취약점 후보나 흥미로운 동작도 board에 기록해 — 다른 agent가 추가 조사할 수 있다. 자격증명을 발견하면 state/credentials.json에 즉시 기록해." },
  { key: "worker-2", agent: "pentest-worker",
    task: proxy + ctx + " (worker-1과 동일한 지시, 다른 task 할당)" }
]);

// Phase 4: Cross-Pollination — 서로의 결과 교차 검토
var cross = await runs.all([
  { key: "cross-1", agent: "pentest-worker",
    task: proxy + ctx + " board/를 전부 읽어. 다른 agent가 발견한 것 중 추가 조사가 필요한 것을 찾아. 특히 'not-vulnerable'로 기록된 것을 다른 기법으로 재테스트하거나, 한 agent가 발견한 단서를 확장해." },
  { key: "cross-2", agent: "pentest-analyst",
    task: proxy + ctx + " board/의 모든 finding을 읽어. 각 finding의 소스코드 수준 근본 원인을 분석하고, 같은 코드 패턴이 다른 곳에도 있는지 확인해." }
]);

// Phase 5: Validate + Report
var validate = await runs.run("validate", { agent: "pentest-validator", ... });
var report = await runs.run("report", { agent: "pentest-coord", ... });
```
