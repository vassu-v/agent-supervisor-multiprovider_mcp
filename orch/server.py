"""HTTP API + live dashboard for the orchestrator. Loopback only. Every call except /api/health needs the bearer token, and
the Host/Origin headers must be the loopback address (defeats DNS rebinding and cross-site requests)."""
import hmac
import json
import os
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from orch import identity, providers  # noqa: E402
from orch import workspace as wsmod  # noqa: E402
from orch.core import HERE, HOME, Orchestrator  # noqa: E402

PORT = int(os.environ.get("ORCH_PORT", "8765"))
TOKEN_FILE = os.path.join(HOME, "orch", "token.txt")
os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
ORCH = Orchestrator()


def token():
    t = ""
    try:
        t = open(TOKEN_FILE).read().strip()
    except OSError:
        pass
    if len(t) < 16:                        # missing or empty token file: generate a fresh one
        t = secrets.token_hex(24)
        with open(TOKEN_FILE, "w") as f:
            f.write(t)
        try:
            os.chmod(TOKEN_FILE, 0o600)
        except OSError:
            pass
    return t


TOKEN = token()


def _int(v, name):
    try:
        return int(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer")


def _bool(v):
    return v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")


def _own_ws(who):
    return ORCH._get(who["aid"]).ws


def _check_ws(who, aid):
    """An agent may only look at / stop agents in its own workspace."""
    if who["kind"] == "agent" and ORCH._get(aid).ws != _own_ws(who):
        raise PermissionError("that agent is in a different workspace")


def api(path, body, query, who):
    o = ORCH
    by = who["by"]
    is_agent = who["kind"] == "agent"
    if path == "/api/spawn":
        parent = who["aid"] if is_agent else (body.get("parent") or None)
        return o.spawn(body["task"], body["cwd"], body.get("provider", "auto"), body.get("model"), body.get("tier"),
                       body.get("id"), by, None if is_agent else body.get("opts"), goal=body.get("goal"),
                       paths=body.get("paths"), parent=parent, session=who.get("session_id"))
    if path == "/api/send":
        return o.send(body["id"], body["msg"], body.get("mode", "queue"), by)
    if path == "/api/interrupt":
        return o.interrupt(body["id"], by)
    if path == "/api/stop":
        _check_ws(who, body["id"])
        return o.stop(body["id"], by, body.get("reason", ""))
    if path == "/api/hello":
        ws = None
        if body.get("workspace"):
            try:
                ws = wsmod.resolve(body["workspace"], override=False)["id"]
            except ValueError:
                ws = None
        s = o.sessions.hello(body.get("client") or "unknown", body.get("label"), ws, body.get("pid"))
        return {"session": s["id"], "workspace": ws, "client": s["client"], "label": s["label"]}
    if path == "/api/sessions":
        live = o.sessions.list_live()
        if is_agent:                              # agents see who is attached to their own workspace, never ids or pids
            return [{"client": s["client"], "label": s.get("label")} for s in live if s.get("workspace") == _own_ws(who)]
        return live
    if path == "/api/workspaces":
        return o.workspaces()
    if path == "/api/workspace":
        if body.get("path"):
            r = wsmod.resolve(body["path"], override=False)
            if is_agent and r["id"] != _own_ws(who):
                raise PermissionError("agents may only resolve paths inside their own workspace")
            return r
        ws = _own_ws(who) if is_agent else body.get("ws")
        return next((w for w in o.workspaces() if w["id"] == ws), None) or {"id": ws, "agents": 0}
    if path == "/api/declare":
        aid = who["aid"] if is_agent else body["id"]
        return o.declare(aid, body.get("goal"), body.get("paths"), by)
    if path in ("/api/announce", "/api/ask"):
        ws = _own_ws(who) if is_agent else (body.get("ws") or None)
        if not ws:
            raise ValueError("ws is required (the workspace id to post to; see /api/workspaces)")
        if path == "/api/announce":
            return o.board_post(ws, by, body.get("kind", "info"), body["text"], body.get("paths"),
                                _int(body["reply_to"], "reply_to") if body.get("reply_to") not in (None, "") else None)
        return o.board_ask(ws, by, body["text"], body.get("paths"))
    if path == "/api/answer":
        pid = _int(body["id"], "id")
        q = o.store.get_post(pid)
        if not q:
            raise KeyError(f"post {pid}")
        if is_agent and q["ws"] != _own_ws(who):
            raise PermissionError("that question is in a different workspace")
        return o.board_answer(pid, by, body["text"], ws=q["ws"])
    if path == "/api/board":
        ws = _own_ws(who) if is_agent else (body.get("ws") or None)
        if not ws:
            raise ValueError("ws is required (the workspace id to read; see /api/workspaces)")
        kinds = [k for k in str(body.get("kind") or "").split(",") if k] or None
        return o.board_read(ws, _int(body.get("since", 0), "since"), kinds, _int(body.get("n", 100), "n"))
    if path == "/api/briefing":
        ws = _own_ws(who) if is_agent else body.get("ws")
        return {"text": o.briefing(ws, who["aid"] if is_agent else None)}
    if path == "/api/resolve":
        return o.resolve(body["escalation"], body["decision"], body.get("note", ""), by)
    if path == "/api/route":
        return o.policy.route(body["task"], body.get("tier"), body.get("provider"), body.get("model"), o.available())
    if path == "/api/list":
        ws = _own_ws(who) if is_agent else (None if _bool(body.get("all", False)) else (body.get("ws") or None))
        return o.list(ws=ws, tree=_bool(body.get("tree", False)))
    if path == "/api/status":
        _check_ws(who, body["id"])
        return o._get(body["id"]).info(full=True)
    if path == "/api/tail":
        _check_ws(who, body["id"])
        return o.tail(body["id"], _int(body.get("n", 15), "n"))
    if path == "/api/events":
        return o.events(body["id"], _int(body.get("since", 0), "since"), _int(body.get("n", 100), "n"))
    if path == "/api/result":
        _check_ws(who, body["id"])
        return o.result(body["id"])
    if path == "/api/escalations":
        return [e for e in list(o.escalations.values()) if _bool(body.get("all", False)) or e["state"] == "pending"]
    if path == "/api/audit":
        return list(o.audit)[-_int(body.get("n", 50), "n"):]
    if path == "/api/summary":
        return {"text": providers.summary_text()}
    if path == "/api/providers":
        return providers.all_status(with_models=str(body.get("models", "1")) != "0")
    if path == "/api/models":
        names = [body["provider"]] if body.get("provider") else providers.NAMES
        for n in names:
            if n not in providers.NAMES:
                raise ValueError(f"unknown provider {n!r}; valid: {providers.NAMES}")
        limit = _int(body.get("limit", 50), "limit")
        flt = str(body.get("filter") or "").lower()
        out = {}
        for n in names:
            rec = providers.models(n, force=_bool(body.get("refresh", False)) and not is_agent)   # agents must not force CLI runs
            hit = [m for m in rec["models"] if flt in m["id"].lower()] if flt else rec["models"]
            out[n] = {"enabled": providers.enabled(n), "source": rec["source"], "error": rec["error"],
                      "total": len(rec["models"]), "matched": len(hit), "models": hit[:limit], "truncated": len(hit) > limit}
        return out
    if path == "/api/provider":
        out = providers.set_enabled(body["name"], _bool(body["enabled"]))
        o._audit("provider_toggle", provider=body["name"], enabled=out["enabled"], by=by)
        return out
    if path == "/api/health":
        return {"ok": True, "providers": o.available(), "agents": len(o.agents)}
    raise KeyError(path)


PUBLIC = {"/api/health"}
WRITES = {"/api/spawn", "/api/send", "/api/interrupt", "/api/stop", "/api/resolve", "/api/provider", "/api/hello",
          "/api/declare", "/api/announce", "/api/ask", "/api/answer"}
MAX_BODY = 1_000_000
CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; "
       "img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    @staticmethod
    def _err(e):
        if isinstance(e, KeyError):
            return {"error": f"missing or unknown value: {e.args[0]}", "kind": "KeyError"}
        return {"error": str(e) or type(e).__name__, "kind": type(e).__name__}

    def _send(self, code, data, ctype, extra=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _origin_ok(self):
        """Host must be the loopback address (blocks DNS rebinding); a browser Origin, if sent, must match it too."""
        ok = {f"127.0.0.1:{PORT}", f"localhost:{PORT}", f"[::1]:{PORT}"}
        if (self.headers.get("Host") or "") not in ok:
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in {f"http://{h}" for h in ok}

    def _identify(self, path, body_by):
        """-> who dict or None. admin token: full access. Agent token: the agent's own scope only, identity taken from the
        token (the request's claimed `by` is ignored). A session header labels admin callers (MCP bridge, dashboard)."""
        got = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
        if not got:
            return {"kind": "public", "by": "external"} if path in PUBLIC else None
        if hmac.compare_digest(got.encode(), TOKEN.encode()):
            who = {"kind": "admin", "by": str(body_by or "external")[:40]}
            sid = self.headers.get("X-Switchyard-Session")
            if sid and ORCH.sessions.touch(sid):
                s = ORCH.sessions.get(sid)
                who.update(by=identity.identity_string("session", s["client"], s.get("label")), session_id=sid)
            return who
        aid = ORCH.tokens.verify(got)
        if aid:
            return {"kind": "agent", "aid": aid, "by": identity.identity_string("agent", aid)}
        return {"kind": "public", "by": "external"} if path in PUBLIC else None

    def _gate(self, path, body_by=None):
        if not self._origin_ok():
            self._json({"error": "bad Host/Origin (loopback only)"}, 403)
            return None
        who = self._identify(path, body_by)
        if who is None:
            self._json({"error": "bad or missing token"}, 401)
            return None
        if who["kind"] == "agent" and not identity.agent_allowed(path):
            ORCH._audit("agent_denied", agent=who["aid"], path=path)
            self._json({"error": f"agents may not call {path}"}, 403)
            return None
        return who

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            if not self._origin_ok():
                return self._json({"error": "bad Host/Origin (loopback only)"}, 403)
            html = open(os.path.join(HERE, "orch", "dashboard.html"), encoding="utf-8").read().replace("__TOKEN__", TOKEN)
            return self._send(200, html.encode(), "text/html; charset=utf-8",
                              [("Content-Security-Policy", CSP), ("X-Frame-Options", "DENY"), ("Referrer-Policy", "no-referrer")])
        if not u.path.startswith("/api/"):
            return self._json({"error": "not found"}, 404)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        who = self._gate(u.path, q.get("by"))
        if who is None:
            return
        if u.path in WRITES:
            return self._json({"error": "use POST for this call"}, 405)
        try:
            self._json(api(u.path, q, q, who))
        except Exception as e:
            self._json(self._err(e), 400)

    def do_POST(self):
        u = urlparse(self.path)
        who = self._gate(u.path)
        if who is None:
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._json({"error": "bad Content-Length"}, 400)
        if n > MAX_BODY:
            return self._json({"error": "body too large"}, 413)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
        except (ValueError, RecursionError) as e:
            return self._json({"error": f"invalid JSON: {type(e).__name__}"}, 400)
        if who["kind"] == "admin" and "session_id" not in who and body.get("by"):
            who["by"] = str(body["by"])[:40]          # admin CLI may label itself; agents never can (see _identify)
        try:
            self._json(api(u.path, body, {}, who))
        except Exception as e:
            self._json(self._err(e), 400)

    def log_message(self, *a):
        pass


_JOB = []


def _kill_children_on_exit():
    """Windows: put the daemon in a kill-on-close Job Object so its agent processes die with it (crash, taskkill, closed console)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k.GetCurrentProcess.restype = wintypes.HANDLE

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

        class Ext(ctypes.Structure):
            _fields_ = [("Basic", Basic), ("Io", IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        job = k.CreateJobObjectW(None, None)
        info = Ext()
        info.Basic.LimitFlags = 0x2000                      # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if job and k.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)) \
                and k.AssignProcessToJobObject(job, k.GetCurrentProcess()):
            _JOB.append(job)                                # keep the handle alive for the daemon's lifetime
    except Exception:
        pass


class Server(ThreadingHTTPServer):
    allow_reuse_address = False       # on Windows SO_REUSEADDR would let a second daemon share the port
    daemon_threads = True


def main():
    _kill_children_on_exit()
    try:
        srv = Server(("127.0.0.1", PORT), H)
    except OSError as e:
        sys.exit(f"cannot bind 127.0.0.1:{PORT} ({e}). Is Switchyard already running? Set ORCH_PORT to use another port.")
    providers.refresh_all_async()      # warm the model lists in the background
    print(f"switchyard on http://127.0.0.1:{PORT}  usable providers={ORCH.available()}"
          f"{'  (agents die with the daemon)' if _JOB else ''}", flush=True)
    try:
        srv.serve_forever()
    finally:
        ORCH.shutdown()


if __name__ == "__main__":
    main()
