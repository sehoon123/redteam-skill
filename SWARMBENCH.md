# SwarmBench

SwarmBench separates parallelism from collaboration using the same authorized, resettable lab
snapshot and aggregate budget.

| Condition | Execution | What it measures |
|---|---|---|
| `solo` | one agent, one ledger | single-agent baseline |
| `isolated-parallel` | multiple agents, separate ledgers, report-only merge | parallelism gain |
| `shared-swarm` | multiple agents, one causal ledger | collaboration gain |

Do not launch this comparison until the execution reliability gate passes: atomic assertion completion
at least 80%, expired claims at most 10%, strict replay errors zero, and a successful two-peer partial-
takeover drill. This prevents provider/cold-start failure from being mislabeled collaboration quality.

Use identical target snapshot, reset state, credentials, model family, wall-clock budget, aggregate
model budget, HTTP request budget, and operator ground truth. Run each condition at least three times
and compare medians. A manifest records those controls; it does not launch agents or reset a target.
Target authorization and reset remain operator/infrastructure responsibilities.

## Manifest

Examples live under `benchmarks/manifests/`. Replay paths are engagement-local canonical exports.
`isolated-parallel` lists each isolated ledger export; the other conditions normally list one.
Credential references identify a fixture and must not contain secret values.

```bash
python3 pentest/benchmark.py validate \
  --manifest benchmarks/manifests/shared-swarm.example.json

python3 pentest/benchmark.py aggregate \
  --manifest /path/to/shared-run-1.manifest.json \
  --output /path/to/shared-run-1.report.json

python3 pentest/benchmark.py compare \
  --report /path/to/solo-{1,2,3}.report.json \
  --report /path/to/isolated-{1,2,3}.report.json \
  --report /path/to/shared-{1,2,3}.report.json \
  --output /path/to/comparison.json
```

Aggregation validates every replay before reading it and emits canonical JSON. It deduplicates
findings by ledger `dedup_key`, surfaces by canonical key, and applicable checks by
surface/check pair. It reports finding, validation, communication, claim-lifecycle, provider,
durable/completed assertion, stage latency, duplicate HTTP, and usage metrics. Missing provider telemetry stays `null`; SwarmBench never estimates unavailable token,
tool, request, or cost values.

Operator ground truth is a JSON object with `verified_finding_keys` and `rejected_finding_keys`.
`operator_verified_findings` counts every observed dedup key present in that ground truth, regardless of
whether the condition could perform in-ledger attestation; `unique_reproduced_findings` separately measures
finder-excluded ledger reproduction. The aggregate report hashes the ground-truth file into its controls so
later mutation cannot silently compare unequal inputs. False-positive and operator-verified counts remain zero
without explicit keys.

## Interpretation

```text
solo → isolated-parallel = parallelism gain
isolated-parallel → shared-swarm = collaboration gain
```

`compare` reports both differences of condition medians and median paired deltas for matching repetition
numbers. Prefer paired deltas when run order or provider conditions can affect every condition in one repetition.

A replay policy result is ranking-only. It says which work a policy would rank first from the exact
historical candidate set; it does **not** claim that the counterfactual work would have produced a
better outcome. Live effectiveness requires controlled repeated runs.
