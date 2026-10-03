"""Server side of the new dashboard: /ui/ static whitelist, /api/changes long-poll, /api/timeline, new info()/escalation/provider
fields, agentctl dashboard. Real isolated daemon (tests/harness.py, fake provider), no model turns.
Run: python tests/test_ui_server.py"""
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tests.harness import api, read_http_log, spawn_fake, start_daemon, wait_status  # noqa: E402

UI = os.path.join(ROOT, "orch", "ui")
UI_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; "
          "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
SERVED_EXT = (".html", ".js", ".css", ".svg", ".json")


def raw(d, path, headers=None, host=None, method="GET"):
    """-> (status, headers dict (lower-case keys), body bytes). No auth header unless given."""
    c = http.client.HTTPConnection("127.0.0.1", d.port, timeout=30)
    h = {"Host": host or f"127.0.0.1:{d.port}"}
    h.update(headers or {})
    try:
        c.request(method, path, None, h)
        r = c.getresponse()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
    finally:
        c.close()


def served_files():
    out = []
    for dp, dns, fns in os.walk(UI):
        dns[:] = [x for x in dns if not x.startswith(".")]
        for fn in fns:
            if not fn.startswith(".") and fn.lower().endswith(SERVED_EXT):
                out.append(os.path.relpath(os.path.join(dp, fn), UI).replace(os.sep, "/"))
    return sorted(out)


def http_step(method, path, **kw):
    return {"http": {"method": method, "path": path}, **kw}


class UiServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = start_daemon()
        cls.tmp = tempfile.mkdtemp(prefix="sy-ui-")
        cls.repo = os.path.join(cls.tmp, "repo")
        os.makedirs(os.path.join(cls.repo, ".git"))
        cls.ws = api(cls.d, "GET", "/api/workspace?path=" + cls.repo)[1]["id"]

    @classmethod
    def tearDownClass(cls):
        cls.d.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def rev(self):
        return api(self.d, "GET", "/api/changes?wait=0")[1]["rev"]

    def spawn(self, script, sub="a", **kw):
        st, r = spawn_fake(self.d, script, os.path.join(self.repo, sub), **kw)
        self.assertEqual(st, 200, r)
        return r

    # ------------------------------------------------------------------ static
    def test_static_whitelist(self):
        files = served_files()
        self.assertIn("js/net/api.js", files)
        for rel in files:
            st, h, body = raw(self.d, "/ui/" + rel)
            self.assertEqual(st, 200, rel)
            self.assertEqual(h["x-content-type-options"], "nosniff")
            self.assertEqual(h["cache-control"], "no-cache")
            self.assertEqual(h["content-security-policy"], UI_CSP)
            self.assertEqual(h["x-frame-options"], "DENY")
            self.assertEqual(h["referrer-policy"], "no-referrer")
            self.assertNotIn(self.d.token.encode(), body, rel)
            ext = os.path.splitext(rel)[1]
            want = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml",
                    ".json": "application/json"}[ext]
            self.assertTrue(h["content-type"].startswith(want), (rel, h["content-type"]))
            self.assertTrue(h["etag"])

    def test_etag_304(self):
        st, h, _ = raw(self.d, "/ui/js/net/api.js")
        self.assertEqual(st, 200)
        st, h2, body = raw(self.d, "/ui/js/net/api.js", {"If-None-Match": h["etag"]})
        self.assertEqual((st, body), (304, b""))
        self.assertEqual(h2["etag"], h["etag"])
        self.assertEqual(raw(self.d, "/ui/js/net/api.js", {"If-None-Match": '"nope"'})[0], 200)

    def test_index_served_when_present(self):
        if not os.path.exists(os.path.join(UI, "index.html")):
            self.skipTest("orch/ui/index.html not built yet")
        for p in ("/ui/", "/ui/index.html"):
            st, h, body = raw(self.d, p)
            self.assertEqual(st, 200, p)
            self.assertTrue(h["content-type"].startswith("text/html"))
            self.assertNotIn(self.d.token.encode(), body)
            self.assertNotIn(b"__TOKEN__", body)

    def test_traversal_and_unknown_are_404(self):
        for p in ("/ui/../server.py", "/ui/%2e%2e/server.py", "/ui/js/../../server.py", "/ui/..%2fserver.py",
                  "/ui/js%5C..%5Cx.js", "/ui/js\\api.js", "/ui/nope.js", "/ui/js/net/", "/ui//etc/passwd",
                  "/ui/js/x.mjs", "/ui/%00", "/ui/orch/ui/js/net/api.js", "/ui/js/net/API.JS.bak"):
            self.assertEqual(raw(self.d, p)[0], 404, p)
        self.assertEqual(raw(self.d, "/ui/js/net/api.js/..")[0], 404)

    def test_redirects_and_legacy(self):
        for p in ("/", "/ui"):
            st, h, _ = raw(self.d, p)
            self.assertEqual((st, h["location"]), (302, "/ui/"), p)
        st, h, body = raw(self.d, "/legacy")
        self.assertEqual(st, 200)
        self.assertIn(self.d.token.encode(), body)              # the old dashboard still embeds the token
        self.assertIn("unsafe-inline", h["content-security-policy"])
        self.assertEqual(h["x-frame-options"], "DENY")

    def test_host_check_applies(self):
        for p in ("/ui/", "/ui/js/net/api.js", "/", "/ui", "/legacy"):
            self.assertEqual(raw(self.d, p, host="evil.example")[0], 403, p)
        self.assertEqual(raw(self.d, "/ui/js/net/api.js", {"Origin": "http://evil.example"})[0], 403)

    # ------------------------------------------------------------------ /api/changes
    def test_changes_auth(self):
        self.assertEqual(api(self.d, "GET", "/api/changes?wait=0", token=False)[0], 401)
        self.assertEqual(api(self.d, "GET", "/api/changes?wait=0", token="x" * 48)[0], 401)

    def test_changes_agent_token_forbidden(self):
        r = self.spawn([http_step("GET", "/api/changes?wait=0"), http_step("GET", "/api/timeline?all=1"),
                        http_step("GET", f"/api/events?id=tok-agent")], id="tok-agent")
        wait_status(self.d, "tok-agent", "idle")
        log = read_http_log(os.path.join(self.repo, "a"))
        self.assertEqual([e["status"] for e in log], [403, 403, 403], log)

    def test_changes_semantics(self):
        st, r = api(self.d, "GET", "/api/changes")                  # no since: reset + current rev, returns at once
        self.assertEqual(st, 200)
        self.assertEqual(set(r), {"rev", "reset", "agents", "boards", "escalations", "audit", "providers", "workspaces"})
        self.assertTrue(r["reset"])
        rev = r["rev"]
        st, r = api(self.d, "GET", f"/api/changes?since={rev}&wait=0")
        self.assertEqual((r["reset"], r["rev"], r["agents"], r["boards"]), (False, rev, [], []))
        self.assertFalse(any(r[k] for k in ("escalations", "audit", "providers", "workspaces")))
        self.assertEqual(api(self.d, "POST", "/api/announce", {"ws": self.ws, "text": "hello sem", "kind": "info"})[0], 200)
        st, r = api(self.d, "GET", f"/api/changes?since={rev}&wait=0")
        self.assertFalse(r["reset"])
        self.assertGreater(r["rev"], rev)
        self.assertEqual(r["boards"], [self.ws])
        self.assertEqual(api(self.d, "GET", f"/api/changes?since={r['rev']}&wait=0")[1]["boards"], [])
        st, r = api(self.d, "GET", f"/api/changes?since={rev + 100000}&wait=0")    # ahead of the daemon (it restarted)
        self.assertTrue(r["reset"])
        self.assertEqual(api(self.d, "GET", "/api/changes?since=abc")[0], 400)

    def test_changes_returns_within_wait(self):
        rev = self.rev()
        t = time.time()
        st, r = api(self.d, "GET", f"/api/changes?since={rev}&wait=1")
        el = time.time() - t
        self.assertTrue(0.9 <= el < 2.0, el)
        self.assertFalse(r["reset"])

    def test_changes_wakes_fast_on_bump(self):
        rev = self.rev()
        res = {}

        def poll():
            res["r"] = api(self.d, "GET", f"/api/changes?since={rev}&wait=20")
            res["t"] = time.time()
        th = threading.Thread(target=poll)
        th.start()
        time.sleep(0.5)
        t0 = time.time()
        api(self.d, "POST", "/api/announce", {"ws": self.ws, "text": "wake me", "kind": "info"})
        t1 = time.time()
        th.join(10)
        self.assertLess(res["t"] - t1, 0.1)
        self.assertLess(res["t"] - t0, 2.0)
        self.assertEqual(res["r"][1]["boards"], [self.ws])

    def test_changes_spawn_marks_agent_and_workspace(self):
        rev = self.rev()
        self.spawn([{"say": "x"}], sub="sp", id="spawn-chg")
        wait_status(self.d, "spawn-chg", "idle")
        r = api(self.d, "GET", f"/api/changes?since={rev}&wait=0")[1]
        self.assertIn("spawn-chg", r["agents"])
        self.assertTrue(r["audit"] and r["workspaces"])

    def test_ninth_waiter_gets_429(self):
        rev = self.rev()
        out = []

        def poll():
            out.append(api(self.d, "GET", f"/api/changes?since={rev}&wait=6")[0])
        ths = [threading.Thread(target=poll) for _ in range(8)]
        for t in ths:
            t.start()
        time.sleep(1.0)
        st, r = api(self.d, "GET", f"/api/changes?since={rev}&wait=1")
        self.assertEqual(st, 429, r)
        api(self.d, "POST", "/api/announce", {"ws": self.ws, "text": "release waiters", "kind": "info"})
        for t in ths:
            t.join(10)
        self.assertEqual(out, [200] * 8)
        self.assertEqual(api(self.d, "GET", f"/api/changes?since={rev}&wait=0")[0], 200)   # slots were released

    def test_provider_toggle_bumps_providers(self):
        rev = self.rev()
        api(self.d, "POST", "/api/provider", {"name": "opencode", "enabled": True})
        r = api(self.d, "GET", f"/api/changes?since={rev}&wait=0")[1]
        self.assertTrue(r["providers"])

    # ------------------------------------------------------------------ info / escalation / providers
    def test_info_fields_and_escalation(self):
        rev = self.rev()
        self.spawn([{"tool": "run_command", "input": "git push origin main"}, {"sleep": 5}], sub="esc", id="esc-a")
        deadline = time.time() + 20
        e = None
        while time.time() < deadline and e is None:
            e = next((x for x in api(self.d, "GET", "/api/escalations")[1] if x["agent"] == "esc-a"), None)
            time.sleep(0.1)
        self.assertIsNotNone(e)
        self.assertEqual(e["workspace"], self.ws)
        self.assertAlmostEqual(e["deadline"] - e["ts"], 600, delta=0.01)
        self.assertEqual(e["state"], "pending")
        r = api(self.d, "GET", f"/api/changes?since={rev}&wait=0")[1]
        self.assertTrue(r["escalations"])
        row = next(x for x in api(self.d, "GET", "/api/list?all=1&tree=1")[1] if x["id"] == "esc-a")
        for k in ("last_event", "current_tool", "turn_t0", "pending_escalation"):
            self.assertIn(k, row)
        self.assertEqual(row["pending_escalation"], e["id"])
        self.assertIn(row["last_event"]["type"], ("tool_start", "tool_end", "guard", "status", "text", "session", "result"))
        self.assertIsInstance(row["last_event"]["ts"], float)
        rev = self.rev()
        api(self.d, "POST", "/api/resolve", {"escalation": e["id"], "decision": "deny", "note": "no"})
        row = api(self.d, "GET", "/api/status?id=esc-a")[1]
        self.assertIsNone(row["pending_escalation"])
        self.assertTrue(api(self.d, "GET", f"/api/changes?since={rev}&wait=0")[1]["escalations"])
        api(self.d, "POST", "/api/stop", {"id": "esc-a", "reason": "test done"})

    def test_providers_caps(self):
        st, rows = api(self.d, "GET", "/api/providers?models=0")
        self.assertEqual(st, 200)
        self.assertTrue(rows)
        for r in rows:
            self.assertIsInstance(r["caps"], dict, r)
            self.assertEqual(set(r["caps"]), {"steer", "interrupt", "native_queue", "usage"}, r)
        caps = {r["name"]: r["caps"] for r in rows}
        self.assertEqual(caps["claude"]["interrupt"], True)
        self.assertEqual(caps["agy"]["interrupt"], "restart")

    def test_events_workspace_guard_present(self):
        src = open(os.path.join(ROOT, "orch", "server.py"), encoding="utf-8").read()
        i = src.index('if path == "/api/events":')
        self.assertIn("_check_ws(who", src[i:i + 120])
        self.assertNotIn("/api/events", __import__("orch.identity", fromlist=["x"]).AGENT_ALLOWED)

    # ------------------------------------------------------------------ timeline
    def test_timeline(self):
        self.spawn([{"say": "hi"}, {"tool": "run_command", "input": "echo ok"}], sub="tl", id="tl-a")
        wait_status(self.d, "tl-a", "idle")
        api(self.d, "POST", "/api/ask", {"ws": self.ws, "text": "tl question?"})
        st, tl = api(self.d, "GET", f"/api/timeline?ws={self.ws}")
        self.assertEqual(st, 200, tl)
        self.assertEqual(set(tl), {"from", "to", "truncated", "agents", "items"})
        self.assertFalse(tl["truncated"])
        ag = next(a for a in tl["agents"] if a["id"] == "tl-a")
        self.assertEqual(set(ag), {"id", "provider", "model", "created", "ended"})
        kinds = {i["k"] for i in tl["items"] if i.get("a") == "tl-a"}
        self.assertTrue({"turn", "tool"} <= kinds, kinds)
        turn = next(i for i in tl["items"] if i["k"] == "turn" and i["a"] == "tl-a")
        self.assertEqual(set(turn), {"a", "k", "t0", "t1", "ok", "int"})
        self.assertTrue(turn["ok"] is True and turn["t1"] >= turn["t0"] and turn["int"] is False)
        tool = next(i for i in tl["items"] if i["k"] == "tool" and i["a"] == "tl-a")
        self.assertEqual(set(tool), {"a", "k", "t0", "t1", "tool", "ok"})
        self.assertEqual((tool["tool"], tool["ok"]), ("run_command", True))
        post = next(i for i in tl["items"] if i["k"] == "post" and i["kind"] == "question")
        self.assertEqual(set(post), {"a", "k", "t", "id", "kind", "ws"})
        self.assertEqual((post["a"], post["kind"], post["ws"]), (None, "question", self.ws))
        blob = json.dumps(tl)
        self.assertNotIn("echo ok", blob)                         # text-free
        self.assertEqual([i for i in tl["items"] if i["k"] != "post" and (i.get("t0") or i.get("t")) < tl["from"]], [])
        starts = [i.get("t0", i.get("t")) for i in tl["items"]]
        self.assertEqual(starts, sorted(starts))

    def test_timeline_limits_and_args(self):
        self.assertEqual(api(self.d, "GET", "/api/timeline")[0], 400)               # ws or all=1 required
        self.assertEqual(api(self.d, "GET", "/api/timeline?all=1", token=False)[0], 401)
        self.assertEqual(api(self.d, "GET", "/api/timeline?all=1&since=abc")[0], 400)
        st, tl = api(self.d, "GET", "/api/timeline?all=1&max=1")
        self.assertEqual(st, 200)
        self.assertLessEqual(len(tl["items"]), 1)
        full = api(self.d, "GET", "/api/timeline?all=1")[1]
        if len(full["items"]) > 1:
            self.assertTrue(tl["truncated"])
            self.assertEqual(tl["items"][-1], full["items"][-1])                     # newest kept
        st, tl = api(self.d, "GET", f"/api/timeline?all=1&since={time.time() + 1000}")
        self.assertEqual((tl["items"], tl["agents"]), ([], []))
        st, tl = api(self.d, "GET", "/api/timeline?ws=no-such-ws")
        self.assertEqual((st, tl["items"]), (200, []))

    # ------------------------------------------------------------------ CLI
    def test_agentctl_dashboard_no_open(self):
        env = dict(os.environ, ORCH_URL=self.d.url, SWITCHYARD_HOME=self.d.home)
        env.pop("ORCH_TOKEN", None)
        p = subprocess.run([sys.executable, os.path.join(ROOT, "agentctl.py"), "dashboard", "--no-open"], env=env,
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(), f"http://127.0.0.1:{self.d.port}/ui/#t={self.d.token}")


CORE_SCRIPT = textwrap.dedent("""
    import json, os, sys, time
    sys.path.insert(0, os.environ["ROOT"])
    from orch import core
    o = core.Orchestrator()
    rec = core.AgentRec("u1", "fake", "m", os.getcwd(), "standard", "t", False)
    rec.ws = "w"
    o.agents["u1"] = rec
    ev = lambda **k: o._on_event(rec, k)
    out = {}
    ev(type="tool_start", tool="Bash", id="t1", input="ls")
    ev(type="tool_start", tool="Read", id="t2", input="x")
    out["two"] = rec.info()["current_tool"]
    ev(type="tool_end", tool="", id="t2", ok=True)               # matched by id, empty name (claude style)
    out["after_t2"] = rec.info()["current_tool"]
    ev(type="tool_end", tool="Bash", ok=True)                     # no id: falls back to name
    out["after_bash"] = rec.info()["current_tool"]
    ev(type="tool_start", tool="Edit", id="t3", input="x")
    ev(type="result", text="done", ok=True)
    out["after_result"] = rec.info()["current_tool"]
    rec.turns.append({"prompt": "p", "mode": "queue", "t0": 123.0, "partial": ""})
    out["t0_open"] = rec.info()["turn_t0"]
    rec.turns[-1]["response"] = "r"
    out["t0_closed"] = rec.info()["turn_t0"]
    r0 = o.rev
    for _ in range(50):
        ev(type="text", text="x")
    out["text_bumps"] = sum(1 for e in o._chlog if e[0] > r0 and e[1] == "agent")
    time.sleep(0.3)
    ev(type="text", text="y")
    out["text_bumps_later"] = sum(1 for e in o._chlog if e[0] > r0 and e[1] == "agent")
    # waiter cap and reset on log overflow
    import threading
    res = []
    def w():
        try:
            o.wait_changes(o.rev, 1.5)
            res.append("ok")
        except core.TooManyWaiters:
            res.append("429")
    ts = [threading.Thread(target=w) for _ in range(9)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    out["waiters"] = sorted(res)
    for _ in range(core.CHANGE_LOG + 10):
        o._bump("audit")
    out["overflow_reset"] = o.wait_changes(1, 0)["reset"]
    out["fresh_ok"] = o.wait_changes(o.rev, 0)["reset"]
    print(json.dumps(out))
""")


class CoreUnit(unittest.TestCase):
    def test_core_in_isolated_process(self):
        home = tempfile.mkdtemp(prefix="sy-core-")
        try:
            env = dict(os.environ, ROOT=ROOT, SWITCHYARD_HOME=home, PYTHONPATH=ROOT)
            p = subprocess.run([sys.executable, "-c", CORE_SCRIPT], env=env, cwd=home, capture_output=True, text=True, timeout=60)
            self.assertEqual(p.returncode, 0, p.stderr[-2000:])
            o = json.loads(p.stdout.strip().splitlines()[-1])
        finally:
            shutil.rmtree(home, ignore_errors=True)
        self.assertEqual(o["two"], "Read")
        self.assertEqual(o["after_t2"], "Bash")
        self.assertIsNone(o["after_bash"])
        self.assertIsNone(o["after_result"])
        self.assertEqual(o["t0_open"], 123.0)
        self.assertIsNone(o["t0_closed"])
        self.assertLessEqual(o["text_bumps"], 1)                    # 50 text events inside 250 ms -> one bump
        self.assertEqual(o["text_bumps_later"], o["text_bumps"] + 1)
        self.assertEqual(o["waiters"], ["429", "ok", "ok", "ok", "ok", "ok", "ok", "ok", "ok"])
        self.assertTrue(o["overflow_reset"])
        self.assertFalse(o["fresh_ok"])


class NodeNet(unittest.TestCase):
    def test_node_net(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        p = subprocess.run([node, "--test", os.path.join(ROOT, "tests", "ui", "test_net.mjs")], capture_output=True, text=True,
                           timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout[-3000:] + p.stderr[-2000:])


if __name__ == "__main__":
    unittest.main(verbosity=2)
