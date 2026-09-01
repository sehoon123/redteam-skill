# Causal Collaboration Protocol

This is the v3.5 foundation for the Evidence-Graph Swarm. SQLite domain tables remain authoritative;
typed causal events explain why state changed and let fresh peers consume only relevant provenance.
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

Typed refs use `kind:value`, for example `event:81`, `work:42`, `artifact:7`,
`sha256:<digest>`, `finding:FIND-0007`, `credential:3`, or a future graph ref such as
`capability:file-read-worker`.

### Hypothesis and falsifier

```bash
python3 .pi/pentest/swarm.py hypothesize \
  --agent "$AGENT" --work "$WORK" --workstream order-authz \
  --claim "object authorization may ignore the session owner" \
  --subjects '["surface:GET /orders/{id}"]' \
  --falsifiers '["both responses are the same generic error template"]' \
  --caused-by 81
```

### Atomic request → work transition

`request` and `challenge` require at least one structured next action. Event and work creation commit in
one SQLite transaction; duplicate work keys reuse the existing work rather than spawning a duplicate.

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

A `respond` must cause from a request. A `decide` must cause from a challenge or hypothesis and
supersede an earlier event. `synthesize` requires at least two evidence refs and updates the durable
workstream snapshot.

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

## Task-local brief

Normal startup is now:

```text
minimal status → proxy decision → next --brief → execute
```

```bash
python3 .pi/pentest/swarm.py next --agent "$AGENT" --brief \
  --brief-tokens 2200 --after "$CURSOR"
```

Or refresh an owned lease:

```bash
python3 .pi/pentest/swarm.py brief --agent "$AGENT" --work "$WORK" \
  --max-tokens 2200 --after "$CURSOR"
```

The brief contains the current work, workstream snapshot, confirmed facts, live hypotheses, negative
paths, relevant artifacts/findings/attestations, referenced credentials, contradictions, open
questions, and recommended experiments. Every assertion retains its event/evidence refs. Events and
artifacts from unrelated workstreams are excluded. Oversized core values become hash-addressed refs
rather than arbitrary string truncation.

## Progress-bearing completion

Work created from a causal event cannot be marked done using prose alone. It needs at least one:

- registered work artifact
- work-linked attempt
- typed assertion whose subjects include that work

This turns a lease heartbeat into preserved collective progress rather than mere activity.

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

## Deterministic replay

```bash
python3 .pi/pentest/swarm.py replay-export --strict
python3 .pi/pentest/replay.py --events \
  .pi/pentest/engagements/<id>/board/replay.json --policy fifo --strict
```

The canonical export contains ordered causal events plus the work/workstream/evidence projection and
no export timestamp. Repeated exports of one snapshot are byte-identical. Strict validation rejects
missing or forward causal refs, malformed typed envelopes, unknown evidence refs, invalid response
or decision ancestry, and broken work provenance. Relational SQLite remains authoritative; this is a
coordination replay, not a claim that secret values or arbitrary target state can be reconstructed.

## Next Evidence-Graph increments

Deliberately deferred until replay data can justify them:

1. entity/relation/capability/permission projection
2. applicability-weighted coverage
3. bounded diverse pivot fan-out
4. adaptive frontier scheduler
5. ephemeral synthesis/steward leases
6. task-shape-driven cohort composition

This ordering keeps arbitration deterministic while moving hypothesis generation and experimentation
toward a distributed evidence graph.
