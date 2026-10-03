"""Claude Code adapter: ONE persistent `claude -p` stream-json process per agent.
Mid-turn user messages are queued by Claude Code and seen at the next tool boundary (soft steer). Interrupt uses the
stream-json control_request; if the process does not answer it within INTERRUPT_GRACE seconds we fall back to
kill + `--resume <session>` (status restarted=True). Wire formats marked VERIFY are checked by tests/claude/test_claude.py."""
import json
import os
import shutil
import subprocess
import threading
import time
import uuid

from orch.base import Adapter, kill_tree

INTERRUPT_GRACE = 8


class ClaudeAdapter(Adapter):
    name = "claude"
    capabilities = {"steer": True, "interrupt": True, "usage": True, "native_queue": False}

    def __init__(self, agent_id, cwd, model, emit, opts=None):
        super().__init__(agent_id, cwd, model or "claude-haiku-4-5-20251001", emit, opts)
        self.proc = None
        self._lock = threading.Lock()
        self._got_text = False
        self._interrupting = False
        self._turn_open = False

    @property
    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def _child_env(self):
        env = {k: v for k, v in os.environ.items() if (not k.startswith("CLAUDE_CODE_") or k == "CLAUDE_CODE_GIT_BASH_PATH") and k != "CLAUDECODE"}
        env.update({str(k): str(v) for k, v in (self.opts.get("env") or {}).items()})
        return env

    def start(self):
        exe = self.opts.get("command") or shutil.which("claude") or "claude"
        cmd = [exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
               "--include-partial-messages", "--permission-mode", "bypassPermissions", "--model", self.model]
        if self.opts.get("effort"):                   # fixed at launch: a different effort means a new process (it is kept across restarts/--resume)
            cmd += ["--effort", self.opts["effort"]]
        if self.session_id:
            cmd += ["--resume", self.session_id]
        env = self._child_env()
        self.proc = subprocess.Popen(cmd, cwd=self.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1, env=env)
        threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()

    def _write(self, obj):
        with self._lock:
            self.proc.stdin.write(json.dumps(obj) + "\n")
            self.proc.stdin.flush()

    def send(self, text, mode="queue"):
        if not self.alive:
            self.start()
        if mode != "steer" or not self._turn_open:
            self._got_text = False
            self._turn_open = True
            self.emit({"type": "status", "state": "busy"})
        self._write({"type": "user", "message": {"role": "user", "content": text}, "parent_tool_use_id": None})

    def interrupt(self):
        if not self.alive:
            return
        self._interrupting = True
        self._write({"type": "control_request", "request_id": "int-" + uuid.uuid4().hex[:8],
                     "request": {"subtype": "interrupt"}})
        proc = self.proc
        threading.Thread(target=self._interrupt_watchdog, args=(proc,), daemon=True).start()

    def _interrupt_watchdog(self, proc):
        t0 = time.time()
        while time.time() - t0 < INTERRUPT_GRACE:
            if not self._interrupting or proc is not self.proc:
                return                       # a result arrived: native interrupt worked
            time.sleep(0.2)
        if self._interrupting and proc is self.proc and self.session_id:
            self._interrupting = False
            self.proc = None
            kill_tree(proc)
            self.emit({"type": "result", "text": "", "ok": False, "stop": "cancelled"})
            self.start()
            self.emit({"type": "status", "state": "idle", "restarted": True})

    def kill(self):
        old, self.proc = self.proc, None
        kill_tree(old)
        self.emit({"type": "status", "state": "dead"})

    def _reader(self, proc):
        try:
            for line in proc.stdout:
                if proc is not self.proc:
                    return
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                try:
                    self._map(ev)
                except Exception as e:
                    self.emit({"type": "error", "message": f"claude map: {e!r}"})
        except Exception as e:
            self.emit({"type": "error", "message": f"claude reader: {e!r}"})
        if proc is self.proc:
            self.emit({"type": "status", "state": "dead"})

    def _map(self, ev):
        t = ev.get("type")
        if t == "system" and ev.get("subtype") == "init":
            sid = ev.get("session_id")
            if sid and sid != self.session_id:
                self.session_id = sid
                self.emit({"type": "session", "id": sid})
        elif t == "stream_event":
            d = (ev.get("event") or {}).get("delta") or {}
            if d.get("type") == "text_delta" and d.get("text"):
                self._got_text = True
                self.emit({"type": "text", "text": d["text"]})
        elif t == "assistant":
            for b in (ev.get("message") or {}).get("content") or []:
                if b.get("type") == "tool_use":
                    inp = b.get("input") or {}
                    self.emit({"type": "tool_start", "tool": b.get("name"), "id": b.get("id"),
                               "input": inp.get("command") or inp.get("file_path") or inp})
                elif b.get("type") == "text" and not self._got_text and b.get("text"):
                    self.emit({"type": "text", "text": b["text"]})
        elif t == "user":
            c = (ev.get("message") or {}).get("content")
            for b in c if isinstance(c, list) else []:
                if b.get("type") == "tool_result":
                    out = b.get("content")
                    out = out if isinstance(out, str) else json.dumps(out)
                    self.emit({"type": "tool_end", "tool": "", "id": b.get("tool_use_id"),
                               "ok": not b.get("is_error"), "output": (out or "")[:2000]})
        elif t == "control_response":
            pass                                  # VERIFY: ack of our interrupt; the turn then ends with a result event
        elif t == "result":
            u = ev.get("usage") or {}
            self.emit({"type": "usage", "input": u.get("input_tokens", 0), "output": u.get("output_tokens", 0),
                       "cache_read": u.get("cache_read_input_tokens", 0),
                       "cache_write": u.get("cache_creation_input_tokens", 0)})
            was_int, self._interrupting, self._turn_open = self._interrupting, False, False
            ok = ev.get("subtype") == "success" and not ev.get("is_error")
            stop = "cancelled" if was_int and not ok or (was_int and "interrupt" in str(ev.get("result", "")).lower()) \
                else ("end_turn" if ok else "error")
            self.emit({"type": "result", "text": ev.get("result") or "", "ok": ok, "stop": stop})
            self.emit({"type": "status", "state": "idle"})
