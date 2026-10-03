"""End-to-end identity / workspace / hierarchy tests against a real isolated daemon with scripted fake agents (no model, no cost).
Run: python tests/test_identity_e2e.py"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.harness import api, read_http_log, spawn_fake, start_daemon, wait_status  # noqa: E402


def http(method, path, body=None, **kw):
    step = {"http": {"method": method, "path": path}}
    if body is not None:
        step["http"]["body"] = body
    return {**step, **kw}


def by_path(log, path, method=None):
    return [e for e in log if e["path"].split("?")[0] == path and (method is None or e["method"] == method)]


class E2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = start_daemon()
        cls.tmp = tempfile.mkdtemp(prefix="sy-e2e-")
        cls.A = os.path.join(cls.tmp, "repoA")
        cls.B = os.path.join(cls.tmp, "repoB")
        for r in (cls.A, cls.B):
            os.makedirs(os.path.join(r, ".git"))
        st, w = api(cls.d, "GET", "/api/workspace?path=" + cls.A)
        cls.wsA = w["id"]
        st, w = api(cls.d, "GET", "/api/workspace?path=" + cls.B)
        cls.wsB = w["id"]

    @classmethod
    def tearDownClass(cls):
        cls.d.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def spawn(self, script, cwd, **kw):
        st, r = spawn_fake(self.d, script, cwd, **kw)
        self.assertEqual(st, 200, r)
        return r

    # ------------------------------------------------------------------ scopes, spoofing, isolation
    def test_agent_scope_isolation_and_spoofing(self):
        b = self.spawn([{"say": "b"}], os.path.join(self.B, "svc"), id="iso-b")
        wait_status(self.d, "iso-b", "idle")
        script = [
            http("POST", "/api/resolve", {"escalation": "e1", "decision": "allow"}),
            http("POST", "/api/send", {"id": "iso-b", "msg": "hi"}),
            http("POST", "/api/provider", {"name": "codex", "enabled": False}),
            http("GET", "/api/escalations"),
            http("GET", "/api/audit"),
            http("GET", "/api/list"),
            http("GET", "/api/status?id=iso-b"),
            http("POST", "/api/stop", {"id": "iso-b", "reason": "cross-workspace attempt"}),
            http("POST", "/api/declare", {"goal": "refactor the parser", "paths": ["src/parse/*"], "by": "dashboard"}),
        ]
        self.spawn(script, os.path.join(self.A, "src"), id="iso-a")
        wait_status(self.d, "iso-a", "idle")
        log = read_http_log(os.path.join(self.A, "src"))
        for path in ("/api/resolve", "/api/send", "/api/provider", "/api/escalations", "/api/audit"):
            e = by_path(log, path)[0]
            self.assertEqual(e["status"], 403, f"{path} must be denied to agents: {e}")
        lst = by_path(log, "/api/list")[0]
        self.assertEqual(lst["status"], 200)
        ids = {a["id"] for a in lst["response"]}
        self.assertIn("iso-a", ids)
        self.assertNotIn("iso-b", ids, "an agent must only see its own workspace")
        self.assertGreaterEqual(by_path(log, "/api/status")[0]["status"], 400, "status of another workspace's agent")
        self.assertGreaterEqual(by_path(log, "/api/stop")[0]["status"], 400, "stopping another workspace's agent")
        self.assertEqual(api(self.d, "GET", "/api/status?id=iso-b")[1]["status"], "idle", "the stop attempt must not have worked")
        info = api(self.d, "GET", "/api/status?id=iso-a")[1]
        self.assertEqual(info["goal"], "refactor the parser")
        self.assertEqual(info["paths"], ["src/parse/*"])
        # spoofed `by` in the body must be ignored: the audit shows the verified agent identity
        aud = api(self.d, "GET", "/api/audit?n=200")[1]
        decl = [x for x in aud if x["kind"] == "declare" and x.get("agent") == "iso-a"]
        self.assertTrue(decl and all(x["by"] == "agent:iso-a" for x in decl), decl)
        denied = [x for x in aud if x["kind"] == "agent_denied" and x.get("agent") == "iso-a"]
        self.assertGreaterEqual(len(denied), 5, "every denied call is audited")

    # ------------------------------------------------------------------ hierarchy
    def test_parent_forced_cwd_must_be_inside_workspace(self):
        script = [
            http("POST", "/api/spawn", {"task": json.dumps([{"say": "kid"}]), "provider": "fake",
                                         "cwd": os.path.join(self.A, "kid1"), "id": "hp-kid", "parent": "someone-else"}),
            http("POST", "/api/spawn", {"task": json.dumps([{"say": "x"}]), "provider": "fake",
                                         "cwd": os.path.join(self.B, "kid"), "id": "hp-out"}),
        ]
        hp = os.path.join(self.A, "hp")
        self.spawn(script, hp, id="hp-a")
        wait_status(self.d, "hp-a", "idle")
        log = read_http_log(hp)
        ok, bad = by_path(log, "/api/spawn")[0], by_path(log, "/api/spawn")[1]
        self.assertEqual(ok["status"], 200, ok)
        self.assertEqual(ok["response"]["parent"], "hp-a", "parent is forced to the caller, a claimed parent is ignored")
        self.assertGreaterEqual(bad["status"], 400, "child outside the parent's workspace must be refused")
        self.assertIn("workspace", json.dumps(bad["response"]).lower())

    def test_child_limit_and_depth_limit(self):
        # 5 children allowed, the 6th refused
        steps = [http("POST", "/api/spawn", {"task": json.dumps([{"say": "k"}]), "provider": "fake",
                                              "cwd": os.path.join(self.A, f"lim{i}"), "id": f"lim-k{i}"}) for i in range(6)]
        limp = os.path.join(self.A, "limp")
        self.spawn(steps, limp, id="lim-p")
        wait_status(self.d, "lim-p", "idle")
        sts = [e["status"] for e in by_path(read_http_log(limp), "/api/spawn")]
        self.assertEqual(sts[:5], [200] * 5, sts)
        self.assertGreaterEqual(sts[5], 400, "the 6th live child must be refused")
        # depth: a(1) -> c(2) -> g(3) -> great-grandchild refused
        cwd = os.path.join(self.B, "deep")
        great = json.dumps([{"say": "too deep"}])
        g = json.dumps([http("POST", "/api/spawn", {"task": great, "provider": "fake", "cwd": os.path.join(self.B, "deep", "d4"), "id": "dp-d4"})])
        c = json.dumps([http("POST", "/api/spawn", {"task": g, "provider": "fake", "cwd": os.path.join(self.B, "deep", "d3"), "id": "dp-d3"})])
        self.spawn([http("POST", "/api/spawn", {"task": c, "provider": "fake", "cwd": os.path.join(self.B, "deep", "d2"), "id": "dp-d2"})],
                   cwd, id="dp-d1")
        wait_status(self.d, "dp-d3", "idle", timeout=30)
        deepest = by_path(read_http_log(os.path.join(self.B, "deep", "d3")), "/api/spawn")
        self.assertTrue(deepest and deepest[0]["status"] >= 400, f"depth 4 must be refused: {deepest}")

    def test_tree_listing_orders_children_under_parent(self):
        steps = [http("POST", "/api/spawn", {"task": json.dumps([{"say": "k"}]), "provider": "fake",
                                              "cwd": os.path.join(self.A, "tree-k"), "id": "tr-kid"})]
        self.spawn(steps, os.path.join(self.A, "tree"), id="tr-par")
        wait_status(self.d, "tr-kid", "idle", timeout=20)
        st, rows = api(self.d, "GET", f"/api/list?ws={self.wsA}&tree=1")
        ids = [r["id"] for r in rows]
        self.assertLess(ids.index("tr-par"), ids.index("tr-kid"))
        by = {r["id"]: r for r in rows}
        self.assertEqual(by["tr-par"]["depth"], 0)
        self.assertEqual(by["tr-kid"]["depth"], 1)
        self.assertIn("tr-kid", by["tr-par"]["children"])

    # ------------------------------------------------------------------ sessions, workspaces, briefing
    def test_session_labels_admin_calls_and_workspace_resolution(self):
        st, h = api(self.d, "POST", "/api/hello", {"client": "claude-code", "label": "lbl", "workspace": self.A})
        self.assertEqual(st, 200)
        self.assertEqual(h["workspace"], self.wsA)
        st, r = spawn_fake(self.d, [{"say": "s"}], os.path.join(self.A, "sess"), id="ses-a", headers={"X-Switchyard-Session": h["session"]}) \
            if False else (None, None)
        os.makedirs(os.path.join(self.A, "sess"), exist_ok=True)
        st, r = api(self.d, "POST", "/api/spawn", {"provider": "fake", "task": json.dumps([{"say": "s"}]),
                                                    "cwd": os.path.join(self.A, "sess"), "id": "ses-a"},
                    headers={"X-Switchyard-Session": h["session"]})
        self.assertEqual(st, 200, r)
        self.assertEqual(r["owner"], "session:claude-code/lbl")
        self.assertEqual(r["workspace"], self.wsA, "an agent in a subfolder belongs to the repo's workspace")
        self.assertEqual(r["subdir"], "sess")
        st, sess = api(self.d, "GET", "/api/sessions")
        self.assertTrue(any(s["client"] == "claude-code" for s in sess))
        st, w = api(self.d, "GET", "/api/workspace?path=" + os.path.join(self.A, "sess"))
        self.assertEqual(w["id"], self.wsA)

    def test_briefing_lists_peers_and_says_paths_are_advisory(self):
        self.spawn([{"say": "x"}], os.path.join(self.A, "brief"), id="br-a", goal="build the api", paths=["api/*"])
        st, b = api(self.d, "GET", f"/api/briefing?ws={self.wsA}")
        self.assertEqual(st, 200)
        self.assertIn("br-a", b["text"])
        self.assertIn("build the api", b["text"])
        self.assertIn("advisory", b["text"].lower())
        st, wl = api(self.d, "GET", "/api/workspaces")
        self.assertTrue(any(w["id"] == self.wsA and w["agents"] >= 1 for w in wl))

    def test_workspace_refused_for_home_and_drive_root(self):
        home = os.path.expanduser("~")
        st, r = api(self.d, "POST", "/api/spawn", {"provider": "fake", "task": "[]", "cwd": home})
        self.assertGreaterEqual(st, 400)
        self.assertIn("workspace", json.dumps(r).lower())

    def test_admin_only_endpoints_still_work_for_admin(self):
        self.assertEqual(api(self.d, "GET", "/api/escalations")[0], 200)
        self.assertEqual(api(self.d, "GET", "/api/audit")[0], 200)
        self.assertEqual(api(self.d, "POST", "/api/resolve", {"escalation": "nope", "decision": "allow"})[0], 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
