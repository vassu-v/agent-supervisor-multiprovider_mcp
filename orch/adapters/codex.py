"""Codex adapter: persistent `codex app-server --listen stdio://` speaking JSON-RPC 2.0 JSONL."""
import json
import os
import shutil
import subprocess
import threading

from orch.base import Adapter, kill_tree

REQ_TIMEOUT = 30


def _find_codex():
    p = shutil.which("codex")
    if p:
        return p
    la = os.environ.get("LOCALAPPDATA", "")
    c = os.path.join(la, "Programs", "OpenAI", "Codex", "bin", "codex.exe")
    return c if os.path.exists(c) else "codex"


class CodexAdapter(Adapter):
    name = "codex"
    capabilities = {"steer": True, "interrupt": True, "usage": True, "native_queue": False}

    def __init__(self, agent_id, cwd, model, emit, opts=None):
        super().__init__(agent_id, cwd, model, emit, opts)
        self.proc = None
        self._wlock = threading.Lock()
        self._lock = threading.RLock()
        self._next_id = 1
        self._pending = {}      # id -> [Event, response-dict]
        self._turn_id = None
        self._busy = False
        self._idle = threading.Event()
        self._idle.set()
        self._text = []
        self._queue = []
        self._dead = False
        self._killed = False

    # ---- lifecycle ------------------------------------------------
    def start(self):
        cmd = self.opts.get("command")
        if cmd is None:
            cmd = [_find_codex(), "app-server", "--listen", "stdio://"]
        elif isinstance(cmd, str):
            cmd = [cmd]
        self.proc = subprocess.Popen(
            list(cmd), cwd=self.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1)
        threading.Thread(target=self._reader, daemon=True).start()
        threading.Thread(target=self._drain_err, daemon=True).start()
        try:
            self._request("initialize", {"clientInfo": {"name": "agy-supervisor", "version": "1.0"}})
            self._notify("initialized")
            params = {"cwd": self.cwd, "approvalPolicy": "never", "sandbox": "danger-full-access"}
            if self.model:
                params["model"] = self.model
            r = self._request("thread/start", params)
            self.session_id = r["thread"]["id"]
            self.emit({"type": "session", "id": self.session_id})
            self.emit({"type": "status", "state": "idle"})
        except Exception as e:
            self.emit({"type": "error", "message": "codex start failed: %s" % e})
            self.kill()

    @property
    def alive(self):
        return bool(self.proc) and self.proc.poll() is None and not self._dead

    def kill(self):
        with self._lock:
            already = self._killed
            self._killed = True
        if already:
            return
        kill_tree(self.proc)
        self._mark_dead()

    def _mark_dead(self):
        with self._lock:
            if self._dead:
                return
            self._dead = True
            self._busy = False
        for ev, _ in list(self._pending.values()):
            ev.set()
        self._idle.set()
        self.emit({"type": "status", "state": "dead"})

    # ---- JSON-RPC plumbing ---------------------------------------
    def _write(self, obj):
        with self._wlock:
            self.proc.stdin.write(json.dumps(obj) + "\n")
            self.proc.stdin.flush()

    def _notify(self, method, params=None):
        m = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            m["params"] = params
        self._write(m)

    def _request(self, method, params, timeout=REQ_TIMEOUT):
        with self._lock:
            rid = self._next_id
            self._next_id += 1
            slot = [threading.Event(), None]
            self._pending[rid] = slot
        self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        if not slot[0].wait(timeout) or slot[1] is None:
            self._pending.pop(rid, None)
            raise RuntimeError("%s: no response (timeout or process died)" % method)
        self._pending.pop(rid, None)
        resp = slot[1]
        if "error" in resp:
            raise RuntimeError("%s: %s" % (method, resp["error"]))
        return resp.get("result") or {}

    def _drain_err(self):
        try:
            for _ in self.proc.stderr:
                pass
        except Exception:
            pass

    def _reader(self):
        try:
            for line in self.proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                try:
                    self._dispatch(msg)
                except Exception as e:  # never die on a bad message
                    self.emit({"type": "error", "message": "codex dispatch: %r" % e})
        except Exception as e:
            self.emit({"type": "error", "message": "codex reader: %r" % e})
        finally:
            self._mark_dead()

    def _dispatch(self, msg):
        if "method" in msg and "id" in msg:
            return self._server_request(msg)
        if "method" in msg:
            return self._notification(msg["method"], msg.get("params") or {})
        if "id" in msg:
            slot = self._pending.get(msg["id"])
            if slot:
                slot[1] = msg
                slot[0].set()

    # ---- server requests (approvals) -----------------------------
    def _server_request(self, msg):
        m, p, rid = msg["method"], msg.get("params") or {}, msg["id"]
        if m == "item/commandExecution/requestApproval":
            tool, inp, res = "shell", p.get("command"), {"decision": "accept"}
        elif m == "item/fileChange/requestApproval":
            tool, inp, res = "file_change", p.get("reason") or p.get("itemId"), {"decision": "accept"}
        elif m == "item/permissions/requestApproval":
            tool, inp = "permissions", p.get("permissions")
            res = {"permissions": p.get("permissions") or {}, "scope": "turn"}
        elif m in ("execCommandApproval", "applyPatchApproval"):
            tool, inp, res = m, p.get("command") or p.get("fileChanges"), {"decision": "approved"}
        else:
            self._write({"jsonrpc": "2.0", "id": rid,
                         "error": {"code": -32601, "message": "unsupported: " + m}})
            return
        self.emit({"type": "permission", "id": str(rid), "tool": tool, "input": inp})
        self._write({"jsonrpc": "2.0", "id": rid, "result": res})

    # ---- notifications -------------------------------------------
    def _notification(self, m, p):
        if m == "turn/started":
            with self._lock:
                self._turn_id = (p.get("turn") or {}).get("id") or self._turn_id
        elif m == "item/agentMessage/delta":
            d = p.get("delta") or ""
            if d:
                self._text.append(d)
                self.emit({"type": "text", "text": d})
        elif m == "item/started":
            it = p.get("item") or {}
            t = it.get("type")
            if t == "commandExecution":
                self.emit({"type": "tool_start", "tool": "shell", "input": it.get("command"), "id": it.get("id")})
            elif t == "mcpToolCall":
                self.emit({"type": "tool_start", "tool": "%s.%s" % (it.get("server"), it.get("tool")),
                           "input": it.get("arguments"), "id": it.get("id")})
            elif t == "fileChange":
                self.emit({"type": "tool_start", "tool": "file_change", "input": it.get("changes"), "id": it.get("id")})
        elif m == "item/completed":
            it = p.get("item") or {}
            t = it.get("type")
            if t == "commandExecution":
                self.emit({"type": "tool_end", "tool": "shell", "id": it.get("id"),
                           "ok": it.get("status") == "completed" and it.get("exitCode") in (0, None),
                           "output": (it.get("aggregatedOutput") or "")[:2000]})
            elif t == "mcpToolCall":
                self.emit({"type": "tool_end", "tool": "%s.%s" % (it.get("server"), it.get("tool")),
                           "id": it.get("id"), "ok": it.get("status") == "completed",
                           "output": json.dumps(it.get("result"))[:2000] if it.get("result") else ""})
            elif t == "fileChange":
                self.emit({"type": "tool_end", "tool": "file_change", "id": it.get("id"),
                           "ok": it.get("status") == "completed"})
            elif t == "agentMessage" and not self._text and it.get("text"):
                self._text.append(it["text"])  # no deltas seen: use final text
        elif m == "thread/tokenUsage/updated":
            last = (p.get("tokenUsage") or {}).get("last") or {}
            self.emit({"type": "usage", "input": last.get("inputTokens", 0),
                       "output": last.get("outputTokens", 0),
                       "cache_read": last.get("cachedInputTokens", 0), "cache_write": 0})
        elif m == "error":
            self.emit({"type": "error", "message": str(p.get("error") or p)})
        elif m == "turn/completed":
            self._turn_done(p.get("turn") or {})

    def _turn_done(self, turn):
        status = turn.get("status")
        stop = {"completed": "end_turn", "interrupted": "cancelled"}.get(status, "error")
        text = "".join(self._text)
        if status == "failed" and not text:
            text = str((turn.get("error") or {}).get("message") or "")
        self._text = []
        self.emit({"type": "result", "text": text, "ok": stop == "end_turn", "stop": stop})
        nxt = None
        with self._lock:
            self._busy = False
            self._turn_id = None
            if self._queue and not self._dead:
                nxt = self._queue.pop(0)
        self.emit({"type": "status", "state": "idle"})
        self._idle.set()
        if nxt is not None:
            # must not block the reader thread waiting on a response
            threading.Thread(target=self._start_turn, args=(nxt,), daemon=True).start()

    # ---- commands --------------------------------------------------
    @staticmethod
    def _input(text):
        return [{"type": "text", "text": text}]

    def _start_turn(self, text):
        with self._lock:
            self._busy = True
            self._idle.clear()
            self._text = []
        self.emit({"type": "status", "state": "busy"})
        try:
            r = self._request("turn/start", {"threadId": self.session_id, "input": self._input(text)})
            with self._lock:
                tid = (r.get("turn") or {}).get("id")
                if tid and self._busy:
                    self._turn_id = tid
        except Exception as e:
            with self._lock:
                self._busy = False
            self._idle.set()
            self.emit({"type": "error", "message": str(e)})
            self.emit({"type": "result", "text": "", "ok": False, "stop": "error"})
            self.emit({"type": "status", "state": "idle"})

    def send(self, text, mode="queue"):
        if not self.alive:
            self.emit({"type": "error", "message": "codex adapter not alive"})
            return
        with self._lock:
            busy, tid = self._busy, self._turn_id
        if not busy:
            return self._start_turn(text)
        if mode == "steer" and tid:
            try:
                self._request("turn/steer", {"threadId": self.session_id, "expectedTurnId": tid,
                                             "input": self._input(text)})
                return
            except Exception as e:  # turn may have just ended: fall back to queue
                self.emit({"type": "error", "message": "steer failed, queued: %s" % e})
        elif mode == "interrupt":
            self.interrupt()
            self._idle.wait(15)
            if self.alive and not self._busy:
                return self._start_turn(text)
        with self._lock:
            if self._busy:
                self._queue.append(text)
                return
        self._start_turn(text)

    def interrupt(self):
        with self._lock:
            busy = self._busy
        if not busy or not self.alive:
            return
        for _ in range(50):  # turn/start response (turn id) may not be in yet
            if self._turn_id or not self._busy:
                break
            self._idle.wait(0.1)
        tid = self._turn_id
        if tid:
            try:
                self._request("turn/interrupt", {"threadId": self.session_id, "turnId": tid})
            except Exception as e:
                self.emit({"type": "error", "message": "interrupt failed: %s" % e})
