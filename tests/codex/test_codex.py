import os, sys, time, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
from orch.adapters.codex import CodexAdapter


class T(unittest.TestCase):
    def setUp(self):
        self.ev = []
        self.a = CodexAdapter("t", HERE, None, self.ev.append,
                              {"command": [sys.executable, os.path.join(HERE, "mock_app_server.py")]})
        self.a.start()

    def tearDown(self):
        self.a.kill()

    def wait(self, pred, t=10):
        end = time.time() + t
        while time.time() < end:
            if pred():
                return True
            time.sleep(0.02)
        self.fail("timeout; events=%r" % self.ev)

    def results(self):
        return [e for e in self.ev if e["type"] == "result"]

    def test_simple(self):
        self.assertEqual(self.a.session_id, "thread-1")
        self.a.send("hi")
        self.wait(lambda: self.results())
        r = self.results()[0]
        self.assertEqual((r["stop"], r["ok"]), ("end_turn", True))
        self.assertIn("hello", r["text"])
        types = [e["type"] for e in self.ev]
        for t in ("tool_start", "tool_end", "usage", "session"):
            self.assertIn(t, types)
        self.wait(lambda: self.ev[-1] == {"type": "status", "state": "idle"})

    def test_steer(self):
        self.a.send("slow")
        self.wait(lambda: any(e["type"] == "tool_end" for e in self.ev))
        self.a.send("go left", "steer")
        self.wait(lambda: self.results(), 15)
        self.assertIn("steer:go left", self.results()[0]["text"])

    def test_interrupt_then_followup(self):
        self.a.send("slow")
        self.wait(lambda: any(e["type"] == "tool_end" for e in self.ev))
        self.a.send("next", "interrupt")
        self.wait(lambda: len(self.results()) == 2)
        r = self.results()
        self.assertEqual(r[0]["stop"], "cancelled")
        self.assertEqual(r[1]["stop"], "end_turn")

    def test_approval(self):
        self.a.send("approve")
        self.wait(lambda: self.results())
        self.assertTrue(any(e["type"] == "permission" and e["tool"] == "shell" for e in self.ev))
        self.assertIn("accept", self.results()[0]["text"])

    def test_kill(self):
        self.a.send("slow")
        self.a.kill()
        self.a.kill()
        self.assertFalse(self.a.alive)
        self.wait(lambda: any(e == {"type": "status", "state": "dead"} for e in self.ev))


if __name__ == "__main__":
    unittest.main()
