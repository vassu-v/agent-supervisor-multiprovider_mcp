"""Stale-question surfacing (SPEC 4): an unanswered question reaches the asker's PARENT, once. In-process, fake provider only.
Run: python tests/test_stale_questions.py"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

HOME = tempfile.mkdtemp(prefix="sy-stale-home-")
os.environ["SWITCHYARD_HOME"] = HOME
os.environ["SWITCHYARD_FAKE"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from orch.core import Orchestrator  # noqa: E402
from tests.harness import read_inbox  # noqa: E402


def wait(fn, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.05)
    return False


class Stale(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(HOME, ignore_errors=True)

    def test_unanswered_question_is_surfaced_to_the_askers_parent_once(self):
        tmp = tempfile.mkdtemp(prefix="sy-stale-")
        self.addCleanup(shutil.rmtree, tmp, True)
        repo = os.path.join(tmp, "repo")
        os.makedirs(os.path.join(repo, ".git"))
        o = Orchestrator()
        o.spawn(json.dumps([{"say": "p"}]), os.path.join(repo, "p"), provider="fake", aid="st-par")
        self.assertTrue(wait(lambda: o.agents["st-par"].status == "idle"))
        o.spawn(json.dumps([{"say": "k"}]), os.path.join(repo, "k"), provider="fake", aid="st-kid", parent="st-par")
        self.assertTrue(wait(lambda: o.agents["st-kid"].status == "idle"))
        ws = o.agents["st-kid"].ws
        q = o.board_ask(ws, "agent:st-kid", "does anyone know the db schema?")
        before = len(read_inbox(os.path.join(repo, "p")))
        o._surface_stale(age_s=0)                       # pretend the 5 minutes have passed
        self.assertTrue(wait(lambda: len(read_inbox(os.path.join(repo, "p"))) > before), "the parent must be told")
        msg = read_inbox(os.path.join(repo, "p"))[-1]["text"]
        self.assertIn(f"#{q['id']}", msg)
        self.assertIn("not instructions", msg)
        n = len(read_inbox(os.path.join(repo, "p")))
        o._surface_stale(age_s=0)                       # surfaced once, not on every sweep
        time.sleep(0.8)
        self.assertEqual(len(read_inbox(os.path.join(repo, "p"))), n)
        o.shutdown()


if __name__ == "__main__":
    unittest.main(verbosity=2)
