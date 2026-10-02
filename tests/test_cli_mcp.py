"""CLI (agentctl.py) and MCP bridge (orch/mcp_bridge.py) tests against a throwaway daemon with the fake provider (no model turns).
Run: python tests/test_cli_mcp.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tests.harness import api, free_port, spawn_fake, start_daemon, wait_status  # noqa: E402

CTL = os.path.join(ROOT, "agentctl.py")
BRIDGE = os.path.join(ROOT, "orch", "mcp_bridge.py")
NL = chr(10) + "board:"
NEW_TOOLS = ["board_read", "board_post", "board_ask", "board_answer", "agent_declare", "workspace_info", "sessions_list"]


def clean_env(**extra):
    e = {k: v for k, v in os.environ.items() if not k.startswith(("ORCH_", "SWITCHYARD_"))}
    e["PYTHONIOENCODING"] = "utf-8"
    e.update(extra)
    return e


class Bridge:
    def __init__(self, url, token, cwd, **env):
        self.p = subprocess.Popen([sys.executable, BRIDGE], cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                  env=clean_env(ORCH_URL=url, ORCH_TOKEN=token, **env))
        self.n = 0

    def rpc(self, method, params=None):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}}) + "\n")
        self.p.stdin.flush()
        line = self.p.stdout.readline()
        assert line, "bridge closed: " + self.p.stderr.read()
        return json.loads(line)

    def init(self, name="test-client"):
        return self.rpc("initialize", {"protocolVersion": "2024-11-05", "clientInfo": {"name": name, "version": "1"}})["result"]

    def tool(self, name, **args):
        r = self.rpc("tools/call", {"name": name, "arguments": args})
        if "error" in r:
            return r
        res = r["result"]
        return res["isError"], res["content"][0]["text"]

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(10)
        except Exception:
            self.p.kill()
        for s in (self.p.stdout, self.p.stderr):
            s.close()


class CliMcp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = start_daemon()
        cls.tmp = tempfile.mkdtemp(prefix="sy-climcp-")
        cls.repo = os.path.join(cls.tmp, "myrepo")
        os.makedirs(os.path.join(cls.repo, ".git"))
        os.makedirs(os.path.join(cls.repo, "sub"))
        cls.ws = api(cls.d, "GET", "/api/workspace?path=" + cls.repo)[1]["id"]

    @classmethod
    def tearDownClass(cls):
        cls.d.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def ctl(self, *args, cwd=None, url=None, **env):
        r = subprocess.run([sys.executable, CTL, *args], cwd=cwd or self.repo, capture_output=True, text=True, encoding="utf-8",
                           env=clean_env(ORCH_URL=url or self.d.url, ORCH_TOKEN=self.d.token, **env), timeout=60)
        return r.returncode, r.stdout, r.stderr

    def ok(self, *args, **kw):
        rc, out, err = self.ctl(*args, **kw)
        self.assertEqual(rc, 0, f"{args}: {err}{out}")
        self.assertNotIn("Traceback", err)
        return out

    def fail(self, *args, **kw):
        rc, out, err = self.ctl(*args, **kw)
        self.assertEqual(rc, 1, f"{args}: rc={rc} {out}{err}")
        self.assertNotIn("Traceback", err)
        self.assertEqual(len([x for x in err.splitlines() if x.strip()]), 1, err)
        self.assertTrue(err.startswith("error:"), err)
        return err

    # ------------------------------------------------------------------ CLI
    def test_01_ws_and_default_resolution(self):
        w = json.loads(self.ok("ws"))                      # cwd = repo
        self.assertEqual(w["id"], self.ws)
        w = json.loads(self.ok("ws", os.path.join(self.repo, "sub")))
        self.assertEqual((w["id"], w["subdir"]), (self.ws, "sub"))
        # --ws default comes from the cwd's workspace, from a subfolder too
        self.ok("announce", "hello from sub", cwd=os.path.join(self.repo, "sub"))
        self.assertIn("hello from sub", self.ok("board", "--ws", self.ws))
        self.assertIn("hello from sub", self.ok("board", cwd=self.repo))
        # env ORCH_WORKSPACE wins over the directory
        other = os.path.join(self.tmp, "other")
        os.makedirs(os.path.join(other, ".git"))
        self.assertNotIn("hello from sub", self.ok("board", cwd=other))
        self.assertIn("hello from sub", self.ok("board", cwd=other, ORCH_WORKSPACE=self.ws))

    def test_02_announce_board_ask_answer(self):
        self.ok("announce", "parser done", "--kind", "done", "--paths", "src/a.py,src/b.py")
        out = self.ok("board")
        self.assertRegex(out, r"#\d+ \[done\] \S+ \d\d:\d\d  parser done")
        self.assertTrue(out.splitlines()[0].startswith("open questions:"))
        raw = json.loads(self.ok("board", "--json", "--kind", "done"))
        self.assertTrue(all(p["kind"] == "done" for p in raw["posts"]))
        self.assertEqual(raw["posts"][-1]["paths"], ["src/a.py", "src/b.py"])
        last = raw["last"]
        self.assertEqual(json.loads(self.ok("board", "--json", "--since", str(last)))["posts"], [])
        api(self.d, "POST", "/api/ask", {"ws": self.ws, "text": "which db do we use?", "by": "asker"})
        q = [p for p in json.loads(self.ok("board", "--json", "--kind", "question"))["posts"] if p["status"] == "open"][-1]
        self.assertIn("which db", q["text"])
        self.assertEqual(json.loads(self.ok("board", "--json"))["open_questions"] >= 1, True)
        self.ok("answer", str(q["id"]), "sqlite")
        after = json.loads(self.ok("board", "--json", "--kind", "question"))["posts"]
        self.assertEqual([p for p in after if p["id"] == q["id"]][0]["status"], "answered")

    def test_03_bad_input(self):
        self.fail("announce")                                       # missing text
        self.assertIn("--kind", self.fail("announce", "x", "--kind", "bogus"))
        self.fail("ask")
        self.fail("answer", "5")
        self.assertIn("post number", self.fail("answer", "abc", "text"))
        self.fail("answer", "999999", "nope")                       # unknown post
        self.fail("board", "--since", "abc")
        self.fail("declare")                                        # nothing to declare
        self.fail("declare", "--goal", "g")                         # no id and not an agent
        self.fail("declare", "--goal", "g", "--id", "no-such-agent")
        self.fail("send", "only-id")
        self.fail("status")
        self.fail("announce", "x", "--ws")                          # flag without value
        self.fail("ws", os.path.expanduser("~"))                    # refused workspace root
        self.fail("announce", "x" * 600)                            # daemon rejects >500 chars: one-line error

    def test_04_unreachable_daemon(self):
        err = self.fail("list", url=f"http://127.0.0.1:{free_port()}")
        self.assertIn("cannot reach the daemon", err)

    def test_05_spawn_declare_list_tree(self):
        st, r = spawn_fake(self.d, [{"say": "hi"}], os.path.join(self.repo, "p"), id="parent1")
        self.assertEqual(st, 200, r)
        out = self.ok("spawn", json.dumps([{"say": "child"}]), "--provider", "fake", "--cwd", os.path.join(self.repo, "c"),
                      "--id", "child1", "--goal", "do the child thing", "--paths", "src/x.py,docs/", "--parent", "parent1")
        self.assertEqual(json.loads(out)["id"], "child1")
        wait_status(self.d, "parent1", "idle")
        wait_status(self.d, "child1", "idle")
        info = api(self.d, "GET", "/api/status?id=child1")[1]
        self.assertEqual(info["goal"], "do the child thing")
        self.assertEqual(info["paths"], ["src/x.py", "docs/"])
        self.assertEqual(info["parent"], "parent1")
        self.ok("declare", "--goal", "new goal", "--paths", "a/**", "--id", "parent1")
        info = api(self.d, "GET", "/api/status?id=parent1")[1]
        self.assertEqual((info["goal"], info["paths"]), ("new goal", ["a/**"]))
        tree = self.ok("list", "--tree", "--ws", self.ws).splitlines()
        i_p = next(i for i, l in enumerate(tree) if l.startswith("parent1"))
        self.assertTrue(tree[i_p + 1].startswith("  child1"), tree)
        self.assertIn("goal: do the child thing", tree[i_p + 1])
        plain = json.loads(self.ok("list", "--all"))
        self.assertTrue({"parent1", "child1"} <= {a["id"] for a in plain})
        self.assertEqual({a["id"] for a in json.loads(self.ok("list", "--ws", self.ws))} >= {"parent1", "child1"}, True)
        self.assertIn("parent1", self.ok("who", "--ws", self.ws))
        self.fail("spawn", "x", "--paths")                          # --paths without a value

    def test_06_sessions_and_session_header(self):
        st, h = api(self.d, "POST", "/api/hello", {"client": "hdr-client", "workspace": self.repo})
        self.assertEqual(st, 200, h)
        rows = json.loads(self.ok("sessions"))
        self.assertTrue(any(s["client"] == "hdr-client" for s in rows))
        self.ok("announce", "via session header", "--kind", "info", SWITCHYARD_SESSION=h["session"])
        posts = json.loads(self.ok("board", "--json"))["posts"]
        mine = [p for p in posts if p["text"] == "via session header"][0]
        self.assertTrue(mine["sender"].startswith("session:hdr-client"), mine)

    # ------------------------------------------------------------------ MCP bridge
    def test_10_bridge_full_flow(self):
        b = Bridge(self.d.url, self.d.token, self.repo)
        try:
            init = b.init("my-editor")
            self.assertEqual(init["serverInfo"]["version"], "0.3")
            self.assertIn("Workspace:", init["instructions"])
            self.assertIn("Provider availability", init["instructions"])
            sessions = api(self.d, "GET", "/api/sessions")[1]
            self.assertTrue(any(s["client"] == "my-editor" and s["workspace"] == self.ws for s in sessions), sessions)

            tools = {t["name"]: t for t in b.rpc("tools/list")["result"]["tools"]}
            for n in NEW_TOOLS:
                self.assertIn(n, tools)
            self.assertEqual(tools["board_post"]["inputSchema"]["properties"]["kind"]["enum"],
                             ["started", "done", "changed", "blocked", "info", "handoff"])
            self.assertEqual(tools["agent_list"]["inputSchema"]["properties"]["scope"]["enum"], ["workspace", "all"])
            self.assertEqual(tools["agent_list"]["inputSchema"]["properties"]["tree"]["type"], "boolean")
            for k in ("goal", "paths"):
                self.assertIn(k, tools["agent_spawn"]["inputSchema"]["properties"])
            self.assertEqual(tools["board_answer"]["inputSchema"]["required"], ["id", "text"])
            self.assertIn("mode", tools["agent_send"]["inputSchema"]["properties"])      # existing tools kept

            # no stray notice at first
            err, txt = b.tool("sessions_list")
            self.assertFalse(err)
            self.assertNotIn("board:", txt)

            # another client posts -> trailing notice exactly once
            api(self.d, "POST", "/api/announce", {"ws": self.ws, "text": "other client change", "kind": "changed", "by": "other"})
            err, txt = b.tool("sessions_list")
            self.assertFalse(err)
            self.assertEqual(txt.splitlines()[-1], "board: 1 new since your last call")
            err, txt = b.tool("sessions_list")
            self.assertNotIn("board:", txt)

            # own posts do not count
            err, txt = b.tool("board_post", text="bridge says hi", kind="done", paths=["x/y"])
            self.assertFalse(err, txt)
            # the notice is computed after the post itself: the client's own post must not count (sender format must match the daemon's)
            self.assertNotIn("board:", txt, "own post counted in its own notice")
            err, txt = b.tool("workspace_info")
            self.assertFalse(err, txt)
            self.assertNotIn("board:", txt)
            self.assertEqual(json.loads(txt.split("\nboard:")[0])["id"], self.ws)
            err, txt = b.tool("workspace_info", path=os.path.join(self.repo, "sub"))
            self.assertEqual(json.loads(txt)["subdir"], "sub")

            # ask by the bridge, answer by someone else, and the reverse
            err, txt = b.tool("board_ask", text="bridge question?")
            self.assertFalse(err, txt)
            q = [p for p in api(self.d, "GET", f"/api/board?ws={self.ws}&kind=question")[1]["posts"] if p["text"] == "bridge question?"][0]
            self.assertEqual(api(self.d, "POST", "/api/answer", {"id": q["id"], "text": "yes", "by": "other"})[0], 200)
            api(self.d, "POST", "/api/ask", {"ws": self.ws, "text": "api question?", "by": "other"})
            q2 = [p for p in api(self.d, "GET", f"/api/board?ws={self.ws}&kind=question")[1]["posts"] if p["text"] == "api question?"][0]
            err, txt = b.tool("board_answer", id=q2["id"], text="42")
            self.assertFalse(err, txt)
            err, txt = b.tool("board_answer", id=str(q2["id"]), text="again")        # numeric string tolerated; daemon may refuse
            err, txt = b.tool("board_read", kind="question,done")
            self.assertFalse(err, txt)
            body = json.loads(txt.split("\nboard:")[0])
            self.assertTrue({"bridge says hi", "bridge question?", "api question?"} <= {p["text"] for p in body["posts"]})
            self.assertEqual(body["open_questions"], 0)

            # declare + list tree / scope
            st, r = spawn_fake(self.d, [{"say": "hi"}], os.path.join(self.repo, "m1"), id="mcp-parent")
            self.assertEqual(st, 200, r)
            st, r = spawn_fake(self.d, [{"say": "hi"}], os.path.join(self.repo, "m2"), id="mcp-child", parent="mcp-parent")
            self.assertEqual(st, 200, r)
            wait_status(self.d, "mcp-child", "idle")
            err, txt = b.tool("agent_declare", id="mcp-parent", goal="mcp goal", paths=["m1/**"])
            self.assertFalse(err, txt)
            info = api(self.d, "GET", "/api/status?id=mcp-parent")[1]
            self.assertEqual((info["goal"], info["paths"]), ("mcp goal", ["m1/**"]))
            err, txt = b.tool("agent_list", tree=True)
            self.assertFalse(err, txt)
            rows = json.loads(txt.split("\nboard:")[0])
            ids = [a["id"] for a in rows]
            self.assertEqual(ids.index("mcp-child"), ids.index("mcp-parent") + 1)
            self.assertEqual(rows[ids.index("mcp-child")]["depth"], 1)
            # a different workspace is hidden by default, shown by scope=all
            far = os.path.join(self.tmp, "far")
            os.makedirs(os.path.join(far, ".git"))
            st, r = spawn_fake(self.d, [{"say": "hi"}], far, id="far-agent")
            self.assertEqual(st, 200, r)
            ids = [a["id"] for a in json.loads(b.tool("agent_list")[1].split("\nboard:")[0])]
            self.assertNotIn("far-agent", ids)
            ids = [a["id"] for a in json.loads(b.tool("agent_list", scope="all")[1].split("\nboard:")[0])]
            self.assertIn("far-agent", ids)
            self.assertIn("mcp-child", ids)

            # validation
            err, txt = b.tool("board_post")
            self.assertTrue(err)
            self.assertIn("missing required argument(s): text", txt)
            err, txt = b.tool("board_post", text="x", kind="bogus")
            self.assertTrue(err)
            self.assertIn("kind must be one of", txt)
            err, txt = b.tool("agent_list", scope="elsewhere")
            self.assertTrue(err)
            err, txt = b.tool("board_answer", id="abc", text="x")
            self.assertTrue(err)
            self.assertIn("integer", txt)
            err, txt = b.tool("board_read", kind="nonsense")
            self.assertTrue(err)
            err, txt = b.tool("agent_declare", id="mcp-parent")
            self.assertTrue(err)
            r = b.tool("no_such_tool")
            self.assertEqual(r["error"]["code"], -32602)
            self.assertEqual(b.rpc("bogus/method")["error"]["code"], -32601)
            self.assertEqual(b.rpc("ping")["result"], {})
        finally:
            b.close()

    def test_11_bridge_rehello_after_idle(self):
        b = Bridge(self.d.url, self.d.token, self.repo, SWITCHYARD_REHELLO_S="0.2")
        try:
            b.init("idle-client")
            time.sleep(0.5)
            self.assertFalse(b.tool("sessions_list")[0])           # triggers a transparent re-hello
            time.sleep(0.5)
            err, txt = b.tool("sessions_list")
            self.assertFalse(err, txt)
            self.assertTrue(any(s["client"] == "idle-client" for s in json.loads(txt.split(NL)[0])))
        finally:
            b.close()

    def test_12_bridge_unresolvable_workspace_needs_ws(self):
        home = os.path.expanduser("~")
        b = Bridge(self.d.url, self.d.token, home, SWITCHYARD_WORKSPACE=home)
        try:
            b.init()
            err, txt = b.tool("board_read")
            self.assertTrue(err)
            self.assertIn("pass `ws`", txt)
            err, txt = b.tool("board_read", ws=self.ws)
            self.assertFalse(err, txt)
            err, txt = b.tool("agent_list")                        # no workspace -> defaults to all
            self.assertFalse(err, txt)
            self.assertTrue(txt.lstrip().startswith("["))
        finally:
            b.close()

    def test_13_bridge_daemon_down(self):
        b = Bridge(f"http://127.0.0.1:{free_port()}", self.d.token, self.repo)
        try:
            init = b.init()
            self.assertIn("daemon is not running", init["instructions"])
            self.assertEqual(len(b.rpc("tools/list")["result"]["tools"]) >= 20, True)
            err, txt = b.tool("agent_list")
            self.assertTrue(err)
            self.assertIn("unreachable", txt)
            self.assertNotIn(self.d.token, txt)
            err, txt = b.tool("board_post", text="x")
            self.assertTrue(err)
            self.assertEqual(b.rpc("ping")["result"], {})
        finally:
            b.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
