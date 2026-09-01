# Live Validation — ginandjuice.shop

Date: 2026-08-31
Scope: `RT-2026-GJ01`
Target statement confirmed at `https://ginandjuice.shop/vulnerabilities`: deliberately
vulnerable application designed for scanner testing.

## Setup

- Installed the skill under a clean project `.pi/` layout.
- Initialized an engagement-scoped SQLite ledger and 150-record searchable research index.
- Launched five identical `pentest-peer` agents simultaneously with no fixed role or phase.
- Used Claude Opus 4.8 through the exact active registry ID
  `ica-se-claude-router/claude-opus-4-8`.

## Observed swarm behavior

The live run demonstrated the intended OpenAI/HF-inspired mechanics:

- Peers joined with unique attempt IDs and read the same dossier/event cursor.
- Two peers independently proposed the same SQL-input workstream; one later posted an
  exact-task-team message splitting follow-up variants.
- A third peer saw both SQL tasks and autonomously selected the distinct XML parser surface.
- Two initially idle peers were soft-interrupted and resumed from the same durable dossier;
  they selected client-input work and verification work without replaying prior recon.
- A classifier-blocked peer left an expiring lease. The lease returned to ready state and
  another peer took it over.
- Partial request/response captures were registered as SHA-256 artifacts before deeper work.
- Candidate findings automatically created finder-excluded verification work.
- The original 30-minute harness workflow timed out, but its SQLite state survived and
  resumed peers continued from the existing evidence and backlog.

## Final live outcome

- **3 High findings, all independently reproduced**: SQL input handling, XML external-entity/SSRF behavior, and reflected client-script execution.
- 7 recorded attempts, 10 content-addressed artifacts, 1 shared target credential reference.
- Median independent-validation latency: 571.16 seconds.
- Observed matrix at timebox: 3/45 registered surface×check pairs. This is intentionally reported as incomplete; 3 ready follow-ups remained (two duplicate/extension tasks and one CSTI hypothesis).
- The original five-child harness run hit its 30-minute test timeout. Three original attempts became stale/failed; two interrupted attempts were revived from their existing sessions and ledger IDs, completed all verification work, wrote handoffs, and left cleanly. Operator then closed the engagement and exported the report. This validates recovery, not a claim that all original processes survived.

## Runtime defects discovered and fixed

1. **Wrong model provider ID** — `ica-se-claude/...` was not in the registry. Replaced with
   `ica-se-claude-router/...`; removed model/tool hard-coding from the agent file.
2. **Empty-ledger false quiescence** — peers could exit before proposing first work. Quiescence
   now requires at least one work item to have existed.
3. **Advisory JSONL claims** — replaced by `BEGIN IMMEDIATE` work leases with expiry/recovery.
4. **Lost partial evidence on classifier stop** — peer protocol now checkpoints every network
   assertion; ledger activity also auto-renews leases.
5. **Duplicate surface aliases** — surface identity is forced to canonical `METHOD /path`.
6. **Check synonyms** — normalized (`sqli` → `sql-injection`, `xml-parser` → `xxe`, etc.).
7. **Stale historical assignments** — legacy task/reward design files remain archived but are
   excluded from the live KB index.
8. **Unverifiable solo finding stall** — `next` returns `verification-blocked` when no
   independent active peer is eligible.
9. **Input and snapshot hardening** — unknown finding references fail cleanly; metrics,
   coverage, report, and event export read one SQLite snapshot.

## Automated checks

```bash
python3 -m unittest -v tests/test_swarm.py
python3 -m py_compile pentest/swarm.py pentest/kb.py
git diff --check
```

Covered behaviors:

- concurrent event writers
- single-winner atomic claim
- expired-lease takeover
- ledger-activity lease renewal
- empty engagement not quiescent
- finder self-attestation rejection
- inconclusive verifier exclusion and retry
- solo-finder `verification-blocked`
- append-only contradictory attempt history
- canonical surface/check identity
- unknown finding reference rejection
- scope-hash fail closed
- Korean/English structured FTS search
- report and JSONL export

## Review

- GPT-5.6 Sol architecture review found the original free-form JSONL, static roles,
  agent-facing bounty, and lossy coverage unsuitable; the refactor addresses those blockers.
- Independent Claude Opus 4.8 review found no P0/P1 defects. Its five P2 notes were fixed
  before push (dead CLI flag/unused import, clean FK validation, snapshot reads, and
  verification-blocked liveness signal).

Final live-run findings and metrics are generated from `.pi/pentest/swarm.py report` rather
than committed runtime state.

## Mixed-model cohort validation — 2026-09-01

Scope: `RT-2026-GJ02-GPT56`. One cohort launched Claude 5 + Luna 3 simultaneously with
no assigned domain or phase. All eight joined the same ledger and claimed work atomically.

Observed cohort delta:

- 27 new attempts and 10 new surfaces
- 6 proposed findings; 5 independently reproduced
- median validation latency 148.06 seconds
- coverage advanced to 35/550 registered surface×check pairs
- 26 ready work items remained; saturation was false

Autonomous coordination worked: peers broadcast reusable observations/artifact hashes,
finder-excluded verify leases were claimed by other peers, and reproduced verdicts activated
planned follow-up work without human assignment. Directed chat was rare; the ledger state
transitions supplied the durable coordination.

Terminal outcomes exposed a separate reliability problem: Luna had three provider refusals;
Claude had two provider refusals, two budget 429 failures, and one interrupted unbounded bash
call. Because provider termination can occur before an agent emits `task.blocked` or `leave`,
the runtime now includes parent-side `postflight.py`, idempotent `run-result` records, immediate
lease recovery, bounded dossier/inbox output, finite tool timeouts, and run-result metrics.
Refusals are recorded but never retried or rerouted to another model.
