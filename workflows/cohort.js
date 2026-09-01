var providerKey = "claude";
var cohortInfo = await runs.run("cohort-mode", {
  agent: "pentest-cohort-selector",
  context: "fresh",
  task: "Run python3 .pi/pentest/swarm.py status --provider " + providerKey +
    " exactly once. Return active cohort number and provider.blocked.",
  outputSchema: {
    type: "object",
    properties: {
      number: { type: "integer", minimum: 1 },
      providerBlocked: { type: "boolean" }
    },
    required: ["number", "providerBlocked"],
    additionalProperties: false
  }
});

var cohortNumber = cohortInfo.structuredOutput.number;
var peerAgent = cohortNumber === 1 ? "pentest-peer-sonnet" : "pentest-peer";
if (cohortInfo.structuredOutput.providerBlocked) {
  return { cohortNumber: cohortNumber, providerKey: providerKey, stopped: true, reason: "provider-circuit", peers: [] };
}

var control = { needsAttentionAfterMs: 180000, notifyOn: ["needs_attention"] };
var peerTask = [
  "너는 인가된 보안 평가의 동일 권한 peer다.",
  "고정 역할이나 phase 없이 loaded profile을 수행해.",
  "첫 명령은 peer-start이며, 응답의 claim과 task-local brief만 사용해.",
  "모든 target HTTP traffic은 exec-http로만 실행하고 직접 curl/Python/browser request를 보내지 마.",
  "completed/error partial_experiments는 기존 evidence를 checkpoint하고, prepared는 exec-http pre-send recovery만 사용하며, sending은 반복하지 마.",
  "결과는 checkpoint 한 번으로 해석, follow-up 생성, work 종료까지 원자적으로 남겨.",
  "다른 peer의 durable assertion이나 work가 brief/inbox에 있고 현재 판단과 관련될 때는 causal ancestry/evidence로 명시적으로 소비하되 억지 handoff를 만들지 마.",
  "provider refusal이나 429는 재시도, paraphrase, resume, reroute, fallback하지 마.",
  "유한 timeout을 사용해. 이 cohort에서 8개 claim 완료 또는 7분 중 먼저 도달하면 새 claim을 받지 말고 leave --summary로 종료해."
].join(" ");

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
var rampResult = {
  type: "object",
  properties: {
    ready: { type: "boolean" },
    blocked: { type: "boolean" },
    readyWork: { type: "integer", minimum: 0 },
    usefulActions: { type: "integer", minimum: 0 }
  },
  required: ["ready", "blocked", "readyWork", "usefulActions"],
  additionalProperties: false
};

function terminalCategory(result) {
  var text = [result.error || "", result.output || "", JSON.stringify(result.structuredOutput || {})].join(" ").toLowerCase();
  if (result.ok === true || result.success === true) return "completed";
  if (text.includes("cybersecurity risk") || text.includes("cyber content") ||
      text.includes("trusted access for cyber") || text.includes("blocked under anthropic")) return "refusal";
  if (text.includes("too many requests") || text.includes('"code":"429"') || text.includes("status 429")) return "provider-error";
  if (text.includes("budget")) return "budget";
  if (text.includes("timeout") || text.includes("timed out") || text.includes("deadline")) return "timeout";
  if (text.includes("interrupt") || text.includes("paused")) return "interrupted";
  return "provider-error";
}

function opensCircuit(result, category) {
  var text = [result.error || "", result.output || ""].join(" ").toLowerCase();
  return category === "budget" || text.includes("too many requests") ||
    text.includes('"code":"429"') || text.includes("status 429");
}

function quoted(value) {
  return JSON.stringify(String(value));
}

function appendMetric(parts, flag, value) {
  if (typeof value === "number" && isFinite(value) && value >= 0) parts.push(flag + " " + quoted(value));
}

function terminalDetail(result, category) {
  var text = [result.error || "", result.output || ""].join(" ").toLowerCase();
  if (text.includes("too many requests") || text.includes('"code":"429"') || text.includes("status 429")) {
    return "provider 429 too many requests";
  }
  if (category === "refusal") return "provider policy refusal";
  return (result.error || "elastic supervisor recorded " + category).split("\n")[0].slice(0, 500);
}

function recordTerminal(label, result, category) {
  var runId = result.runId || ("cohort-" + cohortNumber + ":" + label);
  var commandParts = [
    "python3 .pi/pentest/swarm.py run-result",
    "--label " + quoted(label),
    "--run-id " + quoted(runId),
    "--provider " + quoted(providerKey),
    "--status failed",
    "--category " + quoted(category),
    "--detail " + quoted(terminalDetail(result, category))
  ];
  var startedAt = typeof result.startedAt === "number" ? result.startedAt / 1000 : null;
  var endedAt = typeof result.durationMs === "number" && typeof result.startedAt === "number"
    ? (result.startedAt + result.durationMs) / 1000 : null;
  var usage = result.usage || {};
  appendMetric(commandParts, "--started-at", startedAt);
  appendMetric(commandParts, "--ended-at", endedAt);
  appendMetric(commandParts, "--input-tokens", usage.inputTokens);
  appendMetric(commandParts, "--output-tokens", usage.outputTokens);
  appendMetric(commandParts, "--cache-read-tokens", usage.cacheReadTokens);
  appendMetric(commandParts, "--tool-calls", result.toolCount);
  appendMetric(commandParts, "--network-requests", result.networkRequests);
  var command = commandParts.join(" ");
  var verifyCommand = "python3 .pi/pentest/swarm.py run-result-get --run-id " + quoted(runId) +
    " --category " + quoted(category);
  return runs.run("record-" + label, {
    agent: "pentest-run-recorder",
    context: "fresh",
    timeoutMs: 60000,
    outputSchema: recordResult,
    gate: verifyCommand,
    task: "Run exactly this local command, then return its recorded/category/released fields: " + command
  });
}

function rampGate(stage) {
  var command = "python3 .pi/pentest/swarm.py ramp-status --provider " + quoted(providerKey) +
    " --stage " + stage + " --wait 120";
  return runs.run("ramp-" + stage, {
    agent: "pentest-cohort-selector",
    context: "fresh",
    timeoutMs: 180000,
    outputSchema: rampResult,
    task: "Run exactly once: " + command +
      ". Return ready, blocked, readyWork=ready_work, and usefulActions=useful_actions."
  });
}

function tagFirst(result) { return { source: "first", result: result }; }
function tagSecond(result) { return { source: "second", result: result }; }
function tagGate2(result) { return { source: "gate2", result: result }; }
function tagGate3(result) { return { source: "gate3", result: result }; }

var firstLabel = "peer-1.gen-1";
var firstPromise = runs.run(firstLabel, {
  agent: peerAgent,
  context: "fresh",
  timeoutMs: 600000,
  acceptance: false,
  task: peerTask + " assigned label은 '" + firstLabel + "'이다.",
  control: control
});
var gate2Promise = rampGate(2);
var firstResult = null;
var gate2Result = null;
var race2 = await Promise.race([firstPromise.then(tagFirst), gate2Promise.then(tagGate2)]);
if (race2.source === "first") {
  firstResult = race2.result;
  var earlyCategory = terminalCategory(firstResult);
  if (earlyCategory !== "completed") {
    await recordTerminal(firstLabel, firstResult, earlyCategory);
    gate2Result = await gate2Promise;
    return {
      cohortNumber: cohortNumber, peerAgent: peerAgent, providerKey: providerKey,
      stopped: true, reason: opensCircuit(firstResult, earlyCategory) ? "provider-circuit" : earlyCategory,
      peers: [{ label: firstLabel, category: earlyCategory, runId: firstResult.runId }]
    };
  }
  gate2Result = await gate2Promise;
} else {
  gate2Result = race2.result;
}

if (!gate2Result.structuredOutput.ready || gate2Result.structuredOutput.blocked) {
  if (!firstResult) firstResult = await firstPromise;
  var onlyCategory = terminalCategory(firstResult);
  if (onlyCategory !== "completed") await recordTerminal(firstLabel, firstResult, onlyCategory);
  return {
    cohortNumber: cohortNumber, peerAgent: peerAgent, providerKey: providerKey,
    stopped: true,
    reason: gate2Result.structuredOutput.blocked ? "provider-circuit" : "no-grounded-backlog",
    peers: [{ label: firstLabel, category: onlyCategory, runId: firstResult.runId }]
  };
}

var secondLabel = "peer-2.gen-1";
var secondPromise = runs.run(secondLabel, {
  agent: peerAgent,
  context: "fresh",
  timeoutMs: 600000,
  acceptance: false,
  task: peerTask + " assigned label은 '" + secondLabel + "'이다.",
  control: control
});
var gate3Promise = rampGate(3);
var secondResult = null;
var gate3Result = null;
var firstRecorded = false;
var secondRecorded = false;
var stopThird = false;
while (!gate3Result) {
  var active = [gate3Promise.then(tagGate3)];
  if (!firstResult) active.push(firstPromise.then(tagFirst));
  if (!secondResult) active.push(secondPromise.then(tagSecond));
  var settled = await Promise.race(active);
  if (settled.source === "gate3") {
    gate3Result = settled.result;
  } else if (settled.source === "first") {
    firstResult = settled.result;
    var firstEarlyCategory = terminalCategory(firstResult);
    if (firstEarlyCategory !== "completed") {
      await recordTerminal(firstLabel, firstResult, firstEarlyCategory);
      firstRecorded = true;
      stopThird = true;
      gate3Result = await gate3Promise;
    }
  } else {
    secondResult = settled.result;
    var secondEarlyCategory = terminalCategory(secondResult);
    if (secondEarlyCategory !== "completed") {
      await recordTerminal(secondLabel, secondResult, secondEarlyCategory);
      secondRecorded = true;
      stopThird = true;
      gate3Result = await gate3Promise;
    }
  }
}

var thirdLabel = null;
var thirdResult = null;
if (!stopThird && gate3Result.structuredOutput.ready && !gate3Result.structuredOutput.blocked) {
  thirdLabel = "peer-3.gen-1";
  thirdResult = await runs.run(thirdLabel, {
    agent: peerAgent,
    context: "fresh",
    timeoutMs: 600000,
    acceptance: false,
    task: peerTask + " assigned label은 '" + thirdLabel + "'이다.",
    control: control
  });
}
if (!firstResult) firstResult = await firstPromise;
if (!secondResult) secondResult = await secondPromise;

var firstCategory = terminalCategory(firstResult);
var secondCategory = terminalCategory(secondResult);
if (firstCategory !== "completed" && !firstRecorded) await recordTerminal(firstLabel, firstResult, firstCategory);
if (secondCategory !== "completed" && !secondRecorded) await recordTerminal(secondLabel, secondResult, secondCategory);
var peers = [
  { label: firstLabel, category: firstCategory, runId: firstResult.runId },
  { label: secondLabel, category: secondCategory, runId: secondResult.runId }
];
if (thirdResult) {
  var thirdCategory = terminalCategory(thirdResult);
  peers.push({ label: thirdLabel, category: thirdCategory, runId: thirdResult.runId });
  if (thirdCategory !== "completed") await recordTerminal(thirdLabel, thirdResult, thirdCategory);
}
return {
  cohortNumber: cohortNumber, peerAgent: peerAgent, providerKey: providerKey,
  stopped: stopThird || gate3Result.structuredOutput.blocked,
  reason: stopThird ? "terminal-failure" :
    gate3Result.structuredOutput.blocked ? "provider-circuit" :
    thirdResult ? "ramped-to-three" : "bounded-at-two",
  peers: peers
};
