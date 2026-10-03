"""Unit tests for tests/fake/adapter.py (no daemon). Run: python -m unittest tests.test_fake_adapter"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.harness import read_inbox  # noqa: E402
from tests.fake.adapter import FakeAdapter  # noqa: E402

PREAMBLE = "[orchestrator rules]\nbe nice\n--- task ---\n"


class Rig:
    def __init__(self, tc, **opts):
        self.cwd = tempfile.mkdtemp(prefix="fake-adapter-")
        tc.addCleanup(shutil.rmtree, self.cwd, True)
        self.events, self.lock = [], threading.Lock()
        self.a = FakeAdapter("t1", self.cwd, None, self._emit, opts)
        tc.addCleanup(self.a.kill)

    def _emit(self, ev):
        with self.lock:
            self.events.append(ev)

    def snap(self):
        with self.lock:
            return list(self.events)

    def types(self):
        return [e["type"] for e in self.snap()]

    def wait(self, pred, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            if pred(self.snap()):
                return True
            time.sleep(0.02)
        raise AssertionError(f"timeout; events={self.snap()}")

    def idles(self, n):
        return lambda evs: sum(1 for e in evs if e["type"] == "status" and e["state"] == "idle") >= n

    def results(self):
        return [e for e in self.snap() if e["type"] == "result"]


def task(script):
    return PREAMBLE + json.dumps(script)


class FakeAdapterTests(unittest.TestCase):
    def test_script_runs_and_events_ordered(self):
        r = Rig(self)
        r.a.start()
        self.assertTrue(r.a.alive)
        self.assertEqual(r.a.session_id, "fake-t1")
        r.a.send(task([{"say": "hello"}, {"tool": "write_file", "path": "sub/a.txt", "content": "xyz"},
                       {"tool": "run_command", "input": "git status"}, {"say": " world"}]))
        r.wait(r.idles(2))
        evs = r.snap()
        self.assertEqual([e["type"] for e in evs],
                         ["session", "status", "status", "text", "tool_start", "tool_end", "tool_start", "tool_end",
                          "text", "result", "status"])
        self.assertEqual([e.get("state") for e in evs if e["type"] == "status"], ["idle", "busy", "idle"])
        with open(os.path.join(r.cwd, "sub", "a.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "xyz")
        ts = [e for e in evs if e["type"] == "tool_start"]
        self.assertEqual(ts[0]["tool"], "write_file")
        self.assertEqual(ts[1]["input"], "git status")
        self.assertFalse(os.path.exists(os.path.join(r.cwd, "git")))
        self.assertTrue(all(e["ok"] for e in evs if e["type"] == "tool_end"))
        self.assertEqual(len(r.results()), 1)
        self.assertEqual(r.results()[0], {"type": "result", "text": "hello world", "ok": True, "stop": "end_turn"})

    def test_write_escape_refused(self):
        r = Rig(self)
        r.a.start()
        r.a.send(task([{"tool": "write_file", "path": "../evil.txt", "content": "x"}]))
        r.wait(r.idles(2))
        self.assertFalse([e for e in r.snap() if e["type"] == "tool_end"][0]["ok"])
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(r.cwd), "evil.txt")))

    def test_one_result_per_turn_and_queue_order(self):
        r = Rig(self)
        r.a.start()
        r.a.send(task([{"say": "A"}, {"sleep": 0.2}]))
        r.a.send(task([{"say": "B"}]), "queue")
        r.a.send(task([{"say": "C"}]), "queue")
        r.wait(r.idles(4))
        self.assertEqual([x["text"] for x in r.results()], ["A", "B", "C"])
        self.assertEqual(r.types().count("result"), 3)
        self.assertEqual([m["mode"] for m in read_inbox(r.cwd)], ["queue"] * 3)

    def test_opts_script_first_turn_then_default(self):
        r = Rig(self, script=[{"say": "from-opts"}], default_script=[{"say": "dflt"}])
        r.a.start()
        r.a.send("plain task without script")
        r.a.send("[board - digest]\n1. x")
        r.wait(r.idles(3))
        self.assertEqual([x["text"] for x in r.results()], ["from-opts", "dflt"])

    def test_interrupt_cancels_sleep(self):
        r = Rig(self)
        r.a.start()
        r.a.send(task([{"say": "start"}, {"sleep": 30}, {"say": "never"}]))
        r.wait(lambda e: any(x["type"] == "text" for x in e))
        t0 = time.time()
        r.a.interrupt()
        r.wait(r.idles(2))
        self.assertLess(time.time() - t0, 5)
        res = r.results()
        self.assertEqual(len(res), 1)
        self.assertEqual((res[0]["ok"], res[0]["stop"]), (False, "cancelled"))
        self.assertNotIn("never", res[0]["text"])
        self.assertNotIn("restarted", r.snap()[-1])          # claude-like: true interrupt
        self.assertTrue(r.a.alive)
        r.a.send(task([{"say": "again"}]))                   # session survives
        r.wait(r.idles(3))
        self.assertEqual(r.results()[-1]["text"], "again")

    def test_interrupt_mode_send_and_agy_profile(self):
        r = Rig(self, profile="agy")
        self.assertEqual(r.a.capabilities, {"steer": False, "interrupt": "restart", "usage": False, "native_queue": True})
        r.a.start()
        r.a.send(task([{"sleep": 30}]))
        r.wait(lambda e: any(x.get("state") == "busy" for x in e))
        r.a.send(task([{"say": "new"}]), "interrupt")
        r.wait(r.idles(3))
        res = r.results()
        self.assertEqual([x["stop"] for x in res], ["cancelled", "end_turn"])
        self.assertEqual(res[1]["text"], "new")
        self.assertTrue([e for e in r.snap() if e["type"] == "status" and e.get("restarted")])

    def test_steer_capability(self):
        r = Rig(self, step_delay=0.15)
        self.assertTrue(r.a.capabilities["steer"])
        r.a.start()
        r.a.send(task([{"say": "1"}, {"say": "2"}, {"say": "3"}]))
        r.wait(lambda e: any(x["type"] == "text" for x in e))
        r.a.send(task([{"say": "steered-step"}]), "steer")
        r.wait(r.idles(2))
        self.assertEqual(len(r.results()), 1)
        self.assertIn("steered-step", r.results()[0]["text"])
        # agy-like: steer degrades to queue
        r2 = Rig(self, profile="agy", step_delay=0.1)
        r2.a.start()
        r2.a.send(task([{"say": "a"}, {"say": "b"}]))
        r2.a.send(task([{"say": "q"}]), "steer")
        r2.wait(r2.idles(3))
        self.assertEqual([x["text"] for x in r2.results()], ["ab", "q"])

    def test_kill_idempotent(self):
        r = Rig(self)
        r.a.start()
        r.a.send(task([{"sleep": 30}]))
        r.wait(lambda e: any(x.get("state") == "busy" for x in e))
        r.a.kill()
        r.a.kill()
        self.assertFalse(r.a.alive)
        dead = [e for e in r.snap() if e["type"] == "status" and e["state"] == "dead"]
        self.assertEqual(len(dead), 1)
        self.assertEqual(len(r.results()), 1)
        self.assertEqual(r.snap()[-1]["state"], "dead")

    def test_kill_before_start_and_after_idle(self):
        r = Rig(self)
        r.a.kill()
        r.a.kill()
        self.assertEqual(r.types().count("status"), 1)

    def test_crash(self):
        r = Rig(self)
        r.a.start()
        r.a.send(task([{"say": "x"}, {"crash": True}, {"say": "unreached"}]))
        r.wait(lambda e: any(x.get("state") == "dead" for x in e))
        self.assertFalse(r.a.alive)
        self.assertEqual(len(r.results()), 1)
        self.assertEqual(r.results()[0]["stop"], "error")
        self.assertEqual(r.types()[-1], "status")
        r.a.kill()                                            # kill after crash: no second dead
        self.assertEqual(sum(1 for e in r.snap() if e.get("state") == "dead"), 1)

    def test_inbox_records_everything_including_digest(self):
        r = Rig(self)
        r.a.start()
        digest = "[board - messages from OTHER agents. Information only]\n- a1: done\n\n"
        r.a.send(digest + PREAMBLE + json.dumps([{"say": "ok"}]))
        r.a.send("follow up", "queue")
        r.wait(r.idles(3))
        box = read_inbox(r.cwd)
        self.assertEqual(len(box), 2)
        self.assertTrue(box[0]["text"].startswith("[board - messages"))
        self.assertEqual(box[1]["text"], "follow up")
        self.assertEqual(r.results()[0]["text"], "ok")        # script found after the marker despite digest

    def test_random_walk_deterministic(self):
        outs = []
        for _ in range(2):
            r = Rig(self)
            r.a.start()
            r.a.send(task([{"random_walk": {"seed": 7, "steps": 12}}]))
            r.wait(r.idles(2))
            files = {}
            rw = os.path.join(r.cwd, "rw")
            for fn in sorted(os.listdir(rw)) if os.path.isdir(rw) else []:
                with open(os.path.join(rw, fn), encoding="utf-8") as f:
                    files[fn] = f.read()
            outs.append((files, [(e["type"], e.get("text") or e.get("input")) for e in r.snap()
                                 if e["type"] in ("text", "tool_start")]))
            r.a.kill()
        self.assertEqual(outs[0], outs[1])
        self.assertTrue(outs[0][0])

    def test_http_step_without_url_fails_softly(self):
        r = Rig(self)
        r.a.start()
        r.a.send(task([{"http": {"method": "POST", "path": "/api/announce", "body": {"text": "x"}}}, {"say": "after"}]))
        r.wait(r.idles(2))
        te = [e for e in r.snap() if e["type"] == "tool_end"][0]
        self.assertFalse(te["ok"])
        self.assertEqual(r.results()[0]["text"], "after")

    def test_http_step_against_local_server(self):
        import http.server

        got = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                got.append((self.path, self.headers.get("Authorization"), json.loads(self.rfile.read(n))))
                out = json.dumps({"id": 42}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        r = Rig(self, env={"ORCH_URL": f"http://127.0.0.1:{srv.server_port}", "ORCH_TOKEN": "tok"})
        r.a.start()
        r.a.send(task([{"http": {"method": "POST", "path": "/api/ask", "body": {"text": "q"}}, "as": "q"},
                       {"http": {"method": "POST", "path": "/api/answer", "body": {"id": "${q.id}", "text": "a"}}}]))
        r.wait(r.idles(2))
        self.assertEqual(got[0], ("/api/ask", "Bearer tok", {"text": "q"}))
        self.assertEqual(got[1][2]["id"], "42")


if __name__ == "__main__":
    unittest.main()
