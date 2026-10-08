"""Completion hooks (/api/hook, /api/hooks, /api/unhook). Real isolated daemon, fake provider, no model turns.
Run: py -3.10 tests/test_hooks.py"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tests.harness import api, read_http_log, spawn_fake, start_daemon, wait_status  # noqa: E402

WRITE_ENV = ("import os,sys,json;"
             "open(sys.argv[1],'a').write(json.dumps({k:v for k,v in os.environ.items() if k.startswith('SWITCHYARD_')})+chr(10))")


def read_lines(p):
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


class Unit(unittest.TestCase):
    """Hooks._run and Orchestrator._on_result without a daemon."""

    def run_hook(self, code, timeout=None):
        from types import SimpleNamespace
        from orch import hooks as hm
        audits = []
        h = hm.Hooks(SimpleNamespace(agents={}, _audit=lambda kind, **kw: audits.append((kind, kw))))
        cmd = f'"{sys.executable}" -c "{code}"'
        old = hm.TIMEOUT
        if timeout:
            hm.TIMEOUT = timeout
        try:
            h._run({"hook": "h1", "run": cmd}, "a1", os.getcwd(), "idle", "idle", "")
        finally:
            hm.TIMEOUT = old
        return audits[-1][1]

    def test_output_is_bounded_while_running(self):
        from orch import hooks as hm
        a = self.run_hook("import sys;sys.stdout.write('x'*3000000);sys.stderr.write('e'*3000000)")
        self.assertEqual(a["exit"], 0)
        self.assertEqual(a["output"].count("x"), hm.MAX_OUT)

    def test_timeout_kills_child(self):
        a = self.run_hook("import time;print('hi',flush=True);time.sleep(60)", timeout=1)
        self.assertEqual(a["exit"], "timeout")
        self.assertIn("hi", a["output"])

    def test_error_hook_fires_with_queued_work(self):
        import threading
        from types import SimpleNamespace
        from orch.core import Orchestrator
        fired = []
        rec = SimpleNamespace(id="a1", lock=threading.RLock(), turns=[{"t0": time.time()}], queue=["more"], status="busy",
                              parent=None, open_tools=[])
        me = SimpleNamespace(_check_forbidden=lambda r: None, lock=threading.RLock(), _pending_wake={},
                             _deliver_quiet=lambda r, m: None, hooks=SimpleNamespace(fire=lambda r, e, t="": fired.append(e)))
        Orchestrator._on_result(me, rec, {"ok": False, "text": "boom"})
        self.assertEqual(fired, ["error"])
        rec.turns, rec.queue, rec.status = [{"t0": time.time()}], [], "busy"
        fired.clear()
        Orchestrator._on_result(me, rec, {"ok": True, "text": "fine"})
        self.assertEqual(fired, ["idle"])


class Hooks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = start_daemon()
        cls.tmp = tempfile.mkdtemp(prefix="sy-hooks-")
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        cls.d.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def agent(self, script, **kw):
        Hooks.n += 1
        cwd = os.path.join(self.tmp, f"a{Hooks.n}")
        st, r = spawn_fake(self.d, script, cwd, **kw)
        self.assertEqual(st, 200, r)
        return r["id"], cwd

    def hook(self, aid, on, run, **kw):
        return api(self.d, "POST", "/api/hook", dict(id=aid, on=on, run=run, **kw))

    def pycmd(self, code, *args):
        # the interpreter path is quoted, so spaces in a Windows path work with the no-shell splitter
        return " ".join([f'"{sys.executable}"', "-c", f'"{code}"'] + [f'"{a}"' for a in args])

    def wait(self, fn, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            v = fn()
            if v:
                return v
            time.sleep(0.1)
        self.fail("timed out")

    def send(self, aid, text):
        self.assertEqual(api(self.d, "POST", "/api/send", {"id": aid, "msg": text, "mode": "queue"})[0], 200)

    def test_oneshot_idle_with_env(self):
        aid, cwd = self.agent([{"sleep": 1.5}, {"say": "hello result"}])   # busy while the hook is registered
        out = os.path.join(self.tmp, "oneshot.jsonl")
        st, r = self.hook(aid, "idle", self.pycmd(WRITE_ENV, out))
        self.assertEqual(st, 200, r)
        self.assertEqual((r["id"], r["on"], r["repeat"]), (aid, "idle", False))
        rows = self.wait(lambda: read_lines(out))
        self.assertEqual(rows[0]["SWITCHYARD_AGENT"], aid)
        self.assertEqual(rows[0]["SWITCHYARD_EVENT"], "idle")
        self.assertEqual(rows[0]["SWITCHYARD_STATUS"], "idle")
        self.assertIn("hello result", rows[0]["SWITCHYARD_RESULT"])
        wait_status(self.d, aid, "idle")
        self.assertFalse([h for h in api(self.d, "GET", "/api/hooks")[1] if h["id"] == aid])   # one-shot: removed
        self.send(aid, json.dumps([{"say": "again"}]))
        time.sleep(1.5)
        self.assertEqual(len(read_lines(out)), 1)

    def test_repeat_fires_twice_and_unhook(self):
        aid, cwd = self.agent([{"sleep": 1.0}, {"say": "one"}])
        out = os.path.join(self.tmp, "repeat.jsonl")
        st, r = self.hook(aid, "idle", self.pycmd(WRITE_ENV, out), repeat=True)
        self.assertEqual(st, 200, r)
        self.wait(lambda: len(read_lines(out)) >= 1)
        wait_status(self.d, aid, "idle")
        self.send(aid, json.dumps([{"say": "two"}]))
        self.wait(lambda: len(read_lines(out)) >= 2)
        row = self.wait(lambda: next((h for h in api(self.d, "GET", "/api/hooks")[1]
                                      if h["hook"] == r["hook"] and h["fired"] >= 2 and h["last_exit"] is not None), None))
        self.assertEqual(row["last_exit"], 0)
        self.assertTrue(row["last_fired"])
        self.assertEqual(api(self.d, "POST", "/api/unhook", {"hook": r["hook"]}), (200, {"ok": True}))
        self.assertFalse([h for h in api(self.d, "GET", "/api/hooks")[1] if h["hook"] == r["hook"]])
        self.assertEqual(api(self.d, "POST", "/api/unhook", {"hook": r["hook"]})[0], 400)
        wait_status(self.d, aid, "idle", min_turns=2)
        self.send(aid, json.dumps([{"say": "three"}]))
        time.sleep(1.5)
        self.assertEqual(len(read_lines(out)), 2)

    def test_error_hook_on_crash(self):
        aid, cwd = self.agent([{"sleep": 1.0}, {"crash": True}])
        out = os.path.join(self.tmp, "err.jsonl")
        st, r = self.hook(aid, "error", self.pycmd(WRITE_ENV, out))
        self.assertEqual(st, 200, r)
        rows = self.wait(lambda: read_lines(out))
        self.assertIn(rows[0]["SWITCHYARD_EVENT"], ("error", "dead"))

    def test_done_on_stop(self):
        aid, cwd = self.agent([{"say": "x"}])
        wait_status(self.d, aid, "idle")
        out = os.path.join(self.tmp, "done.jsonl")
        self.assertEqual(self.hook(aid, "done", self.pycmd(WRITE_ENV, out))[0], 200)
        api(self.d, "POST", "/api/stop", {"id": aid, "reason": "test"})
        rows = self.wait(lambda: read_lines(out))
        self.assertEqual(rows[0]["SWITCHYARD_EVENT"], "stopped")

    def test_agent_token_forbidden(self):
        # the fake agent calls the three endpoints itself with its own agent token
        aid, cwd = self.agent([
            {"http": {"method": "POST", "path": "/api/hook", "body": {"id": "x", "on": "idle", "run": "echo hi"}}},
            {"http": {"method": "GET", "path": "/api/hooks"}},
            {"http": {"method": "POST", "path": "/api/unhook", "body": {"hook": "h1"}}}])
        wait_status(self.d, aid, "idle")
        log = read_http_log(cwd)
        self.assertEqual([x.get("status") for x in log], [403, 403, 403], log)
        self.assertFalse(api(self.d, "GET", "/api/hooks")[1])

    def test_limits_and_validation(self):
        aid, cwd = self.agent([{"say": "x"}])
        wait_status(self.d, aid, "idle")
        self.assertEqual(self.hook("nope", "idle", "echo hi")[0], 400)
        self.assertEqual(self.hook(aid, "idle", "")[0], 400)
        self.assertEqual(self.hook(aid, "idle", "x" * 2001)[0], 400)
        self.assertEqual(self.hook(aid, "bogus", "echo")[0], 400)
        try:
            st = 200
            for _ in range(60):
                st, r = self.hook(aid, "idle", "echo hi")
                if st != 200:
                    break
            self.assertEqual(len(api(self.d, "GET", "/api/hooks")[1]), 50)
            self.assertEqual(st, 400)
            self.assertIn("too many", r["error"])
        finally:
            for h in api(self.d, "GET", "/api/hooks")[1]:
                api(self.d, "POST", "/api/unhook", {"hook": h["hook"]})

    def test_failing_command_and_no_shell(self):
        aid, cwd = self.agent([{"sleep": 1.0}, {"say": "x"}])
        marker = os.path.join(self.tmp, "pwned.txt")
        run = f'"{sys.executable}" -c "import sys;sys.exit(3)" ; echo pwned > "{marker}" && echo y'
        st, r = self.hook(aid, "idle", run, repeat=True)
        self.assertEqual(st, 200, r)
        row = self.wait(lambda: next((h for h in api(self.d, "GET", "/api/hooks")[1]
                                      if h["hook"] == r["hook"] and h["last_exit"] is not None), None))
        self.assertEqual(row["last_exit"], 3)      # extra args are literal argv, not shell syntax
        self.assertFalse(os.path.exists(marker))
        self.assertEqual(api(self.d, "GET", "/api/health")[0], 200)
        audit = api(self.d, "GET", "/api/audit?n=100")[1]
        self.assertTrue(any(a["kind"] == "hook_fired" and a.get("hook") == r["hook"] for a in audit))
        api(self.d, "POST", "/api/unhook", {"hook": r["hook"]})


if __name__ == "__main__":
    unittest.main()
