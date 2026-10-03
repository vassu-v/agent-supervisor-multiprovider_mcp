"""Chaos tests: hard-killed daemon + restart on the same home, hostile request bodies, concurrency, lifecycle abuse.
Scripted fake agents only (no model, no cost). Run: python tests/test_chaos.py
"""
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tests.harness import api, read_http_log, spawn_fake, start_daemon  # noqa: E402
from tests.test_board_e2e import (announce, audit_file, autos, board, by_path, info, mkrepo,  # noqa: E402
                                  settled, wait_until, ws_of)
from tests.test_board_e2e import http as step_http  # noqa: E402


def make_home():
    home = tempfile.mkdtemp(prefix="sy-chaos-home-")
    os.makedirs(os.path.join(home, "orch"))
    with open(os.path.join(home, "orch", "config.json"), "w") as f:
        json.dump({"max_concurrent": 200}, f)
    return home


def child_pids(pid):
    """pids of live processes whose parent is `pid` (Windows: CIM; else ps)."""
    if sys.platform == "win32":
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              f"(Get-CimInstance Win32_Process -Filter 'ParentProcessId={int(pid)}').ProcessId"],
                             capture_output=True, text=True, timeout=60).stdout
    else:
        out = subprocess.run(["ps", "-o", "pid=", "--ppid", str(pid)], capture_output=True, text=True).stdout
    return [int(x) for x in out.split() if x.strip().isdigit()]


def raw(d, method, path, body=b"", headers=None, token=None):
    """Low-level request; -> (status, text) or (None, repr(exception)) when the server dropped the connection."""
    c = http.client.HTTPConnection("127.0.0.1", d.port, timeout=30)
    h = {"Host": f"127.0.0.1:{d.port}", "Authorization": f"Bearer {token or d.token}"}
    h.update(headers or {})
    try:
        c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for k, v in h.items():
            c.putheader(k, v)
        if "Content-Length" not in h:
            c.putheader("Content-Length", str(len(body)))
        c.endheaders()
        if body:
            c.send(body)
        r = c.getresponse()
        return r.status, r.read().decode("utf-8", "replace")
    except (OSError, http.client.HTTPException) as e:
        return None, repr(e)
    finally:
        c.close()


class Restart(unittest.TestCase):
    """Kill -9 the daemon while a swarm is writing, restart it on the SAME SWITCHYARD_HOME."""

    def test_hard_kill_mid_swarm_then_restart(self):
        home, tmp = make_home(), tempfile.mkdtemp(prefix="sy-chaos-")
        d = start_daemon(home=home)
        try:
            r = mkrepo(tmp, "kill_repo")
            ws = ws_of(d, r)
            for i in range(8):
                st, res = spawn_fake(d, [announce(f"before kill {i}", "started"),
                                         {"random_walk": {"seed": i, "steps": 200}}],
                                     os.path.join(r, "shared"), id=f"k{i}", opts={"step_delay": 0.05})
                self.assertEqual(st, 200, res)
            wait_until(lambda: len([p for p in board(d, ws) if p["kind"] != "auto"]) >= 12, 30, "posts before the kill")
            before = board(d, ws)
            busy = [a for a in (f"k{i}" for i in range(8)) if (info(d, a) or {}).get("status") == "busy"]
            self.assertTrue(busy, "the kill must land mid-swarm")
            kids = child_pids(d.proc.pid)
            subprocess.run(["taskkill", "/PID", str(d.proc.pid), "/F"] if sys.platform == "win32"
                           else ["kill", "-9", str(d.proc.pid)], capture_output=True)
            d.proc.wait(15)
            deadline = time.time() + 15
            while time.time() < deadline and [p for p in kids if _alive(p)]:
                time.sleep(0.2)
            orphans = [p for p in kids if _alive(p)]
            self.assertFalse(orphans, f"orphan processes from the killed daemon: {orphans}")

            d2 = start_daemon(home=home)
            try:
                after = board(d2, ws)
                ids_after = {p["id"] for p in after}
                self.assertTrue({p["id"] for p in before} <= ids_after, "committed posts survive a hard kill")
                for a, b in zip(sorted(before, key=lambda p: p["id"]), sorted(after, key=lambda p: p["id"])):
                    self.assertEqual((a["id"], a["text"], a["sender"]), (b["id"], b["text"], b["sender"]))
                self.assertEqual(api(d2, "GET", "/api/list?all=1")[1], [], "the agent registry is in memory only")
                top = max(ids_after)
                st, res = spawn_fake(d2, [step_http("GET", "/api/board?n=5"), announce("after restart")],
                                     os.path.join(r, "fresh"), id="fresh0")
                self.assertEqual(st, 200, res)
                settled(d2, "fresh0")
                log = read_http_log(os.path.join(r, "fresh"))
                self.assertEqual(by_path(log, "/api/board")[0]["status"], 200, "the new agent's fresh token works")
                ann = by_path(log, "/api/announce")[0]
                self.assertEqual(ann["status"], 200, ann)
                self.assertGreater(ann["response"]["id"], top, "post ids keep increasing across restarts")
                errs = [a for a in audit_file(d2) if a.get("kind") in ("core_error", "board_error")]
                self.assertFalse(errs, errs[:3])
            finally:
                d2.stop()
        finally:
            d.stop()
            shutil.rmtree(home, ignore_errors=True)
            shutil.rmtree(tmp, ignore_errors=True)

    def test_BUG_old_agent_id_reused_after_restart(self):
        """core.spawn: "ids are never reused", but the check is in-memory (self.agents) only. After a restart on the same
        home an old id is accepted and the newcomer silently inherits the dead agent's persisted board identity
        (rate-limit budget, dedupe window, cursor, agent_meta row, logs/<aid>.jsonl). Expected: refuse the id (4xx) or
        give the newcomer a clean identity."""
        home, tmp = make_home(), tempfile.mkdtemp(prefix="sy-chaos-")
        d = start_daemon(home=home)
        try:
            r = mkrepo(tmp, "reuse_repo")
            st, res = spawn_fake(d, [announce(f"old k0 post {i}") for i in range(6)], os.path.join(r, "old"), id="k0")
            self.assertEqual(st, 200, res)
            settled(d, "k0")
            subprocess.run(["taskkill", "/PID", str(d.proc.pid), "/F"] if sys.platform == "win32"
                           else ["kill", "-9", str(d.proc.pid)], capture_output=True)
            d.proc.wait(15)
            d2 = start_daemon(home=home)
            try:
                st, res = spawn_fake(d2, [announce("reborn k0 posts")], os.path.join(r, "reborn"), id="k0")
                if st == 200:
                    settled(d2, "k0")
                    ann = by_path(read_http_log(os.path.join(r, "reborn")), "/api/announce")[0]
                    self.assertEqual(ann["status"], 200, "id k0 reused after a restart inherits the old agent's "
                                                         f"rate limit: {ann['response']}")
                else:
                    self.assertTrue(400 <= st < 500, res)
            finally:
                d2.stop()
        finally:
            d.stop()
            shutil.rmtree(home, ignore_errors=True)
            shutil.rmtree(tmp, ignore_errors=True)

    def test_last_good_policy(self):
        self.skipTest("policy.json is always read from the repo (orch/policy.json, SPEC 7) and SWITCHYARD_HOME does "
                      "not redirect it; corrupting it would mean editing a repo file, which this suite must not do")


def _alive(pid):
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class Hostile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = make_home()
        cls.d = start_daemon(home=cls.home)
        cls.tmp = tempfile.mkdtemp(prefix="sy-chaos-")
        cls.repo = mkrepo(cls.tmp, "hostile")
        cls.ws = ws_of(cls.d, cls.repo)

    @classmethod
    def tearDownClass(cls):
        cls.d.stop()
        shutil.rmtree(cls.home, ignore_errors=True)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def healthy(self):
        st, r = api(self.d, "GET", "/api/health")
        self.assertEqual(st, 200, r)
        self.assertIsNone(self.d.proc.poll(), "daemon died")

    # ------------------------------------------------------------------ bodies
    def test_one_megabyte_announce(self):
        body = json.dumps({"ws": self.ws, "text": "A" * (1024 * 1024), "kind": "info"}).encode()
        st, txt = raw(self.d, "POST", "/api/announce", body, {"Content-Type": "application/json"})
        self.assertTrue(st is None or 400 <= st < 500, f"1 MB body: {st} {txt[:200]}")
        if st is not None:
            self.assertEqual(st, 413, txt[:200])
        self.healthy()
        body = json.dumps({"ws": self.ws, "text": "B" * 900_000, "kind": "info"}).encode()
        st, txt = raw(self.d, "POST", "/api/announce", body, {"Content-Type": "application/json"})
        self.assertEqual(st, 400, txt[:200])
        self.assertIn("too long", txt)
        st, txt = raw(self.d, "POST", "/api/announce", b"x" * 10, {"Content-Length": "999999999999"})
        self.assertTrue(st is None or 400 <= st < 500, (st, txt[:200]))
        self.healthy()
        self.assertFalse([p for p in board(self.d, self.ws) if p["text"].startswith(("AAAA", "BBBB"))])

    def test_malformed_json(self):
        cases = {"not json": b"{not json", "array": b"[1,2,3]", "string": b'"hi"', "bad utf8": b'{"text": "\xff\xfe"}',
                 "nul": b"\x00\x00", "text is a number": json.dumps({"ws": self.ws, "text": 5}).encode(),
                 "missing text": json.dumps({"ws": self.ws}).encode(),
                 "paths not a list": json.dumps({"ws": self.ws, "text": "p", "paths": {"a": 1}}).encode(),
                 "kind bogus": json.dumps({"ws": self.ws, "text": "k", "kind": "urgent"}).encode()}
        for name, b in cases.items():
            st, txt = raw(self.d, "POST", "/api/announce", b, {"Content-Type": "application/json"})
            self.assertEqual(st, 400, f"{name}: {st} {txt[:200]}")
        st, txt = raw(self.d, "POST", "/api/announce", b"{}", {"Content-Length": "abc"})
        self.assertEqual(st, 400, txt)
        self.healthy()

    def test_BUG_deeply_nested_json_drops_connection(self):
        """A 1 MB-limit body of 200k nested brackets makes json.loads raise RecursionError, which is NOT a ValueError:
        server.py do_POST only catches ValueError around json.loads, so the request thread dies without answering."""
        st, txt = raw(self.d, "POST", "/api/announce", b"[" * 200_000 + b"]" * 200_000,
                      {"Content-Type": "application/json"})
        self.healthy()
        self.assertEqual(st, 400, f"server dropped the connection instead of answering 400: {txt[:200]}")

    # ------------------------------------------------------------------ concurrency
    def test_200_concurrent_announces_from_5_agents(self):
        cwds = [os.path.join(self.repo, f"burst{i}") for i in range(5)]
        for i, c in enumerate(cwds):
            st, r = spawn_fake(self.d, [announce(f"burst {i} #{k}") for k in range(40)], c, id=f"burst{i}")
            self.assertEqual(st, 200, r)
        for i in range(5):
            settled(self.d, f"burst{i}", timeout=60)
        accepted = []
        for i, c in enumerate(cwds):
            sts = [e["status"] for e in by_path(read_http_log(c), "/api/announce")]
            self.assertEqual(len(sts), 40)
            self.assertFalse([s for s in sts if s >= 500 or s == 0], f"5xx / dropped: {sts}")
            self.assertEqual(sts.count(200), 6, f"agent burst{i}: rate limit must hold at 6: {sts}")
            self.assertTrue(all(400 <= s < 500 for s in sts if s != 200))
            accepted += [e["response"]["id"] for e in by_path(read_http_log(c), "/api/announce") if e["status"] == 200]
        self.assertEqual(len(set(accepted)), 30)
        ids = [p["id"] for p in board(self.d, self.ws)]
        self.assertEqual(ids, sorted(set(ids)), "ids unique and monotonic")
        on_board = [p for p in board(self.d, self.ws) if p["sender"].startswith("agent:burst") and p["kind"] == "info"]
        self.assertEqual(len(on_board), 30)
        self.healthy()

    def test_concurrent_answers_only_one_wins(self):
        st, q = api(self.d, "POST", "/api/ask", {"ws": self.ws, "text": "race: who answers first?", "by": "qa-admin"})
        self.assertEqual(st, 200, q)
        cwds = [os.path.join(self.repo, f"race{i}") for i in range(8)]
        # each racer first sleeps until a common wall-clock instant so the answers really collide
        go = time.time() + 2.0
        for i, c in enumerate(cwds):
            st, r = spawn_fake(self.d, [{"sleep": max(0.0, go - time.time() - 0.2)},
                                        step_http("POST", "/api/answer", {"id": q["id"], "text": f"racer {i} wins"})],
                               c, id=f"race{i}")
            self.assertEqual(st, 200, r)
        for i in range(8):
            settled(self.d, f"race{i}", timeout=30)
        sts = [by_path(read_http_log(c), "/api/answer")[0]["status"] for c in cwds]
        self.assertEqual(sts.count(200), 1, sts)
        self.assertFalse([s for s in sts if s >= 500])
        answers = [p for p in board(self.d, self.ws) if p["kind"] == "answer" and p["reply_to"] == q["id"]]
        self.assertEqual(len(answers), 1)

    # ------------------------------------------------------------------ lifecycle abuse
    def test_lifecycle_abuse(self):
        c = os.path.join(self.repo, "life")
        st, r = spawn_fake(self.d, [{"say": "x"}], c, id="life1")
        self.assertEqual(st, 200, r)
        settled(self.d, "life1")
        st, r = spawn_fake(self.d, [{"say": "dup"}], c, id="life1")
        self.assertTrue(400 <= st < 500, f"spawning with a taken id: {st} {r}")
        st, r = api(self.d, "POST", "/api/stop", {"id": "life1", "reason": "first"})
        self.assertEqual(st, 200, r)
        st, r = api(self.d, "POST", "/api/stop", {"id": "life1", "reason": "second"})
        self.assertTrue(st == 200 or 400 <= st < 500, f"second stop: {st} {r}")
        self.assertEqual(len(autos(board(self.d, self.ws), "stopped", "life1")), 1, "one stopped event for one agent")
        st, r = api(self.d, "POST", "/api/send", {"id": "life1", "msg": "are you there?"})
        self.assertTrue(400 <= st < 500, f"send to a dead agent: {st} {r}")
        st, r = api(self.d, "POST", "/api/send", {"id": "life1", "msg": "x", "mode": "interrupt"})
        self.assertTrue(400 <= st < 500, (st, r))
        st, r = api(self.d, "POST", "/api/interrupt", {"id": "life1"})
        self.assertLess(st, 500, r)
        for path, body in (("/api/stop", {"id": "nobody", "reason": "x"}), ("/api/send", {"id": "nobody", "msg": "x"}),
                           ("/api/stop", {"id": "life1"}), ("/api/spawn", {"provider": "fake", "task": "[]"}),
                           ("/api/spawn", {"provider": "fake", "task": "[]", "cwd": c, "id": "../../evil"}),
                           ("/api/spawn", {"provider": "fake", "task": "[]", "cwd": c, "id": "x" * 40})):
            st, r = api(self.d, "POST", path, body)
            self.assertTrue(400 <= st < 500, f"{path} {body}: {st} {r}")
        st, r = spawn_fake(self.d, [{"say": "reborn"}], c, id="life1")
        self.assertTrue(400 <= st < 500, f"ids are never reused (core.spawn), even a dead agent's: {st} {r}")
        self.healthy()


if __name__ == "__main__":
    unittest.main(verbosity=2)
