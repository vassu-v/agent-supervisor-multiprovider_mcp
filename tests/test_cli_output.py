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

    def test_06_hook_endpoint_missing_is_not_a_crash(self):
        rc, out, err = self.c("hook", "w1", "--on", "done", "--run", "echo hi")      # the real server may not have it yet
        self.assertIn(rc, (0, 1))
        self.assertNotIn("Traceback", err)
        self.assertTrue(rc == 0 or err.startswith("error:"), err)


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
            self._reply({"result": "removed"})
        else:
            self._reply({"error": "no such endpoint"})

    def do_GET(self):
        Stub.seen.append((self.path, None))
        self._reply([{"id": "h7", "agent": "a1", "on": "done", "run": "echo hi", "repeat": False}] if self.path == "/api/hooks"
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
        self.assertIn("h7", out)
        self.assertIn("echo hi", out)
        self.assertNotIn("{", out)
        rc, out, _ = self.c("hooks", "--json")
        self.assertEqual(json.loads(out)[0]["id"], "h7")
        rc, out, _ = self.c("unhook", "h7")
        self.assertEqual((rc, out.strip()), (0, "removed hook h7"))
        self.assertEqual(Stub.seen[-1][0], "/api/unhook")
        self.assertEqual(Stub.seen[-1][1]["hook"], "h7")
        rc, out, err = self.c("hook", "a1", "--on", "bogus", "--run", "x")
        self.assertEqual(rc, 1)
        self.assertIn("--on", err)


if __name__ == "__main__":
    unittest.main()
