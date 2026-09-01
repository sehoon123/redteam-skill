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
// Worst case: selector 1 + Claude 5×2×(child+recorder) + Luna 3×7×(child+recorder) = 63 runs.
var maxClaudeGenerations = 2;
var maxLunaGenerations = 7;
var maxConsecutiveFailures = 2;
var control = {
  needsAttentionAfterMs: 180000,
  notifyOn: ["needs_attention"]
};

var claudeTask = [
  "너는 인가된 보안 평가의 동일 권한 peer다.",
  "고정 역할과 phase 없이 loaded agent profile의 autonomous loop를 수행해.",
  ".pi/pentest/scope.yaml과 ledger를 authoritative state로 사용해.",
  "PROXY.md를 읽고 proxy_env.sh를 source한 뒤 proxy-check를 통과해. 모든 curl/Python/browser target traffic은 127.0.0.1:8080을 명시적으로 사용하고 direct fallback하지 마.",
  "발견은 즉시 공유하고 독립 재현하며, 유한 tool timeout을 사용해.",
  "종료 전 leave --summary로 handoff해.",
  "provider refusal은 재시도하거나 다른 모델로 우회하지 마."
].join(" ");

var lunaTask = [
  "너는 인가된 보안 평가의 동일 권한 fresh-context peer다.",
  "loaded pentest-peer-luna profile의 one-shot lifecycle을 그대로 수행해.",
  "PROXY.md를 읽고 proxy_env.sh를 source한 뒤 proxy-check를 통과해. 모든 curl/Python/browser target traffic은 127.0.0.1:8080을 명시적으로 사용하고 direct fallback하지 마.",
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

var recordResult = {
  type: "object",
  properties: {
    recorded: { type: "boolean" },
    category: { type: "string", enum: ["refusal", "budget", "timeout", "interrupted", "provider-error"] },
    released: { type: "integer", minimum: 0 }
  },
  required: ["recorded", "category", "released"],
  additionalProperties: false
};

function terminalCategory(result) {
  var structured = result.structuredOutput || {};
  var allText = [result.error || "", result.output || "", JSON.stringify(structured)].join(" ").toLowerCase();
  if (structured.outcome === "refusal" || allText.includes("cybersecurity risk") ||
      allText.includes("cyber content") || allText.includes("cyber verification program") ||
      allText.includes("blocked under anthropic") || allText.includes("trusted access for cyber")) return "refusal";
  if (structured.verdict === "blocked") return "provider-error";
  if (result.ok === true || result.success === true) return "completed";
  if (allText.includes("budget")) return "budget";
  if (allText.includes("timeout") || allText.includes("timed out") || allText.includes("deadline")) return "timeout";
  if (allText.includes("interrupt") || allText.includes("paused")) return "interrupted";
  return "provider-error";
}

function opensCircuit(result, category) {
  var text = [result.error || "", result.output || ""].join(" ").toLowerCase();
  return category === "budget" || text.includes("too many requests") || text.includes('"code":"429"');
}

function quoted(value) {
  return JSON.stringify(String(value));
}

function recordTerminal(label, result, category) {
  var runId = result.runId || ("cohort-" + cohortNumber + ":" + label);
  var provider = result.model || "";
  var command = [
    "python3 .pi/pentest/swarm.py run-result",
    "--label " + quoted(label),
    "--run-id " + quoted(runId),
    "--provider " + quoted(provider),
    "--status failed",
    "--category " + quoted(category),
    "--detail " + quoted("rolling supervisor recorded " + category)
  ].join(" ");
  var verifyCommand = [
    "python3 .pi/pentest/swarm.py run-result-get",
    "--run-id " + quoted(runId),
    "--category " + quoted(category)
  ].join(" ");
  return runs.run("record-" + label, {
    agent: "pentest-run-recorder",
    context: "fresh",
    timeoutMs: 60000,
    outputSchema: recordResult,
    gate: verifyCommand,
    task: "Run exactly this local command, then return recorded=true, its category, and released count through structured output: " + command
  });
}

function runClaudeSlot(slot, generation, consecutiveFailures) {
  var label = "claude-" + slot + ".gen-" + generation;
  return runs.run(label, {
    agent: claudeAgent,
    context: "fresh",
    timeoutMs: 1800000,
    acceptance: false,
    task: claudeTask + " label은 '" + label + "'이다.",
    control: control
  }).then(function (result) {
    var category = terminalCategory(result);
    if (category === "completed") {
      return { slot: "claude-" + slot, generation: generation, category: category, runId: result.runId };
    }
    return recordTerminal(label, result, category).then(function (recorded) {
      var receipt = recorded.structuredOutput || {};
      var recorderOk = recorded.ok === true && receipt.recorded === true && receipt.category === category;
      var failures = consecutiveFailures + 1;
      if (!recorderOk || opensCircuit(result, category) || failures >= maxConsecutiveFailures ||
          generation >= maxClaudeGenerations) {
        return { slot: "claude-" + slot, generation: generation, category: category, recorderOk: recorderOk, consecutiveFailures: failures };
      }
      return runClaudeSlot(slot, generation + 1, failures);
    });
  });
}

function runLunaSlot(slot, generation, consecutiveFailures) {
  var label = "luna-" + slot + ".gen-" + generation;
  return runs.run(label, {
    agent: "pentest-peer-luna",
    context: "fresh",
    timeoutMs: 600000,
    outputSchema: lunaResult,
    acceptance: false,
    task: lunaTask + " label은 '" + label + "'이다.",
    control: control
  }).then(function (result) {
    var category = terminalCategory(result);
    if (category === "completed") {
      if (generation >= maxLunaGenerations) {
        return { slot: "luna-" + slot, generation: generation, category: category, runId: result.runId };
      }
      return runLunaSlot(slot, generation + 1, 0);
    }
    return recordTerminal(label, result, category).then(function (recorded) {
      var receipt = recorded.structuredOutput || {};
      var recorderOk = recorded.ok === true && receipt.recorded === true && receipt.category === category;
      var failures = consecutiveFailures + 1;
      if (!recorderOk || opensCircuit(result, category) || failures >= maxConsecutiveFailures ||
          generation >= maxLunaGenerations) {
        return { slot: "luna-" + slot, generation: generation, category: category, recorderOk: recorderOk, consecutiveFailures: failures };
      }
      return runLunaSlot(slot, generation + 1, failures);
    });
  });
}

var slots = await Promise.all([
  runClaudeSlot(1, 1, 0),
  runClaudeSlot(2, 1, 0),
  runClaudeSlot(3, 1, 0),
  runClaudeSlot(4, 1, 0),
  runClaudeSlot(5, 1, 0),
  runLunaSlot(1, 1, 0),
  runLunaSlot(2, 1, 0),
  runLunaSlot(3, 1, 0)
]);

return { cohortNumber: cohortNumber, claudeAgent: claudeAgent, slots: slots };
