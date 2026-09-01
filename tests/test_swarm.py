import concurrent.futures
import json
import os
import socket
import sqlite3
import subprocess
import threading
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWARM = ROOT / "pentest" / "swarm.py"
POSTFLIGHT = ROOT / "pentest" / "postflight.py"
KB = ROOT / "pentest" / "kb.py"


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        for name in ("state", "scratch", "findings", "board", "memory", "research/board"):
            (self.home / name).mkdir(parents=True, exist_ok=True)
        (self.home / "scope.yaml").write_text(
            'engagement_id: TEST-001\nauthorization: "unit test"\ntargets:\n  - host: example.test\n'
        )
        self.env = {**os.environ, "PENTEST_HOME": str(self.home)}
        self.run_swarm("init")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, script, *args, check=True):
        proc = subprocess.run(
            ["python3", str(script), *map(str, args)], env=self.env,
            text=True, capture_output=True,
        )
        if check and proc.returncode:
            self.fail(f"command failed: {proc.args}\nstdout={proc.stdout}\nstderr={proc.stderr}")
        if not check:
            return proc
        return json.loads(proc.stdout)

    def run_swarm(self, *args, check=True):
        return self.run_cli(SWARM, *args, check=check)

    def join(self, label):
        return self.run_swarm(
            "join", "--label", label, "--continuous", "--no-proxy-required",
        )["agent"]

    def db(self):
        return self.home / "state" / "TEST-001.sqlite3"

    def test_peer_profiles_share_invariants_section(self):
        """All peer profiles must share the same Invariants section."""
        def extract_invariants(text):
            start = text.index("## Invariants")
            end = text.index("## Startup")
            return text[start:end]
        claude = (ROOT / "agents" / "pentest-peer.md").read_text()
        luna = (ROOT / "agents" / "pentest-peer-luna.md").read_text()
        sonnet = (ROOT / "agents" / "pentest-peer-sonnet.md").read_text()
        base = extract_invariants(claude)
        self.assertEqual(base, extract_invariants(luna))
        self.assertEqual(base, extract_invariants(sonnet))
        for profile in (claude, luna, sonnet):
            self.assertIn("--label '<assigned-label>'", profile)
            self.assertIn("--proxy-required", profile)
            self.assertIn("proxy-check", profile)
            self.assertIn("Python must use explicit", profile)
            self.assertNotIn("--label peer-N", profile)

    def test_all_agent_profiles_use_xhigh_or_max(self):
        for path in (ROOT / "agents").glob("*.md"):
            text = path.read_text()
            self.assertTrue("thinking: xhigh" in text or "thinking: max" in text, path.name)

    def test_luna_probe_also_forbids_direct_traffic(self):
        probe = (ROOT / "agents" / "luna-probe.md").read_text()
        self.assertIn("PENTEST_PROXY", probe)
        self.assertIn("no direct fallback", probe)
        self.assertIn("Python must use", probe)

    def test_luna_profile_is_one_lease_only(self):
        luna = (ROOT / "agents" / "pentest-peer-luna.md").read_text()
        self.assertIn("## One-shot lifecycle — exactly one lease", luna)
        self.assertIn("join --label '<assigned-label>' --one-shot", luna)
        self.assertIn("Never claim a second lease", luna)
        self.assertNotIn("## Continuous loop", luna)
        self.assertNotIn("--one-shot", (ROOT / "agents" / "pentest-peer.md").read_text())
        self.assertNotIn("--one-shot", (ROOT / "agents" / "pentest-peer-sonnet.md").read_text())
        self.assertIn("--continuous", (ROOT / "agents" / "pentest-peer.md").read_text())
        self.assertIn("--continuous", (ROOT / "agents" / "pentest-peer-sonnet.md").read_text())
        self.assertNotIn("--continuous", luna)
        self.assertEqual(1, luna.count('swarm.py next --agent "$AGENT"'))

    def test_proxy_required_agent_cannot_claim_before_scoped_connect(self):
        creator = self.join("creator")
        joined = self.run_swarm(
            "join", "--label", "proxied-peer", "--continuous",
        )
        agent = joined["agent"]
        self.assertTrue(joined["proxy_required"])
        self.run_swarm(
            "task-add", "--agent", creator, "--key", "proxied-work",
            "--kind", "hypothesis", "--title", "proxied work",
        )
        blocked = self.run_swarm(
            "next", "--agent", agent, "--wait", 0, "--quiet", 999,
        )
        self.assertEqual("proxy-required", blocked["status"])
        spoof = self.run_swarm(
            "emit", "--agent", agent, "--kind", "proxy.checked", "--body", "fake",
            check=False,
        )
        self.assertIn("reserved event kind", spoof.stderr)

        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(3)
        port = listener.getsockname()[1]
        captured = []

        def accept_once():
            conn, _ = listener.accept()
            with conn:
                data = b""
                while b"\r\n\r\n" not in data:
                    data += conn.recv(2048)
                captured.append(data.decode("latin1"))
                conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            listener.close()

        thread = threading.Thread(target=accept_once, daemon=True)
        thread.start()
        checked = self.run_swarm(
            "proxy-check", "--agent", agent,
            "--proxy", f"http://127.0.0.1:{port}", "--timeout", 2,
        )
        thread.join(2)
        self.assertEqual(200, checked["status"])
        self.assertIn("CONNECT example.test:443", captured[0])
        self.assertIn(f"X-Redteam-Agent: {agent}", captured[0])
        claimed = self.run_swarm(
            "next", "--agent", agent, "--wait", 0, "--quiet", 999,
        )
        self.assertEqual("proxied-work", claimed["key"])

    def test_one_shot_agent_cannot_claim_a_second_lease(self):
        creator = self.join("creator")
        # Safe default: omitting an execution mode is still one-shot.
        joined = self.run_swarm(
            "join", "--label", "luna-slot-1.fresh-1", "--no-proxy-required",
        )
        luna = joined["agent"]
        self.assertEqual(1, joined["max_claims"])
        duplicate = self.run_swarm(
            "join", "--label", "luna-slot-1.fresh-1", "--no-proxy-required", check=False,
        )
        self.assertIn("agent label already joined", duplicate.stderr)
        for key in ("first", "second"):
            self.run_swarm(
                "task-add", "--agent", creator, "--key", key,
                "--kind", "hypothesis", "--title", key,
            )
        first = self.run_swarm("next", "--agent", luna, "--wait", 0, "--quiet", 999)
        self.assertEqual("claimed", first["status"])
        premature = self.run_swarm(
            "done", "--agent", luna, "--work", first["id"], "--summary", "prose only", check=False,
        )
        self.assertIn("one-shot work requires artifact-add", premature.stderr)
        evidence = self.home / "scratch" / "one-shot.txt"
        evidence.write_text("bounded assertion")
        self.run_swarm(
            "artifact-add", "--agent", luna, "--work", first["id"], "--path", evidence,
        )
        self.run_swarm("done", "--agent", luna, "--work", first["id"], "--summary", "one step")
        spoof = self.run_swarm(
            "emit", "--agent", luna, "--kind", "agent.join",
            "--json", '{"max_claims":null}', check=False,
        )
        self.assertIn("reserved event kind", spoof.stderr)
        second = self.run_swarm("next", "--agent", luna, "--wait", 0, "--quiet", 999)
        self.assertEqual({"status": "claim-limit", "max_claims": 1}, second)
        conn = sqlite3.connect(self.db())
        self.assertEqual(1, conn.execute("SELECT max_claims FROM agents WHERE id=?", (luna,)).fetchone()[0])
        self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM work WHERE state='ready'").fetchone()[0])
        conn.close()

    def test_refusal_leave_quarantines_work_before_slot_replacement(self):
        creator = self.join("creator")
        refused = self.run_swarm(
            "join", "--label", "luna-1.gen-1", "--one-shot", "--no-proxy-required",
        )["agent"]
        for key in ("refused-work", "other-work"):
            self.run_swarm(
                "task-add", "--agent", creator, "--key", key,
                "--kind", "hypothesis", "--title", key,
            )
        claimed = self.run_swarm(
            "next", "--agent", refused, "--wait", 0, "--quiet", 999,
        )
        self.assertEqual("refused-work", claimed["key"])
        self.run_swarm(
            "emit", "--agent", refused, "--kind", "task.blocked",
            "--body", "provider refusal",
        )
        # Even if the child forgets --refusal, task.blocked makes leave fail the lease atomically.
        left = self.run_swarm(
            "leave", "--agent", refused, "--summary", "provider refusal",
        )
        self.assertEqual("failed", left["lease_state"])
        recorded = self.run_swarm(
            "run-result", "--label", "luna-1.gen-1", "--run-id", "refused-run",
            "--status", "failed", "--category", "refusal",
        )
        self.assertTrue(recorded["recorded"])
        self.assertEqual(1, recorded["released"])
        verified = self.run_swarm(
            "run-result-get", "--run-id", "refused-run", "--category", "refusal",
        )
        self.assertEqual("refusal", verified["category"])
        mismatch = self.run_swarm(
            "run-result-get", "--run-id", "refused-run", "--category", "timeout",
            check=False,
        )
        self.assertIn("category mismatch", mismatch.stderr)

        replacement = self.run_swarm(
            "join", "--label", "luna-1.gen-2", "--one-shot", "--no-proxy-required",
        )["agent"]
        next_work = self.run_swarm(
            "next", "--agent", replacement, "--wait", 0, "--quiet", 999,
        )
        self.assertEqual("other-work", next_work["key"])
        recovered = self.run_swarm(
            "run-result", "--label", "luna-1.gen-2", "--run-id", "timed-out-run",
            "--status", "failed", "--category", "timeout",
        )
        self.assertEqual(1, recovered["released"])
        conn = sqlite3.connect(self.db())
        states = dict(conn.execute("SELECT work_key,state FROM work"))
        conn.close()
        self.assertEqual("failed", states["refused-work"])
        self.assertEqual("ready", states["other-work"])

    def test_canonical_workflow_rolls_replacements_without_rerouting_models(self):
        script = (ROOT / "workflows" / "cohort.js").read_text()
        self.assertIn('runs.run("cohort-mode"', script)
        self.assertIn('agent: "pentest-cohort-selector"', script)
        selector = (ROOT / "agents" / "pentest-cohort-selector.md").read_text()
        recorder = (ROOT / "agents" / "pentest-run-recorder.md").read_text()
        self.assertIn("thinking: xhigh", selector)
        self.assertIn("thinking: xhigh", recorder)
        self.assertIn('cohortNumber === 1 ? "pentest-peer-sonnet" : "pentest-peer"', script)
        self.assertIn("maxClaudeGenerations = 2", script)
        self.assertIn("maxLunaGenerations = 7", script)
        self.assertIn("maxConsecutiveFailures = 2", script)
        self.assertIn("= 63 runs", script)
        self.assertIn("Promise.all", script)
        self.assertNotIn("runs.lanes", script)
        self.assertNotIn('resume: "previous"', script)
        self.assertIn("function runClaudeSlot", script)
        self.assertEqual(2, script.count("acceptance: false"))
        self.assertIn("timeoutMs: 1800000", script)
        self.assertIn("function runLunaSlot", script)
        self.assertIn("function recordTerminal", script)
        self.assertIn('agent: "pentest-run-recorder"', script)
        self.assertIn("gate: verifyCommand", script)
        self.assertIn("run-result-get", script)
        self.assertIn("Run exactly this local command", script)
        self.assertIn('agent: "pentest-peer-luna"', script)
        self.assertIn('context: "fresh"', script)
        self.assertIn("return runClaudeSlot(slot, generation + 1, failures)", script)
        self.assertIn("return runLunaSlot(slot, generation + 1, 0)", script)
        self.assertIn("return runLunaSlot(slot, generation + 1, failures)", script)
        self.assertIn("receipt.recorded === true", script)
        self.assertIn("receipt.category === category", script)
        self.assertIn("function opensCircuit", script)
        self.assertIn("too many requests", script)
        self.assertIn('category === "budget"', script)
        self.assertIn('verdict: { type: "string", enum: ["complete", "blocked"] }', script)

    def test_skill_has_only_canonical_launch_entrypoints(self):
        skill = (ROOT / "SKILL.md").read_text()
        research = (ROOT / "PROMPTING-RESEARCH.md").read_text()
        self.assertIn("workflows/cohort.js", skill)
        self.assertIn("PROXY.md", skill)
        proxy_doc = (ROOT / "PROXY.md").read_text()
        proxy_env = (ROOT / "pentest" / "proxy_env.sh").read_text()
        self.assertIn("Python requests", proxy_doc)
        self.assertIn("aiohttp", proxy_doc)
        self.assertIn("Playwright", proxy_doc)
        self.assertIn("HTTPS_PROXY", proxy_env)
        self.assertIn("globalConcurrencyLimit: 8", skill)
        self.assertIn("maxSubagentSpawnsPerRun: 63", skill)
        self.assertNotIn("workflows/cohort-sonnet.js", skill)
        self.assertNotIn("workflows/cohort-opus.js", skill)
        self.assertNotIn("workflowScript: `", skill)
        documents = (skill, research, (ROOT / "README.md").read_text(),
                     (ROOT / "SWARM.md").read_text())
        for document in documents:
            self.assertNotIn('agent: "pentest-peer-luna"', document)
        self.assertIn("연구 문서이며 launch source가 아니다", research)

    def test_concurrent_event_writers(self):
        agents = [self.join(f"peer-{i}") for i in range(8)]

        def send(i):
            return self.run_swarm(
                "emit", "--agent", agents[i % len(agents)], "--kind", "intel",
                "--json", json.dumps({"n": i}),
            )["seq"]

        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            seqs = list(pool.map(send, range(120)))
        self.assertEqual(120, len(set(seqs)))
        conn = sqlite3.connect(self.db())
        self.assertEqual(120, conn.execute("SELECT COUNT(*) FROM events WHERE kind='intel'").fetchone()[0])

    def test_status_reports_active_cohort_for_workflow_selector(self):
        status = self.run_swarm("status")
        self.assertEqual("open", status["status"])
        self.assertEqual(1, status["cohort"]["number"])
        self.assertEqual(8, status["cohort"]["target_peers"])

    def test_empty_engagement_is_not_quiescent(self):
        agent = self.join("peer")
        result = self.run_swarm(
            "next", "--agent", agent, "--wait", 0, "--quiet", 0,
        )
        self.assertEqual("wait", result["status"])

    def test_bounded_dossier_and_collaboration_inbox(self):
        joined = self.run_swarm(
            "join", "--label", "peer", "--continuous", "--no-proxy-required",
        )
        agent, cursor = joined["agent"], joined["cursor"]
        self.run_swarm("surface-add", "--agent", agent, "--path", "/one")
        self.run_swarm("surface-add", "--agent", agent, "--path", "/two")
        intel = self.run_swarm(
            "emit", "--agent", agent, "--kind", "intel", "--body", "high signal",
        )
        messages = self.run_swarm(
            "inbox", "--agent", agent, "--after", cursor,
            "--collaboration-only", "--limit", 10,
        )
        self.assertEqual([intel["seq"]], [message["seq"] for message in messages])
        dossier = self.run_swarm("dossier")
        self.assertEqual(20, len(dossier["coverage"]["gaps"]))
        self.assertGreater(dossier["coverage"]["gaps_total"], 20)
        self.assertEqual([], self.run_swarm("dossier", "--gap-limit", 0)["coverage"]["gaps"])
        compact = self.run_swarm(
            "dossier", "--recent", 10, "--gap-limit", 3, "--compact",
        )
        self.assertEqual(3, len(compact["coverage"]["gaps"]))
        self.assertNotIn("surface.discovered", {event["kind"] for event in compact["recent_events"]})
        self.assertTrue(all(
            len(json.dumps(event["body"], ensure_ascii=False)) <= 600
            for event in compact["recent_events"]
        ))
        self.assertLessEqual(len(compact["completed_cohorts"]), 1)
        self.assertTrue(all(agent["status"] in {"active", "idle"} for agent in compact["agents"]))

    def test_postflight_classifies_failures_and_releases_leases(self):
        luna = self.join("luna-1.gen-1")
        self.join("claude-1.gen-1")
        self.run_swarm(
            "task-add", "--agent", luna, "--key", "recover-me",
            "--kind", "hypothesis", "--title", "recover me",
        )
        claimed = self.run_swarm(
            "next", "--agent", luna, "--wait", 0, "--quiet", 999,
        )
        status = self.home / "workflow-status.json"
        status.write_text(json.dumps({
            "mode": "workflow", "runId": "workflow-1", "steps": [
                {"workflowKey": "cohort-mode", "runId": "run-selector", "model": "gpt-5.6-sol",
                 "status": "complete", "structuredOutput": {"number": 1}},
                {"workflowKey": "record-luna-1.gen-1", "runId": "run-recorder",
                 "model": "gpt-5.6-sol", "status": "complete",
                 "structuredOutput": {"recorded": True, "category": "refusal"}},
                {"workflowKey": "luna-1.gen-1", "runId": "run-luna", "model": "gpt-5.6-luna",
                 "status": "failed", "error": "This content was flagged for possible cybersecurity risk"},
                {"workflowKey": "claude-1.gen-1", "runId": "run-claude", "model": "claude-sonnet",
                 "status": "failed", "error": "429 ExceededBudget"},
                {"workflowKey": "luna-2.gen-1", "runId": "run-responsive-refusal",
                 "model": "gpt-5.6-luna", "status": "complete",
                 "structuredOutput": {"verdict": "blocked", "outcome": "refusal",
                                      "summary": "provider policy refusal"}},
                {"workflowKey": "luna-3.gen-1", "runId": "run-label-mismatch",
                 "model": "gpt-5.6-luna", "status": "complete",
                 "structuredOutput": {"verdict": "complete", "outcome": "completed",
                                      "actorLabel": "wrong-label", "summary": "done"}},
            ],
        }))
        first = self.run_cli(POSTFLIGHT, status, "--end-cohort")
        self.assertEqual(["budget", "provider-error", "refusal", "refusal"],
                         sorted(r["category"] for r in first["recorded"]))
        self.assertEqual({"refusal": 2, "budget": 1, "provider-error": 1},
                         first["cohort"]["run_results"])
        conn = sqlite3.connect(self.db())
        state = conn.execute("SELECT state FROM work WHERE id=?", (claimed["id"],)).fetchone()[0]
        conn.close()
        self.assertEqual("failed", state)
        metrics = self.run_swarm("metrics")
        self.assertEqual(2, metrics["run_results"]["refusal"])
        self.assertEqual(1, metrics["run_results"]["budget"])
        self.assertEqual(1, metrics["run_results"]["provider-error"])
        self.assertNotIn("completed", metrics["run_results"])
        second = self.run_cli(POSTFLIGHT, status)
        self.assertTrue(all(r["duplicate"] for r in second["recorded"]))

    def test_atomic_claim_and_expired_lease_recovery(self):
        owner = self.join("owner")
        agents = [self.join(f"peer-{i}") for i in range(12)]
        self.run_swarm(
            "task-add", "--agent", owner, "--key", "GET:/x:server-input",
            "--kind", "hypothesis", "--title", "test x",
        )

        def claim(agent):
            return self.run_swarm("next", "--agent", agent, "--wait", 0, "--lease", 1, "--quiet", 999)

        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            first = list(pool.map(claim, agents))
        winner_indexes = [i for i, r in enumerate(first) if r["status"] == "claimed"]
        self.assertEqual(1, len(winner_indexes))
        survivors = [a for i, a in enumerate(agents) if i != winner_indexes[0]]
        time.sleep(1.1)
        with concurrent.futures.ThreadPoolExecutor(max_workers=11) as pool:
            second = list(pool.map(claim, survivors))
        self.assertEqual(1, len([r for r in second if r["status"] == "claimed"]))

    def test_ledger_activity_renews_lease(self):
        creator = self.join("creator")
        owner = self.join("owner")
        other = self.join("other")
        self.run_swarm(
            "task-add", "--agent", creator, "--key", "renew-me",
            "--kind", "hypothesis", "--title", "lease renewal",
        )
        claimed = self.run_swarm(
            "next", "--agent", owner, "--wait", 0, "--lease", 1, "--quiet", 999,
        )
        self.assertEqual("claimed", claimed["status"])
        time.sleep(0.6)
        self.run_swarm("inbox", "--agent", owner, "--after", 0)
        time.sleep(0.6)
        result = self.run_swarm(
            "next", "--agent", other, "--wait", 0, "--quiet", 999,
        )
        self.assertNotEqual("claimed", result["status"])

    def test_independent_finding_attestation(self):
        finder = self.join("finder")
        verifier = self.join("verifier")
        second = self.join("second-verifier")
        forged = self.run_swarm(
            "task-add", "--agent", verifier, "--key", "verify:FIND-0001",
            "--kind", "verify", "--title", "forged verifier", check=False,
        )
        self.assertIn("ledger-managed", forged.stderr)
        evidence = self.home / "scratch" / "finding.txt"
        evidence.write_text("response marker")
        finding = self.run_swarm(
            "finding-add", "--agent", finder, "--title", "Boundary issue",
            "--severity", "High", "--type", "access-control",
            "--endpoint", "GET /orders/1", "--evidence", evidence,
        )
        self.assertEqual("FIND-0001", finding["id"])
        self.assertNotEqual("claimed", self.run_swarm(
            "next", "--agent", finder, "--wait", 0, "--quiet", 999,
        )["status"])
        verify_work = self.run_swarm(
            "next", "--agent", verifier, "--wait", 0, "--quiet", 999,
        )
        self.assertEqual("verify", verify_work["kind"])
        bypass = self.run_swarm(
            "done", "--agent", verifier, "--work", verify_work["id"], check=False,
        )
        self.assertIn("must attest or use non-terminal fail", bypass.stderr)
        self.assertNotEqual(0, self.run_swarm(
            "finding-attest", "--agent", finder, "--work", verify_work["id"],
            "--finding", finding["id"], "--verdict", "reproduced",
            "--evidence", evidence, check=False,
        ).returncode)
        replay = self.home / "scratch" / "replay.txt"
        replay.write_text("inconclusive replay")
        inconclusive = self.run_swarm(
            "finding-attest", "--agent", verifier, "--work", verify_work["id"],
            "--finding", finding["id"], "--verdict", "inconclusive",
            "--evidence", replay,
        )
        self.assertEqual("proposed", inconclusive["status"])
        self.assertNotEqual("claimed", self.run_swarm(
            "next", "--agent", verifier, "--wait", 0, "--quiet", 999,
        )["status"])
        retry = self.run_swarm(
            "next", "--agent", second, "--wait", 0, "--quiet", 999,
        )
        fresh = self.home / "scratch" / "fresh-replay.txt"
        fresh.write_text("independent response marker")
        attested = self.run_swarm(
            "finding-attest", "--agent", second, "--work", retry["id"],
            "--finding", finding["id"], "--verdict", "reproduced",
            "--evidence", fresh,
        )
        self.assertEqual("reproduced", attested["status"])
        metrics = self.run_swarm("metrics")
        self.assertEqual(1, metrics["findings"]["reproduced"])
        report = self.run_swarm("report")
        self.assertIn("FIND-0001", Path(report["report"]).read_text())
        export = self.run_swarm("export")
        self.assertTrue(Path(export["export"]).is_file())

    def test_solo_finder_reports_verification_blocked(self):
        finder = self.join("solo")
        evidence = self.home / "scratch" / "solo.txt"
        evidence.write_text("candidate")
        self.run_swarm(
            "finding-add", "--agent", finder, "--title", "Solo candidate",
            "--severity", "Medium", "--type", "access-control",
            "--endpoint", "GET /solo", "--evidence", evidence,
        )
        result = self.run_swarm(
            "next", "--agent", finder, "--wait", 0, "--quiet", 0,
        )
        self.assertEqual("verification-blocked", result["status"])

    def test_unknown_finding_references_fail_cleanly(self):
        agent = self.join("peer")
        self.run_swarm("surface-add", "--agent", agent, "--path", "/x")
        attempt = self.run_swarm(
            "attempt-add", "--agent", agent, "--surface", "GET /x",
            "--check", "access-control", "--result", "partial",
            "--finding", "FIND-9999", check=False,
        )
        credential = self.run_swarm(
            "credential-add", "--agent", agent, "--type", "web",
            "--username", "u", "--value", "v", "--finding", "FIND-9999",
            check=False,
        )
        for proc in (attempt, credential):
            self.assertNotEqual(0, proc.returncode)
            self.assertIn("unknown finding", proc.stderr)
            self.assertNotIn("Traceback", proc.stderr)

    def test_coverage_preserves_attempt_history_and_unknowns(self):
        agent = self.join("peer")
        self.run_swarm("surface-add", "--agent", agent, "--method", "GET", "--path", "/catalog")
        duplicate = self.run_swarm(
            "surface-add", "--agent", agent, "--method", "GET", "--path", "catalog",
        )
        self.assertFalse(duplicate["created"])
        self.assertEqual("GET /catalog", duplicate["key"])
        self.run_swarm(
            "attempt-add", "--agent", agent, "--surface", "GET /catalog",
            "--check", "server-input", "--result", "safe",
        )
        self.run_swarm(
            "attempt-add", "--agent", agent, "--surface", "GET /catalog",
            "--check", "server-input", "--result", "vulnerable",
        )
        self.run_swarm("check-add", "--agent", agent, "--name", "custom-parser")
        coverage = self.run_swarm("coverage", "--gaps-only")
        self.assertIn({"surface": "GET /catalog", "check": "custom-parser"}, coverage["gaps"])
        conn = sqlite3.connect(self.db())
        self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0])

    def test_next_materializes_distinct_coverage_claims(self):
        first = self.join("first")
        second = self.join("second")
        self.run_swarm("surface-add", "--agent", first, "--method", "POST", "--path", "/api/order")
        one = self.run_swarm("next", "--agent", first, "--wait", 0, "--quiet", 999)
        two = self.run_swarm("next", "--agent", second, "--wait", 0, "--quiet", 999)
        self.assertEqual("coverage", one["kind"])
        self.assertEqual("coverage", two["kind"])
        self.assertNotEqual(one["key"], two["key"])
        self.assertEqual("POST /api/order", one["payload"]["surface"])
        premature = self.run_swarm(
            "done", "--agent", first, "--work", one["id"], check=False,
        )
        self.assertIn("requires attempt-add", premature.stderr)
        self.run_swarm(
            "attempt-add", "--agent", first, "--surface", one["payload"]["surface"],
            "--check", one["payload"]["check"], "--result", "not-applicable",
        )
        self.assertEqual("done", self.run_swarm(
            "done", "--agent", first, "--work", one["id"],
        )["state"])

    def test_reproduction_activates_planned_follow_ups(self):
        finder = self.join("finder")
        verifier = self.join("verifier")
        evidence = self.home / "scratch" / "primitive.txt"
        evidence.write_text("candidate primitive")
        finding = self.run_swarm(
            "finding-add", "--agent", finder, "--title", "Reusable primitive",
            "--severity", "High", "--type", "access-control", "--endpoint", "GET /object/1",
            "--evidence", evidence, "--details", json.dumps({"follow_ups": [
                {"key": "other-role", "title": "Test primitive with another role",
                 "payload": {"finding_id": "FIND-9999"}},
                {"key": "bulk-path", "title": "Test primitive on bulk endpoint", "priority": 90},
            ]}),
        )
        self.assertEqual(2, finding["planned_follow_ups"])
        verify = self.run_swarm("next", "--agent", verifier, "--wait", 0, "--quiet", 999)
        replay = self.home / "scratch" / "primitive-replay.txt"
        replay.write_text("independent reproduction")
        result = self.run_swarm(
            "finding-attest", "--agent", verifier, "--work", verify["id"],
            "--finding", finding["id"], "--verdict", "reproduced", "--evidence", replay,
        )
        self.assertEqual(2, len(result["follow_ups"]))
        pivot = self.run_swarm("next", "--agent", finder, "--wait", 0, "--quiet", 999)
        self.assertEqual("extension", pivot["kind"])
        self.assertEqual(finding["id"], pivot["payload"]["finding_id"])

        invalid = self.run_swarm(
            "finding-add", "--agent", finder, "--title", "Forged follow-up",
            "--severity", "Low", "--type", "other", "--endpoint", "GET /other",
            "--evidence", evidence, "--details", json.dumps({"follow_ups": [
                {"title": "not a verifier", "kind": "verify"},
            ]}), check=False,
        )
        self.assertIn("cannot be verify or coverage", invalid.stderr)
        self.assertNotIn("Traceback", invalid.stderr)

    def test_underfilled_cohort_does_not_count_toward_saturation(self):
        agent = self.join("only-one")
        self.run_swarm("leave", "--agent", agent, "--summary", "underfilled")
        ended = self.run_swarm("cohort-end")
        self.assertFalse(ended["saturation"]["latest_cohort_peer_target_met"])
        self.assertEqual(0, ended["saturation"]["dry_cohort_streak"])

    def test_cohorts_preserve_work_handoffs_and_detect_saturation(self):
        first = self.join("first")
        for i in range(7):
            self.join(f"first-cohort-peer-{i}")
        self.run_swarm(
            "task-add", "--agent", first, "--key", "carry-me", "--kind", "hypothesis",
            "--title", "Continue this in a fresh context", "--priority", 90,
        )
        claimed = self.run_swarm("next", "--agent", first, "--wait", 0, "--quiet", 999)
        self.run_swarm("leave", "--agent", first, "--summary", "resume carry-me from checkpoint A")
        ended = self.run_swarm("cohort-end", "--reason", "timebox")
        self.assertEqual(1, ended["remaining_work"])
        self.assertFalse(ended["saturation"]["eligible"])

        started = self.run_swarm("cohort-start", "--label", "fresh-2", "--peers", 8)
        self.assertEqual(2, started["number"])
        second = self.join("second")
        for i in range(7):
            self.join(f"second-cohort-peer-{i}")
        dossier = self.run_swarm("dossier")
        self.assertEqual("resume carry-me from checkpoint A", dossier["completed_cohorts"][0]["summary"]["handoffs"][0]["summary"])
        resumed = self.run_swarm("next", "--agent", second, "--wait", 0, "--quiet", 999)
        self.assertEqual(claimed["key"], resumed["key"])
        self.run_swarm("done", "--agent", second, "--work", resumed["id"], "--summary", "completed")
        self.run_swarm("leave", "--agent", second, "--summary", "no remaining leads")
        ended = self.run_swarm("cohort-end", "--reason", "second timebox")
        self.assertTrue(ended["saturation"]["eligible"])
        self.assertEqual("closed", self.run_swarm("close", "--require-saturation")["status"])

    def test_dossier_does_not_count_stale_peer_as_live(self):
        agent = self.join("stale")
        conn = sqlite3.connect(self.db())
        conn.execute("UPDATE agents SET heartbeat_at=0 WHERE id=?", (agent,))
        conn.commit()
        conn.close()
        dossier = self.run_swarm("dossier")
        self.assertEqual(0, dossier["active_cohort"]["live_peers"])
        self.assertEqual("stale", next(a for a in dossier["agents"] if a["id"] == agent)["status"])

    def test_pre_cohort_database_migrates_on_open(self):
        agent = self.join("legacy-agent")
        conn = sqlite3.connect(self.db())
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DROP TABLE cohort_agents")
        conn.execute("DROP TABLE cohorts")
        conn.commit()
        conn.close()
        dossier = self.run_swarm("dossier")
        self.assertEqual(1, dossier["active_cohort"]["number"])
        self.assertEqual(1, dossier["active_cohort"]["joined_peers"])
        self.assertEqual("wait", self.run_swarm(
            "next", "--agent", agent, "--wait", 0, "--quiet", 999,
        )["status"])

    def test_scope_change_fails_closed(self):
        (self.home / "scope.yaml").write_text(
            'engagement_id: TEST-001\nauthorization: "unit test"\ntargets:\n  - host: changed.test\n'
        )
        proc = self.run_swarm("dossier", check=False)
        self.assertNotEqual(0, proc.returncode)
        self.assertIn("scope.yaml changed", proc.stderr)

    def test_korean_and_structured_knowledge_search(self):
        (self.home / "research" / "board" / "recon-baseline.jsonl").write_text(
            json.dumps({"agent": "r", "type": "analysis", "vuln": "인증 취약점", "sink": "innerHTML"}, ensure_ascii=False) + "\n" +
            json.dumps({"agent": "r", "type": "analysis", "source": "prototype", "note": "nested parser", "sink": "pollution"}) + "\n"
        )
        (self.home / "research" / "board" / "refusal-patterns.jsonl").write_text(
            json.dumps({"agent": "legacy", "type": "task", "body": "SAFEGUARD_META"}) + "\n"
        )
        self.run_cli(KB, "index")
        results = self.run_cli(KB, "search", "취약점")
        self.assertEqual("research/board/recon-baseline.jsonl:1", results[0]["origin"])
        self.assertIn("innerHTML", results[0]["body"])
        multi = self.run_cli(KB, "search", "prototype pollution")
        self.assertEqual("research/board/recon-baseline.jsonl:2", multi[0]["origin"])
        self.assertEqual([], self.run_cli(KB, "search", "SAFEGUARD_META"))


if __name__ == "__main__":
    unittest.main()
