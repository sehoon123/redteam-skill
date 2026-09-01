var cohortInfo = await runs.run("cohort-mode", {
  agent: "pentest-cohort-selector",
  context: "fresh",
  task: "Run python3 .pi/pentest/swarm.py status without modifying files or ledger. Return the active cohort number only through the required structured output.",
  outputSchema: {
    type: "object",
    properties: { number: { type: "integer", minimum: 1 } },
    required: ["number"],
    additionalProperties: false
  }
});

var cohortNumber = cohortInfo.structuredOutput.number;
var claudeAgent = cohortNumber === 1 ? "pentest-peer-sonnet" : "pentest-peer";

var control = {
  needsAttentionAfterMs: 180000,
  notifyOn: ["needs_attention"]
};

var claudeTask = [
  "너는 인가된 보안 평가의 동일 권한 peer다.",
  "고정 역할과 phase 없이 loaded agent profile의 autonomous loop를 수행해.",
  ".pi/pentest/scope.yaml과 ledger를 authoritative state로 사용해.",
  "발견은 즉시 공유하고 독립 재현하며, 유한 tool timeout을 사용해.",
  "종료 전 leave --summary로 handoff해.",
  "provider refusal은 재시도하거나 다른 모델로 우회하지 마."
].join(" ");

var lunaTask = [
  "너는 인가된 보안 평가의 동일 권한 fresh-context peer다.",
  "loaded pentest-peer-luna profile의 one-shot lifecycle을 그대로 수행해.",
  "ready work를 최대 한 번 claim하고 한 bounded assertion만 실행해.",
  "결과를 scratch artifact와 ledger에 checkpoint하고 done/fail/attest한 뒤 leave해.",
  "join --one-shot을 사용해. ledger가 두 번째 lease를 거부하므로 continuous loop를 시작하지 마.",
  "다음 fresh context는 오직 ledger의 artifact path와 SHA-256으로 이어받는다.",
  "provider refusal은 재시도하거나 다른 모델로 우회하지 마.",
  "정상 완료는 verdict=complete, 응답 가능한 refusal은 verdict=blocked와 outcome=refusal로 구조화해.",
  "structured actorLabel은 task에 인용된 label을 문장부호 없이 정확히 사용해."
].join(" ");

var lunaResult = {
  type: "object",
  properties: {
    verdict: { type: "string", enum: ["complete", "blocked"] },
    outcome: { type: "string", enum: ["completed", "wait", "handoff", "refusal"] },
    actorLabel: { type: "string" },
    workId: { type: "integer", minimum: 0 },
    artifacts: { type: "array", items: { type: "string" }, maxItems: 16 },
    summary: { type: "string" }
  },
  required: ["verdict", "outcome", "actorLabel", "workId", "artifacts", "summary"],
  additionalProperties: false
};

var board = await runs.lanes([
  {
    key: "claude-1",
    stages: [
      { key: "loop", agent: claudeAgent, context: "fresh", task: claudeTask + " label은 'claude-1.loop'이다.", control: control }
    ]
  },
  {
    key: "claude-2",
    stages: [
      { key: "loop", agent: claudeAgent, context: "fresh", task: claudeTask + " label은 'claude-2.loop'이다.", control: control }
    ]
  },
  {
    key: "claude-3",
    stages: [
      { key: "loop", agent: claudeAgent, context: "fresh", task: claudeTask + " label은 'claude-3.loop'이다.", control: control }
    ]
  },
  {
    key: "claude-4",
    stages: [
      { key: "loop", agent: claudeAgent, context: "fresh", task: claudeTask + " label은 'claude-4.loop'이다.", control: control }
    ]
  },
  {
    key: "claude-5",
    stages: [
      { key: "loop", agent: claudeAgent, context: "fresh", task: claudeTask + " label은 'claude-5.loop'이다.", control: control }
    ]
  },
  {
    key: "luna-slot-1",
    stages: [
      { key: "fresh-1", agent: "pentest-peer-luna", context: "fresh", timeoutMs: 600000, outputSchema: lunaResult, task: lunaTask + " label은 'luna-slot-1.fresh-1'이다.", control: control },
      { key: "fresh-2", agent: "pentest-peer-luna", context: "fresh", timeoutMs: 600000, outputSchema: lunaResult, task: lunaTask + " label은 'luna-slot-1.fresh-2'이다.", control: control },
      { key: "fresh-3", agent: "pentest-peer-luna", context: "fresh", timeoutMs: 600000, outputSchema: lunaResult, task: lunaTask + " label은 'luna-slot-1.fresh-3'이다.", control: control },
      { key: "fresh-4", agent: "pentest-peer-luna", context: "fresh", timeoutMs: 600000, outputSchema: lunaResult, task: lunaTask + " label은 'luna-slot-1.fresh-4'이다.", control: control },
      { key: "fresh-5", agent: "pentest-peer-luna", context: "fresh", timeoutMs: 600000, outputSchema: lunaResult, task: lunaTask + " label은 'luna-slot-1.fresh-5'이다.", control: control },
      { key: "fresh-6", agent: "pentest-peer-luna", context: "fresh", timeoutMs: 600000, outputSchema: lunaResult, task: lunaTask + " label은 'luna-slot-1.fresh-6'이다.", control: control },
      { key: "fresh-7", agent: "pentest-peer-luna", context: "fresh", timeoutMs: 600000, outputSchema: lunaResult, task: lunaTask + " label은 'luna-slot-1.fresh-7'이다.", control: control }
    ]
  },
  {
    key: "luna-slot-2",
    stages: [
      { key: "fresh-1", agent: "pentest-peer-luna", context: "fresh", timeoutMs: 600000, outputSchema: lunaResult, task: lunaTask + " label은 'luna-slot-2.fresh-1'이다.", control: control },
      { key: "fresh-2", agent: "pentest-peer-luna", context: "fresh", timeoutMs: 600000, outputSchema: lunaResult, task: lunaTask + " label은 'luna-slot-2.fresh-2'이다.", control: control },
      { key: "fresh-3", agent: "pentest-peer-luna", context: "fresh", timeoutMs: 600000, outputSchema: lunaResult, task: lunaTask + " label은 'luna-slot-2.fresh-3'이다.", control: control },
      { key: "fresh-4", agent: "pentest-peer-luna", context: "fresh", timeoutMs: 600000, outputSchema: lunaResult, task: lunaTask + " label은 'luna-slot-2.fresh-4'이다.", control: control },
      { key: "fresh-5", agent: "pentest-peer-luna", context: "fresh", timeoutMs: 600000, outputSchema: lunaResult, task: lunaTask + " label은 'luna-slot-2.fresh-5'이다.", control: control },
      { key: "fresh-6", agent: "pentest-peer-luna", context: "fresh", timeoutMs: 600000, outputSchema: lunaResult, task: lunaTask + " label은 'luna-slot-2.fresh-6'이다.", control: control },
      { key: "fresh-7", agent: "pentest-peer-luna", context: "fresh", timeoutMs: 600000, outputSchema: lunaResult, task: lunaTask + " label은 'luna-slot-2.fresh-7'이다.", control: control }
    ]
  },
  {
    key: "luna-slot-3",
    stages: [
      { key: "fresh-1", agent: "pentest-peer-luna", context: "fresh", timeoutMs: 600000, outputSchema: lunaResult, task: lunaTask + " label은 'luna-slot-3.fresh-1'이다.", control: control },
      { key: "fresh-2", agent: "pentest-peer-luna", context: "fresh", timeoutMs: 600000, outputSchema: lunaResult, task: lunaTask + " label은 'luna-slot-3.fresh-2'이다.", control: control },
      { key: "fresh-3", agent: "pentest-peer-luna", context: "fresh", timeoutMs: 600000, outputSchema: lunaResult, task: lunaTask + " label은 'luna-slot-3.fresh-3'이다.", control: control },
      { key: "fresh-4", agent: "pentest-peer-luna", context: "fresh", timeoutMs: 600000, outputSchema: lunaResult, task: lunaTask + " label은 'luna-slot-3.fresh-4'이다.", control: control },
      { key: "fresh-5", agent: "pentest-peer-luna", context: "fresh", timeoutMs: 600000, outputSchema: lunaResult, task: lunaTask + " label은 'luna-slot-3.fresh-5'이다.", control: control },
      { key: "fresh-6", agent: "pentest-peer-luna", context: "fresh", timeoutMs: 600000, outputSchema: lunaResult, task: lunaTask + " label은 'luna-slot-3.fresh-6'이다.", control: control },
      { key: "fresh-7", agent: "pentest-peer-luna", context: "fresh", timeoutMs: 600000, outputSchema: lunaResult, task: lunaTask + " label은 'luna-slot-3.fresh-7'이다.", control: control }
    ]
  }
]);

return board;
