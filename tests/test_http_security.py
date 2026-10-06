"""HTTP hardening tests against a throwaway daemon on its own port (no model is started). Run: python tests/test_http_security.py"""
import http.client
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 18799
TOKEN = open(os.path.join(ROOT, "orch", "token.txt")).read().strip() if os.path.exists(os.path.join(ROOT, "orch", "token.txt")) else ""
env = dict(os.environ, ORCH_PORT=str(PORT))
srv = subprocess.Popen([sys.executable, os.path.join(ROOT, "agentctl.py"), "serve"], cwd=ROOT, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
fails = []


def req(method, path, body=None, host=None, headers=None, token=TOKEN):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=15)
    h = {"Host": host or f"127.0.0.1:{PORT}"}
    if token is not None:
        h["Authorization"] = f"Bearer {token}"
    h.update(headers or {})
    data = body if isinstance(body, (bytes, str)) else (json.dumps(body) if body is not None else None)
    if data is not None:
        h["Content-Type"] = "application/json"
    c.request(method, path, data, h)
    r = c.getresponse()
    txt = r.read().decode("utf-8", "replace")
    return r.status, txt


def check(name, got, expect):
    ok = got == expect
    print(("PASS " if ok else "FAIL ") + f"{name:58s} -> {got}")
    if not ok:
        fails.append((name, got, expect))


try:
    for _ in range(60):                                   # wait for the daemon
        try:
            if req("GET", "/api/health", token=None)[0] == 200:
                break
        except OSError:
            time.sleep(0.3)
    TOKEN = open(os.path.join(ROOT, "orch", "token.txt")).read().strip()      # created on first start

    check("health is public", req("GET", "/api/health", token=None)[0], 200)
    check("api without token -> 401", req("GET", "/api/list", token=None)[0], 401)
    check("api with wrong token -> 401", req("GET", "/api/list", token="nope")[0], 401)
    check("api with empty bearer -> 401", req("GET", "/api/list", token="")[0], 401)
    check("api with right token -> 200", req("GET", "/api/list")[0], 200)
    check("read endpoints need the token too (audit)", req("GET", "/api/audit", token=None)[0], 401)
    check("DNS rebinding: foreign Host on /api -> 403", req("GET", "/api/list", host=f"evil.example:{PORT}")[0], 403)
    check("DNS rebinding: foreign Host on dashboard -> 403", req("GET", "/", host=f"evil.example:{PORT}", token=None)[0], 403)
    check("foreign Origin on POST -> 403", req("POST", "/api/stop", {"id": "x", "reason": "r"}, headers={"Origin": "https://evil.example"})[0], 403)
    check("/ on loopback Host redirects to the new dashboard (302)", req("GET", "/", token=None)[0], 302)
    check("new dashboard on loopback Host -> 200", req("GET", "/ui/", token=None)[0], 200)
    st, page = req("GET", "/legacy", token=None)
    check("dashboard has no inline onclick handlers", "onclick=" in page, False)
    check("write via GET is refused (405)", req("GET", "/api/stop?id=x&reason=y")[0], 405)
    check("agent id path traversal rejected (400)", req("POST", "/api/spawn", {"task": "t", "cwd": ROOT, "id": "..\\..\\evil"})[0], 400)
    check("agent id with a quote rejected (400)", req("POST", "/api/spawn", {"task": "t", "cwd": ROOT, "id": "x');alert(1);//"})[0], 400)
    check("invalid JSON -> 400, daemon survives", req("POST", "/api/send", "{not json")[0], 400)
    check("JSON array body -> 400", req("POST", "/api/send", "[1,2]")[0], 400)
    check("unknown tier -> 400", req("POST", "/api/route", {"task": "x", "tier": "cheap"})[0], 400)
    check("bad send mode -> 400", req("POST", "/api/send", {"id": "nope", "msg": "hi", "mode": "zzz"})[0], 400)
    check("bad escalation decision -> 400", req("POST", "/api/resolve", {"escalation": "e1", "decision": "maybe"})[0], 400)
    check("oversized body -> 413", req("POST", "/api/send", b"x", headers={"Content-Length": "99999999"})[0], 413)
    check("daemon still healthy afterwards", req("GET", "/api/health", token=None)[0], 200)

    dup = subprocess.run([sys.executable, os.path.join(ROOT, "agentctl.py"), "serve"], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=30)
    check("second daemon on the same port refuses to start", dup.returncode != 0 and "cannot bind" in (dup.stdout + dup.stderr), True)
finally:
    subprocess.run(["taskkill", "/PID", str(srv.pid), "/T", "/F"], capture_output=True)

print(f"\n{len(fails)} failure(s)")
for f in fails:
    print("  ", f)
sys.exit(1 if fails else 0)
