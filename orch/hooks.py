"""Completion hooks: "when agent X finishes, run this command".

Semantics (events the core reports -> hook `on` values that match):
  idle    the agent finished a turn successfully and is idle with nothing queued   (event "idle")
  done    terminal status (stopped by someone, or died) OR an error-free idle      (events "idle", "stopped", "dead")
  error   a turn ended in error, or the agent died unexpectedly                    (events "error", "dead")
  any     all of the above
A crashing agent can report both a failed turn ("error") and then "dead", so a repeat hook may fire twice for one crash.
One-shot by default: a hook is removed when it fires, unless repeat=True.

SECURITY: hooks execute commands. Registration/listing are admin-only (the endpoints are not in identity.AGENT_ALLOWED).
The command runs via shlex.split with NO shell, cwd = the agent's cwd, 60 s timeout, in a daemon thread. Agent output is
only ever passed through environment variables (SWITCHYARD_*), never interpolated into the command line.
Hooks live in memory only: a daemon restart clears them.
"""
import os
import shlex
import subprocess
import sys
import threading
import time
import uuid

from orch.base import kill_tree

ON = ("done", "idle", "error", "any")
MATCH = {"idle": {"idle"}, "done": {"idle", "stopped", "dead"}, "error": {"error", "dead"},
         "any": {"idle", "stopped", "dead", "error"}}
MAX_HOOKS = 50
MAX_RUN = 2000
TIMEOUT = 60
MAX_OUT = 4096
MAX_RESULT = 8192


def split_command(run):
    """shlex.split without a shell. On Windows posix=False keeps backslashes in paths; matching outer quotes are stripped."""
    if sys.platform == "win32":
        parts = shlex.split(run, posix=False)
        parts = [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p for p in parts]
    else:
        parts = shlex.split(run)
    if not parts:
        raise ValueError("run must contain a command")
    return parts


def _drain(pipe, buf):
    """Read a child pipe to EOF, keeping at most MAX_OUT bytes and discarding the rest (memory stays bounded while it runs)."""
    try:
        while True:
            chunk = pipe.read(8192)
            if not chunk:
                break
            if len(buf) < MAX_OUT:
                buf += chunk[:MAX_OUT - len(buf)]
    except (OSError, ValueError):
        pass
    finally:
        try:
            pipe.close()
        except OSError:
            pass


def _clean(s):
    return str(s).replace("\x00", "")


class Hooks:
    def __init__(self, orch):
        self.o = orch
        self.lock = threading.Lock()
        self.items = {}                           # hook id -> dict (insertion ordered)

    def add(self, aid, on, run, repeat=False):
        if aid not in self.o.agents:
            raise ValueError(f"unknown agent {aid!r}")
        if on not in ON:
            raise ValueError(f"on must be one of {list(ON)}")
        if not isinstance(run, str) or not run.strip():
            raise ValueError("run must be a non-empty string")
        if len(run) > MAX_RUN:
            raise ValueError(f"run is too long (max {MAX_RUN} chars)")
        split_command(run)                        # reject unparsable commands up front
        with self.lock:
            if len(self.items) >= MAX_HOOKS:
                raise ValueError(f"too many hooks (max {MAX_HOOKS})")
            hid = "h" + uuid.uuid4().hex[:8]
            self.items[hid] = {"hook": hid, "id": aid, "on": on, "run": run, "repeat": bool(repeat),
                               "fired": 0, "last_fired": None, "last_exit": None}
        self.o._audit("hook_add", hook=hid, target=aid, on=on, run=run[:200], repeat=bool(repeat))
        return {"hook": hid, "id": aid, "on": on, "run": run, "repeat": bool(repeat)}

    def list(self):
        with self.lock:
            return [dict(h) for h in self.items.values()]

    def remove(self, hid):
        with self.lock:
            h = self.items.pop(hid, None)
        if h is None:
            raise ValueError(f"unknown hook {hid!r}")
        self.o._audit("hook_remove", hook=hid, target=h["id"])
        return {"ok": True}

    def fire(self, rec, event, result=""):
        """Called by the core on idle/stopped/dead/error. Never blocks and never raises."""
        try:
            with self.lock:
                hit = [h for h in self.items.values() if h["id"] == rec.id and event in MATCH[h["on"]]]
                for h in hit:
                    if not h["repeat"]:
                        self.items.pop(h["hook"], None)
                    h["fired"] += 1
                    h["last_fired"] = time.time()
                hit = [dict(h) for h in hit]
            for h in hit:
                threading.Thread(target=self._run, args=(h, rec.id, rec.cwd, rec.status, event, result), daemon=True).start()
        except Exception as e:
            try:
                self.o._audit("hook_error", agent=rec.id, error=repr(e))
            except Exception:
                pass

    def _run(self, h, aid, cwd, status, event, result):
        code, out = None, ""
        try:
            env = dict(os.environ)
            env.update(SWITCHYARD_AGENT=_clean(aid), SWITCHYARD_STATUS=_clean(status), SWITCHYARD_EVENT=event,
                       SWITCHYARD_RESULT=_clean(result or "")[:MAX_RESULT])
            p = subprocess.Popen(split_command(h["run"]), shell=False, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=sys.platform != "win32")
            bufs = [bytearray(), bytearray()]
            readers = [threading.Thread(target=_drain, args=(pipe, buf), daemon=True) for pipe, buf in zip((p.stdout, p.stderr), bufs)]
            for t in readers:
                t.start()
            try:
                code = p.wait(timeout=TIMEOUT)
            except subprocess.TimeoutExpired:
                kill_tree(p)
                p.wait()
                code = "timeout"
            for t in readers:
                t.join(5)                              # a grandchild holding the pipe open must not hang the hook
            out = "".join(bytes(b).decode("utf-8", "replace") for b in bufs)
        except Exception as e:
            code, out = "error", repr(e)
        with self.lock:
            live = self.items.get(h["hook"])
            if live is not None:
                live["last_exit"] = code
            else:                                  # one-shot already removed: nothing to update
                pass
        try:
            self.o._audit("hook_fired", agent=aid, hook=h["hook"], event=event, exit=code, output=out[:MAX_OUT])
        except Exception:
            pass
