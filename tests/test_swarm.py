import concurrent.futures
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import tempfile
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWARM = ROOT / "pentest" / "swarm.py"
POSTFLIGHT = ROOT / "pentest" / "postflight.py"
KB = ROOT / "pentest" / "kb.py"
WORKSPACE = ROOT / "pentest" / "workspace.py"
BENCHMARK = ROOT / "pentest" / "benchmark.py"
sys.path.insert(0, str(ROOT / "pentest"))
from replay import validate as validate_replay
from scheduler import candidate_set_hash, rank_candidates


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
            self.assertIn("engagement_env.sh", profile)
            self.assertIn("peer-start", profile)
            self.assertIn("exec-http", profile)
            self.assertIn("checkpoint", profile)
            self.assertIn("partial_experiments", profile)
            self.assertIn("typed", profile.lower())
            self.assertIn("never use direct curl", profile)
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
        self.assertIn("peer-start --label '<assigned-label>' --one-shot", luna)
        self.assertIn("Never call `next` for a second lease", luna)
        self.assertNotIn("## Continuous loop", luna)
        self.assertNotIn("--one-shot", (ROOT / "agents" / "pentest-peer.md").read_text())
        self.assertNotIn("--one-shot", (ROOT / "agents" / "pentest-peer-sonnet.md").read_text())
        self.assertIn("--continuous", (ROOT / "agents" / "pentest-peer.md").read_text())
        self.assertIn("--continuous", (ROOT / "agents" / "pentest-peer-sonnet.md").read_text())
        self.assertNotIn("--continuous", luna)
        self.assertNotIn("swarm.py next", luna)
        self.assertIn("exec-http", luna)
        self.assertIn("checkpoint", luna)

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
            "--kind", "analysis", "--title", "proxied work",
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
        offline_result = self.home / "scratch" / "offline-analysis.txt"
        offline_result.write_text("offline analysis result")
        self.run_swarm(
            "artifact-add", "--agent", auto_agent, "--work", claimed["id"],
            "--path", offline_result,
        )
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

    def test_workstream_concentration_reports_share_and_hhi(self):
        creator = self.join("concentration-creator")
        work_index = 0
        for stream, count in (("stream-a", 5), ("stream-b", 3), ("stream-c", 2)):
            for _ in range(count):
                work_index += 1
                self.run_swarm(
                    "task-add", "--agent", creator, "--key", f"concentration-{work_index}",
                    "--kind", "analysis", "--title", f"work {work_index}",
                    "--workstream", stream,
                )
        for index in range(10):
            peer = self.join(f"concentration-{index}")
            self.assertEqual("claimed", self.run_swarm(
                "next", "--agent", peer, "--wait", 0, "--quiet", 999,
            )["status"])
        metrics = self.run_swarm("communication-metrics")
        self.assertEqual(.5, metrics["dominant_workstream_share"])
        self.assertEqual(.38, metrics["workstream_hhi"])
        self.assertNotIn("workstream_herding_index", metrics)

    def test_typed_causal_protocol_links_work_confidence_and_metrics(self):
        creator = self.join("causal-creator")
        responder = self.join("causal-responder")
        root = self.run_swarm(
            "task-add", "--agent", creator, "--key", "order-root",
            "--kind", "analysis", "--title", "Order boundary experiment",
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
            "--next-actions", json.dumps([{
                "key": "order-fresh-replay", "title": "Fresh non-owner replay",
                "kind": "experiment", "priority": 90, "diversity_key": "fresh-session",
                "expected_information_gain": .9, "estimated_cost": 1.5,
            }]),
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
            "--next-actions", '[{"key":"stream-a-work","title":"A experiment","kind":"experiment","priority":100}]',
        )
        self.run_swarm(
            "request", "--agent", creator, "--workstream", "stream-b",
            "--claim", "B-secret-unrelated", "--subjects", '["surface:GET /b"]',
            "--next-actions", '[{"key":"stream-b-work","title":"B experiment","kind":"experiment","priority":90}]',
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

    def test_agent_cannot_hold_multiple_active_claims(self):
        creator = self.join("claim-creator")
        worker = self.join("claim-worker")
        for key in ("claim-one", "claim-two"):
            self.run_swarm(
                "task-add", "--agent", creator, "--key", key,
                "--kind", "analysis", "--title", key, "--workstream", "claims",
            )
        first = self.run_swarm(
            "next", "--agent", worker, "--wait", 0, "--quiet", 999,
            "--brief", "--brief-tokens", 800,
        )
        again = self.run_swarm(
            "next", "--agent", worker, "--wait", 0, "--quiet", 999,
            "--brief", "--brief-tokens", 800,
        )
        self.assertEqual("claimed", first["status"])
        self.assertEqual("active-lease", again["status"])
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(first["claim_id"], again["claim_id"])
        self.assertEqual(first["claim_id"], again["brief"]["current_claim"]["id"])
        conn = sqlite3.connect(self.db())
        self.assertEqual(1, conn.execute(
            "SELECT COUNT(*) FROM work_claims WHERE actor_id=? AND ended_at IS NULL", (worker,)
        ).fetchone()[0])
        claim_event = conn.execute(
            "SELECT claim_event FROM work_claims WHERE id=?", (first["claim_id"],)
        ).fetchone()[0]
        self.assertEqual(first["claim_id"], conn.execute(
            "SELECT claim_id FROM events WHERE seq=? AND kind='work.claimed'", (claim_event,)
        ).fetchone()[0])
        self.assertEqual(1, conn.execute(
            "SELECT COUNT(*) FROM work WHERE state='leased' AND owner_id=?", (worker,)
        ).fetchone()[0])
        other_work = conn.execute(
            "SELECT id FROM work WHERE work_key='claim-two'"
        ).fetchone()[0]
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO work_claims(
                       engagement_id,work_id,actor_id,generation,claimed_at,lease_until
                   ) VALUES('TEST-001',?,?,1,0,9999999999)""",
                (other_work, worker),
            )
        conn.close()

    def test_active_claim_is_auto_resolved_by_mutations(self):
        creator = self.join("auto-provenance-creator")
        worker = self.join("auto-provenance-worker")
        self.run_swarm(
            "task-add", "--agent", creator, "--key", "auto-provenance",
            "--kind", "analysis", "--title", "auto provenance", "--workstream", "auto",
        )
        claimed = self.run_swarm("next", "--agent", worker, "--wait", 0, "--quiet", 999)
        evidence = self.home / "scratch" / "auto-provenance.txt"
        evidence.write_text("claim-scoped mutation")
        artifact = self.run_swarm(
            "artifact-add", "--agent", worker, "--path", evidence,
        )
        self.assertEqual(claimed["claim_id"], artifact["claim_id"])
        self.run_swarm("surface-add", "--agent", worker, "--path", "/auto-provenance")
        attempt = self.run_swarm(
            "attempt-add", "--agent", worker, "--surface", "GET /auto-provenance",
            "--check", "access-control", "--result", "partial",
        )
        assertion = self.run_swarm(
            "observe", "--agent", worker, "--claim", "Auto-linked observation",
            "--subjects", '["surface:GET /auto-provenance"]',
            "--evidence", json.dumps([f"sha256:{artifact['sha256']}"]),
        )
        finding = self.run_swarm(
            "finding-add", "--agent", worker, "--title", "Auto-linked candidate",
            "--severity", "Low", "--type", "other", "--endpoint", "GET /auto-provenance",
            "--evidence", evidence,
        )
        conn = sqlite3.connect(self.db())
        self.assertEqual(claimed["claim_id"], conn.execute(
            "SELECT claim_id FROM attempts WHERE id=?", (attempt["id"],)
        ).fetchone()[0])
        self.assertEqual(claimed["claim_id"], conn.execute(
            "SELECT claim_id FROM events WHERE seq=?", (assertion["seq"],)
        ).fetchone()[0])
        self.assertEqual(claimed["claim_id"], conn.execute(
            "SELECT claim_id FROM findings WHERE id=?", (int(finding["id"].split("-")[1]),)
        ).fetchone()[0])
        conn.close()

    def test_ungrounded_legacy_cause_cannot_create_hypothesis(self):
        agent = self.join("legacy-trace")
        legacy = self.run_swarm(
            "emit", "--agent", agent, "--kind", "intel",
            "--workstream", "legacy-stream", "--body", "legacy source",
        )
        rejected = self.run_swarm(
            "hypothesize", "--agent", agent, "--claim", "Legacy fact may generalize",
            "--subjects", '["surface:GET /legacy"]',
            "--falsifiers", '["fresh replay differs"]', "--caused-by", legacy["seq"],
            check=False,
        )
        self.assertIn("requires an observation", rejected.stderr)
        conn = sqlite3.connect(self.db())
        self.assertEqual(0, conn.execute(
            "SELECT COUNT(*) FROM events WHERE kind='causal.hypothesis'"
        ).fetchone()[0])
        conn.close()

    def test_bare_credential_does_not_ground_a_hypothesis(self):
        agent = self.join("credential-grounding")
        bare = self.run_swarm(
            "credential-add", "--agent", agent, "--type", "web",
            "--username", "bare", "--value", "secret",
        )
        rejected = self.run_swarm(
            "hypothesize", "--agent", agent, "--workstream", "credential-stream",
            "--claim", "Bare credential may imply access", "--subjects", '["work:1"]',
            "--evidence", json.dumps([f"credential:{bare['id']}"]),
            "--falsifiers", '["credential has no evidence-backed source"]', check=False,
        )
        self.assertIn("requires an observation", rejected.stderr)

        evidence = self.home / "scratch" / "credential-source.txt"
        evidence.write_text("evidence-backed credential source")
        finding = self.run_swarm(
            "finding-add", "--agent", agent, "--title", "Credential source",
            "--severity", "Info", "--type", "credential", "--endpoint", "GET /login",
            "--evidence", evidence,
        )
        sourced = self.run_swarm(
            "credential-add", "--agent", agent, "--type", "web",
            "--username", "sourced", "--value", "secret", "--finding", finding["id"],
        )
        accepted = self.run_swarm(
            "hypothesize", "--agent", agent, "--workstream", "credential-stream",
            "--claim", "Evidence-backed credential may enable a bounded replay",
            "--subjects", '["finding:FIND-0001"]',
            "--evidence", json.dumps([f"credential:{sourced['id']}"]),
            "--falsifiers", '["credential is rejected"]',
        )
        self.assertEqual("hypothesis", accepted["type"])
        replay = self.run_swarm(
            "replay-export", "--strict", "--output", self.home / "board" / "credential.json",
        )
        self.assertEqual([], replay["validation_errors"])

    def test_owned_work_cannot_emit_into_foreign_workstream(self):
        creator = self.join("stream-owner")
        worker = self.join("stream-worker")
        self.run_swarm(
            "task-add", "--agent", creator, "--key", "stream-bound",
            "--kind", "analysis", "--title", "stream bound", "--workstream", "stream-a",
        )
        claimed = self.run_swarm("next", "--agent", worker, "--wait", 0, "--quiet", 999)
        rejected = self.run_swarm(
            "hypothesize", "--agent", worker, "--work", claimed["id"],
            "--workstream", "stream-b", "--claim", "Wrong stream",
            "--subjects", '["work:1"]', "--falsifiers", '["same stream"]', check=False,
        )
        self.assertIn("cannot emit into a different workstream", rejected.stderr)
        conn = sqlite3.connect(self.db())
        self.assertEqual(0, conn.execute(
            "SELECT COUNT(*) FROM events WHERE actor_id=? AND kind='causal.hypothesis'", (worker,)
        ).fetchone()[0])
        conn.close()

    def test_decision_cannot_supersede_unrelated_trace(self):
        agent = self.join("decision-scope")
        evidence_path = self.home / "scratch" / "decision-grounding.txt"
        evidence_path.write_text("observed response")
        evidence = self.run_swarm(
            "artifact-add", "--agent", agent, "--path", evidence_path,
        )
        evidence_ref = json.dumps([f"artifact:{evidence['id']}"])
        first = self.run_swarm(
            "hypothesize", "--agent", agent, "--workstream", "decision-stream",
            "--claim", "First hypothesis", "--subjects", '["surface:GET /x"]',
            "--evidence", evidence_ref, "--falsifiers", '["first false"]',
        )
        second = self.run_swarm(
            "hypothesize", "--agent", agent, "--workstream", "decision-stream",
            "--claim", "Independent hypothesis", "--subjects", '["surface:GET /x"]',
            "--evidence", evidence_ref, "--falsifiers", '["second false"]',
        )
        rejected = self.run_swarm(
            "decide", "--agent", agent, "--claim", "Invalid cross-trace decision",
            "--subjects", '["surface:GET /x"]',
            "--evidence", json.dumps([f"event:{first['seq']}"]),
            "--caused-by", first["seq"], "--supersedes", second["seq"], check=False,
        )
        self.assertIn("share the decision trace", rejected.stderr)

    def test_unknown_evidence_ref_kind_is_rejected(self):
        agent = self.join("ref-registry")
        rejected = self.run_swarm(
            "observe", "--agent", agent, "--workstream", "refs",
            "--claim", "Unresolvable proof", "--subjects", '["surface:GET /refs"]',
            "--evidence", '["imaginary-proof:trusted"]', check=False,
        )
        self.assertIn("unknown evidence ref type", rejected.stderr)

    def test_duplicate_key_with_different_fingerprint_is_conflict(self):
        agent = self.join("fingerprint")
        first = self.run_swarm(
            "request", "--agent", agent, "--workstream", "fingerprints",
            "--claim", "Create canonical task", "--subjects", '["surface:GET /fp"]',
            "--next-actions", '[{"key":"same-key","title":"Replay","kind":"experiment",'
                              '"payload":{"role":"user"},"diversity_key":"role"}]',
        )
        conn = sqlite3.connect(self.db())
        before_events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        before_fingerprint = conn.execute(
            "SELECT fingerprint FROM work WHERE work_key='same-key'"
        ).fetchone()[0]
        conn.close()
        conflict = self.run_swarm(
            "request", "--agent", agent, "--workstream", "fingerprints",
            "--claim", "Conflicting task", "--subjects", '["surface:GET /fp"]',
            "--next-actions", '[{"key":"same-key","title":"Replay","kind":"experiment",'
                              '"payload":{"role":"admin"},"diversity_key":"role"}]',
            check=False,
        )
        self.assertIn("work-key-conflict", conflict.stderr)
        conn = sqlite3.connect(self.db())
        self.assertEqual(before_events, conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        self.assertEqual(before_fingerprint, conn.execute(
            "SELECT fingerprint FROM work WHERE work_key='same-key'"
        ).fetchone()[0])
        self.assertEqual(first["created_work"][0]["id"], conn.execute(
            "SELECT id FROM work WHERE work_key='same-key'"
        ).fetchone()[0])
        conn.close()

    def test_old_claim_progress_cannot_complete_new_claim(self):
        creator = self.join("claim-history-creator")
        worker = self.join("claim-history-worker")
        self.run_swarm(
            "task-add", "--agent", creator, "--key", "claim-history",
            "--kind", "analysis", "--title", "claim history", "--workstream", "claims",
        )
        first = self.run_swarm("next", "--agent", worker, "--wait", 0, "--quiet", 999)
        evidence = self.home / "scratch" / "claim-history.txt"
        evidence.write_text("first generation evidence")
        self.run_swarm(
            "artifact-add", "--agent", worker, "--work", first["id"], "--path", evidence,
        )
        self.run_swarm("fail", "--agent", worker, "--work", first["id"])
        second = self.run_swarm("next", "--agent", worker, "--wait", 0, "--quiet", 999)
        self.assertNotEqual(first["claim_id"], second["claim_id"])
        stale = self.run_swarm(
            "done", "--agent", worker, "--work", second["id"], check=False,
        )
        self.assertIn("current claim", stale.stderr)
        self.run_swarm(
            "artifact-add", "--agent", worker, "--work", second["id"], "--path", evidence,
        )
        done = self.run_swarm("done", "--agent", worker, "--work", second["id"])
        self.assertEqual("done", done["outcome"])
        conn = sqlite3.connect(self.db())
        claims = conn.execute(
            "SELECT generation,outcome FROM work_claims WHERE work_id=? ORDER BY generation",
            (first["id"],),
        ).fetchall()
        self.assertEqual([(1, "released"), (2, "done")], claims)
        self.assertEqual(2, conn.execute(
            "SELECT COUNT(*) FROM work_artifacts WHERE work_id=?", (first["id"],)
        ).fetchone()[0])
        conn.close()
        replay = self.run_swarm(
            "replay-export", "--strict",
            "--output", self.home / "board" / "claim-history-replay.json",
        )
        self.assertEqual([], replay["validation_errors"])

    def test_coverage_done_requires_current_claim_attempt(self):
        agent = self.join("coverage-claim")
        self.run_swarm("surface-add", "--agent", agent, "--path", "/claim-coverage")
        first = self.run_swarm("next", "--agent", agent, "--wait", 0, "--quiet", 999)
        self.run_swarm(
            "attempt-add", "--agent", agent, "--work", first["id"],
            "--surface", first["payload"]["surface"], "--check", first["payload"]["check"],
            "--result", "safe",
        )
        self.run_swarm("fail", "--agent", agent, "--work", first["id"])
        second = self.run_swarm("next", "--agent", agent, "--wait", 0, "--quiet", 999)
        self.assertEqual(first["id"], second["id"])
        wrong_check = "auth-session" if second["payload"]["check"] == "access-control" else "access-control"
        self.run_swarm(
            "attempt-add", "--agent", agent, "--work", second["id"],
            "--surface", second["payload"]["surface"], "--check", wrong_check,
            "--result", "safe",
        )
        rejected = self.run_swarm(
            "done", "--agent", agent, "--work", second["id"], check=False,
        )
        self.assertIn("current claim", rejected.stderr)
        self.run_swarm(
            "attempt-add", "--agent", agent, "--work", second["id"],
            "--surface", second["payload"]["surface"], "--check", second["payload"]["check"],
            "--result", "safe",
        )
        self.assertEqual("done", self.run_swarm(
            "done", "--agent", agent, "--work", second["id"],
        )["state"])

    def test_strict_replay_validates_complete_typed_body(self):
        agent = self.join("typed-replay")
        source = self.run_swarm(
            "emit", "--agent", agent, "--kind", "intel", "--workstream", "typed-replay",
            "--body", "source",
        )
        self.run_swarm(
            "observe", "--agent", agent, "--workstream", "typed-replay",
            "--claim", "Typed body is complete", "--subjects", '["surface:GET /typed"]',
            "--evidence", json.dumps([f"event:{source['seq']}"]),
        )
        path = self.home / "board" / "typed-replay.json"
        self.run_swarm("replay-export", "--strict", "--output", path)
        payload = json.loads(path.read_text())
        causal = next(item for item in payload["events"] if item["kind"] == "causal.observation")
        unknown_payload = json.loads(json.dumps(payload))
        unknown = next(
            item for item in unknown_payload["events"] if item["kind"] == "causal.observation"
        )
        unknown["evidence_refs"] = ["imaginary-proof:trusted"]
        unknown["body"]["evidence_refs"] = ["imaginary-proof:trusted"]
        unknown_path = self.home / "board" / "unknown-proof-replay.json"
        unknown_path.write_text(json.dumps(unknown_payload))
        unknown_rejected = subprocess.run(
            ["python3", str(ROOT / "pentest" / "replay.py"),
             "--events", str(unknown_path), "--strict"], text=True, capture_output=True,
        )
        self.assertNotEqual(0, unknown_rejected.returncode)
        self.assertIn("unknown evidence ref", unknown_rejected.stderr)
        causal["body"].pop("conditions")
        corrupt = self.home / "board" / "typed-body-corrupt.json"
        corrupt.write_text(json.dumps(payload))
        rejected = subprocess.run(
            ["python3", str(ROOT / "pentest" / "replay.py"), "--events", str(corrupt), "--strict"],
            text=True, capture_output=True,
        )
        self.assertNotEqual(0, rejected.returncode)
        self.assertIn("invalid typed body", rejected.stderr)

    def test_brief_failure_preserves_recoverable_claim(self):
        creator = self.join("brief-creator")
        worker = self.join("brief-worker")
        self.run_swarm(
            "task-add", "--agent", creator, "--key", "brief-recovery",
            "--kind", "analysis", "--title", "brief recovery", "--workstream", "brief-recovery",
        )
        conn = sqlite3.connect(self.db())
        conn.execute(
            "UPDATE workstreams SET current_snapshot_json='{' WHERE canonical_key='brief-recovery'"
        )
        conn.commit(); conn.close()
        failed = self.run_swarm(
            "next", "--agent", worker, "--wait", 0, "--quiet", 999,
            "--brief", check=False,
        )
        self.assertIn("active claim preserved", failed.stderr)
        conn = sqlite3.connect(self.db())
        claim_id = conn.execute(
            "SELECT id FROM work_claims WHERE actor_id=? AND ended_at IS NULL", (worker,)
        ).fetchone()[0]
        conn.execute(
            "UPDATE workstreams SET current_snapshot_json='{}' WHERE canonical_key='brief-recovery'"
        )
        conn.commit(); conn.close()
        recovered = self.run_swarm(
            "next", "--agent", worker, "--wait", 0, "--quiet", 999,
            "--brief", "--brief-tokens", 800,
        )
        self.assertEqual("active-lease", recovered["status"])
        self.assertEqual(claim_id, recovered["claim_id"])

    def test_scheduler_decision_and_claim_commit_atomically(self):
        creator = self.join("scheduler-creator")
        worker = self.join("scheduler-worker")
        parent = self.run_swarm(
            "task-add", "--agent", creator, "--key", "scheduler-parent",
            "--kind", "analysis", "--title", "parent", "--priority", 50,
            "--workstream", "scheduler",
        )
        child = self.run_swarm(
            "task-add", "--agent", creator, "--key", "scheduler-child",
            "--kind", "analysis", "--title", "child", "--priority", 100,
            "--workstream", "scheduler", "--parent", parent["id"],
        )
        forbidden = self.run_swarm(
            "task-add", "--agent", creator, "--key", "scheduler-forbidden",
            "--kind", "analysis", "--title", "forbidden", "--priority", 90,
            "--workstream", "scheduler", "--forbidden-actor", worker,
        )
        eligible = self.run_swarm(
            "task-add", "--agent", creator, "--key", "scheduler-selected",
            "--kind", "analysis", "--title", "selected", "--priority", 80,
            "--workstream", "scheduler", "--information-gain", .9,
        )
        claimed = self.run_swarm(
            "next", "--agent", worker, "--wait", 0, "--quiet", 999,
        )
        self.assertEqual(eligible["id"], claimed["id"])
        conn = sqlite3.connect(self.db())
        conn.row_factory = sqlite3.Row
        decision = conn.execute(
            "SELECT * FROM scheduler_decisions WHERE id=?", (claimed["scheduler_decision_id"],)
        ).fetchone()
        claim = conn.execute(
            "SELECT * FROM work_claims WHERE id=?", (claimed["claim_id"],)
        ).fetchone()
        candidates = [dict(row) for row in conn.execute(
            "SELECT * FROM scheduler_candidates WHERE decision_id=? ORDER BY work_id",
            (decision["id"],),
        )]
        claim_event_body = json.loads(conn.execute(
            "SELECT body_json FROM events WHERE seq=?", (claim["claim_event"],)
        ).fetchone()[0])
        conn.close()
        self.assertEqual(claimed["claim_id"], decision["claim_id"])
        self.assertEqual(claimed["id"], decision["selected_work_id"])
        self.assertEqual(decision["id"], claim_event_body["scheduler_decision_id"])
        reasons = {item["work_id"]: item["exclusion_reason"] for item in candidates}
        self.assertEqual("parent-not-done", reasons[child["id"]])
        self.assertEqual("forbidden-actor", reasons[forbidden["id"]])
        self.assertEqual(decision["candidate_set_hash"], candidate_set_hash(candidates))
        self.assertEqual(decision["candidate_set_hash"], candidate_set_hash(list(reversed(candidates))))
        self.assertEqual(claimed["id"], rank_candidates(candidates, "fifo-v1")[0])

    def test_fifo_v1_preserves_priority_creation_id_order(self):
        creator = self.join("fifo-creator")
        worker = self.join("fifo-worker")
        self.run_swarm(
            "task-add", "--agent", creator, "--key", "fifo-low",
            "--kind", "analysis", "--title", "low", "--priority", 50,
        )
        first = self.run_swarm(
            "task-add", "--agent", creator, "--key", "fifo-first",
            "--kind", "analysis", "--title", "first", "--priority", 70,
        )
        second = self.run_swarm(
            "task-add", "--agent", creator, "--key", "fifo-second",
            "--kind", "analysis", "--title", "second", "--priority", 70,
        )
        claimed = self.run_swarm("next", "--agent", worker, "--wait", 0, "--quiet", 999)
        self.assertEqual(first["id"], claimed["id"])
        conn = sqlite3.connect(self.db())
        ranks = dict(conn.execute(
            "SELECT work_id,fifo_rank FROM scheduler_candidates WHERE decision_id=?",
            (claimed["scheduler_decision_id"],),
        ))
        conn.close()
        self.assertEqual(1, ranks[first["id"]])
        self.assertEqual(2, ranks[second["id"]])

    def test_scheduler_ranking_policies_use_decision_time_candidates_only(self):
        candidates = [
            {"work_id": 1, "eligible": 1, "exclusion_reason": None, "priority": 100,
             "age_seconds": 100, "expected_information_gain": .1, "estimated_cost": 1,
             "generation_count": 0, "verification_urgency": 0,
             "workstream_active_claims": 0, "diversity_collision_count": 0,
             "parent_depth": 0, "revisit_trigger_satisfied": 1, "fifo_rank": 1},
            {"work_id": 2, "eligible": 1, "exclusion_reason": None, "priority": 90,
             "age_seconds": 90, "expected_information_gain": .9, "estimated_cost": 1,
             "generation_count": 0, "verification_urgency": 1,
             "workstream_active_claims": 5, "diversity_collision_count": 5,
             "parent_depth": 0, "revisit_trigger_satisfied": 1, "fifo_rank": 2},
            {"work_id": 3, "eligible": 1, "exclusion_reason": None, "priority": 80,
             "age_seconds": 80, "expected_information_gain": .9, "estimated_cost": 1,
             "generation_count": 0, "verification_urgency": 0,
             "workstream_active_claims": 0, "diversity_collision_count": 0,
             "parent_depth": 0, "revisit_trigger_satisfied": 1, "fifo_rank": 3},
            {"work_id": 999, "eligible": 0, "exclusion_reason": "parent-not-done",
             "priority": 1000, "age_seconds": 1000, "expected_information_gain": 1,
             "estimated_cost": 1, "generation_count": 0, "verification_urgency": 1,
             "workstream_active_claims": 0, "diversity_collision_count": 0,
             "parent_depth": 1, "revisit_trigger_satisfied": 1, "fifo_rank": None},
        ]
        self.assertEqual(1, rank_candidates(candidates, "fifo-v1")[0])
        self.assertEqual(2, rank_candidates(candidates, "verify-first-v1")[0])
        self.assertEqual(2, rank_candidates(candidates, "gain-per-cost-v1")[0])
        self.assertEqual(3, rank_candidates(candidates, "diversity-aware-v1")[0])
        for policy in ("fifo-v1", "verify-first-v1", "gain-per-cost-v1", "diversity-aware-v1"):
            self.assertNotIn(999, rank_candidates(candidates, policy))

    def test_strict_replay_rejects_scheduler_decision_claim_mismatch(self):
        creator = self.join("scheduler-replay-creator")
        worker = self.join("scheduler-replay-worker")
        self.run_swarm(
            "task-add", "--agent", creator, "--key", "scheduler-replay",
            "--kind", "analysis", "--title", "scheduler replay",
        )
        self.run_swarm("next", "--agent", worker, "--wait", 0, "--quiet", 999)
        path = self.home / "board" / "scheduler-replay.json"
        self.run_swarm("replay-export", "--strict", "--output", path)
        replayed = subprocess.run(
            ["python3", str(ROOT / "pentest" / "replay.py"), "--events", str(path),
             "--policy", "gain-per-cost-v1", "--strict"], text=True, capture_output=True,
        )
        self.assertEqual(0, replayed.returncode, replayed.stderr)
        result = json.loads(replayed.stdout)
        historical_candidates = {
            item["work_id"] for item in json.loads(path.read_text())["scheduler_candidates"]
        }
        self.assertTrue(all(
            set(item["ranked_work"]) <= historical_candidates for item in result["decision_replay"]
        ))
        corrupted = json.loads(path.read_text())
        corrupted["scheduler_decisions"][0]["claim_id"] = 999999
        corrupt_path = self.home / "board" / "scheduler-mismatch.json"
        corrupt_path.write_text(json.dumps(corrupted))
        rejected = subprocess.run(
            ["python3", str(ROOT / "pentest" / "replay.py"),
             "--events", str(corrupt_path), "--strict"], text=True, capture_output=True,
        )
        self.assertNotEqual(0, rejected.returncode)
        self.assertIn("claim mismatch", rejected.stderr)

    def test_replay_export_is_deterministic_and_strictly_validated(self):
        agent = self.join("replay-agent")
        self.run_swarm(
            "request", "--agent", agent, "--workstream", "replay-stream",
            "--claim", "Generate deterministic work", "--subjects", '["surface:GET /replay"]',
            "--next-actions", '[{"key":"replay-work","title":"Replay work","kind":"experiment"}]',
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
            "join", "--label", "luna-slot-1.fresh-1", "--no-proxy-required",
        )
        self.assertTrue(duplicate["resumed"])
        self.assertEqual(luna, duplicate["agent"])
        for key in ("first", "second"):
            self.run_swarm(
                "task-add", "--agent", creator, "--key", key,
                "--kind", "analysis", "--title", key,
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
                "--kind", "analysis", "--title", key,
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

    def test_canonical_workflow_uses_provider_aware_elastic_ramp(self):
        script = (ROOT / "workflows" / "cohort.js").read_text()
        self.assertIn('runs.run("cohort-mode"', script)
        self.assertIn('agent: "pentest-cohort-selector"', script)
        selector = (ROOT / "agents" / "pentest-cohort-selector.md").read_text()
        recorder = (ROOT / "agents" / "pentest-run-recorder.md").read_text()
        self.assertIn("thinking: xhigh", selector)
        self.assertIn("thinking: xhigh", recorder)
        self.assertIn('cohortNumber === 1 ? "pentest-peer-sonnet" : "pentest-peer"', script)
        self.assertIn("providerKey", script)
        self.assertIn("function rampGate", script)
        self.assertIn("ramp-status", script)
        self.assertIn("var firstPromise = runs.run", script)
        self.assertIn("var secondPromise = runs.run", script)
        self.assertIn("await Promise.race", script)
        self.assertIn("thirdResult = await runs.run", script)
        self.assertNotIn("runs.lanes", script)
        self.assertNotIn('resume: "previous"', script)
        self.assertEqual(3, script.count("acceptance: false"))
        self.assertIn("timeoutMs: 600000", script)
        self.assertIn("8개 claim 완료 또는 7분", script)
        self.assertIn('var providerKey = "claude"', script)
        self.assertIn("status --provider", script)
        self.assertIn("function recordTerminal", script)
        self.assertIn('agent: "pentest-run-recorder"', script)
        self.assertIn("gate: verifyCommand", script)
        self.assertIn("run-result-get", script)
        self.assertIn("function opensCircuit", script)
        self.assertIn("too many requests", script)
        self.assertIn('category === "budget"', script)
        self.assertNotIn('agent: "pentest-peer-luna"', script)
        self.assertNotIn("generation + 1", script)
        self.assertIn("peer-start", script)
        self.assertIn("exec-http", script)
        self.assertIn("checkpoint", script)

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
        self.assertIn("exec-http", proxy_doc)
        self.assertIn("Direct ad-hoc target traffic", proxy_doc)
        self.assertIn("PENTEST_NETWORK_MODE", proxy_env)
        self.assertIn("unset HTTP_PROXY", proxy_env)
        self.assertIn("globalConcurrencyLimit: 3", skill)
        self.assertIn("maxSubagentSpawnsPerRun: 9", skill)
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
        self.assertIn("scheme: https", scope_text)
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

        nonstandard = subprocess.run(
            ["python3", str(WORKSPACE), "ensure", "--target", "http://plain.test:8081",
             "--authorization", "Operator explicitly authorized this unit-test assessment."],
            env=env, text=True, capture_output=True,
        )
        self.assertEqual(0, nonstandard.returncode, nonstandard.stderr)
        plain = json.loads(nonstandard.stdout)
        self.assertEqual("http", plain["scheme"])
        self.assertIn("scheme: http", Path(plain["scope"]).read_text())
        self.assertEqual(0, subprocess.run(
            ["python3", str(SWARM), "init"], env=env, text=True, capture_output=True,
        ).returncode)
        boot = json.loads(subprocess.check_output(
            ["python3", str(SWARM), "peer-start", "--label", "plain-bootstrap",
             "--one-shot", "--proxy-policy", "off"], env=env, text=True,
        ))
        self.assertEqual("http://plain.test:8081/", boot["claim"]["payload"]["url"])
        subprocess.check_output(
            ["python3", str(SWARM), "leave", "--agent", boot["agent"]], env=env, text=True,
        )

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
        self.assertEqual(3, status["cohort"]["target_peers"])

    def test_empty_engagement_materializes_grounded_bootstrap_work(self):
        agent = self.join("peer")
        result = self.run_swarm(
            "next", "--agent", agent, "--wait", 0, "--quiet", 0,
        )
        self.assertEqual("claimed", result["status"])
        self.assertEqual("experiment", result["kind"])
        self.assertTrue(result["key"].endswith(":reachability"))
        conn = sqlite3.connect(self.db())
        self.assertEqual(2, conn.execute(
            "SELECT COUNT(*) FROM work WHERE work_key LIKE 'baseline:%'"
        ).fetchone()[0])
        conn.close()

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

    def test_run_result_usage_telemetry_is_nullable_and_idempotent(self):
        self.join("telemetry-peer")
        recorded = self.run_swarm(
            "run-result", "--label", "telemetry-peer", "--run-id", "telemetry-run",
            "--provider", "test/model", "--status", "failed", "--category", "provider-error",
            "--started-at", 10, "--ended-at", 12.5, "--input-tokens", 100,
            "--output-tokens", 20, "--cache-read-tokens", 5, "--tool-calls", 7,
            "--network-requests", 3,
        )
        self.assertTrue(recorded["recorded"])
        stored = self.run_swarm("run-result-get", "--run-id", "telemetry-run")
        self.assertEqual(10, stored["started_at"])
        self.assertEqual(12.5, stored["ended_at"])
        self.assertEqual(100, stored["input_tokens"])
        self.assertEqual(7, stored["tool_calls"])
        duplicate = self.run_swarm(
            "run-result", "--label", "telemetry-peer", "--run-id", "telemetry-run",
            "--provider", "test/model", "--status", "failed", "--category", "provider-error",
            "--detail", "earlier provider response was 429 too many requests",
        )
        self.assertTrue(duplicate["duplicate"])
        circuit = self.run_swarm("provider-status", "--provider", "test/model")
        self.assertTrue(circuit["blocked"])
        opened_until = circuit["opened_until"]
        self.run_swarm(
            "run-result", "--label", "telemetry-peer", "--run-id", "telemetry-run",
            "--provider", "test/model", "--status", "failed", "--category", "provider-error",
            "--detail", "429 too many requests",
        )
        self.assertEqual(opened_until, self.run_swarm(
            "provider-status", "--provider", "test/model"
        )["opened_until"])
        self.assertEqual(100, self.run_swarm(
            "run-result-get", "--run-id", "telemetry-run",
        )["input_tokens"])
        missing = self.run_swarm(
            "run-result", "--label", "missing-telemetry", "--run-id", "missing-telemetry-run",
            "--status", "failed", "--category", "timeout",
        )
        self.assertTrue(missing["recorded"])
        self.assertIsNone(self.run_swarm(
            "run-result-get", "--run-id", "missing-telemetry-run",
        )["input_tokens"])
        negative = self.run_swarm(
            "run-result", "--label", "x", "--run-id", "negative", "--status", "failed",
            "--category", "timeout", "--tool-calls", -1, check=False,
        )
        self.assertIn("cannot be negative", negative.stderr)

    def test_postflight_classifies_failures_and_releases_leases(self):
        luna = self.join("luna-1.gen-1")
        self.join("claude-1.gen-1")
        self.run_swarm(
            "task-add", "--agent", luna, "--key", "recover-me",
            "--kind", "analysis", "--title", "recover me",
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
        self.assertTrue(self.run_swarm(
            "provider-status", "--provider", "claude"
        )["blocked"])
        second = self.run_cli(POSTFLIGHT, status)
        self.assertTrue(all(r["duplicate"] for r in second["recorded"]))

    def test_atomic_claim_and_expired_lease_recovery(self):
        owner = self.join("owner")
        agents = [self.join(f"peer-{i}") for i in range(12)]
        self.run_swarm(
            "task-add", "--agent", owner, "--key", "GET:/x:server-input",
            "--kind", "analysis", "--title", "test x",
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

    def test_owner_cannot_resurrect_expired_claim(self):
        creator = self.join("expiry-creator")
        owner = self.join("expiry-owner")
        self.run_swarm(
            "task-add", "--agent", creator, "--key", "expiry-owner-work",
            "--kind", "analysis", "--title", "strict expiry",
        )
        claimed = self.run_swarm(
            "next", "--agent", owner, "--wait", 0, "--quiet", 999, "--lease", 1,
        )
        time.sleep(1.1)
        expired = self.run_swarm(
            "next", "--agent", owner, "--wait", 0, "--quiet", 999,
        )
        self.assertEqual("claim-expired", expired["status"])
        self.assertEqual(claimed["claim_id"], expired["previous_claim_id"])
        self.assertEqual("ready", expired["work_state"])
        conn = sqlite3.connect(self.db())
        self.assertEqual("expired", conn.execute(
            "SELECT outcome FROM work_claims WHERE id=?", (claimed["claim_id"],)
        ).fetchone()[0])
        conn.close()

    def test_other_peer_can_claim_immediately_after_expiry(self):
        creator = self.join("takeover-creator")
        owner = self.join("takeover-owner")
        other = self.join("takeover-other")
        self.run_swarm(
            "task-add", "--agent", creator, "--key", "takeover-work",
            "--kind", "analysis", "--title", "take over",
        )
        first = self.run_swarm(
            "next", "--agent", owner, "--wait", 0, "--quiet", 999, "--lease", 1,
        )
        time.sleep(1.1)
        takeover = self.run_swarm(
            "next", "--agent", other, "--wait", 0, "--quiet", 999,
        )
        self.assertEqual("claimed", takeover["status"])
        self.assertEqual(first["id"], takeover["id"])
        self.assertNotEqual(first["claim_id"], takeover["claim_id"])

    def test_expired_owner_cannot_append_claim_scoped_progress(self):
        creator = self.join("expired-progress-creator")
        owner = self.join("expired-progress-owner")
        other = self.join("expired-progress-other")
        self.run_swarm(
            "task-add", "--agent", creator, "--key", "expired-progress",
            "--kind", "analysis", "--title", "expired progress",
        )
        claimed = self.run_swarm(
            "next", "--agent", owner, "--wait", 0, "--quiet", 999, "--lease", 1,
        )
        evidence = self.home / "scratch" / "too-late.txt"
        evidence.write_text("late evidence")
        time.sleep(1.1)
        rejected = self.run_swarm(
            "artifact-add", "--agent", owner, "--work", claimed["id"],
            "--path", evidence, check=False,
        )
        self.assertIn("claim-expired", rejected.stderr)
        conn = sqlite3.connect(self.db())
        self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM work_artifacts").fetchone()[0])
        conn.close()
        takeover = self.run_swarm(
            "next", "--agent", other, "--wait", 0, "--quiet", 999,
        )
        self.assertEqual("claimed", takeover["status"])

    def test_active_nonexpired_claim_is_renewed_normally(self):
        creator = self.join("renew-live-creator")
        owner = self.join("renew-live-owner")
        other = self.join("renew-live-other")
        self.run_swarm(
            "task-add", "--agent", creator, "--key", "renew-live",
            "--kind", "analysis", "--title", "renew live",
        )
        claimed = self.run_swarm(
            "next", "--agent", owner, "--wait", 0, "--quiet", 999, "--lease", 1,
        )
        evidence = self.home / "scratch" / "renew-live.txt"
        evidence.write_text("timely evidence")
        time.sleep(.6)
        added = self.run_swarm(
            "artifact-add", "--agent", owner, "--work", claimed["id"], "--path", evidence,
        )
        self.assertEqual(claimed["claim_id"], added["claim_id"])
        time.sleep(.6)
        result = self.run_swarm(
            "next", "--agent", other, "--wait", 0, "--quiet", 999,
        )
        self.assertNotEqual("claimed", result["status"])

    def test_bookkeeping_activity_does_not_renew_lease(self):
        creator = self.join("creator")
        owner = self.join("owner")
        other = self.join("other")
        self.run_swarm(
            "task-add", "--agent", creator, "--key", "renew-me",
            "--kind", "analysis", "--title", "lease renewal",
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
        self.assertEqual("claimed", result["status"])
        self.assertEqual(claimed["id"], result["id"])

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
        replay_export = self.run_swarm(
            "replay-export", "--strict",
            "--output", self.home / "board" / "finding-replay.json",
        )
        self.assertEqual([], replay_export["validation_errors"])

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
            "attempt-add", "--agent", first, "--work", one["id"],
            "--surface", one["payload"]["surface"],
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
        for i in range(2):
            self.join(f"first-cohort-peer-{i}")
        self.run_swarm(
            "task-add", "--agent", first, "--key", "carry-me", "--kind", "analysis",
            "--title", "Continue this in a fresh context", "--priority", 90,
        )
        claimed = self.run_swarm("next", "--agent", first, "--wait", 0, "--quiet", 999)
        self.run_swarm("leave", "--agent", first, "--summary", "resume carry-me from checkpoint A")
        ended = self.run_swarm("cohort-end", "--reason", "timebox")
        self.assertEqual(1, ended["remaining_work"])
        self.assertFalse(ended["saturation"]["eligible"])

        started = self.run_swarm("cohort-start", "--label", "fresh-2", "--peers", 3)
        self.assertEqual(2, started["number"])
        second = self.join("second")
        for i in range(2):
            self.join(f"second-cohort-peer-{i}")
        dossier = self.run_swarm("dossier")
        self.assertEqual("resume carry-me from checkpoint A", dossier["completed_cohorts"][0]["summary"]["handoffs"][0]["summary"])
        resumed = self.run_swarm("next", "--agent", second, "--wait", 0, "--quiet", 999)
        self.assertEqual(claimed["key"], resumed["key"])
        resumed_evidence = self.home / "scratch" / "resumed.txt"
        resumed_evidence.write_text("fresh-context completion")
        self.run_swarm(
            "artifact-add", "--agent", second, "--work", resumed["id"],
            "--path", resumed_evidence,
        )
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
        conn.execute("DROP INDEX events_claim")
        conn.execute("DROP INDEX work_workstream")
        conn.execute("DROP INDEX one_active_claim_per_actor")
        conn.execute("DROP INDEX one_active_claim_per_work")
        conn.execute("DROP INDEX attempts_claim")
        conn.execute("DROP INDEX findings_claim")
        conn.execute("DROP INDEX attestations_claim")
        conn.execute("DROP INDEX work_artifacts_legacy_unique")
        conn.execute("DROP INDEX scheduler_candidates_work")
        conn.execute("DROP TABLE scheduler_candidates")
        conn.execute("DROP TABLE scheduler_decisions")
        for column in (
            "started_at", "ended_at", "input_tokens", "output_tokens", "cache_read_tokens",
            "tool_calls", "network_requests",
        ):
            conn.execute(f"ALTER TABLE run_results DROP COLUMN {column}")
        conn.execute("ALTER TABLE agents DROP COLUMN proxy_fail_open")
        conn.execute("ALTER TABLE events DROP COLUMN trace_id")
        conn.execute("ALTER TABLE events DROP COLUMN claim_id")
        conn.execute("ALTER TABLE work DROP COLUMN diversity_key")
        conn.execute("ALTER TABLE work DROP COLUMN fingerprint")
        conn.execute("ALTER TABLE attempts DROP COLUMN work_id")
        conn.execute("ALTER TABLE attempts DROP COLUMN claim_id")
        conn.execute("ALTER TABLE findings DROP COLUMN work_id")
        conn.execute("ALTER TABLE findings DROP COLUMN claim_id")
        conn.execute("ALTER TABLE attestations DROP COLUMN work_id")
        conn.execute("ALTER TABLE attestations DROP COLUMN claim_id")
        conn.executescript("""
            ALTER TABLE work_artifacts RENAME TO work_artifacts_v351;
            CREATE TABLE work_artifacts (
                work_id INTEGER NOT NULL REFERENCES work(id),
                artifact_id INTEGER NOT NULL REFERENCES artifacts(id),
                actor_id TEXT NOT NULL REFERENCES agents(id),
                created_at REAL NOT NULL,
                PRIMARY KEY(work_id,artifact_id)
            );
            INSERT OR IGNORE INTO work_artifacts(work_id,artifact_id,actor_id,created_at)
            SELECT work_id,artifact_id,actor_id,created_at FROM work_artifacts_v351;
            DROP TABLE work_artifacts_v351;
            DROP TABLE work_claims;
        """)
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
        work_artifact_columns = {row[1] for row in migrated.execute("PRAGMA table_info(work_artifacts)")}
        claim_columns = {row[1] for row in migrated.execute("PRAGMA table_info(work_claims)")}
        run_result_columns = {row[1] for row in migrated.execute("PRAGMA table_info(run_results)")}
        scheduler_decision_columns = {
            row[1] for row in migrated.execute("PRAGMA table_info(scheduler_decisions)")
        }
        scheduler_candidate_columns = {
            row[1] for row in migrated.execute("PRAGMA table_info(scheduler_candidates)")
        }
        migrated.close()
        self.assertIn("proxy_fail_open", agent_columns)
        self.assertIn("trace_id", event_columns)
        self.assertIn("claim_id", event_columns)
        self.assertIn("diversity_key", work_columns)
        self.assertIn("fingerprint", work_columns)
        self.assertIn("work_id", attempt_columns)
        self.assertIn("claim_id", attempt_columns)
        self.assertIn("work_id", finding_columns)
        self.assertIn("claim_id", finding_columns)
        self.assertIn("work_id", attestation_columns)
        self.assertIn("claim_id", attestation_columns)
        self.assertIn("claim_id", work_artifact_columns)
        self.assertEqual(
            {"id", "engagement_id", "work_id", "actor_id", "generation", "claimed_at",
             "lease_until", "lease_seconds", "no_progress_seconds", "last_progress_at",
             "brief_returned_at", "ended_at", "outcome", "claim_event"}, claim_columns,
        )
        self.assertTrue({
            "started_at", "ended_at", "input_tokens", "output_tokens", "cache_read_tokens",
            "tool_calls", "network_requests",
        } <= run_result_columns)
        self.assertIn("candidate_set_hash", scheduler_decision_columns)
        self.assertIn("exclusion_reason", scheduler_candidate_columns)
        self.assertEqual(1, dossier["active_cohort"]["number"])
        self.assertEqual(1, dossier["active_cohort"]["joined_peers"])
        migrated_next = self.run_swarm(
            "next", "--agent", agent, "--wait", 0, "--quiet", 999,
        )
        self.assertEqual("claimed", migrated_next["status"])
        self.assertEqual("experiment", migrated_next["kind"])

    def test_scope_change_fails_closed(self):
        (self.home / "scope.yaml").write_text(
            'engagement_id: TEST-001\nauthorization: "unit test"\ntargets:\n  - host: changed.test\n'
        )
        proc = self.run_swarm("dossier", check=False)
        self.assertNotEqual(0, proc.returncode)
        self.assertIn("scope.yaml changed", proc.stderr)

    def test_swarmbench_aggregation_and_comparison_are_deterministic(self):
        creator = self.join("bench-creator")
        worker = self.join("bench-worker")
        self.run_swarm(
            "task-add", "--agent", creator, "--key", "bench-work",
            "--kind", "analysis", "--title", "benchmark work", "--workstream", "bench",
        )
        claim = self.run_swarm("next", "--agent", worker, "--wait", 0, "--quiet", 999)
        evidence = self.home / "scratch" / "bench.txt"
        evidence.write_text("benchmark evidence")
        artifact = self.run_swarm(
            "artifact-add", "--agent", worker, "--work", claim["id"], "--path", evidence,
        )
        self.run_swarm("surface-add", "--agent", worker, "--path", "/bench")
        self.run_swarm(
            "attempt-add", "--agent", worker, "--work", claim["id"],
            "--surface", "GET /bench", "--check", "access-control", "--result", "safe",
        )
        self.run_swarm(
            "observe", "--agent", worker, "--work", claim["id"],
            "--claim", "Benchmark observation", "--subjects", '["surface:GET /bench"]',
            "--evidence", json.dumps([f"sha256:{artifact['sha256']}"]),
        )
        self.run_swarm("done", "--agent", worker, "--work", claim["id"])
        self.run_swarm(
            "run-result", "--label", "bench-worker", "--run-id", "bench-run",
            "--status", "completed", "--category", "completed", "--started-at", 10,
            "--ended-at", 20, "--input-tokens", 100, "--output-tokens", 25,
            "--cache-read-tokens", 5, "--tool-calls", 8, "--network-requests", 2,
        )
        replay = self.home / "board" / "bench-replay.json"
        self.run_swarm("replay-export", "--strict", "--output", replay)

        reports = []
        for condition, replay_count in (("solo", 1), ("isolated-parallel", 2), ("shared-swarm", 1)):
            manifest = self.home / "board" / f"{condition}.manifest.json"
            manifest.write_text(json.dumps({
                "schema_version": 1, "benchmark_id": f"bench-{condition}",
                "condition": condition, "repetition": 1, "target_snapshot": "lab-web-v1",
                "wall_clock_budget_seconds": 2700,
                "aggregate_model_budget": {"tokens": 1000, "cost": None},
                "http_request_budget": 100, "starting_credentials": [],
                "target_reset_state": "fixture-reset", "model_profiles": ["test/model"],
                "replays": [replay.name] * replay_count,
            }))
            first = self.home / "board" / f"{condition}.report.json"
            second = self.home / "board" / f"{condition}.report-copy.json"
            self.run_cli(BENCHMARK, "aggregate", "--manifest", manifest, "--output", first)
            self.run_cli(BENCHMARK, "aggregate", "--manifest", manifest, "--output", second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            reports.append(first)
        shared = json.loads(reports[-1].read_text())
        self.assertEqual(1, shared["outcomes"]["new_surfaces"])
        self.assertEqual(1, shared["outcomes"]["tested_applicable_checks"])
        self.assertEqual(100, shared["cost"]["input_tokens"])
        self.assertEqual(10, shared["cost"]["wall_clock_seconds"])
        self.assertGreater(shared["collaboration"]["typed_event_adoption_ratio"], 0)
        comparison = self.home / "board" / "comparison.json"
        command = ["compare"]
        for report in reports:
            command.extend(("--report", report))
        command.extend(("--output", comparison))
        self.run_cli(BENCHMARK, *command)
        compared = json.loads(comparison.read_text())
        self.assertEqual(1, compared["repetitions"]["shared-swarm"])
        self.assertIn("parallelism_gain", compared["deltas"])
        self.assertIn("collaboration_gain", compared["deltas"])

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


class HttpExecutionTest(unittest.TestCase):
    class Handler(BaseHTTPRequestHandler):
        def _respond(self, body=True):
            self.server.requests.append({
                "path": self.path, "headers": dict(self.headers), "method": self.command,
            })
            conn = sqlite3.connect(self.server.db_path)
            self.server.saw_sending.append(conn.execute(
                "SELECT COUNT(*) FROM experiments WHERE status='sending'"
            ).fetchone()[0] > 0)
            conn.close()
            if self.path.startswith("/redirect"):
                self.send_response(302)
                self.send_header("Location", "http://outside.invalid/")
                self.end_headers()
                return
            if self.path.startswith("/slow"):
                time.sleep(.3)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            if self.path.startswith("/trickle"):
                for _ in range(20):
                    try:
                        self.wfile.write(b"x")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        break
                    time.sleep(.03)
                return
            if body:
                try:
                    self.wfile.write(b"atomic-response")
                except BrokenPipeError:
                    pass

        def do_GET(self):
            self._respond()

        def do_HEAD(self):
            self._respond(False)

        def log_message(self, *_):
            pass

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        for name in ("state", "scratch", "findings", "board", "memory", "research/board"):
            (self.home / name).mkdir(parents=True, exist_ok=True)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.Handler)
        self.server.requests = []
        self.server.saw_sending = []
        port = self.server.server_port
        (self.home / "scope.yaml").write_text(
            f'engagement_id: HTTP-TEST\nauthorization: "unit test"\ntargets:\n'
            f'  - host: 127.0.0.1\n    scheme: http\n    ports: [{port}]\n'
        )
        self.env = {**os.environ, "PENTEST_HOME": str(self.home)}
        self.run_swarm("init")
        self.server.db_path = self.home / "state" / "HTTP-TEST.sqlite3"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.tmp.cleanup()

    def run_swarm(self, *args, check=True):
        proc = subprocess.run(
            ["python3", str(SWARM), *map(str, args)], env=self.env,
            text=True, capture_output=True,
        )
        if not check:
            return proc
        self.assertEqual(0, proc.returncode, proc.stderr)
        return json.loads(proc.stdout)

    def start(self, label="http-peer", lease=30, no_progress=20):
        return self.run_swarm(
            "peer-start", "--label", label, "--continuous", "--proxy-policy", "off",
            "--lease", lease, "--no-progress", no_progress,
        )

    def execute(self, started, path="/", method="GET", check="baseline-reachability",
                timeout=2):
        return self.run_swarm(
            "exec-http", "--agent", started["agent"], "--work", started["claim"]["id"],
            "--method", method, "--url", self.url + path, "--check", check,
            "--timeout", timeout, "--expected", '{"status":[200,302]}',
        )

    def checkpoint(self, started, experiment, **extra):
        args = [
            "checkpoint", "--agent", started["agent"], "--work", started["claim"]["id"],
            "--experiment", experiment["experiment_id"], "--message-type", "observation",
            "--claim", "host runner captured a bounded response", "--finish", "done",
        ]
        for key, value in extra.items():
            args.extend(("--" + key.replace("_", "-"), value))
        return self.run_swarm(*args)

    def test_peer_start_recovers_same_active_label(self):
        first = self.start("recoverable-start")
        recovered = self.start("recoverable-start")
        self.assertTrue(recovered["resumed"])
        self.assertEqual(first["agent"], recovered["agent"])
        self.assertEqual(first["claim"]["claim_id"], recovered["claim"]["claim_id"])
        conn = sqlite3.connect(self.server.db_path)
        self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0])
        conn.close()

    def test_exec_http_is_fenced_durable_idempotent_and_checkpointed(self):
        started = self.start()
        executed = self.execute(started, "/root")
        self.assertEqual("executed", executed["status"])
        self.assertEqual(200, executed["status_code"])
        self.assertEqual(2, len(executed["artifact_refs"]))
        self.assertTrue(all(self.server.saw_sending))
        headers = {key.lower(): value for key, value in self.server.requests[0]["headers"].items()}
        self.assertEqual(started["agent"], headers["x-redteam-agent"])
        self.assertEqual(str(started["claim"]["claim_id"]), headers["x-redteam-claim"])
        self.assertEqual(str(executed["experiment_id"]), headers["x-redteam-experiment"])
        before = len(self.server.requests)
        duplicate = self.execute(started, "/root")
        self.assertEqual("resume-required", duplicate["status"])
        self.assertEqual(before, len(self.server.requests))
        finished = self.checkpoint(started, executed)
        self.assertEqual("done", finished["state"])
        replay = self.run_swarm(
            "replay-export", "--strict", "--output", self.home / "board" / "http.json",
        )
        self.assertEqual([], replay["validation_errors"])
        metrics = self.run_swarm("metrics")["execution"]
        self.assertEqual(1, metrics["durable_assertions"])
        self.assertEqual(1, metrics["completed_assertions"])

    def test_replay_redacts_secrets_and_rejects_provenance_corruption(self):
        started = self.start("secret-peer")
        secret = "unit-secret-token"
        executed = self.run_swarm(
            "exec-http", "--agent", started["agent"], "--work", started["claim"]["id"],
            "--url", self.url + "/secret?token=" + secret,
            "--check", "baseline-reachability", "--timeout", 2,
            "--headers", json.dumps({"Authorization": "Bearer " + secret,
                                     "Cookie": "session=" + secret}),
        )
        self.checkpoint(started, executed)
        replay_path = self.home / "board" / "secret-replay.json"
        self.run_swarm("replay-export", "--strict", "--output", replay_path)
        replay = json.loads(replay_path.read_text())
        self.assertNotIn(secret, replay_path.read_text())
        self.assertEqual(0o600, replay_path.stat().st_mode & 0o777)
        experiment = replay["experiments"][0]
        self.assertFalse("?" in experiment["spec"]["url"])
        request_artifact = next(
            item for item in replay["artifacts"]
            if item["id"] == experiment["request_artifact_id"]
        )
        raw_request = self.home / request_artifact["path"]
        self.assertIn(secret, raw_request.read_text())
        self.assertEqual(0o600, raw_request.stat().st_mode & 0o777)

        bad_fingerprint = json.loads(json.dumps(replay))
        bad_fingerprint["experiments"][0]["spec"]["headers"][0]["value_sha256"] = "0" * 64
        self.assertTrue(any("invalid request fingerprint" in error
                            for error in validate_replay(bad_fingerprint)))

        bad_link = json.loads(json.dumps(replay))
        response_id = bad_link["experiments"][0]["response_artifact_id"]
        bad_link["work_artifacts"] = [
            item for item in bad_link["work_artifacts"] if item["artifact_id"] != response_id
        ]
        self.assertTrue(any("response_artifact_id provenance" in error
                            for error in validate_replay(bad_link)))

        bad_checkpoint = json.loads(json.dumps(replay))
        checkpoint_id = bad_checkpoint["experiments"][0]["checkpoint_event_id"]
        checkpoint = next(item for item in bad_checkpoint["events"] if item["seq"] == checkpoint_id)
        missing = f"artifact:{response_id}"
        checkpoint["evidence_refs"].remove(missing)
        checkpoint["body"]["evidence_refs"].remove(missing)
        self.assertTrue(any("checkpoint omits provenance" in error
                            for error in validate_replay(bad_checkpoint)))

    def test_scope_header_redirect_timeout_and_expired_claim_fail_closed(self):
        started = self.start()
        outside = self.run_swarm(
            "exec-http", "--agent", started["agent"], "--work", started["claim"]["id"],
            "--url", "http://example.invalid/", "--check", "baseline", check=False,
        )
        self.assertIn("outside scope", outside.stderr)
        forged = self.run_swarm(
            "exec-http", "--agent", started["agent"], "--work", started["claim"]["id"],
            "--url", self.url + "/", "--check", "baseline", "--headers",
            '{"X-Redteam-Claim":"forged"}', check=False,
        )
        self.assertIn("host-owned", forged.stderr)
        for forbidden_headers in ('{"Host":"outside.invalid"}',
                                  '{"Transfer-Encoding":"chunked"}',
                                  '{"Content-Length":"999"}'):
            rejected = self.run_swarm(
                "exec-http", "--agent", started["agent"],
                "--work", started["claim"]["id"], "--url", self.url + "/",
                "--check", "baseline", "--headers", forbidden_headers, check=False,
            )
            self.assertIn("host-owned", rejected.stderr)
        redirect = self.execute(started, "/redirect")
        self.assertEqual(302, redirect["status_code"])
        self.assertEqual(1, len(self.server.requests))

        other = self.start("timeout-peer")
        timed = self.execute(other, "/slow", timeout=.1)
        self.assertEqual("error", timed["experiment_status"])
        trickle_peer = self.start("trickle-peer")
        trickled = self.execute(trickle_peer, "/trickle", timeout=.12)
        self.assertEqual("error", trickled["experiment_status"])
        self.assertLess(trickled["elapsed_ms"], 300)
        conn = sqlite3.connect(self.server.db_path)
        conn.execute("UPDATE work_claims SET lease_until=0 WHERE id=?",
                     (started["claim"]["claim_id"],))
        conn.commit(); conn.close()
        count = len(self.server.requests)
        fenced = self.run_swarm(
            "exec-http", "--agent", started["agent"], "--work", started["claim"]["id"],
            "--url", self.url + "/late", "--check", "baseline", check=False,
        )
        self.assertIn("claim-expired", fenced.stderr)
        self.assertEqual(count, len(self.server.requests))

    def test_prepared_experiment_is_safely_rebound_after_termination(self):
        first = self.start("prepared-first")
        scratch = self.home / "scratch"
        scratch.chmod(0o500)
        try:
            failed = self.run_swarm(
                "exec-http", "--agent", first["agent"], "--work", first["claim"]["id"],
                "--url", self.url + "/prepared", "--check", "baseline-reachability",
                "--timeout", 2, check=False,
            )
        finally:
            scratch.chmod(0o700)
        self.assertNotEqual(0, failed.returncode)
        self.assertEqual(0, len(self.server.requests))
        conn = sqlite3.connect(self.server.db_path)
        self.assertEqual("prepared", conn.execute(
            "SELECT status FROM experiments"
        ).fetchone()[0])
        conn.close()
        self.run_swarm(
            "fail", "--agent", first["agent"], "--work", first["claim"]["id"],
            "--summary", "host process terminated before send",
        )
        second = self.start("prepared-second")
        self.assertEqual("prepared", second["claim"]["brief"]["partial_experiments"][0]["status"])
        executed = self.execute(second, "/prepared")
        self.assertEqual("executed", executed["status"])
        self.assertEqual(1, len(self.server.requests))
        self.checkpoint(second, executed)
        replay = self.run_swarm(
            "replay-export", "--strict", "--output", self.home / "board" / "prepared.json",
        )
        self.assertEqual([], replay["validation_errors"])

    def test_forced_termination_preserves_sending_uncertainty(self):
        first = self.start("killed-sender")
        command = [
            "python3", str(SWARM), "exec-http", "--agent", first["agent"],
            "--work", str(first["claim"]["id"]), "--url", self.url + "/slow",
            "--check", "baseline-reachability", "--timeout", "2",
        ]
        process = subprocess.Popen(command, env=self.env, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.time() + 3
        saw_sending = False
        while time.time() < deadline:
            conn = sqlite3.connect(self.server.db_path)
            row = conn.execute("SELECT status FROM experiments ORDER BY id DESC LIMIT 1").fetchone()
            conn.close()
            if row and row[0] == "sending":
                saw_sending = True
                break
            time.sleep(.01)
        self.assertTrue(saw_sending)
        process.terminate()
        try:
            process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=2)
        request_deadline = time.time() + 1
        while not self.server.requests and time.time() < request_deadline:
            time.sleep(.01)
        before = len(self.server.requests)
        self.assertLessEqual(before, 1)
        self.run_swarm(
            "fail", "--agent", first["agent"], "--work", first["claim"]["id"],
            "--summary", "host process was forcibly terminated after send fencing",
        )
        second = self.start("killed-takeover")
        uncertain = second["claim"]["brief"]["partial_experiments"][0]
        self.assertEqual("sending", uncertain["status"])
        duplicate = self.execute(second, "/slow")
        self.assertEqual("indeterminate", duplicate["status"])
        self.assertEqual(before, len(self.server.requests))
        self.run_swarm(
            "fail", "--agent", second["agent"], "--work", second["claim"]["id"],
            "--summary", "indeterminate send requires operator resolution",
        )
        replay = self.run_swarm(
            "replay-export", "--strict", "--output", self.home / "board" / "killed.json",
        )
        self.assertEqual([], replay["validation_errors"])

    def test_partial_takeover_reuses_response_without_second_request(self):
        first = self.start("first")
        executed = self.execute(first, "/takeover")
        self.run_swarm(
            "fail", "--agent", first["agent"], "--work", first["claim"]["id"],
            "--summary", "model terminated after response capture",
        )
        second = self.run_swarm(
            "peer-start", "--label", "second", "--continuous", "--proxy-policy", "off",
            "--lease", 30, "--no-progress", 20,
        )
        self.assertEqual(first["claim"]["id"], second["claim"]["id"])
        partial = second["claim"]["brief"]["partial_experiments"]
        self.assertEqual(executed["experiment_id"], partial[0]["id"])
        before = len(self.server.requests)
        resumed = self.run_swarm(
            "exec-http", "--agent", second["agent"], "--work", second["claim"]["id"],
            "--method", "GET", "--url", self.url + "/takeover",
            "--check", "baseline-reachability", "--timeout", 2,
        )
        self.assertEqual("resume-required", resumed["status"])
        self.assertEqual(before, len(self.server.requests))
        self.checkpoint(second, resumed)
        conn = sqlite3.connect(self.server.db_path)
        self.assertEqual(1, conn.execute(
            "SELECT COUNT(*) FROM events WHERE kind='work.partial'"
        ).fetchone()[0])
        self.assertEqual([(1, "released"), (2, "done")], conn.execute(
            "SELECT generation,outcome FROM work_claims WHERE work_id=? ORDER BY generation",
            (first["claim"]["id"],),
        ).fetchall())
        conn.close()
        replay = self.run_swarm(
            "replay-export", "--strict", "--output", self.home / "board" / "takeover.json",
        )
        self.assertEqual([], replay["validation_errors"])

    def test_no_progress_stalls_and_provider_429_opens_global_circuit(self):
        first = self.start("stalled", lease=3, no_progress=1)
        time.sleep(1.1)
        before = len(self.server.requests)
        stale = self.run_swarm(
            "exec-http", "--agent", first["agent"], "--work", first["claim"]["id"],
            "--url", self.url + "/stale", "--check", "baseline", "--timeout", .2,
            check=False,
        )
        self.assertIn("claim-stalled", stale.stderr)
        self.assertEqual(before, len(self.server.requests))
        other = self.start("takeover", lease=30, no_progress=20)
        self.assertEqual(first["claim"]["id"], other["claim"]["id"])
        conn = sqlite3.connect(self.server.db_path)
        self.assertEqual("stalled", conn.execute(
            "SELECT outcome FROM work_claims WHERE id=?", (first["claim"]["claim_id"],)
        ).fetchone()[0])
        conn.close()
        self.run_swarm(
            "run-result", "--label", "stalled", "--run-id", "provider-429",
            "--provider", "claude", "--status", "failed",
            "--category", "timeout", "--detail", "429 too many requests",
        )
        status = self.run_swarm("provider-status", "--provider", "claude")
        self.assertTrue(status["blocked"])
        self.assertTrue(self.run_swarm(
            "status", "--provider", "claude"
        )["provider"]["blocked"])
        ramp = self.run_swarm(
            "ramp-status", "--provider", "claude", "--stage", 2,
        )
        self.assertTrue(ramp["blocked"])
        self.assertFalse(ramp["ready"])

    def test_populated_v36_claim_table_migrates_with_child_foreign_keys(self):
        started = self.start("migration-peer")
        executed = self.execute(started, "/migration")
        conn = sqlite3.connect(self.server.db_path, isolation_level=None)
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("PRAGMA legacy_alter_table=ON")
        conn.executescript("""
            BEGIN IMMEDIATE;
            ALTER TABLE work_claims RENAME TO work_claims_v37;
            CREATE TABLE work_claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                engagement_id TEXT NOT NULL REFERENCES engagements(id),
                work_id INTEGER NOT NULL REFERENCES work(id),
                actor_id TEXT NOT NULL REFERENCES agents(id),
                generation INTEGER NOT NULL,
                claimed_at REAL NOT NULL,
                lease_until REAL NOT NULL,
                lease_seconds REAL,
                no_progress_seconds REAL,
                last_progress_at REAL,
                brief_returned_at REAL,
                ended_at REAL,
                outcome TEXT CHECK(outcome IS NULL OR outcome IN (
                    'done','released','expired','failed','refusal','interrupted','superseded'
                )),
                claim_event INTEGER REFERENCES events(seq),
                UNIQUE(work_id,generation)
            );
            INSERT INTO work_claims SELECT * FROM work_claims_v37;
            DROP TABLE work_claims_v37;
            COMMIT;
        """)
        conn.close()

        self.run_swarm("dossier")
        migrated = sqlite3.connect(self.server.db_path)
        sql = migrated.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='work_claims'"
        ).fetchone()[0]
        self.assertIn("'stalled'", sql)
        self.assertEqual([], migrated.execute("PRAGMA foreign_key_check").fetchall())
        self.assertEqual(started["claim"]["claim_id"], migrated.execute(
            "SELECT claim_id FROM experiments WHERE id=?", (executed["experiment_id"],)
        ).fetchone()[0])
        migrated.close()
        replay = self.run_swarm(
            "replay-export", "--strict", "--output", self.home / "board" / "migrated.json",
        )
        self.assertEqual([], replay["validation_errors"])

    def test_ten_atomic_baselines_all_produce_attempts_and_completion(self):
        started = self.start("reliability")
        current = started
        for index in range(10):
            claim = current["claim"]
            check = claim["payload"].get("check", "baseline-reachability")
            method = claim["payload"].get("method") or claim["payload"].get(
                "surface", "GET /"
            ).split()[0]
            executed = self.execute(current, "/", method=method, check=check)
            self.checkpoint(current, executed)
            if index < 9:
                next_claim = self.run_swarm(
                    "next", "--agent", started["agent"], "--wait", 0, "--quiet", 999,
                    "--brief", "--lease", 30, "--no-progress", 20,
                )
                current = {"agent": started["agent"], "claim": next_claim}
        metrics = self.run_swarm("metrics")
        self.assertEqual(10, metrics["execution"]["durable_assertions"])
        self.assertEqual(10, metrics["execution"]["completed_assertions"])
        self.assertEqual(0, metrics["claims"]["expired_rate"])
        self.assertEqual(0, metrics["claims"]["stalled_rate"])


if __name__ == "__main__":
    unittest.main()
