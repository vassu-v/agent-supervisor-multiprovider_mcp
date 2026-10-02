"""QA harness: throwaway Switchyard daemon with the fake provider, plus small API helpers.

    from tests.harness import start_daemon, api, spawn_fake, wait_status, read_inbox
    d = start_daemon()
    try:
        st, r = spawn_fake(d, [{"say": "hi"}], cwd)
        wait_status(d, r["id"], "idle", 20)
    finally:
        d.stop()

Needs (SPEC 0.3 section 7): env SWITCHYARD_HOME (isolated logs/db/token/config) and SWITCHYARD_FAKE=1 (provider 'fake').
"""
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Daemon:
    def __init__(self, proc, port, home, own_home, log_path):
        self.proc, self.port, self.home, self.own_home, self.log_path = proc, port, home, own_home, log_path
        self.url = f"http://127.0.0.1:{port}"
        self.token = ""

    def output(self):
        try:
            with open(self.log_path, encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return ""

    def stop(self):
        p = self.proc
        if p.poll() is None:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
            else:
                p.kill()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                pass
        if self.own_home:
            shutil.rmtree(self.home, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.stop()


def start_daemon(port=None, home=None, env=None, timeout=30):
    port = port or free_port()
    own_home = home is None
    home = os.path.abspath(home or tempfile.mkdtemp(prefix="switchyard-home-"))
    os.makedirs(home, exist_ok=True)
    e = dict(os.environ, ORCH_PORT=str(port), SWITCHYARD_HOME=home, SWITCHYARD_FAKE="1", PYTHONUNBUFFERED="1",
             PYTHONPATH=ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""))
    e.update({k: str(v) for k, v in (env or {}).items()})
    log_path = os.path.join(home, "daemon.out")
    log = open(log_path, "ab")
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "agentctl.py"), "serve"], cwd=ROOT, env=e,
                            stdout=log, stderr=subprocess.STDOUT)
    log.close()
    d = Daemon(proc, port, home, own_home, log_path)
    token_file = os.path.join(home, "orch", "token.txt")
    deadline = time.time() + timeout
    ok = False
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        try:
            if api(d, "GET", "/api/health", token=False)[0] == 200 and os.path.exists(token_file):
                ok = True
                break
        except OSError:
            pass
        time.sleep(0.2)
    if not ok:
        out = d.output()
        d.stop()
        raise RuntimeError(f"daemon did not become healthy (exit={proc.poll()}):\n{out[-2000:]}")
    with open(token_file, encoding="utf-8") as f:
        d.token = f.read().strip()
    return d


def api(daemon, method, path, body=None, token=None, host=None, headers=None):
    """-> (status, parsed JSON or raw text). token=None uses the admin token; token=False sends none;
    any string is sent as the bearer. host overrides the Host header."""
    tok = daemon.token if token is None else token
    c = http.client.HTTPConnection("127.0.0.1", daemon.port, timeout=30)
    h = {"Host": host or f"127.0.0.1:{daemon.port}"}
    if tok is not False:
        h["Authorization"] = f"Bearer {tok}"
    h.update(headers or {})
    data = body if isinstance(body, (bytes, str)) else (json.dumps(body) if body is not None else None)
    if data is not None:
        h["Content-Type"] = "application/json"
    try:
        c.request(method, path, data, h)
        r = c.getresponse()
        txt = r.read().decode("utf-8", "replace")
    finally:
        c.close()
    try:
        return r.status, json.loads(txt)
    except ValueError:
        return r.status, txt


def spawn_fake(daemon, script, cwd, **kw):
    """Spawn a fake agent whose task is the JSON script. kw: id, model, tier, goal, paths, parent, owner, opts (dict
    merged into the adapter opts: profile/capabilities/step_delay/...), token (agent token to spawn as), by.
    -> (status, response)."""
    token = kw.pop("token", None)
    os.makedirs(cwd, exist_ok=True)
    body = {"provider": "fake", "task": script if isinstance(script, str) else json.dumps(script), "cwd": cwd}
    body.update(kw)
    return api(daemon, "POST", "/api/spawn", body, token=token)


def get_agent(daemon, aid):
    return api(daemon, "GET", f"/api/status?id={aid}")[1]


def wait_status(daemon, aid, status, timeout=20):
    """Poll /api/status until the agent's status equals `status` (str or collection of str). Returns the info dict;
    raises AssertionError on timeout."""
    want = {status} if isinstance(status, str) else set(status)
    deadline = time.time() + timeout
    info = None
    while time.time() < deadline:
        st, info = api(daemon, "GET", f"/api/status?id={aid}")
        if st == 200 and isinstance(info, dict) and (info.get("status") or info.get("state")) in want:
            return info
        time.sleep(0.1)
    raise AssertionError(f"agent {aid} did not reach {sorted(want)} in {timeout}s; last={info}\n"
                         f"daemon log tail:\n{daemon.output()[-1500:]}")


def read_inbox(cwd):
    """-> list of {ts, agent, mode, text} for every message the fake agent in `cwd` received."""
    p = os.path.join(cwd, ".fake_inbox.jsonl")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def read_http_log(cwd):
    p = os.path.join(cwd, ".fake_http.jsonl")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]
