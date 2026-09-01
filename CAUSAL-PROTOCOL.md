# Causal Collaboration Protocol

This is the v3.6 causal-integrity and scheduler-observation foundation for the Evidence-Graph Swarm.
SQLite domain tables
remain authoritative; typed causal events explain why state changed and let fresh peers consume only
relevant provenance. Evidence Graph entities and adaptive scheduling are still deliberately absent.
`emit` remains a legacy escape hatch, especially for `task.blocked`.

## Typed assertion envelope

Every typed command records:

- schema version
- trace, causation, and correlation IDs
- workstream
- subject and evidence refs
- confidence
- falsifiers and conditions
- superseded event
- structured next actions

Supported commands:

```text
observe · hypothesize · request · respond
challenge · decide · handoff · synthesize
```

All commands use the same bounded envelope:

```bash
python3 .pi/pentest/swarm.py observe \
  --agent "$AGENT" --work "$WORK" --workstream order-authz \
  --claim "non-owner response differs from the owner response" \
  --confidence 0.72 \
  --subjects '["surface:GET /orders/{id}","work:42"]' \
  --evidence '["sha256:4b9f..."]' \
  --conditions '{"role":"user","owner":"different-account"}'
```

Typed refs use `kind:value`. `subject_refs` remain extensible for future graph subjects.
`evidence_refs` are fail-closed and resolve only the registry `sha256`, `artifact`, `event`, `work`,
`attempt`, `finding`, and `credential`. Unknown proof prefixes are rejected live and during strict
replay; future graph evidence types must be added explicitly.

### Hypothesis and falsifier

```bash
python3 .pi/pentest/swarm.py hypothesize \
  --agent "$AGENT" --work "$WORK" --workstream order-authz \
  --claim "object authorization may ignore the session owner" \
  --subjects '["surface:GET /orders/{id}"]' \
  --falsifiers '["both responses are the same generic error template"]' \
  --caused-by 81
```

New v3.7 causal envelopes use storage `schema_version=2`; version 1 replay rows are grandfathered.
A hypothesis or hypothesis-kind next action must cite an artifact, attempt, finding, observation, or
recursively evidence-backed causal ancestor. A credential grounds work only when linked to an evidence-backed
finding; a bare credential or free-form legacy event does not. Empty engagements receive
deterministic baseline experiment work instead of speculative hypotheses.

### Atomic request → work transition

`request` and `challenge` require at least one structured next action. Event and work creation commit in
one SQLite transaction. A canonical fingerprint covers workstream, kind, normalized payload,
diversity key, gain/cost metadata, revisit trigger, and prerequisites. A duplicate global work key is
reused only when its fingerprint matches; otherwise the entire transition rolls back with
`work-key-conflict`.

```bash
python3 .pi/pentest/swarm.py request \
  --agent "$AGENT" --work "$WORK" --workstream order-authz \
  --claim "fresh non-owner session must discriminate the hypothesis" \
  --subjects '["surface:GET /orders/{id}"]' --caused-by 82 \
  --next-actions '[{
    "key":"orders:fresh-non-owner",
    "title":"Replay with a fresh non-owner session",
    "kind":"experiment",
    "priority":90,
    "diversity_key":"fresh-session",
    "expected_information_gain":0.9,
    "estimated_cost":1.5,
    "revisit_trigger":{"credential":"second-user"}
  }]'
```

A `respond` must cause from a request. A `decide` must cause from a challenge or hypothesis and may
supersede only a hypothesis/challenge in the same trace and workstream. Correlation is inherited from
the cause. Every typed event has a trace; a legacy parent without one starts a new trace while staying
linked by `causation_id`. An event attached to leased work cannot override that work's stream; branching
uses `next_actions` to create child work. `synthesize` requires at least two evidence refs and updates
the durable workstream snapshot.

## Work metadata

Manual `task-add` also accepts:

```text
--workstream
--diversity-key
--estimated-cost
--information-gain
--revisit-trigger JSON
```

The current scheduler still preserves the proven priority/FIFO behavior. These fields are collected
now so replay can evaluate a later adaptive-frontier policy without changing live scheduling first.

## Claim provenance

Every lease creates a durable `work_claims` generation with its claim event, configured lease/no-progress
windows, `last_progress_at`, optional brief timestamp, and terminal outcome. Partial unique indexes enforce
one active claim per actor and one per work. Repeated `next` calls return the same `active-lease` without
renewal. Expiry and no-progress are distinct: deadline reaping yields `expired`; a live lease with no
material progress yields `stalled`. Bookkeeping, inbox, brief, and heartbeat never renew. Host execution,
current-claim artifact/attempt, typed assertion, finding, or attestation may renew only a still-live claim.
No activity resurrects an ended generation.

Causal events, work-artifact associations, attempts, findings, and attestations record `claim_id` when
they mutate leased work. Legacy rows remain nullable rather than receiving invented provenance.

## Task-local brief

Normal startup is one control-plane call:

```text
peer-start → join + proxy decision + bootstrap/claim + brief
```

```bash
python3 .pi/pentest/swarm.py peer-start --label '<assigned-label>' --continuous \
  --lease 180 --no-progress 120 --brief-tokens 1400
```

Or refresh an owned lease:

```bash
python3 .pi/pentest/swarm.py brief --agent "$AGENT" --work "$WORK" \
  --max-tokens 2200 --after "$CURSOR"
```

The brief begins with any uncheckpointed `partial_experiments` and their exact remaining action, then
contains the current work, workstream snapshot, confirmed facts, live hypotheses, negative paths,
relevant artifacts/findings/attestations, referenced credentials, contradictions, open questions, and
recommended experiments. A takeover must checkpoint existing response evidence instead of repeating
its request. Every assertion retains its event/evidence refs. Events and
artifacts from unrelated workstreams are excluded. Oversized core values become hash-addressed refs
rather than arbitrary string truncation.

## Atomic execution and semantic checkpoint

`exec-http` commits an ExperimentSpec before target I/O, fences immediately before sending, then records
bounded request/response artifacts, canonical surface/check, and a claim-linked `partial|error` attempt.
The runner never converts an HTTP status into a vulnerability verdict. A duplicate request fingerprint
returns the existing experiment rather than sending again.

`checkpoint` validates that the experiment belongs to the same work and has durable attempt/artifacts,
then creates one existing typed causal assertion, structured next work, experiment checkpoint, and
work/claim completion in one transaction. A new generation may interpret an old generation's durable
experiment; its new assertion/completion uses the new claim while original I/O provenance remains old.
Malformed evidence or next work rolls back the entire checkpoint. Coverage must still match its exact
surface/check and verification must still finish through independent attestation.

## Operator-only communication metrics

```bash
python3 .pi/pentest/swarm.py communication-metrics
```

Reports:

- typed event adoption ratio
- actionable event ratio
- median time to cross-peer consumption
- causal unlock count
- duplicate spawn rate
- workstream herding index
- challenge-resolution latency

These are aggregate operator metrics. They are not shown as peer scores, ranks, points, or rewards.

## Scheduler decision observation

The live scheduler remains `fifo-v1`. Each successful new claim records, in the same transaction:

- the selected work and resulting claim
- every ready/leased decision-time candidate
- eligibility or an explicit exclusion reason
- priority, age, gain/cost, prior generations, verification urgency
- active workstream/diversity collisions, parent depth, and revisit-trigger state
- a canonical candidate-set SHA-256

This records why FIFO selected a task without changing selection behavior.

## Deterministic replay

```bash
python3 .pi/pentest/swarm.py replay-export --strict
python3 .pi/pentest/replay.py --events \
  .pi/pentest/engagements/<id>/board/replay.json --policy fifo-v1 --strict
# ranking-only alternatives:
# verify-first-v1 | gain-per-cost-v1 | diversity-aware-v1
```

The canonical export contains ordered causal events, work claims, fingerprints, experiments,
provider circuits, execution metrics, and the work/workstream/evidence projection with no export timestamp. Repeated exports of one snapshot are
byte-identical. Strict validation reruns typed-body protocol validation and rejects missing or forward
causal refs, unknown evidence prefixes, invalid response/decision ancestry or supersession scope,
claim/work/actor mismatches, experiment→attempt/artifact/checkpoint drift, fingerprint drift,
candidate-set hash drift, invalid provider circuits, ineligible selections, and broken work provenance.
Alternative policies rank only the candidates that existed at each historical
decision. They do not infer counterfactual outcomes. Relational SQLite remains authoritative; this is
a coordination replay, not a claim that secret values or arbitrary target state can be reconstructed.

## SwarmBench before Evidence Graph

`SWARMBENCH.md` defines controlled solo, isolated-parallel, and shared-swarm conditions. Deterministic
aggregation separates parallelism gain from collaboration gain and preserves unavailable usage as
`null`. At least three reset repetitions per condition are required before making effectiveness claims.

## Deferred Evidence-Graph increments

Deliberately deferred until benchmark and decision-replay data can justify them:

1. entity/relation/capability/permission projection
2. applicability-weighted coverage
3. bounded diverse pivot fan-out
4. adaptive frontier scheduler
5. ephemeral synthesis/steward leases
6. task-shape-driven cohort composition

Do not implement these until atomic assertion completion reaches at least 80% and controlled
Solo/isolated/shared repetitions show a collaboration bottleneck they would address.
