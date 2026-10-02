"""OpenCode adapter: one `opencode serve` per agent, driven over HTTP + SSE."""
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request

try:
    from ..base import Adapter, kill_tree
except ImportError:  # run as loose module
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from base import Adapter, kill_tree


def _find_opencode():
    appdata = os.environ.get("APPDATA", "")
    exe = os.path.join(appdata, "npm", "node_modules", "opencode-ai", "bin", "opencode.exe")
    if os.path.exists(exe):
        return exe
    w = shutil.which("opencode")
    if w and w.lower().endswith(".exe"):
        return w
    if w:
        cand = os.path.join(os.path.dirname(w), "node_modules", "opencode-ai", "bin", "opencode.exe")
        if os.path.exists(cand):
            return cand
    return w or "opencode"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class OpenCodeAdapter(Adapter):
    name = "opencode"
    # A mid-turn prompt_async is queued natively and consumed at the next step
    # boundary (after the running tool call finishes); it does not preempt a tool.
    capabilities = {"steer": False, "interrupt": True, "usage": True, "native_queue": True}

    def __init__(self, agent_id, cwd, model, emit, opts=None):
        super().__init__(agent_id, cwd, model, emit, opts)
        self.proc = None
        self.base = None
        self._dead = False
        self._dead_emitted = False
        self._busy = False
        self._text = []
        self._text_parts = set()
        self._user_msgs = set()
        self._tools_done = set()
        self._tools_started = set()
        self._lock = threading.Lock()
        self._cancelled = False
        self._error = None
        self._ready = threading.Event()
        self._log = None

    # ---- http helpers
    def _req(self, method, path, body=None, timeout=30):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + path, data=data, method=method,
                                   headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=timeout) as f:
            raw = f.read().decode() or "null"
        try:
            return json.loads(raw)
        except ValueError:
            return raw

    @property
    def alive(self):
        return (not self._dead) and self.proc is not None and self.proc.poll() is None

    # ---- lifecycle
    def start(self):
        port = _free_port()
        self.base = "http://127.0.0.1:%d" % port
        env = dict(os.environ)
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps({"permission": "allow"})
        env.pop("OPENCODE_SERVER_PASSWORD", None)
        cmd = [_find_opencode(), "serve", "--port", str(port), "--hostname", "127.0.0.1"]
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        logdir = os.environ.get("TEMP", ".")
        self._log = open(os.path.join(logdir, "oc_%s_%d.log" % (self.agent_id, port)), "wb")
        self.proc = subprocess.Popen(cmd, cwd=self.cwd, env=env, stdout=self._log,
                                     stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     creationflags=flags)
        threading.Thread(target=self._bootstrap, daemon=True).start()

    def _bootstrap(self):
        try:
            deadline = time.time() + 60
            while time.time() < deadline:
                if self._dead:
                    return
                if self.proc.poll() is not None:
                    raise RuntimeError("opencode serve exited (%s)" % self.proc.returncode)
                try:
                    self._req("GET", "/global/health", timeout=2)
                    break
                except Exception:
                    time.sleep(0.3)
            else:
                raise RuntimeError("opencode serve did not become healthy")
            threading.Thread(target=self._reader, daemon=True).start()
            time.sleep(0.3)
            s = self._req("POST", "/session", {})
            self.session_id = s["id"]
            self.emit({"type": "session", "id": self.session_id})
            self._ready.set()
        except Exception as e:
            self.emit({"type": "error", "message": "opencode start failed: %s" % e})
            self._mark_dead()

    def _mark_dead(self):
        self._dead = True
        self._ready.set()
        if not self._dead_emitted:
            self._dead_emitted = True
            self.emit({"type": "status", "state": "dead"})

    def kill(self):
        self._dead = True
        try:
            kill_tree(self.proc)
        except Exception:
            pass
        try:
            if self._log:
                self._log.close()
        except Exception:
            pass
        self._mark_dead()

    # ---- commands
    def send(self, text, mode="queue"):
        if mode == "interrupt":
            self.interrupt()
        if not self._ready.wait(90) or self._dead:
            self.emit({"type": "error", "message": "opencode not ready"})
            return
        body = {"parts": [{"type": "text", "text": text}]}
        if self.model and "/" in self.model:
            p, m = self.model.split("/", 1)
            body["model"] = {"providerID": p, "modelID": m}
        try:
            self._req("POST", "/session/%s/prompt_async" % self.session_id, body)
        except Exception as e:
            self.emit({"type": "error", "message": "send failed: %s" % e})

    def interrupt(self):
        if not self.session_id or self._dead:
            return
        try:
            with self._lock:
                if self._busy:
                    self._cancelled = True
            self._req("POST", "/session/%s/abort" % self.session_id, {})
        except Exception as e:
            self.emit({"type": "error", "message": "abort failed: %s" % e})

    # ---- SSE reader (reconnects; never dies silently)
    def _reader(self):
        fails = 0
        while not self._dead:
            try:
                resp = urllib.request.urlopen(self.base + "/event", timeout=120)
                fails = 0
                for line in resp:
                    if self._dead:
                        return
                    line = line.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    try:
                        self._handle(json.loads(line[5:]))
                    except Exception as e:
                        self.emit({"type": "error", "message": "event handling: %r" % e})
            except Exception as e:
                if self._dead:
                    return
                fails += 1
                if self.proc.poll() is not None or fails > 20:
                    self.emit({"type": "error", "message": "opencode event stream lost: %s" % e})
                    self._mark_dead()
                    return
                time.sleep(0.5)

    def _set_busy(self):
        with self._lock:
            if self._busy:
                return
            self._busy = True
            self._text = []
        self.emit({"type": "status", "state": "busy"})

    def _handle(self, ev):
        t = ev.get("type")
        p = ev.get("properties", {})
        sid = p.get("sessionID")
        if sid is not None and sid != self.session_id:
            return
        if t == "session.status":
            st = (p.get("status") or {}).get("type")
            if st == "busy":
                self._set_busy()
            elif st == "idle":
                self._finish()
        elif t == "session.idle":
            self._finish()
        elif t == "message.updated":
            info = p.get("info", {})
            if info.get("role") == "user":
                self._user_msgs.add(info.get("id"))
        elif t == "message.part.updated":
            self._part(p.get("part", {}))
        elif t == "message.part.delta":
            if p.get("field") == "text" and p.get("partID") in self._text_parts:
                d = p.get("delta", "")
                self._set_busy()
                self._text.append(d)
                self.emit({"type": "text", "text": d})
        elif t in ("permission.updated", "permission.asked"):
            pid = p.get("id")
            self.emit({"type": "permission", "id": pid,
                       "tool": p.get("type") or p.get("permission") or p.get("title", ""),
                       "input": p.get("metadata") or p.get("patterns") or p.get("title")})
            try:
                self._req("POST", "/session/%s/permissions/%s" % (self.session_id, pid),
                          {"response": "always"})
            except Exception:
                try:
                    self._req("POST", "/permission/%s/reply" % pid, {"reply": "always"})
                except Exception as e:
                    self.emit({"type": "error", "message": "permission reply failed: %s" % e})
        elif t == "session.error":
            err = p.get("error") or {}
            if err.get("name") == "MessageAbortedError":
                return
            msg = (err.get("data") or {}).get("message") or err.get("name") or str(err)
            self.emit({"type": "error", "message": msg})
            self._error = msg

    def _part(self, part):
        ty = part.get("type")
        if ty == "text":
            if part.get("messageID") in self._user_msgs:
                return
            self._text_parts.add(part.get("id"))
        elif ty == "tool":
            cid = part.get("callID") or part.get("id")
            st = part.get("state", {})
            s = st.get("status")
            inp = st.get("input") or {}
            shown = inp.get("command", inp) if isinstance(inp, dict) else inp
            if s == "running" and cid not in self._tools_started:
                self._tools_started.add(cid)
                self._set_busy()
                self.emit({"type": "tool_start", "tool": part.get("tool"), "input": shown, "id": cid})
            elif s in ("completed", "error") and cid not in self._tools_done:
                self._tools_done.add(cid)
                if cid not in self._tools_started:
                    self._tools_started.add(cid)
                    self.emit({"type": "tool_start", "tool": part.get("tool"), "input": shown, "id": cid})
                out = st.get("output") if s == "completed" else st.get("error")
                self.emit({"type": "tool_end", "tool": part.get("tool"), "id": cid,
                           "ok": s == "completed", "output": str(out or "")[:2000]})
        elif ty == "step-finish":
            tk = part.get("tokens") or {}
            c = tk.get("cache") or {}
            self.emit({"type": "usage", "input": tk.get("input", 0), "output": tk.get("output", 0),
                       "cache_read": c.get("read", 0), "cache_write": c.get("write", 0)})

    def _finish(self):
        with self._lock:
            if not self._busy:
                return
            self._busy = False
            cancelled, self._cancelled = self._cancelled, False
            text = "".join(self._text)
            self._text = []
        err, self._error = self._error, None
        stop = "cancelled" if cancelled else ("error" if err else "end_turn")
        self.emit({"type": "result", "text": text, "ok": stop == "end_turn", "stop": stop})
        self.emit({"type": "status", "state": "idle"})
