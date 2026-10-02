"""agy (Antigravity CLI) adapter.
Verified facts (see CLAUDE.md decisions): stdin is consumed turn-by-turn (a 2nd message queues natively, it does NOT
steer); no cancel/interrupt control event exists; killing agy kills its child shell commands; resume with
--conversation <id> keeps context. So: steer == queue, interrupt == kill + resume (emits status restarted=True)."""
import json
import os
import shutil
import subprocess
import threading

from orch.base import Adapter, kill_tree

AGY = (os.environ.get("AGY_BIN") or shutil.which("agy")
       or os.path.join(os.environ.get("LOCALAPPDATA", ""), "agy", "bin", "agy.exe"))


class AgyAdapter(Adapter):
    name = "agy"
    capabilities = {"steer": False, "interrupt": "restart", "usage": True, "native_queue": True}

    def __init__(self, agent_id, cwd, model, emit, opts=None):
        super().__init__(agent_id, cwd, model or "gemini-3.8-flash-medium", emit, opts)
        self.proc = None
        self.sandbox = bool(self.opts.get("sandbox"))
        self.agent = self.opts.get("agent")
        self._tool_ids = {}
        self._text = []

    @property
    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        cmd = [AGY, "--input-format", "stream-json", "--output-format", "stream-json",
               "--model", self.model, "--dangerously-skip-permissions", "--print="]
        if self.agent:
            cmd += ["--agent", self.agent]
        if self.sandbox:
            cmd += ["--sandbox"]
        if self.session_id:
            cmd += ["--conversation", self.session_id]
        self.proc = subprocess.Popen(cmd, cwd=self.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, encoding="utf-8", bufsize=1)
        threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()

    def _write(self, text):
        self._text = []
        self.emit({"type": "status", "state": "busy"})
        self.proc.stdin.write(json.dumps({"event": "user", "message": {"role": "user", "content": text}}) + "\n")
        self.proc.stdin.flush()

    def send(self, text, mode="queue"):
        # mode "interrupt": the core already called interrupt() (kill+resume) before delivering; don't repeat it
        if not self.alive:
            self.start()
        self._write(text)               # agy queues stdin turn-by-turn itself

    def interrupt(self, restart_only=False):
        """No native cancel: kill the tree and resume the same conversation."""
        old = self.proc
        self.proc = None                # reader of the old proc becomes stale
        kill_tree(old)
        self.emit({"type": "result", "text": "", "ok": False, "stop": "cancelled"})
        if self.session_id:
            self.start()
            self.emit({"type": "status", "state": "idle", "restarted": True})
        else:
            self.emit({"type": "status", "state": "dead"})

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
                self._map(ev)
        except Exception as e:
            self.emit({"type": "error", "message": f"agy reader: {e!r}"})
        if proc is self.proc:
            self.emit({"type": "status", "state": "dead"})

    def _map(self, ev):
        kind = ev.get("event")
        if kind == "init":
            cid = ev.get("conversation_id")
            if cid and cid != self.session_id:
                self.session_id = cid
                self.emit({"type": "session", "id": cid})
            self.emit({"type": "status", "state": "idle"})
        elif kind == "step_update":
            u = ev["step_update"]
            st = u.get("step_type")
            if st == "agent_response" and u.get("text_delta"):
                self._text.append(u["text_delta"])
                self.emit({"type": "text", "text": u["text_delta"]})
            elif st == "tool":
                info = u.get("tool_info") or {}
                tid = f"{u.get('step_index')}"
                if u.get("state") == "ACTIVE" and tid not in self._tool_ids:
                    self._tool_ids[tid] = True
                    params = info.get("parameters") or {}
                    self.emit({"type": "tool_start", "tool": u.get("tool_name"), "id": tid,
                               "input": params.get("CommandLine") or params})
                elif u.get("state") == "DONE":
                    self.emit({"type": "tool_end", "tool": u.get("tool_name"), "id": tid, "ok": True})
        elif kind == "result":
            r = ev["result"]
            cid = r.get("conversation_id")
            if cid and cid != self.session_id:
                self.session_id = cid
                self.emit({"type": "session", "id": cid})
            u = r.get("usage") or {}
            self.emit({"type": "usage", "input": u.get("input_tokens", 0), "output": u.get("output_tokens", 0),
                       "cache_read": u.get("cache_read_tokens", 0)})
            ok = r.get("status") == "SUCCESS"
            self.emit({"type": "result", "text": r.get("response") or "".join(self._text), "ok": ok,
                       "stop": "end_turn" if ok else "error"})
            self.emit({"type": "status", "state": "idle"})
