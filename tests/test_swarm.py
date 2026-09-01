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
WORKSPACE = ROOT / "pentest" / "workspace.py"


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
            self.assertIn("--proxy-policy", profile)
            self.assertIn("PENTEST_PROXY_POLICY", profile)
            self.assertIn("PENTEST_NETWORK_MODE", profile)
            self.assertIn("engagement_env.sh", profile)
            self.assertIn("PENTEST_SCRATCH", profile)
            self.assertIn("proxy-check", profile)
            self.assertIn("next --agent \"$AGENT\"", profile)
            self.assertIn("--brief", profile)
            self.assertIn("typed", profile.lower())
            self.assertIn("When `PENTEST_NETWORK_MODE=proxy`", profile)
            self.assertNotIn("--label peer-N", profile)

    def test_all_agent_profiles_use_xhigh_or_max(self):
        for path in (ROOT / "agents").glob("*.md"):
            text = path.read_text()
            self.assertTrue("thinking: xhigh" in text or "thinking: max" in text, path.name)

    def test_luna_probe_prefers_proxy_but_allows_direct_mode(self):
        probe = (ROOT / "agents" / "luna-probe.md").read_text()
        self.assertIn("PENTEST_PROXY", probe)
        self.assertIn("PENTEST_NETWORK_MODE=proxy", probe)
        self.assertIn("continue without proxy settings", probe)

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

    def test_proxy_auto_agent_cannot_claim_before_scoped_decision(self):
        creator = self.join("creator")
        joined = self.run_swarm(
            "join", "--label", "proxied-peer", "--continuous",
        )
        agent = joined["agent"]
        self.assertTrue(joined["proxy_required"])
        self.assertEqual("auto", joined["proxy_policy"])
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
        self.assertIn("X-Redteam-Engagement: TEST-001", captured[0])
        claimed = self.run_swarm(
            "next", "--agent", agent, "--wait", 0, "--quiet", 999,
        )
        self.assertEqual("proxied-work", claimed["key"])

    def test_proxy_auto_fails_open_but_required_policy_stays_closed(self):
        creator = self.join("creator")
        automatic = self.run_swarm("join", "--label", "auto-direct", "--continuous")
        auto_agent = automatic["agent"]
        self.assertEqual("auto", automatic["proxy_policy"])
        self.assertEqual("proxy-required", self.run_swarm(
            "next", "--agent", auto_agent, "--wait", 0, "--quiet", 999,
        )["status"])

        unused = socket.socket()
        unused.bind(("127.0.0.1", 0))
        port = unused.getsockname()[1]
        unused.close()
        no_ledger = subprocess.run(
            ["sh", "-c", '. "$1"; printf "%s|%s" "$PENTEST_NETWORK_MODE" "${HTTPS_PROXY-unset}"',
             "sh", str(ROOT / "pentest" / "proxy_env.sh")],
            cwd=ROOT, env={**self.env, "PENTEST_PROXY": f"http://127.0.0.1:{port}"},
            text=True, capture_output=True,
        )
        self.assertEqual(0, no_ledger.returncode, no_ledger.stderr)
        self.assertEqual("direct|unset", no_ledger.stdout)
        decision = self.run_swarm(
            "proxy-check", "--agent", auto_agent,
            "--proxy", f"http://127.0.0.1:{port}", "--timeout", .2,
        )
        self.assertEqual("unavailable", decision["status"])
        self.assertEqual("direct", decision["network_mode"])
        self.assertEqual("direct", self.run_swarm(
            "proxy-mode", "--agent", auto_agent,
        )["network_mode"])
        shell_env = {
            **self.env, "PENTEST_SWARM": str(SWARM),
            "HTTP_PROXY": "http://should-be-unset", "HTTPS_PROXY": "http://should-be-unset",
        }
        sourced = subprocess.run(
            ["sh", "-c", '. "$1" --agent "$2"; printf "%s|%s" "$PENTEST_NETWORK_MODE" "${HTTP_PROXY-unset}"',
             "sh", str(ROOT / "pentest" / "proxy_env.sh"), auto_agent],
            cwd=ROOT, env=shell_env, text=True, capture_output=True,
        )
        self.assertEqual(0, sourced.returncode, sourced.stderr)
        self.assertEqual("direct|unset", sourced.stdout)
        self.assertIn("continuing in direct/offline mode", sourced.stderr)
        self.run_swarm(
            "task-add", "--agent", creator, "--key", "offline-reversing",
            "--kind", "analysis", "--title", "Reverse local binary",
        )
        claimed = self.run_swarm(
            "next", "--agent", auto_agent, "--wait", 0, "--quiet", 999,
        )
        self.assertEqual("offline-reversing", claimed["key"])
        self.run_swarm(
            "done", "--agent", auto_agent, "--work", claimed["id"],
            "--summary", "offline analysis complete",
        )

        rejecting = socket.socket()
        rejecting.bind(("127.0.0.1", 0))
        rejecting.listen(1)
        reject_port = rejecting.getsockname()[1]

        def reject_once():
            conn, _ = rejecting.accept()
            with conn:
                request = b""
                while b"\r\n\r\n" not in request:
                    request += conn.recv(2048)
                conn.sendall(b"HTTP/1.1 403 Proxy policy denied\r\n\r\n")
            rejecting.close()

        reject_thread = threading.Thread(target=reject_once, daemon=True)
        reject_thread.start()
        rejected = self.run_swarm(
            "proxy-check", "--agent", auto_agent,
            "--proxy", f"http://127.0.0.1:{reject_port}", "--timeout", 1,
            check=False,
        )
        reject_thread.join(2)
        self.assertIn("required proxy CONNECT failed", rejected.stderr)
        self.assertEqual("blocked", self.run_swarm(
            "proxy-mode", "--agent", auto_agent,
        )["network_mode"])
        self.assertEqual("proxy-required", self.run_swarm(
            "next", "--agent", auto_agent, "--wait", 0, "--quiet", 999,
        )["status"])

        strict = self.run_swarm(
            "join", "--label", "strict-proxy", "--continuous", "--proxy-required",
        )
        failed = self.run_swarm(
            "proxy-check", "--agent", strict["agent"],
            "--proxy", f"http://127.0.0.1:{port}", "--timeout", .2, check=False,
        )
        self.assertIn("required proxy unavailable", failed.stderr)
        self.assertEqual("proxy-required", self.run_swarm(
            "next", "--agent", strict["agent"], "--wait", 0, "--quiet", 999,
        )["status"])

        off = self.run_swarm(
            "join", "--label", "offline-only", "--continuous", "--proxy-policy", "off",
        )
        disabled = self.run_swarm(
            "proxy-check", "--agent", off["agent"],
            "--proxy", f"http://127.0.0.1:{port}", "--timeout", .2,
        )
        self.assertEqual("disabled", disabled["status"])
        self.assertEqual("direct", self.run_swarm(
            "proxy-mode", "--agent", off["agent"],
        )["network_mode"])

    def test_typed_causal_protocol_links_work_confidence_and_metrics(self):
        creator = self.join("causal-creator")
        responder = self.join("causal-responder")
        root = self.run_swarm(
            "task-add", "--agent", creator, "--key", "order-root",
            "--kind", "hypothesis", "--title", "Order boundary experiment",
            "--workstream", "order-authz", "--diversity-key", "owner-boundary",
            "--information-gain", .8,
        )
        claimed = self.run_swarm(
            "next", "--agent", creator, "--wait", 0, "--quiet", 999,
        )
        self.assertEqual(root["id"], claimed["id"])
        evidence_path = self.home / "scratch" / "causal.txt"
        evidence_path.write_text("owner and non-owner responses differ")
        artifact = self.run_swarm(
            "artifact-add", "--agent", creator, "--work", claimed["id"],
            "--path", evidence_path, "--kind", "response-pair",
        )
        observation = self.run_swarm(
            "observe", "--agent", creator, "--work", claimed["id"],
            "--workstream", "order-authz", "--claim", "Response class differs by owner",
            "--confidence", .8, "--subjects", '["surface:GET /orders/{id}"]',
            "--evidence", json.dumps([f"sha256:{artifact['sha256']}"]),
            "--conditions", '{"role":"user"}',
        )
        hypothesis = self.run_swarm(
            "hypothesize", "--agent", creator, "--work", claimed["id"],
            "--workstream", "order-authz", "--claim", "Object ownership is enforced inconsistently",
            "--confidence", .6, "--subjects", '["surface:GET /orders/{id}"]',
            "--falsifiers", '["responses are a shared error template"]',
            "--caused-by", observation["seq"],
        )
        request = self.run_swarm(
            "request", "--agent", creator, "--work", claimed["id"],
            "--workstream", "order-authz", "--claim", "Compare a fresh non-owner session",
            "--subjects", '["surface:GET /orders/{id}"]', "--caused-by", hypothesis["seq"],
            "--next-actions", json.dumps([{
                "key": "order-fresh-replay", "title": "Fresh non-owner replay",
                "kind": "experiment", "priority": 90, "diversity_key": "fresh-session",
                "expected_information_gain": .9, "estimated_cost": 1.5,
            }]),
        )
        self.assertEqual(1, len(request["created_work"]))
        duplicate = self.run_swarm(
            "request", "--agent", creator, "--work", claimed["id"],
            "--workstream", "order-authz", "--claim", "A second request deduplicates",
            "--subjects", '["surface:GET /orders/{id}"]', "--caused-by", hypothesis["seq"],
            "--next-actions", '[{"key":"order-fresh-replay","title":"Duplicate replay"}]',
        )
        self.assertEqual(1, len(duplicate["reused_work"]))
        response = self.run_swarm(
            "respond", "--agent", responder,
            "--claim", "Fresh session reproduced the response difference", "--confidence", .9,
            "--subjects", '["work:1"]', "--evidence", json.dumps([f"event:{request['seq']}"]),
            "--caused-by", request["seq"],
        )
        challenge = self.run_swarm(
            "challenge", "--agent", creator, "--work", claimed["id"],
            "--workstream", "order-authz", "--claim", "Tenant membership may explain the result",
            "--subjects", '["surface:GET /orders/{id}"]', "--caused-by", hypothesis["seq"],
            "--falsifiers", '["cross-tenant replay behaves the same"]',
            "--next-actions", '[{"key":"order-cross-tenant","title":"Cross-tenant replay","diversity_key":"tenant"}]',
        )
        decision = self.run_swarm(
            "decide", "--agent", responder,
            "--claim", "Keep the ownership hypothesis and test tenant separately", "--confidence", .75,
            "--subjects", '["surface:GET /orders/{id}"]',
            "--evidence", json.dumps([f"event:{challenge['seq']}"]),
            "--caused-by", challenge["seq"], "--supersedes", hypothesis["seq"],
        )
        synthesis = self.run_swarm(
            "synthesize", "--agent", creator, "--work", claimed["id"],
            "--workstream", "order-authz", "--claim", "Ownership remains the best explanation",
            "--confidence", .82, "--subjects", '["surface:GET /orders/{id}"]',
            "--evidence", json.dumps([f"event:{observation['seq']}", f"event:{decision['seq']}"]),
            "--caused-by", decision["seq"],
        )
        self.assertEqual(observation["trace_id"], hypothesis["trace_id"])
        self.assertEqual(request["trace_id"], response["trace_id"])
        self.assertEqual(request["correlation_id"], response["correlation_id"])
        self.assertEqual(challenge["correlation_id"], decision["correlation_id"])
        self.assertTrue(synthesis["trace_id"])
        self.assertEqual("done", self.run_swarm(
            "done", "--agent", creator, "--work", claimed["id"],
            "--summary", "causal state updated",
        )["state"])

        invalid = self.run_swarm(
            "observe", "--agent", responder, "--workstream", "order-authz",
            "--claim", "invalid confidence", "--confidence", 2,
            "--subjects", '["surface:GET /orders/{id}"]',
            "--evidence", json.dumps([f"event:{observation['seq']}"]), check=False,
        )
        self.assertIn("confidence", invalid.stderr)
        missing_evidence = self.run_swarm(
            "observe", "--agent", responder, "--workstream", "order-authz",
            "--claim", "unknown evidence", "--subjects", '["surface:GET /orders/{id}"]',
            "--evidence", json.dumps(["sha256:" + "0" * 64]), check=False,
        )
        self.assertIn("unknown evidence ref", missing_evidence.stderr)
        rolled_back = self.run_swarm(
            "request", "--agent", responder, "--workstream", "order-authz",
            "--claim", "malformed transition", "--subjects", '["surface:GET /orders/{id}"]',
            "--next-actions", '[{"key":"must-not-exist"}]', check=False,
        )
        self.assertIn("requires string key and title", rolled_back.stderr)
        conn = sqlite3.connect(self.db())
        self.assertEqual(0, conn.execute(
            "SELECT COUNT(*) FROM work WHERE work_key='must-not-exist'"
        ).fetchone()[0])
        conn.close()
        communication = self.run_swarm("communication-metrics")
        self.assertEqual(8, communication["typed_events"])
        self.assertEqual(2, communication["causal_unlock_count"])
        self.assertGreaterEqual(communication["cross_agent_causal_edges"], 2)
        self.assertGreater(communication["evidence_linked_ratio"], 0)
        self.assertGreater(communication["task_linked_ratio"], 0)
        self.assertGreater(communication["actionable_event_ratio"], 0)
        self.assertGreater(communication["duplicate_spawn_rate"], 0)
        self.assertIsNotNone(communication["challenge_resolution_latency_seconds_median"])
        self.assertFalse(any(word in json.dumps(communication).lower()
                             for word in ("winner", "points", "rank")))
        conn = sqlite3.connect(self.db())
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE events SET body_json='{}' WHERE seq=?", (observation["seq"],))
        conn.close()

    def test_task_local_brief_excludes_unrelated_workstreams(self):
        creator = self.join("brief-creator")
        worker_a = self.join("brief-worker-a")
        worker_b = self.join("brief-worker-b")
        request_a = self.run_swarm(
            "request", "--agent", creator, "--workstream", "stream-a",
            "--claim", "A-only objective", "--subjects", '["surface:GET /a"]',
            "--next-actions", '[{"key":"stream-a-work","title":"A experiment","priority":100}]',
        )
        self.run_swarm(
            "request", "--agent", creator, "--workstream", "stream-b",
            "--claim", "B-secret-unrelated", "--subjects", '["surface:GET /b"]',
            "--next-actions", '[{"key":"stream-b-work","title":"B experiment","priority":90}]',
        )
        claimed_a = self.run_swarm(
            "next", "--agent", worker_a, "--wait", 0, "--quiet", 999,
        )
        self.assertEqual("stream-a-work", claimed_a["key"])
        evidence_path = self.home / "scratch" / "brief-a.txt"
        evidence_path.write_text("A evidence")
        artifact = self.run_swarm(
            "artifact-add", "--agent", worker_a, "--work", claimed_a["id"],
            "--path", evidence_path,
        )
        self.run_swarm("surface-add", "--agent", worker_a, "--path", "/a")
        self.run_swarm(
            "attempt-add", "--agent", worker_a, "--work", claimed_a["id"],
            "--surface", "GET /a", "--check", "access-control", "--result", "blocked",
            "--notes", "requires another account",
        )
        self.run_swarm(
            "observe", "--agent", worker_a, "--work", claimed_a["id"],
            "--claim", "A-only fact", "--subjects", '["surface:GET /a"]',
            "--evidence", json.dumps([f"sha256:{artifact['sha256']}"]),
        )
        brief = self.run_swarm(
            "brief", "--agent", worker_a, "--work", claimed_a["id"],
            "--max-tokens", 1200,
        )
        encoded = json.dumps(brief, ensure_ascii=False)
        self.assertEqual("stream-a", brief["current_work"]["workstream"])
        self.assertIn("A-only objective", encoded)
        self.assertIn("A-only fact", encoded)
        self.assertIn("requires another account", encoded)
        self.assertIn(artifact["sha256"], encoded)
        self.assertNotIn("B-secret-unrelated", encoded)
        stream_inbox = self.run_swarm(
            "inbox", "--agent", worker_a, "--after", 0,
            "--collaboration-only", "--workstream", "stream-a",
        )
        self.assertTrue(all(item["workstream"] == "stream-a" for item in stream_inbox))
        self.assertNotIn("B-secret-unrelated", json.dumps(stream_inbox, ensure_ascii=False))
        denied = self.run_swarm(
            "brief", "--agent", worker_b, "--work", claimed_a["id"], check=False,
        )
        self.assertIn("leased by this agent", denied.stderr)
        self.run_swarm(
            "done", "--agent", worker_a, "--work", claimed_a["id"],
            "--summary", "A state updated",
        )
        claimed_b = self.run_swarm(
            "next", "--agent", worker_b, "--wait", 0, "--quiet", 999,
            "--brief", "--brief-tokens", 1200,
        )
        self.assertEqual("stream-b-work", claimed_b["key"])
        self.assertIn("B-secret-unrelated", json.dumps(claimed_b["brief"], ensure_ascii=False))
        self.assertNotIn("A-only fact", json.dumps(claimed_b["brief"], ensure_ascii=False))
        reused = self.run_swarm(
            "artifact-add", "--agent", worker_b, "--work", claimed_b["id"],
            "--path", evidence_path,
        )
        self.assertEqual(artifact["id"], reused["id"])
        conn = sqlite3.connect(self.db())
        self.assertEqual(2, conn.execute(
            "SELECT COUNT(*) FROM work_artifacts WHERE artifact_id=?", (artifact["id"],)
        ).fetchone()[0])
        conn.close()
        refreshed_b = self.run_swarm(
            "brief", "--agent", worker_b, "--work", claimed_b["id"],
        )
        self.assertIn(artifact["sha256"], json.dumps(refreshed_b))
        self.assertEqual(f"event:{request_a['seq']}", brief["current_work"]["caused_by"])

    def test_replay_export_is_deterministic_and_strictly_validated(self):
        agent = self.join("replay-agent")
        self.run_swarm(
            "request", "--agent", agent, "--workstream", "replay-stream",
            "--claim", "Generate deterministic work", "--subjects", '["surface:GET /replay"]',
            "--next-actions", '[{"key":"replay-work","title":"Replay work"}]',
        )
        first_path = self.home / "board" / "replay-one.json"
        second_path = self.home / "board" / "replay-two.json"
        first = self.run_swarm("replay-export", "--output", first_path, "--strict")
        second = self.run_swarm("replay-export", "--output", second_path, "--strict")
        self.assertEqual(first["sha256"], second["sha256"])
        self.assertEqual(first_path.read_bytes(), second_path.read_bytes())
        stable_bytes = first_path.read_bytes()
        conn = sqlite3.connect(self.db())
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("UPDATE work SET caused_by_event=999999 WHERE work_key='replay-work'")
        conn.commit()
        conn.close()
        strict_failure = self.run_swarm(
            "replay-export", "--output", first_path, "--strict", check=False,
        )
        self.assertIn("unknown caused_by_event", strict_failure.stderr)
        self.assertEqual(stable_bytes, first_path.read_bytes())
        replayed = subprocess.run(
            ["python3", str(ROOT / "pentest" / "replay.py"), "--events", str(first_path),
             "--policy", "fifo", "--strict"],
            text=True, capture_output=True,
        )
        self.assertEqual(0, replayed.returncode, replayed.stderr)
        self.assertEqual([], json.loads(replayed.stdout)["validation_errors"])
        corrupted = json.loads(first_path.read_text())
        typed = next(item for item in corrupted["events"] if item["kind"].startswith("causal."))
        typed["causation_id"] = typed["seq"]
        corrupt_path = self.home / "board" / "corrupt.json"
        corrupt_path.write_text(json.dumps(corrupted))
        rejected = subprocess.run(
            ["python3", str(ROOT / "pentest" / "replay.py"), "--events", str(corrupt_path),
             "--strict"], text=True, capture_output=True,
        )
        self.assertNotEqual(0, rejected.returncode)
        self.assertIn("invalid causation_id", rejected.stderr)

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
        self.assertIn("WORKSPACES.md", skill)
        self.assertIn("CAUSAL-PROTOCOL.md", skill)
        self.assertIn("workspace.py ensure", skill)
        causal_doc = (ROOT / "CAUSAL-PROTOCOL.md").read_text()
        self.assertIn("Task-local brief", causal_doc)
        self.assertIn("replay-export", causal_doc)
        self.assertIn("사용자는 `create/use`를 직접 다루지 않는다", (ROOT / "README.md").read_text())
        workspace_doc = (ROOT / "WORKSPACES.md").read_text()
        self.assertIn("PENTEST_ENGAGEMENT", workspace_doc)
        self.assertIn("engagements/SITE-A", workspace_doc)
        proxy_doc = (ROOT / "PROXY.md").read_text()
        proxy_env = (ROOT / "pentest" / "proxy_env.sh").read_text()
        self.assertIn("Python requests", proxy_doc)
        self.assertIn("aiohttp", proxy_doc)
        self.assertIn("Playwright", proxy_doc)
        self.assertIn("fail-open by default", proxy_doc)
        self.assertIn("PENTEST_NETWORK_MODE", proxy_env)
        self.assertIn("unset HTTP_PROXY", proxy_env)
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

    def test_redteam_target_bootstrap_is_automatic_idempotent_and_live_safe(self):
        runtime = self.home / "automatic-runtime"
        runtime.mkdir()
        env = {**os.environ, "PENTEST_RUNTIME_ROOT": str(runtime)}
        env.pop("PENTEST_HOME", None)
        command = [
            "python3", str(WORKSPACE), "ensure", "--target", "https://Example.Test/shop",
            "--authorization", "Operator explicitly authorized this unit-test assessment.",
        ]
        first = subprocess.run(command, env=env, text=True, capture_output=True)
        self.assertEqual(0, first.returncode, first.stderr)
        created = json.loads(first.stdout)
        self.assertEqual("site-example.test-p443", created["engagement"])
        self.assertTrue(created["created"])
        self.assertEqual("site-example.test-p443\n", (runtime / "active-engagement").read_text())
        scope_text = Path(created["scope"]).read_text()
        self.assertIn('host: "example.test"', scope_text)
        self.assertIn("ports: [443]", scope_text)

        repeated = json.loads(subprocess.check_output(command, env=env, text=True))
        self.assertFalse(repeated["created"])
        initialized = subprocess.run(
            ["python3", str(SWARM), "init"], env=env, text=True, capture_output=True,
        )
        self.assertEqual(0, initialized.returncode, initialized.stderr)
        agent = json.loads(subprocess.check_output(
            ["python3", str(SWARM), "join", "--label", "live-bootstrap-test",
             "--continuous", "--no-proxy-required"], env=env, text=True,
        ))["agent"]

        other = [
            "python3", str(WORKSPACE), "ensure", "--target", "https://other.test",
            "--authorization", "Operator explicitly authorized this unit-test assessment.",
        ]
        blocked = subprocess.run(other, env=env, text=True, capture_output=True)
        self.assertNotEqual(0, blocked.returncode)
        self.assertIn("live peers", blocked.stderr)
        self.assertEqual("site-example.test-p443\n", (runtime / "active-engagement").read_text())

        left = subprocess.run(
            ["python3", str(SWARM), "leave", "--agent", agent],
            env=env, text=True, capture_output=True,
        )
        self.assertEqual(0, left.returncode, left.stderr)
        switched = subprocess.run(other, env=env, text=True, capture_output=True)
        self.assertEqual(0, switched.returncode, switched.stderr)
        self.assertEqual("site-other.test-p443\n", (runtime / "active-engagement").read_text())

        pending = subprocess.run(
            ["python3", str(WORKSPACE), "ensure", "--target", "https://third.test",
             "--authorization", "pending"], env=env, text=True, capture_output=True,
        )
        self.assertNotEqual(0, pending.returncode)

    def test_multi_site_workspaces_isolate_scope_state_evidence_and_reports(self):
        runtime = self.home / "runtime"
        runtime.mkdir()
        base_env = {**os.environ, "PENTEST_RUNTIME_ROOT": str(runtime)}
        base_env.pop("PENTEST_HOME", None)
        homes = {}
        for engagement in ("SITE-A", "SITE-B"):
            scope_path = self.home / f"{engagement}.yaml"
            scope_path.write_text(
                f'engagement_id: {engagement}\nauthorization: "unit test"\n'
                f'targets:\n  - host: {engagement.lower()}.example.test\n'
            )
            created = subprocess.run(
                ["python3", str(WORKSPACE), "create", "--id", engagement,
                 "--scope", str(scope_path)], env=base_env, text=True, capture_output=True,
            )
            self.assertEqual(0, created.returncode, created.stderr)
            home = runtime / "engagements" / engagement
            homes[engagement] = home
            env = {**base_env, "PENTEST_ENGAGEMENT": engagement}
            initialized = subprocess.run(
                ["python3", str(SWARM), "init"], env=env, text=True, capture_output=True,
            )
            self.assertEqual(0, initialized.returncode, initialized.stderr)
            indexed = subprocess.run(
                ["python3", str(KB), "index"], env=env, text=True, capture_output=True,
            )
            self.assertEqual(0, indexed.returncode, indexed.stderr)
            reported = subprocess.run(
                ["python3", str(SWARM), "report"], env=env, text=True, capture_output=True,
            )
            self.assertEqual(0, reported.returncode, reported.stderr)
            (home / "scratch" / "same-name.txt").write_text(engagement)

        self.assertNotEqual(
            next((homes["SITE-A"] / "state").glob("SITE-A.sqlite3")),
            next((homes["SITE-B"] / "state").glob("SITE-B.sqlite3")),
        )
        self.assertEqual("SITE-A", (homes["SITE-A"] / "scratch" / "same-name.txt").read_text())
        self.assertEqual("SITE-B", (homes["SITE-B"] / "scratch" / "same-name.txt").read_text())
        self.assertTrue((homes["SITE-A"] / "findings" / "report.md").is_file())
        self.assertTrue((homes["SITE-B"] / "findings" / "report.md").is_file())
        self.assertTrue((homes["SITE-A"] / "state" / "knowledge.sqlite3").is_file())
        self.assertTrue((homes["SITE-B"] / "state" / "knowledge.sqlite3").is_file())

        selected = subprocess.run(
            ["python3", str(WORKSPACE), "use", "--id", "SITE-A"],
            env=base_env, text=True, capture_output=True,
        )
        self.assertEqual(0, selected.returncode, selected.stderr)
        current = json.loads(subprocess.check_output(
            ["python3", str(WORKSPACE), "current"], env=base_env, text=True,
        ))
        self.assertEqual("SITE-A", current["engagement"])
        overridden = json.loads(subprocess.check_output(
            ["python3", str(WORKSPACE), "current"],
            env={**base_env, "PENTEST_ENGAGEMENT": "SITE-B"}, text=True,
        ))
        self.assertEqual("SITE-B", overridden["engagement"])

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
        verify_brief = self.run_swarm(
            "brief", "--agent", verifier, "--work", verify_work["id"],
        )
        self.assertEqual("FIND-0001", verify_brief["relevant_findings"][0]["ref"])
        self.assertEqual(finding["evidence"]["sha256"],
                         verify_brief["relevant_findings"][0]["evidence_sha"])
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
        conn.execute("DROP TRIGGER events_immutable_update")
        conn.execute("DROP TRIGGER events_immutable_delete")
        conn.execute("DROP INDEX events_trace")
        conn.execute("DROP INDEX events_workstream")
        conn.execute("DROP INDEX work_workstream")
        conn.execute("ALTER TABLE agents DROP COLUMN proxy_fail_open")
        conn.execute("ALTER TABLE events DROP COLUMN trace_id")
        conn.execute("ALTER TABLE work DROP COLUMN diversity_key")
        conn.execute("ALTER TABLE attempts DROP COLUMN work_id")
        conn.execute("ALTER TABLE findings DROP COLUMN work_id")
        conn.execute("ALTER TABLE attestations DROP COLUMN work_id")
        conn.commit()
        conn.close()
        dossier = self.run_swarm("dossier")
        migrated = sqlite3.connect(self.db())
        agent_columns = {row[1] for row in migrated.execute("PRAGMA table_info(agents)")}
        event_columns = {row[1] for row in migrated.execute("PRAGMA table_info(events)")}
        work_columns = {row[1] for row in migrated.execute("PRAGMA table_info(work)")}
        attempt_columns = {row[1] for row in migrated.execute("PRAGMA table_info(attempts)")}
        finding_columns = {row[1] for row in migrated.execute("PRAGMA table_info(findings)")}
        attestation_columns = {row[1] for row in migrated.execute("PRAGMA table_info(attestations)")}
        migrated.close()
        self.assertIn("proxy_fail_open", agent_columns)
        self.assertIn("trace_id", event_columns)
        self.assertIn("diversity_key", work_columns)
        self.assertIn("work_id", attempt_columns)
        self.assertIn("work_id", finding_columns)
        self.assertIn("work_id", attestation_columns)
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
