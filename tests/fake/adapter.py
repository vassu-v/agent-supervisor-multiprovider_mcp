"""FakeAdapter: a scripted, zero-cost provider for QA. Always ready, no CLI, no model.

Script = JSON list of steps, given in the task text after the marker `--- task ---` (the daemon prepends a
preamble), or in opts["script"] (used for the first turn when the text carries none).  Steps:
  {"say": text}
  {"tool": "write_file", "path": rel, "content": text}      really writes under cwd (escape -> tool_end ok False)
  {"tool": "run_command", "input": "..."}                    tool_start/tool_end only, never executed
  {"sleep": seconds}                                         interruptible by interrupt()/kill()
  {"http": {"method","path","body"}, "as": name?}            calls ORCH_URL with ORCH_TOKEN from opts["env"];
                                                             "${name.key.sub}" in later path/body strings is replaced
  {"crash": true}                                            process "dies": error + result(error) + status dead
  {"random_walk": {"seed": n, "steps": k}}                   seeded random writes / announcements / say
opts: script, default_script (turns with no script, default [{"say":"ack"}]), env, profile ("claude"|"agy"),
      capabilities (dict override), step_delay (seconds between steps, default 0).
Every received message is appended to <cwd>/.fake_inbox.jsonl; http responses to <cwd>/.fake_http.jsonl.
"""
import collections
import json
import os
import random
import re
import threading
import time
import urllib.error
import urllib.request

from orch.base import Adapter

MARKER = "--- task ---"
PROFILES = {
    "claude": {"steer": True, "interrupt": True, "usage": False, "native_queue": False},
    "agy": {"steer": False, "interrupt": "restart", "usage": False, "native_queue": True},
}


def parse_script(text):
    """Return a list of steps found in `text` (after the marker), or None."""
    if not isinstance(text, str):
        return None
    has_marker = MARKER in text
    body = text.split(MARKER, 1)[1].strip() if has_marker else text.strip()
    if not has_marker and not body.startswith(("[", "{")):
        return None
    dec = json.JSONDecoder()
    for i, ch in enumerate(body):
        if ch in "[{":
            try:
                obj, _ = dec.raw_decode(body[i:])
            except ValueError:
                if has_marker:
                    continue
                return None
            if isinstance(obj, dict):
                obj = obj.get("script")
            if isinstance(obj, list):
                return obj
            if not has_marker:
                return None
    return None


_JSONL_LOCK = threading.Lock()   # module level: every adapter in this process shares it, so appends to one file never tear

class FakeAdapter(Adapter):
    name = "fake"
    capabilities = dict(PROFILES["claude"])

    def __init__(self, agent_id, cwd, model, emit, opts=None):
        super().__init__(agent_id, cwd, model or "fake-1", emit, opts)
        caps = dict(PROFILES.get(self.opts.get("profile", "claude"), PROFILES["claude"]))
        caps.update(self.opts.get("capabilities") or {})
        self.capabilities = caps
        self.env = dict(self.opts.get("env") or {})
        self._cv = threading.Condition()
        self._q = collections.deque()          # (text, mode)
        self._alive = False
        self._killed = False
        self._dead_emitted = False
        self._cancel = None                    # Event of the running turn, or None
        self._steer = []                       # steer texts for the running turn
        self._worker = None
        self._turn_no = 0
        self._n = 0
        self._inbox_lock = _JSONL_LOCK
        self._vars = {}

    @property
    def alive(self):
        return self._alive

    # --- lifecycle ------------------------------------------------------
    def start(self):
        if self._alive:
            return
        self._alive = True
        self._killed = False
        self._dead_emitted = False
        if self.session_id is None:
            self.session_id = f"fake-{self.agent_id}"
        self.emit({"type": "session", "id": self.session_id})
        self.emit({"type": "status", "state": "idle"})
        self._worker = threading.Thread(target=self._loop, daemon=True, name=f"fake-{self.agent_id}")
        self._worker.start()

    def send(self, text, mode="queue"):
        if not self._alive:
            self.start()
        self._record(text, mode)
        with self._cv:
            running = self._cancel is not None
            if mode == "steer" and running and self.capabilities.get("steer") is True:
                self._steer.append(text)
            elif mode == "interrupt":
                if running:
                    self._cancel.set()
                self._q.appendleft((text, mode))
            else:
                self._q.append((text, mode))
            self._cv.notify_all()

    def interrupt(self):
        with self._cv:
            if self._cancel is not None:
                self._cancel.set()

    def kill(self):
        with self._cv:
            self._killed = True
            self._alive = False
            self._q.clear()
            if self._cancel is not None:
                self._cancel.set()
            self._cv.notify_all()
        w = self._worker
        if w is not None and w is not threading.current_thread():
            w.join(5)
        self._emit_dead()

    def _emit_dead(self):
        with self._cv:
            if self._dead_emitted:
                return
            self._dead_emitted = True
        self.emit({"type": "status", "state": "dead"})

    # --- inbox ----------------------------------------------------------
    def _jsonl(self, name, rec):
        try:
            os.makedirs(self.cwd, exist_ok=True)
            with self._inbox_lock, open(os.path.join(self.cwd, name), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _record(self, text, mode):
        self._jsonl(".fake_inbox.jsonl", {"ts": time.time(), "agent": self.agent_id, "mode": mode, "text": text})

    # --- worker ---------------------------------------------------------
    def _loop(self):
        while True:
            with self._cv:
                while not self._q and not self._killed:
                    self._cv.wait()
                if self._killed:
                    return
                text, mode = self._q.popleft()
                cancel = self._cancel = threading.Event()
                self._steer = []
            try:
                self._turn(text, mode, cancel)
            except Exception as e:                               # a bug in a script must not hang the agent
                self.emit({"type": "error", "message": f"fake turn failed: {e!r}"})
                self.emit({"type": "result", "text": "", "ok": False, "stop": "error"})
                self.emit({"type": "status", "state": "idle"})
            finally:
                with self._cv:
                    self._cancel = None

    def _turn(self, text, mode, cancel):
        n, self._turn_no = self._turn_no, self._turn_no + 1
        steps = parse_script(text)
        if steps is None:
            steps = self.opts.get("script") if n == 0 else self.opts.get("default_script")
            if isinstance(steps, str):
                steps = parse_script(steps) or []
            if steps is None:
                steps = [{"say": "ack"}]
        steps = list(steps)
        self.emit({"type": "status", "state": "busy"})
        said = []
        crashed = False
        i = 0
        delay = float(self.opts.get("step_delay") or 0)
        while i < len(steps) and not cancel.is_set():
            if self._steer:                                       # steer: injected into the in-flight turn
                for s in self._steer:
                    self.emit({"type": "text", "text": f"[steered] {s[:200]}"})
                    extra = parse_script(s)
                    if extra:
                        steps[i:i] = extra
                self._steer = []
            step = steps[i]
            i += 1
            if not isinstance(step, dict):
                continue
            if self._step(step, cancel, said) == "crash":
                crashed = True
                break
            if delay:
                cancel.wait(delay)
        if crashed:
            self.emit({"type": "error", "message": "fake agent crashed"})
            self.emit({"type": "result", "text": "".join(said), "ok": False, "stop": "error"})
            with self._cv:
                self._alive = False
                self._killed = True
                self._q.clear()
            self._emit_dead()
            return
        if self.capabilities.get("usage"):
            self.emit({"type": "usage", "input": 1, "output": 1, "cache_read": 0})
        if self._killed:
            self.emit({"type": "result", "text": "".join(said), "ok": False, "stop": "cancelled"})
        elif cancel.is_set():
            self.emit({"type": "result", "text": "".join(said), "ok": False, "stop": "cancelled"})
            ev = {"type": "status", "state": "idle"}
            if self.capabilities.get("interrupt") is not True:
                ev["restarted"] = True
            self.emit(ev)
        else:
            self.emit({"type": "result", "text": "".join(said), "ok": True, "stop": "end_turn"})
            self.emit({"type": "status", "state": "idle"})

    # --- steps ----------------------------------------------------------
    def _step(self, step, cancel, said):
        if "say" in step:
            t = str(step["say"])
            said.append(t)
            self.emit({"type": "text", "text": t})
        elif step.get("crash"):
            return "crash"
        elif "sleep" in step:
            cancel.wait(float(step["sleep"]))
        elif "http" in step:
            self._http(step)
        elif "random_walk" in step:
            self._random_walk(step["random_walk"] or {}, cancel, said)
        elif step.get("tool") == "write_file":
            self._write_file(step.get("path", ""), str(step.get("content", "")))
        elif step.get("tool") == "run_command":
            cmd = step.get("input", step.get("command", ""))
            tid = self._tid()
            self.emit({"type": "tool_start", "tool": "run_command", "id": tid, "input": cmd})
            self.emit({"type": "tool_end", "tool": "run_command", "id": tid, "ok": True,
                       "output": str(step.get("output", ""))[:2000]})
        else:
            self.emit({"type": "error", "message": f"fake: unknown step {step!r}"[:300]})
        return None

    def _tid(self):
        self._n += 1
        return f"{self.agent_id}-t{self._n}"

    def _write_file(self, rel, content):
        tid = self._tid()
        self.emit({"type": "tool_start", "tool": "write_file", "id": tid, "input": {"path": rel, "content": content}})
        base = os.path.abspath(self.cwd)
        full = os.path.abspath(os.path.join(base, rel))
        ok, out = True, f"wrote {len(content)} bytes"
        try:
            escapes = (not rel) or os.path.commonpath([base, full]) != base
        except ValueError:                                        # different drives
            escapes = True
        if escapes:
            ok, out = False, "path escapes cwd"
        else:
            try:
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "w", encoding="utf-8", newline="") as f:
                    f.write(content)
            except OSError as e:
                ok, out = False, repr(e)
        self.emit({"type": "tool_end", "tool": "write_file", "id": tid, "ok": ok, "output": out})

    def _subst(self, v):
        if isinstance(v, str):
            def rep(m):
                cur = self._vars
                for k in m.group(1).split("."):
                    cur = cur.get(k) if isinstance(cur, dict) else None
                return "" if cur is None else str(cur)
            return re.sub(r"\$\{([\w.]+)\}", rep, v)
        if isinstance(v, dict):
            return {k: self._subst(x) for k, x in v.items()}
        if isinstance(v, list):
            return [self._subst(x) for x in v]
        return v

    def _http(self, step):
        spec = self._subst(step["http"])
        method = str(spec.get("method", "GET")).upper()
        path = spec.get("path", "/")
        body = spec.get("body")
        tid = self._tid()
        self.emit({"type": "tool_start", "tool": "http", "id": tid, "input": f"{method} {path}"})
        base = self.env.get("ORCH_URL", "").rstrip("/")
        token = self.env.get("ORCH_TOKEN", "")
        status, data = 0, None
        if not base:
            data = {"error": "no ORCH_URL in opts['env']"}
        else:
            data_b = json.dumps(body).encode() if body is not None and method != "GET" else None
            req = urllib.request.Request(base + path, data=data_b, method=method)
            req.add_header("Authorization", f"Bearer {token}")
            if data_b is not None:
                req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    status, raw = r.status, r.read()
            except urllib.error.HTTPError as e:
                status, raw = e.code, e.read()
            except Exception as e:
                status, raw = 0, repr(e).encode()
            try:
                data = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                data = raw.decode("utf-8", "replace")
        if step.get("as"):
            self._vars[step["as"]] = {"status": status, **(data if isinstance(data, dict) else {"body": data})}
        self._jsonl(".fake_http.jsonl", {"ts": time.time(), "method": method, "path": path, "body": body,
                                          "status": status, "response": data})
        self.emit({"type": "tool_end", "tool": "http", "id": tid, "ok": 0 < status < 400,
                   "output": f"{status} {json.dumps(data)}"[:2000]})

    def _random_walk(self, spec, cancel, said):
        rng = random.Random(spec.get("seed", 0))
        for k in range(int(spec.get("steps", 5))):
            if cancel.is_set():
                return
            pick = rng.choice(["write", "write", "announce", "say"])
            if pick == "write":
                self._write_file(f"rw/f{rng.randint(0, 4)}.txt", f"step {k} value {rng.randint(0, 10**6)}\n")
            elif pick == "announce" and self.env.get("ORCH_URL"):
                self._http({"http": {"method": "POST", "path": "/api/announce",
                                     "body": {"text": f"rw {self.agent_id} step {k} n{rng.randint(0, 10**6)}",
                                              "kind": rng.choice(["info", "changed", "done"])}}})
            else:
                t = f"rw say {k}"
                said.append(t)
                self.emit({"type": "text", "text": t})
