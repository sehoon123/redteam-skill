# Multi-Engagement Workspaces

One project folder may contain multiple authorized sites. Runtime code is shared, but every site's
scope, SQLite state, evidence, reports, exports, memory, and cache are isolated:

```text
.pi/pentest/
├── active-engagement
├── engagements/
│   ├── SITE-A/{scope.yaml,state/,scratch/,findings/,board/,memory/,cache/}
│   └── SITE-B/{scope.yaml,state/,scratch/,findings/,board/,memory/,cache/}
├── swarm.py
├── workspace.py
├── engagement_env.sh
└── research/board/        # shared read-only operational research
```

## Normal `/redteam` use

Users do not create or select a workspace manually. A target-bearing request such as:

```text
/redteam "https://example.com"에 대한 모의해킹을 진행해줘
```

makes the skill run `workspace.py ensure`, `swarm.py init`, and `kb.py index` before launching peers.
`ensure` derives a stable `site-<host>-p<port>` ID, preserves the requested `http|https` scheme in
`scope.yaml`, creates or reuses the site workspace, and selects it. An explicit assessment request is recorded as the operator's authorization assertion;
ambiguous authorization is confirmed before target traffic.

## Manual create and select

Use this only for a complex hand-written scope. The workspace ID must exactly equal `engagement_id`
in its scope file.

```bash
python3 .pi/pentest/workspace.py create \
  --id SITE-A --scope scopes/site-a.yaml
python3 .pi/pentest/workspace.py create \
  --id SITE-B --scope scopes/site-b.yaml

python3 .pi/pentest/workspace.py use --id SITE-A
python3 .pi/pentest/workspace.py current
python3 .pi/pentest/workspace.py list
python3 .pi/pentest/swarm.py init
```

The atomic `active-engagement` pointer is for sequential operation. `ensure`/`use` refuse to switch
away from a workspace with a fresh live peer or lease. The canonical workflow and postflight resolve
the same pointer.

## Simultaneous sites

Use a separate Pi process/pane per site and set the environment before starting Pi:

```bash
PENTEST_ENGAGEMENT=SITE-A pi
PENTEST_ENGAGEMENT=SITE-B pi
```

`PENTEST_ENGAGEMENT` overrides the shared pointer and the Herdr launcher exports it into each peer pane before Pi starts.
`PENTEST_HOME` remains the highest-priority explicit override for tests or custom deployments.

## Evidence paths

Every peer sources:

```bash
. .pi/pentest/engagement_env.sh
```

This exports `PENTEST_ENGAGEMENT_ID`, `PENTEST_ACTIVE_HOME`, `PENTEST_SCRATCH`,
`PENTEST_FINDINGS`, and `PENTEST_BOARD`. Proxy logs use the engagement ID to distinguish sites.
Evidence must be written under those paths. The ledger rejects artifacts outside the selected
engagement home.

Reports and exports become:

```text
.pi/pentest/engagements/SITE-A/findings/report.md
.pi/pentest/engagements/SITE-A/board/events.jsonl
```

The knowledge index is also site-local. Shared operational research remains read-only under the
runtime root; local memory and findings never cross site boundaries.

## Legacy layout

If neither `PENTEST_ENGAGEMENT` nor `active-engagement` is set, the existing single-site
`.pi/pentest/{scope.yaml,state/,scratch/...}` layout remains active without migration.

```bash
python3 .pi/pentest/workspace.py use --legacy
```

Migration is intentionally not automatic. Finish or stop live peers, copy legacy evidence into a
new workspace deliberately, verify hashes, then select it.
