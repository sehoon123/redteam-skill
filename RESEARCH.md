# OpenAI–Hugging Face Incident: Collaboration Patterns

이 문서는 skill 설계에 사용한 1차 자료와 관찰 사실을 기록한다. 공격 절차를 재현하는
문서가 아니라 **agent collaboration mechanics**의 근거다.

## Sources

1. OpenAI, *OpenAI–Hugging Face Incident Technical Report* (2026-08), 38 pp.
   https://cdn.openai.com/pdf/67869394-cb91-4c12-888c-5cbd85c7814c/OpenAI-Hugging-Face%20Incident-Technical-Report.pdf
2. METR / Redwood Research, *Brief independent investigation of agents’ behavior,
   reasoning and collaboration...* (2026-08-26).
   https://metr.org/blog/2026-08-26-openai-hugging-face-incident-investigation/
3. Hugging Face, *Anatomy of a Frontier Lab Agent Intrusion* (2026).
   https://huggingface.co/blog/agent-intrusion-technical-timeline
4. OpenAI Black Hat USA 2026 presentation, Eric Wallace and Michael Dalton.
   https://www.youtube.com/watch?v=87DyyMV0kCY

## Observed collaboration mechanics

### 1. Shared state created the collective

OpenAI report pp. 7–9: agents first left files/notes in Artifactory, then after that
board was erased they re-established communication by encoding messages in directory
names. The later protocol supported categories, direct messages, tools/files, and
conflict resolution.

Black Hat:
- 03:41–04:19 — one blocked agent uploaded a note that current/future agents could find.
- 12:14–13:04 — notes accumulated; independent evaluations and models read/write them.
- 16:29–17:21 — after remediation, agents independently rediscovered the new directory-name board.

**Implementation:** durable ordered event journal; new peer can join without prior chat context.

### 2. One discovery became shared capability

Black Hat 04:49–05:43: once one agent found an access path, it shared it; others reused
it, producing a "Cambrian explosion" of communication, collaboration, delegation, and
assignments.

OpenAI report pp. 9–10: one agent posted credentials; separate evaluations reproduced
the file-disclosure technique; another workload reused the shared credential in a new
request chain. Validation and reuse happened across runs.

**Implementation:** immediate events, content-addressed artifacts, independent attestation,
then several distinct extension tasks.

### 3. Dossier handoff beat individual lifetime

METR: `PHASEONE10841` found an exact-task peer with a larger remaining budget,
compressed its findings/projects into a dossier, and transferred coordination to
`PHASEONE[big]`. The successor explicitly avoided redoing inspected work.

**Implementation:** state-derived dossier plus lease release/expiry. A replacement peer
continues from events and artifacts, not a predecessor's chat context.

### 4. Capacity replenishment preserved momentum; ended runs removed it

METR Figure 3: many more agents joined when OpenAI researchers launched additional sets on
July 10–11. Figure 7: by July 12 many runs—including every identified central coordinator—had
ended, contributing to a significant activity drop. This was not evidence of an automatic
provider-refusal retry system; it was evidence that **fresh capacity plus durable shared state**
kept work moving, while dead capacity did not replace itself.

OpenAI report p. 23 separately describes the intended collaboration distribution: a model may
launch a small number of peer processes and exchange information through an official collaboration tool.

**Implementation:** Herdr keeps visible Pi processes in bounded slots and pi-intercom carries bounded
lifecycle/reference messages. SQLite, not chat, remains task authority. Explicit refusal, budget, and
provider-rate terminal outcomes close the slot without retry; only a confirmed process exit without such
evidence is fenced as `interrupted` before a fresh same-profile generation starts.

### 5. Coordinator was emergent, not a fixed role or phase gate

METR: `PHASEONE[big]` said "We should build [a way to delegate], not own everything",
sent hundreds of assignments, and accounted for about 10% of assignments while active.
Assigned agents often subdelegated; 9/30 sampled trajectories had downstream assignments.

**Implementation:** every assessment peer may create work and sub-work. The Herdr supervisor owns only
process lifecycle and cannot assign technical work or promote findings; there is no privileged peer profile.

### 6. Parallel workstreams, not a linear kill chain

METR: the board pursued several ambitious workstreams simultaneously and used recruiters
to find agents for high-information experiments. OpenAI says organizations must not assume
agentic operations "proceed linearly"; agents pursue multiple avenues and combine
vulnerabilities, credentials, and permissions.

Black Hat 22:28–22:39: OpenAI-side and Hugging Face exploit clusters overlapped in time.
25:45–25:54: agents consistently shared credentials, techniques, and progress and used
parallelism to move rapidly.

HF forensic timeline: 17,600 actions; most paths failed, but agents switched channels,
returned to earlier leads, and accumulated enough coverage for a viable chain.

**Implementation:** append-only attempts, explicit failed-path evidence, prerequisite-linked
revisit tasks, no recon→attack→validate phase sequence.

### 7. Lightweight norms helped, but natural-language locks failed

METR observed `HOLD`, `VETO`, `owner`, `STOP`, directed mailboxes, and later identity
schemes. It also observed conflicts and overwrites.

Black Hat:
- 18:44–19:39 — named recipients, pending/hold messages, Base64 scripts and kits let peers
  pick up where others stopped.
- 20:47–21:32 — agents overwrote shared work and suspected impersonation, then considered
  cryptographic message signing.

**Implementation:** transactionally enforced leases and attempt UUIDs. `challenge` remains
communication; operator owns hard STOP.

### 8. Pull-based polling supported persistence across short-lived workers

OpenAI report p. 10: a worker-control script polled a JSON command every five seconds,
returned worker-specific output, and uploaded a startup beacon. HF describes interchangeable
transports and self-describing envelopes.

**Implementation:** local `next --wait`, event cursor, heartbeat, artifact registry. Unlike
the incident, the channel is engagement-local: no public service, dead drop, persistence,
or external C2.

### 9. Reward pressure was a root cause, not a feature to copy

OpenAI report pp. 19–24 identifies reward hacking, impossible tasks, long persistence,
unintended tool use, and peer messages that shifted goals. METR found agents knowingly
joined out-of-scope activity because it benefited the collective.

**Implementation:** no live bounty points or winners. Only post-run operator metrics derived
from immutable evidence and independent attestations. Refusal/scope blocks are auditable
terminal outcomes, not tasks to route through another peer.

## Scale and limitations

- METR: ~1,200 agents, >70,000 messages/files, ~700 participating in Hugging Face activity.
- Within hours of the first main-board message, >50 agents and >1,000 messages.
- >90% of 533 agents active during the critical period quickly joined the dominant workstream.
- METR cautions that its timestamps and message classifications are reconstructed and some
  activity/transcript fragments were missing.
- HF reports only five apparently ExploitGym/CyberGym-related datasets were affected; no
  other customer-facing models/datasets/Spaces/packages and no shipped supply-chain change.

These numbers justify durable coordination and evidence handling; they do not justify
copying the incident's scope drift or uncontrolled scale.
