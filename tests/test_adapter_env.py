"""opts['env'] is merged into each adapter's child-process environment."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from orch.adapters.agy import AgyAdapter
from orch.adapters.claude import ClaudeAdapter
from orch.adapters.codex import CodexAdapter
from orch.adapters.opencode import OpenCodeAdapter

EXTRA = {"ORCH_URL": "http://x", "ORCH_TOKEN": "t0k", "ORCH_AGENT": "a1",
         "ORCH_WORKSPACE": "w", "ORCH_PARENT": "p"}


def make(cls, env=EXTRA):
    return cls("a1", os.getcwd(), "m", lambda e: None, {"env": dict(env)} if env is not None else {})


class AdapterEnv(unittest.TestCase):
    def _launch_env(self, cls):
        with mock.patch("subprocess.Popen") as popen, mock.patch("threading.Thread"):
            make(cls).start()
        return popen.call_args.kwargs["env"]

    def check(self, env):
        for k, v in EXTRA.items():
            self.assertEqual(env[k], v)
        self.assertTrue(any(k.upper() == "PATH" for k in env))

    def test_agy_popen(self):
        self.check(self._launch_env(AgyAdapter))

    def test_claude_popen_and_filtering(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_FOO": "1", "CLAUDECODE": "1",
                                          "CLAUDE_CODE_GIT_BASH_PATH": "C:\bash.exe",
                                          "ORCH_TOKEN": "stale"}):
            env = self._launch_env(ClaudeAdapter)
        self.check(env)
        self.assertNotIn("CLAUDE_CODE_FOO", env)
        self.assertNotIn("CLAUDECODE", env)
        self.assertEqual(env["CLAUDE_CODE_GIT_BASH_PATH"], "C:\bash.exe")

    def test_claude_extras_win_over_filter(self):
        a = make(ClaudeAdapter, {"CLAUDE_CODE_X": "keep", "ORCH_TOKEN": "t0k"})
        self.assertEqual(a._child_env()["CLAUDE_CODE_X"], "keep")

    def test_opencode_child_env(self):
        with mock.patch.dict(os.environ, {"OPENCODE_SERVER_PASSWORD": "pw"}):
            env = make(OpenCodeAdapter)._child_env()
        self.check(env)
        self.assertIn("OPENCODE_CONFIG_CONTENT", env)
        self.assertNotIn("OPENCODE_SERVER_PASSWORD", env)

    def test_codex_child_env(self):
        self.check(make(CodexAdapter)._child_env())

    def test_no_env_opt_and_str_coercion(self):
        for cls in (AgyAdapter, ClaudeAdapter, OpenCodeAdapter, CodexAdapter):
            env = make(cls, None)._child_env()
            self.assertEqual(env.get("ORCH_TOKEN"), os.environ.get("ORCH_TOKEN"))
            self.assertTrue(all(isinstance(v, str) for v in make(cls, {"ORCH_N": 5})._child_env().values()))


if __name__ == "__main__":
    unittest.main()
