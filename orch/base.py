"""Adapter interface. One Adapter = one persistent agent process/session on one provider.

Contract (every adapter MUST follow it exactly):
  - __init__(agent_id, cwd, model, emit, opts)   emit(dict) is thread-safe; call it from any thread.
  - start()            launch the persistent process/session; must NOT block on the first model turn.
  - send(text, mode)   mode: "queue" | "steer" | "interrupt"
        queue     -> deliver after the current turn finishes (adapter may queue natively or rely on the core)
        steer     -> inject into the in-flight turn if capabilities["steer"], else behave like "queue"
        interrupt -> cancel current turn (session stays alive, context kept) THEN deliver text as a new turn;
                     if capabilities["interrupt"] is False, do kill+resume (set restarted=True in a status event).
  - interrupt()        cancel current turn, keep the session. No new text.
  - kill()             terminate process(es) and everything they spawned. Idempotent.
  - alive              property: process/session usable.
  - session_id         provider session/conversation/thread id once known (else None).
  - capabilities       dict: {"steer": bool, "interrupt": bool, "usage": bool, "native_queue": bool}

Normalised events passed to emit() (all dicts, "type" required, "ts" added by core):
  {"type":"status","state":"busy"|"idle"|"dead","restarted":bool?}      turn lifecycle (emit busy at turn start, idle at turn end)
  {"type":"text","text":str}                                             assistant text delta/chunk
  {"type":"tool_start","tool":str,"input":str|dict,"id":str?}            tool/shell call begins (input = command or args)
  {"type":"tool_end","tool":str,"id":str?,"ok":bool?,"output":str?}      (output truncated to 2000 chars)
  {"type":"permission","id":str,"tool":str,"input":...}                  provider asked for permission (adapter auto-approves
                                                                         if it can, but MUST still emit this event)
  {"type":"usage","input":int,"output":int,"cache_read":int,"cache_write":int?}   when known
  {"type":"result","text":str,"ok":bool,"stop":"end_turn"|"cancelled"|"error"}    exactly one per turn
  {"type":"error","message":str}
  {"type":"session","id":str}                                            when the provider session id becomes known
Adapters run with permission prompts OFF and must be scoped to `cwd` (spawn processes with cwd=cwd).
Kill must kill child processes too (on Windows use `taskkill /PID <pid> /T /F`).
"""
import subprocess
import sys


class Adapter:
    name = "base"
    capabilities = {"steer": False, "interrupt": False, "usage": False, "native_queue": False}

    def __init__(self, agent_id, cwd, model, emit, opts=None):
        self.agent_id, self.cwd, self.model, self.emit = agent_id, cwd, model, emit
        self.opts = opts or {}
        self.session_id = None

    # --- to implement -------------------------------------------------
    def start(self):
        raise NotImplementedError

    def send(self, text, mode="queue"):
        raise NotImplementedError

    def interrupt(self):
        raise NotImplementedError

    def kill(self):
        raise NotImplementedError

    @property
    def alive(self):
        raise NotImplementedError


def kill_tree(proc):
    """Kill a subprocess and all its children (Windows-safe)."""
    if proc is None or proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True)
    else:
        proc.kill()
