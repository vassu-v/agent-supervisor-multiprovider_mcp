"""agentctl.py readable output, --json, wait, hook client and unknown-command handling (fake provider, no model turns).
Run: python tests/test_cli_output.py"""
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tests.harness import spawn_fake, start_daemon, wait_status  # noqa: E402
from tests.test_cli_mcp import CTL, clean_env  # noqa: E402


def ctl(url, token, *args, cwd=None, timeout=60):
    r = subprocess.run([sys.executable, CTL, *args], cwd=cwd or ROOT, capture_output=True, text=True, encoding="utf-8",
                       env=clean_env(ORCH_URL=url, ORCH_TOKEN=token), timeout=timeout)
    return r.returncode, r.stdout, r.stderr


class Output(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = start_daemon()
        cls.tmp = tempfile.mkdtemp(prefix="sy-cliout-")

    @classmethod
    def tearDownClass(cls):
        cls.d.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def c(self, *args, **kw):
        return ctl(self.d.url, self.d.token, *args, **kw)

    def mk(self, aid, script, **kw):
        st, r = spawn_fake(self.d, script, os.path.join(self.tmp, aid), id=aid, **kw)
        self.assertEqual(st, 200, r)
        return r

    def test_01_spawn_list_status_result_tail(self):
        cwd = os.path.join(self.tmp, "w1")
        rc, out, err = self.c("spawn", json.dumps([{"tool": "run_command", "input": "python primes.py"}, {"say": "all done"}]),
                              "--provider", "fake", "--cwd", cwd, "--id", "w1", "--goal", "make primes")
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(out.strip().splitlines()), 1, out)
        self.assertTrue(out.startswith("started w1 on fake:"), out)
        self.assertIn("(tier ", out)
        self.assertTrue(out.strip().endswith(cwd), out)
        self.assertIn("created", err)                                   # the missing --cwd was created
        wait_status(self.d, "w1", "idle")
        rc, out, _ = self.c("spawn", "x", "--provider", "fake", "--cwd", cwd, "--id", "w1b", "--json")
        self.assertEqual(json.loads(out)["id"], "w1b")
        rc, out, _ = self.c("list")
        self.assertEqual(rc, 0)
        self.assertNotIn("{", out)
        head, *rows = out.splitlines()
        self.assertTrue(head.startswith("ID") and "PROVIDER:MODEL" in head and "GOAL" in head, out)
        self.assertTrue(any(r.startswith("w1 ") and "make primes" in r for r in rows), out)
        rc, out, _ = self.c("status", "w1")
        self.assertTrue(out.startswith("w1  idle"), out)
        rc, out, _ = self.c("result", "w1")
        self.assertEqual(out.strip(), "all done")
        rc, out, _ = self.c("tail", "w1")
        self.assertIn("[tool] run_command: python primes.py", out)
        self.assertIn("[text] all done", out)
        self.assertIn("[status] idle", out)
        self.assertNotIn('"seq"', out)
        self.assertNotIn('"ts"', out)
        rc, out, _ = self.c("--json", "status", "w1")                  # --json works anywhere on the line
        self.assertEqual(json.loads(out)["id"], "w1")
        rc, out, _ = self.c("tail", "w1", "--json")
        self.assertIsInstance(json.loads(out), list)
        rc, out, _ = self.c("result", "w1", "--json")
        self.assertIn("turns", json.loads(out))

    def test_02_send_stop_interrupt(self):
        self.mk("s1", [{"say": "hi"}])
        wait_status(self.d, "s1", "idle")
        rc, out, _ = self.c("send", "s1", "more")
        self.assertEqual((rc, out.split(":")[0]), (0, "s1"), out)
        self.assertNotIn("{", out)
        wait_status(self.d, "s1", "idle", min_turns=2)
        rc, out, _ = self.c("interrupt", "s1")
        self.assertIn("nothing to interrupt", out)
        rc, out, _ = self.c("stop", "s1", "--reason", "done")
        self.assertEqual(out.strip(), "stopped s1")
        rc, out, _ = self.c("stop", "s1", "--reason", "again", "--json")
        self.assertIn("result", json.loads(out))

    def test_03_wait(self):
        self.mk("wt1", [{"sleep": 1.5}, {"say": "slow answer"}])
        rc, out, err = self.c("wait", "wt1", "--timeout", "30")
        self.assertEqual((rc, out.strip()), (0, "slow answer"), err)
        rc, out, _ = self.c("wait", "wt1", "--json")
        self.assertEqual(json.loads(out)["status"], "idle")
        rc, out, err = self.c("wait", "wt1", "--until", "done")
        self.assertEqual((rc, out.strip()), (0, "slow answer"), err)
        self.mk("wt2", [{"sleep": 30}])
        rc, out, err = self.c("wait", "wt2", "--timeout", "1")
        self.assertEqual(rc, 2, err)
        self.assertIn("timed out", err)
        self.c("stop", "wt2", "--reason", "x")
        rc, out, err = self.c("wait", "wt2", "--timeout", "10")
        self.assertEqual(rc, 1, err)
        self.assertIn("ended", err)
        rc, out, err = self.c("wait", "nope-nope")
        self.assertEqual(rc, 1)
        self.assertTrue(err.startswith("error:"), err)
        rc, out, err = self.c("wait")
        self.assertEqual(rc, 1)
        self.assertIn("missing ID", err)

    def test_04_spawn_wait(self):
        cwd = os.path.join(self.tmp, "sw")
        rc, out, err = self.c("spawn", json.dumps([{"say": "finished it"}]), "--provider", "fake", "--cwd", cwd, "--id", "sw1", "--wait",
                              "--timeout", "30")
        self.assertEqual(rc, 0, err)
        lines = out.strip().splitlines()
        self.assertTrue(lines[0].startswith("started sw1"), out)
        self.assertEqual(lines[-1], "finished it")
        rc, out, err = self.c("spawn", json.dumps([{"say": "j"}]), "--provider", "fake", "--cwd", cwd, "--id", "sw2", "--wait", "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["id"], "sw2")                  # one JSON document: the final status

    def test_05_unknown_command_and_help(self):
        rc, out, err = self.c("wat")
        self.assertEqual(rc, 2)
        self.assertIn("unknown command 'wat'", err)
        self.assertIn("did you mean", err)
        self.assertIn("run: python agentctl.py --help", err)
        self.assertEqual(out, "")
        rc, out, err = self.c("lst")
        self.assertIn("'list'", err)
        for a in ([], ["--help"]):
            rc, out, _ = self.c(*a)
            self.assertEqual(rc, 0)
            self.assertIn("python agentctl.py", out)
            self.assertNotIn("py -3.10", out)
        rc, out, err = self.c("send", "a1")
        self.assertEqual(rc, 1)
        self.assertEqual(err.count("\n"), 1)
        self.assertIn("missing", err)
        rc, out, err = self.c("hook", "a1", "--on", "done")
        self.assertEqual((rc, err.count("\n")), (1, 1))
        self.assertIn("--run", err)

    def test_06_hook_against_the_real_server(self):
        self.mk("hk1", [{"say": "x"}])
        rc, out, err = self.c("hook", "hk1", "--on", "done", "--run", "echo hi")
        self.assertEqual(rc, 0, err)
        self.assertTrue(out.startswith("hook h"), out)
        rc, out, err = self.c("hook", "no-such-agent", "--on", "done", "--run", "echo hi")
        self.assertEqual(rc, 1)
        self.assertEqual(err.count("\n"), 1, err)
        self.assertTrue(err.startswith("error:") and "unknown agent" in err, err)

    def test_07_json_token_is_a_message_after_double_dash(self):
        self.mk("jt1", [{"say": "x"}])
        wait_status(self.d, "jt1", "idle")
        rc, out, err = self.c("send", "jt1", "--json")                 # no `--`: the token is the --json flag, so no message
        self.assertEqual(rc, 1)
        self.assertIn("missing", err)
        rc, out, err = self.c("send", "jt1", "--json", "--", "--json")  # flag before `--`, literal message after
        self.assertEqual(rc, 0, err)
        self.assertIn("sent", out)
        wait_status(self.d, "jt1", "idle", min_turns=2)
        with open(os.path.join(self.tmp, "jt1", ".fake_inbox.jsonl"), encoding="utf-8") as f:
            self.assertIn("--json", f.read())

    def test_08_spawn_timeout_needs_wait(self):
        rc, out, err = self.c("spawn", "x", "--provider", "fake", "--cwd", os.path.join(self.tmp, "nw"), "--timeout", "5")
        self.assertEqual(rc, 1)
        self.assertIn("--wait", err)


class WaitUnit(unittest.TestCase):
    """cmd_wait against canned /api/status replies (no daemon)."""

    def run_wait(self, replies):
        import contextlib
        import io
        import agentctl
        seq = list(replies)
        calls = []
        old = agentctl.get, agentctl.time.sleep, agentctl.JSON_MODE
        agentctl.get = lambda path: (calls.append(path), seq.pop(0) if len(seq) > 1 else seq[0])[1]
        agentctl.time.sleep = lambda s: None
        agentctl.JSON_MODE = False
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = agentctl.cmd_wait("a1", {})
        finally:
            agentctl.get, agentctl.time.sleep, agentctl.JSON_MODE = old
        return rc, out.getvalue(), err.getvalue(), len(calls)

    def info(self, st="idle", **turn):
        return {"status": st, "turns": 1, "queued": 0, "turns_full": [dict(response="boom", **turn)]}

    def test_failed_last_turn_exits_1(self):
        rc, out, err, _ = self.run_wait([self.info(ok=False, stop="error")])
        self.assertEqual(rc, 1)
        self.assertIn("boom", out)
        self.assertIn("failed", err)

    def test_cancelled_or_ok_turn_exits_0(self):
        self.assertEqual(self.run_wait([self.info(ok=False, stop="cancelled")])[0], 0)
        self.assertEqual(self.run_wait([self.info(ok=True, stop="end")])[0], 0)

    def test_idle_must_hold_for_two_polls(self):
        busy = {"status": "busy", "turns": 2, "queued": 0, "turns_full": [{"response": "x", "ok": True}, {"partial": "..."}]}
        rc, out, _, n = self.run_wait([self.info(ok=True), busy, self.info(ok=True), self.info(ok=True)])
        self.assertEqual((rc, n), (0, 4))                      # the blip to idle before the follow-up turn started is not enough

    def test_failed_states_are_immediate(self):
        rc, _, err, n = self.run_wait([{"status": "dead", "turns": 1, "queued": 0, "turns_full": [{"response": "x"}]}])
        self.assertEqual((rc, n), (1, 1))


class ResultText(unittest.TestCase):
    def test_newest_turn_only(self):
        import agentctl
        rt = agentctl.result_text
        self.assertEqual(rt({"turns_full": [{"response": "old"}, {"response": ""}]}), "")
        self.assertEqual(rt({"turns_full": [{"response": "old"}, {"partial": "new so far"}]}), "new so far")
        self.assertEqual(rt({"turns_full": [{"response": "a"}, {"response": "b"}]}), "b")
        self.assertEqual(rt({"turns": []}), "")


class Stub(http.server.BaseHTTPRequestHandler):
    seen = []

    def _reply(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        Stub.seen.append((self.path, body))
        if self.path == "/api/hook":
            self._reply({"hook": "h7", "id": body["id"], "on": body["on"], "run": body["run"], "repeat": body["repeat"]})
        elif self.path == "/api/unhook":
            self._reply({"ok": True})
        else:
            self._reply({"error": "no such endpoint"})

    def do_GET(self):
        Stub.seen.append((self.path, None))
        self._reply([{"hook": "h7", "id": "a1", "on": "done", "run": "echo hi", "repeat": False}] if self.path == "/api/hooks"
                    else {"error": "nope"})

    def log_message(self, *a):
        pass


class HookClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def c(self, *args):
        return ctl(self.url, "t", *args)

    def test_hook_hooks_unhook(self):
        rc, out, err = self.c("hook", "a1", "--on", "done", "--run", "echo hi", "--repeat")
        self.assertEqual(rc, 0, err)
        self.assertIn("hook h7", out)
        self.assertEqual(Stub.seen[-1][0], "/api/hook")
        body = Stub.seen[-1][1]
        self.assertEqual((body["id"], body["on"], body["run"], body["repeat"]), ("a1", "done", "echo hi", True))
        rc, out, _ = self.c("hooks")
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("h7  a1  on done"), out)       # hook id first (what unhook takes), then the agent
        self.assertIn("echo hi", out)
        self.assertNotIn("{", out)
        rc, out, _ = self.c("hooks", "--json")
        self.assertEqual(json.loads(out)[0]["hook"], "h7")
        rc, out, _ = self.c("unhook", "h7")
        self.assertEqual((rc, out.strip()), (0, "removed hook h7"))
        self.assertEqual(Stub.seen[-1][0], "/api/unhook")
        self.assertEqual(Stub.seen[-1][1]["hook"], "h7")
        rc, out, err = self.c("hook", "a1", "--on", "bogus", "--run", "x")
        self.assertEqual(rc, 1)
        self.assertIn("--on", err)


if __name__ == "__main__":
    unittest.main()
