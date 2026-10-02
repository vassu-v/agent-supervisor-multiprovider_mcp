"""Orchestrator core: registry, routing, guard/escalation, AGENTS.md discipline. Provider-agnostic."""
import collections
import hashlib
import importlib
import json
import os
import re
import sys
import threading
import time
import uuid

from orch import identity, providers  # noqa: E402
from orch import workspace as wsmod  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.abspath(os.environ.get("SWITCHYARD_HOME") or HERE)       # logs, db, token, config live here (tests isolate it)
LOGS = os.path.join(HOME, "logs")
POLICY = os.path.join(HERE, "orch", "policy.json")
PROVIDERS = {"agy": "orch.adapters.agy:AgyAdapter", "claude": "orch.adapters.claude:ClaudeAdapter",
             "codex": "orch.adapters.codex:CodexAdapter", "opencode": "orch.adapters.opencode:OpenCodeAdapter"}
if os.environ.get("SWITCHYARD_FAKE"):                                    # zero-cost scripted provider for QA
    PROVIDERS["fake"] = "tests.fake.adapter:FakeAdapter"

PREAMBLE = """[orchestrator rules - follow silently]
1. Work ONLY inside your working directory ({cwd}). Do not read/write outside it unless the task says so.
2. Read {notes} in this directory first if it exists. Record durable learnings (decisions, commands, gotchas) under a
   '## Agent notes' section in {notes} - it is the single shared notes file for every agent/harness. Prefer it over
   CLAUDE.md/GEMINI.md (anything you add to those is mirrored into {notes} automatically).
3. If you see another agent doing something harmful, stop it: "{py}" "{orch_cli}" stop <agent_id> --reason "<why>" (the CLI finds its own token; a reason is required).
4. Your agent id is {aid}. Dangerous actions (git push, deleting trees, secrets, external POSTs, installs) get escalated - prefer safe alternatives.
5. Providers in this fleet (only use available ones):
{providers}
6. You are agent {aid}{parent_line} in workspace "{ws_name}". Other agents in this workspace (declared paths are advisory, nothing
   locks files, so check before editing the same file):
{peers}
--- task ---
"""


def now():
    return time.time()


_POLICY = {"mtime": None, "data": None, "warned": False}


def load_policy():
    """Policy is hot-reloaded when the file changes. It is validated and its regexes compiled once; if the new file is
    malformed the LAST GOOD policy keeps governing (the guard never silently turns off)."""
    try:
        m = os.path.getmtime(POLICY)
        if _POLICY["data"] is not None and _POLICY["mtime"] == m:
            return _POLICY["data"]
        with open(POLICY, encoding="utf-8") as f:
            d = json.load(f)
        g = d["guard"]
        d["_compiled"] = {"block": [(p, re.compile(p, re.I | re.M)) for p in g["block_patterns"]],
                          "escalate": [(p, re.compile(p, re.I)) for p in g["escalate_patterns"]]}
        _POLICY.update(mtime=m, data=d, warned=False)
        return d
    except Exception:
        if _POLICY["data"] is not None:
            _POLICY["warned"] = True
            return _POLICY["data"]
        raise


def _flatten(inp):
    """Everything a tool call carries as one searchable string (dict/list inputs are walked, not JSON-dumped)."""
    if isinstance(inp, str):
        return inp
    if isinstance(inp, dict):
        return "\n".join(_flatten(v) for v in inp.values())
    if isinstance(inp, (list, tuple)):
        return "\n".join(_flatten(v) for v in inp)
    return "" if inp is None else str(inp)


def _hash(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()
    except OSError:
        return None


class Policy:
    """Decisions: routing (which provider/model) and guard (pass / escalate / block)."""

    def route(self, task, tier=None, provider=None, model=None, available=None):
        p = load_policy()["routing"]
        reasons = []
        text = (task or "").lower()
        if not tier:
            tier = p["default_tier"]
            if any(re.search(r"\b" + re.escape(k.strip()) + r"\b", text) for k in p["bump_to_hard_keywords"]):
                tier = "hard"
                reasons.append("sensitive keyword -> hard tier")
        if model and (not provider or provider == "auto"):
            raise ValueError("model needs an explicit provider (provider='auto' routes by tier and chooses the model itself)")
        if tier not in p["tiers"]:
            raise ValueError(f"unknown tier {tier!r}; valid tiers: {list(p['tiers'])}")
        review = p["tiers"][tier].get("review", False)
        usable = providers.usable()
        if provider and provider != "auto":
            st = providers.status(provider, with_models=False) if provider in providers.NAMES else None
            if st is None or st["state"] not in ("ready", "degraded"):
                why = "unknown provider" if st is None else st["state"].replace("_", " ")
                raise ValueError(f"provider {provider!r} is unavailable ({why}). Usable providers: {usable or 'none'}")
            if model:
                ok, close = providers.validate_model(provider, model)
                if not ok:
                    raise ValueError(f"model {model!r} is not offered by {provider}. Some that are: {close}")
            return {"provider": provider, "model": model, "tier": tier, "reasons": reasons or ["explicit"], "review": review}
        skipped = []
        for c in p["tiers"][tier]["candidates"]:
            prov, _, pat = c.partition(":")
            if prov not in usable:
                skipped.append(f"{c}: provider unavailable")
                continue
            mod = providers.resolve_model(prov, pat)
            if pat and pat != "default" and mod is None:
                skipped.append(f"{c}: no matching model")
                continue
            return {"provider": prov, "model": None if mod == "default" else mod, "tier": tier,
                    "reasons": reasons + [f"tier {tier} -> {prov}:{mod or 'default'}"] + ([f"skipped {s}" for s in skipped] if skipped else []),
                    "review": review}
        raise ValueError(f"no available provider/model for tier {tier}. Skipped: {skipped}. Usable providers: {usable or 'none'}")

    def guard(self, cwd, tool, inp):
        """-> ("pass"|"escalate"|"block", reason). Callers treat an exception as escalate (fail closed)."""
        d = load_policy()
        g, cp = d["guard"], d["_compiled"]
        s = _flatten(inp)[:4000]                       # bounded input keeps regex time bounded
        for pat, rx in cp["block"]:
            m = rx.search(s)
            if m:
                return "block", f"blocked command: {m.group(0).strip()[:60]!r}"
        for pat, rx in cp["escalate"]:
            m = rx.search(s)
            if m:
                return "escalate", f"risky action: {m.group(0).strip()[:60]!r}"
        if g.get("outside_cwd") and cwd:
            base = os.path.normcase(os.path.abspath(cwd))
            ctl = os.path.normcase(os.path.abspath(os.path.join(HERE, "agentctl.py")))
            text = re.sub(r"[A-Za-z][A-Za-z0-9+.-]*://\S+", " ", s)          # URLs are not filesystem paths
            for tup in re.findall(r'"([A-Za-z]:[\\/][^"]*)"|\'([A-Za-z]:[\\/][^\']*)\'|([A-Za-z]:[\\/][^\s"\'|;&<>]*)', text):
                m = next(x for x in tup if x)
                full = os.path.normcase(os.path.abspath(m))
                if full == ctl:
                    continue                                                  # agents may call the orchestrator CLI
                try:
                    inside = os.path.commonpath([base, full]) == base
                except ValueError:                                            # different drive
                    inside = False
                if not inside:
                    return g["outside_cwd"], f"path outside cwd: {m}"
        return "pass", ""


class AgentRec:
    def __init__(self, aid, provider, model, cwd, tier, owner, review):
        self.id, self.provider, self.model, self.cwd = aid, provider, model, cwd
        self.tier, self.owner, self.review = tier, owner, review
        self.status = "starting"
        self.turns, self.queue = [], []
        self.events = collections.deque(maxlen=3000)
        self.seq = 0
        self.usage = {"input": 0, "output": 0, "cache_read": 0}
        self.adapter = None
        self.created = now()
        self.lock = threading.RLock()
        self.restarts = 0
        self.guard_hits = []
        self.stopped_by = None
        self.parent = None                        # aid of the agent that spawned this one (hierarchy)
        self.ws = self.ws_name = self.ws_root = None
        self.subdir = ""
        self.goal, self.paths = "", []            # declared by the agent/spawner; advisory only
        self.session = None                       # id of the client session that spawned it
        self.logpath = os.path.join(LOGS, aid + ".jsonl")

    def info(self, full=False):
        last = self.turns[-1] if self.turns else {}
        d = {"id": self.id, "provider": self.provider, "model": self.model, "cwd": self.cwd, "tier": self.tier,
             "status": self.status, "owner": self.owner, "turns": len(self.turns), "queued": len(self.queue),
             "usage": self.usage, "restarts": self.restarts, "session": getattr(self.adapter, "session_id", None),
             "caps": getattr(self.adapter, "capabilities", {}), "needs_review": self.review and self.status == "idle",
             "stopped_by": self.stopped_by, "age_s": round(now() - self.created),
             "parent": self.parent, "workspace": self.ws, "workspace_name": self.ws_name, "subdir": self.subdir,
             "goal": self.goal, "paths": self.paths, "created": self.created,
             "last_text": (last.get("response") or last.get("partial") or "")[-300:]}
        if full:
            d["turns_full"] = self.turns
        return d


class Orchestrator:
    def __init__(self):
        os.makedirs(LOGS, exist_ok=True)
        self.agents = {}
        self._dir_snap = {}                       # cwd -> {file: (hash, bytes)}; one snapshot per directory
        self.policy = Policy()
        self.escalations = {}
        self.audit = collections.deque(maxlen=1000)
        self.lock = threading.RLock()
        self.subscribers = []
        self.tokens = identity.TokenRegistry()    # per-agent tokens (agents never hold the admin token)
        self.sessions = identity.SessionRegistry()  # attached clients (MCP bridges, CLI, dashboard)
        self.url = "http://127.0.0.1:" + os.environ.get("ORCH_PORT", "8765")

    # ------------------------------------------------------------ helpers
    def _load(self, provider):
        spec = PROVIDERS.get(provider)
        if not spec:
            raise ValueError(f"unknown provider {provider!r}; have {list(PROVIDERS)}")
        mod, cls = spec.split(":")
        return getattr(importlib.import_module(mod), cls)

    def available(self):
        return providers.usable()

    def _audit(self, kind, **kw):
        rec = {"ts": now(), "kind": kind, **kw}
        self.audit.append(rec)
        with open(os.path.join(LOGS, "audit.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        return rec

    def _get(self, aid):
        a = self.agents.get(aid)
        if not a:
            raise KeyError(f"unknown agent {aid}")
        return a

    # ------------------------------------------------------------ spawn
    @staticmethod
    def _clean_goal_paths(goal, paths):
        if goal is not None:
            goal = str(goal).strip()
            if len(goal) > 200:
                raise ValueError("goal must be 200 characters or fewer")
        if paths is not None:
            if isinstance(paths, str):
                paths = [p for p in re.split(r"[,\n]", paths)]
            if not isinstance(paths, (list, tuple)):
                raise ValueError("paths must be a list of globs or a comma-separated string")
            paths = [str(p).strip() for p in paths if str(p).strip()]
            if len(paths) > 20 or any(len(p) > 200 for p in paths):
                raise ValueError("paths: at most 20 entries of at most 200 characters")
        return goal, paths

    def spawn(self, task, cwd, provider="auto", model=None, tier=None, aid=None, owner="external", opts=None,
              goal=None, paths=None, parent=None, session=None):
        if not task or not str(task).strip():
            raise ValueError("task is required")
        if aid is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", str(aid)):
            raise ValueError("id must be 1-32 characters: letters, digits, '-' or '_'")
        if not cwd or not str(cwd).strip():
            raise ValueError("cwd is required (the directory this agent works in)")
        cwd = os.path.abspath(cwd)
        if not os.path.isdir(cwd) and not os.path.isdir(os.path.dirname(cwd)):
            raise ValueError(f"cwd {cwd} does not exist and neither does its parent; create the parent first (typo guard)")
        try:
            ws = wsmod.resolve(cwd)
        except ValueError as e:
            raise ValueError(f"workspace: {e}")
        goal, paths = self._clean_goal_paths(goal, paths)
        if parent:                                # an agent spawning a child: enforce the hierarchy limits
            with self.lock:
                view = {k: {"parent": a.parent, "status": a.status} for k, a in self.agents.items()}
                pa = self.agents.get(parent)
            ok, why = identity.can_spawn(view, parent)
            if not ok:
                raise PermissionError(why)
            if not wsmod.contains(pa.ws_root, cwd):
                raise PermissionError("a child must work inside its parent's workspace")
        with self.lock:
            busy = sum(1 for a in self.agents.values() if a.status in ("busy", "starting"))
            cap = providers.load_config()["max_concurrent"]
            if busy >= cap:
                raise RuntimeError(f"concurrency cap {cap} reached (raise max_concurrent in orch/config.json)")
            route = self.policy.route(task, tier, provider, model, self.available())
            aid = aid or "a" + uuid.uuid4().hex[:5]
            if aid in self.agents and self.agents[aid].status != "dead":
                raise ValueError(f"agent id {aid} exists")
            os.makedirs(cwd, exist_ok=True)
            rec = AgentRec(aid, route["provider"], route["model"], cwd, route["tier"], owner, route.get("review"))
            rec.parent, rec.ws, rec.ws_name, rec.ws_root, rec.subdir = parent or None, ws["id"], ws["name"], ws["root"], ws["subdir"]
            rec.goal, rec.paths, rec.session = goal or "", paths or [], session
            self.agents[aid] = rec
        notes = load_policy()["shared_notes_file"]
        np = os.path.join(cwd, notes)
        if not os.path.exists(np):
            with open(np, "w", encoding="utf-8") as f:
                f.write("# AGENTS.md\n\nShared notes for every agent working in this directory.\n\n## Agent notes\n")
        with self.lock:
            key = os.path.normcase(cwd)
            if key not in self._dir_snap:
                self._dir_snap[key] = {fn: (_hash(os.path.join(cwd, fn)), self._read(os.path.join(cwd, fn)))
                                       for fn in load_policy()["mirror_files"]}
        self._audit("spawn", agent=aid, provider=rec.provider, model=rec.model, cwd=cwd, tier=rec.tier,
                    reasons=route["reasons"], owner=owner, parent=parent, workspace=rec.ws, goal=rec.goal, paths=rec.paths)
        tok = self.tokens.mint(aid)               # this agent's own credential: limited scope, revoked when it ends
        opts = dict(opts or {})
        opts["env"] = {**(opts.get("env") or {}), "ORCH_URL": self.url, "ORCH_TOKEN": tok, "ORCH_AGENT": aid,
                       "ORCH_WORKSPACE": ws["id"], "ORCH_PARENT": parent or ""}
        try:
            cls = self._load(rec.provider)
            rec.adapter = cls(aid, cwd, rec.model, lambda ev, r=rec: self._on_event(r, ev), opts)
            rec.adapter.start()
        except Exception as e:
            rec.status = "dead"
            self.tokens.revoke(aid)
            self._audit("spawn_failed", agent=aid, error=repr(e))
            raise RuntimeError(f"could not start {rec.provider}: {e}") from e
        orch_cli = os.path.join(HERE, "agentctl.py")
        full = PREAMBLE.format(cwd=cwd, notes=notes, orch_cli=orch_cli, aid=aid, py=sys.executable,
                               providers=providers.summary_text(),
                               parent_line=f" (spawned by {parent})" if parent else "", ws_name=rec.ws_name,
                               peers=self._peers_text(rec)) + task
        self._deliver(rec, full, "queue", display=task)
        return rec.info() | {"route": route}

    @staticmethod
    def _read(path):
        try:
            with open(path, "rb") as f:
                return f.read()
        except OSError:
            return None

    # ------------------------------------------------------------ messaging
    def _deliver(self, rec, text, mode, display=None):
        with rec.lock:
            if rec.status == "dead":
                raise RuntimeError("agent is dead")
            rec.turns.append({"prompt": display or text, "mode": mode, "t0": now(), "partial": ""})
            rec.status = "busy"
        try:
            rec.adapter.send(text, mode)
        except Exception as e:
            with rec.lock:
                if rec.turns and "response" not in rec.turns[-1]:
                    rec.turns[-1].update(response="", ok=False, stop="error", error=repr(e))
                rec.status = "idle" if getattr(rec.adapter, "alive", False) else "dead"
            self._audit("send_failed", agent=rec.id, error=repr(e))
            raise

    def _deliver_quiet(self, rec, text):
        try:
            self._deliver(rec, text, "queue")
        except Exception:
            pass

    def send(self, aid, text, mode="queue", by="external"):
        if mode not in ("queue", "steer", "interrupt"):
            raise ValueError(f"mode must be queue, steer or interrupt (got {mode!r})")
        if not text or not str(text).strip():
            raise ValueError("msg is required")
        rec = self._get(aid)
        if rec.status == "dead":
            raise RuntimeError("agent is dead")
        caps = rec.adapter.capabilities
        self._audit("send", agent=aid, mode=mode, by=by, text=text[:200])
        with rec.lock:
            busy = rec.status == "busy"
        if not busy:
            self._deliver(rec, text, "queue")
            return {"result": "sent"}
        if mode == "interrupt":
            with rec.lock:
                held, rec.queue = rec.queue, []      # messages already queued must survive the interrupt
                for tr in rec.turns:
                    if "response" not in tr:
                        tr["interrupted"] = True
            rec.adapter.interrupt()                 # native cancel, or kill+resume for agy
            self._deliver(rec, text, "interrupt")
            with rec.lock:
                rec.queue = held + rec.queue
            return {"result": "interrupted+sent", "queued_kept": len(held), "how": self._how(caps)}
        if mode == "steer" and caps.get("steer"):
            with rec.lock:
                rec.turns[-1].setdefault("steers", []).append(text)
            rec.adapter.send(text, "steer")
            return {"result": "steered"}
        with rec.lock:
            rec.queue.append(text)
        out = {"result": "queued", "depth": len(rec.queue), "delivered": "after the current turn finishes"}
        if mode == "steer":
            out["note"] = "this provider cannot take a message mid-turn, so it was queued instead (use mode=interrupt to cut in)"
        return out

    @staticmethod
    def _how(caps):
        i = caps.get("interrupt")
        return ("native cancel: turn stopped, session and context kept" if i is True else
                "this provider has no native cancel: the process was restarted on the same conversation (context kept)")

    def interrupt(self, aid, by="external"):
        rec = self._get(aid)
        if rec.status != "busy":
            return {"result": f"nothing to interrupt (agent is {rec.status})"}
        self._audit("interrupt", agent=aid, by=by)
        with rec.lock:
            for tr in rec.turns:
                if "response" not in tr:
                    tr["interrupted"] = True
        rec.adapter.interrupt()
        return {"result": "interrupted", "how": self._how(rec.adapter.capabilities)}

    def stop(self, aid, by="external", reason=""):
        rec = self._get(aid)
        pol = load_policy()["stop_authority"]
        if pol.get("require_reason") and not reason:
            raise ValueError("a reason is required to stop an agent")
        if by != "external" and by != "dashboard" and not pol.get("any_agent_may_stop"):
            raise PermissionError("agents may not stop other agents")
        self.tokens.revoke(aid)
        rec.stopped_by = {"by": by, "reason": reason, "ts": now()}
        self._audit("stop", agent=aid, by=by, reason=reason)
        if rec.adapter is not None:
            rec.adapter.kill()
        with rec.lock:
            rec.status = "dead"
            rec.queue.clear()
        return {"result": "stopped"}

    # ------------------------------------------------------------ events
    def _on_event(self, rec, ev):
        try:
            ev["ts"] = now()
            with rec.lock:
                rec.seq += 1
                ev["seq"] = rec.seq
                rec.events.append(ev)
            try:
                with open(rec.logpath, "a", encoding="utf-8") as f:
                    f.write(json.dumps(ev) + "\n")
            except OSError:
                pass                                   # a locked or full log must never stop state tracking
            t = ev.get("type")
            if t == "status":
                with rec.lock:
                    if ev["state"] == "dead" and rec.status != "dead":
                        rec.status = "dead"
                        self.tokens.revoke(rec.id)
                    elif ev["state"] == "idle" and rec.status != "busy":
                        rec.status = "idle"
                    if ev.get("restarted"):
                        rec.restarts += 1
            elif t == "text":
                with rec.lock:
                    if rec.turns:
                        rec.turns[-1]["partial"] = (rec.turns[-1].get("partial", "") + ev["text"])[-4000:]
            elif t == "usage":
                for k in rec.usage:
                    rec.usage[k] += ev.get(k, 0) or 0
            elif t in ("tool_start", "permission"):
                self._check_guard(rec, ev)
            elif t == "result":
                self._on_result(rec, ev)
        except Exception as e:  # event handling must never kill an adapter thread
            try:
                self._audit("core_error", agent=rec.id, error=repr(e))
            except Exception:
                pass

    def _on_result(self, rec, ev):
        with rec.lock:
            cancelled = ev.get("stop") == "cancelled"
            pending = [t for t in rec.turns if "response" not in t]
            # a cancelled result belongs to the interrupted turn, a normal one to the oldest still-open turn
            tr = next((t for t in pending if t.get("interrupted") == cancelled), pending[0] if pending else None)
            if tr is not None:
                tr.update(response=ev.get("text", ""), ok=ev.get("ok"), stop=ev.get("stop"),
                          secs=round(now() - tr["t0"], 1))
            still_open = any("response" not in t for t in rec.turns)
            if rec.status != "dead" and not still_open:
                rec.status = "idle"
        self._check_forbidden(rec)
        with rec.lock:
            nxt = rec.queue.pop(0) if rec.queue and rec.status == "idle" else None
        if nxt is not None:
            threading.Thread(target=self._deliver_quiet, args=(rec, nxt), daemon=True).start()

    def _check_forbidden(self, rec):
        """Harness-specific notes files (CLAUDE.md, GEMINI.md...) are NOT forbidden: if an agent or user changed one,
        leave it alone and mirror the newly added lines into AGENTS.md ('## Agent notes') so knowledge ends up in one place.
        One snapshot per directory: a change is mirrored exactly once, however many agents share the directory. It is credited
        to the agent only when it is the only one active there; otherwise to 'unknown'."""
        key = os.path.normcase(rec.cwd)
        notes = os.path.join(rec.cwd, load_policy()["shared_notes_file"])
        with self.lock:
            snap = self._dir_snap.setdefault(key, {})
            others = [a for a in self.agents.values() if a is not rec and os.path.normcase(a.cwd) == key
                      and a.status in ("busy", "starting")]
            who = rec.id if not others else "unknown"
            for fn in load_policy()["mirror_files"]:
                h, old = snap.get(fn, (None, None))
                p = os.path.join(rec.cwd, fn)
                if _hash(p) == h:
                    continue
                new = self._read(p) or b""
                snap[fn] = (_hash(p), new)
                added = [l for l in new.decode("utf-8", "replace").splitlines()
                         if l.strip() and l not in (old or b"").decode("utf-8", "replace").splitlines()]
                if added:
                    with open(notes, "a", encoding="utf-8") as f:
                        f.write(f"\n<!-- mirrored from {fn} by agent {who} -->\n" + "\n".join(added) + "\n")
                    self._audit("mirrored_to_agents_md", agent=who, file=fn, lines=len(added))
                    self._on_event(rec, {"type": "guard", "decision": "mirrored",
                                         "why": f"{len(added)} line(s) from {fn} copied to AGENTS.md"})

    # ------------------------------------------------------------ guard / escalation
    def _check_guard(self, rec, ev):
        inp = ev.get("input")
        if inp is None:
            return
        try:
            decision, why = self.policy.guard(rec.cwd, ev.get("tool", ""), inp)
        except Exception as e:                                   # fail CLOSED: a broken guard escalates, never passes
            decision, why = "escalate", f"guard error ({e!r}); failing closed"
        if decision == "pass":
            return
        shown = _flatten(inp)[:300]
        hit = {"ts": now(), "decision": decision, "why": why, "tool": ev.get("tool"), "input": shown}
        rec.guard_hits.append(hit)
        self._audit("guard", agent=rec.id, **hit)
        self._on_event(rec, {"type": "guard", "decision": decision, "why": why})
        if decision == "block":
            # react off the adapter's reader thread: stopping/interrupting from inside it can deadlock on its own replies
            threading.Thread(target=self._guard_stop, args=(rec, why), daemon=True).start()
            return
        with self.lock:
            for e in self.escalations.values():              # one pending escalation per agent+action
                if e["agent"] == rec.id and e["state"] == "pending" and e["input"] == shown:
                    return
            eid = "e" + uuid.uuid4().hex[:5]
            self.escalations[eid] = {"id": eid, "agent": rec.id, "agent_created": rec.created, "why": why,
                                     "tool": ev.get("tool"), "input": shown, "ts": now(), "state": "pending", "cwd": rec.cwd}
        if load_policy()["escalation"]["on_escalate"] == "interrupt":
            threading.Thread(target=self._guard_interrupt, args=(rec,), daemon=True).start()
        threading.Thread(target=self._escalation_timeout, args=(eid,), daemon=True).start()

    def _guard_stop(self, rec, why):
        try:
            self.stop(rec.id, by="guard", reason=why)
        except Exception:
            pass

    def _guard_interrupt(self, rec):
        try:
            rec.adapter.interrupt()
        except Exception:
            pass

    def resolve(self, eid, decision, note="", by="external"):
        if decision not in ("allow", "deny"):
            raise ValueError(f"decision must be allow or deny (got {decision!r})")
        with self.lock:
            e = self.escalations.get(eid)
            if not e or e["state"] != "pending":
                raise KeyError("no such pending escalation")
            e.update(state=decision, note=note, resolved_by=by, resolved=now())
        self._audit("resolve", escalation=eid, decision=decision, by=by, note=note)
        rec = self._get(e["agent"])
        if rec.status == "dead":
            return e
        if decision == "allow":
            self.send(rec.id, f"Orchestrator decision: ALLOWED. {note} Continue with the action you were about to take.", "queue", by=by)
        else:
            self.send(rec.id, f"Orchestrator decision: DENIED. {note} Do not perform that action; choose a safe alternative.", "queue", by=by)
        return e

    def _escalation_timeout(self, eid):
        pol = load_policy()["escalation"]
        time.sleep(pol["pending_timeout_s"])
        e = self.escalations.get(eid)
        if e and e["state"] == "pending":
            e["state"] = "timeout"
            self._audit("escalation_timeout", escalation=eid)
            ag = self.agents.get(e["agent"])
            if pol["timeout_action"] == "stop" and ag is not None and ag.created == e.get("agent_created"):
                try:
                    self.stop(e["agent"], by="guard", reason=f"escalation {eid} unanswered")
                except Exception:
                    pass

    # ------------------------------------------------------------ queries
    def declare(self, aid, goal=None, paths=None, by="external"):
        rec = self._get(aid)
        goal, paths = self._clean_goal_paths(goal, paths)
        with rec.lock:
            if goal is not None:
                rec.goal = goal
            if paths is not None:
                rec.paths = paths
        self._audit("declare", agent=aid, by=by, goal=rec.goal, paths=rec.paths)
        return rec.info()

    def list(self, ws=None, tree=False):
        recs = [a for a in list(self.agents.values()) if ws is None or a.ws == ws]
        out = [a.info() for a in recs]
        kids = {}
        for i in out:
            if i["parent"]:
                kids.setdefault(i["parent"], []).append(i["id"])
        for i in out:
            i["children"] = kids.get(i["id"], [])
        if tree:                                  # roots first, each followed by its descendants, with a depth for indentation
            by_id = {i["id"]: i for i in out}
            ordered, seen = [], set()

            def walk(i, d):
                if i["id"] in seen:
                    return
                seen.add(i["id"])
                i["depth"] = d
                ordered.append(i)
                for c in i["children"]:
                    if c in by_id:
                        walk(by_id[c], d + 1)
            for i in sorted(out, key=lambda x: x["created"]):
                if not i["parent"] or i["parent"] not in by_id:
                    walk(i, 0)
            out = ordered
        return out

    def _peers_text(self, rec, limit=10):
        peers = [a for a in list(self.agents.values()) if a is not rec and a.ws == rec.ws and a.status != "dead"]
        if not peers:
            return "   (none yet)"
        lines = []
        for a in peers[:limit]:
            lines.append(f"   - {a.id} ({a.provider}, {a.status}){' child of ' + a.parent if a.parent else ''}: "
                         f"{a.goal or 'no goal declared'}{' | paths: ' + ', '.join(a.paths[:5]) if a.paths else ''}")
        if len(peers) > limit:
            lines.append(f"   (+{len(peers) - limit} more: run `agentctl who`)")
        return "\n".join(lines)

    def workspaces(self):
        out = {}
        for a in list(self.agents.values()):
            w = out.setdefault(a.ws, {"id": a.ws, "name": a.ws_name, "root": a.ws_root, "agents": 0, "busy": 0, "live": 0})
            w["agents"] += 1
            w["busy"] += a.status == "busy"
            w["live"] += a.status != "dead"
        for s in self.sessions.list_live():
            if s.get("workspace"):
                w = out.setdefault(s["workspace"], {"id": s["workspace"], "name": s["workspace"], "root": None,
                                                     "agents": 0, "busy": 0, "live": 0})
        for w in out.values():
            w["sessions"] = [{"client": s["client"], "label": s.get("label")} for s in self.sessions.list_live()
                             if s.get("workspace") == w["id"]]
        return list(out.values())

    def briefing(self, ws=None, aid=None):
        """Fixed-size situational summary: used as the MCP initialize instructions and by `agentctl who`."""
        lines = []
        info = next((w for w in self.workspaces() if w["id"] == ws), None) if ws else None
        if ws:
            lines.append(f"Workspace: {info['name'] if info else ws}" + (f" ({info['root']})" if info and info.get("root") else ""))
            sess = [f"{s['client']}{'/' + s['label'] if s.get('label') else ''}" for s in (info or {}).get("sessions", [])]
            if sess:
                lines.append("Attached clients: " + ", ".join(sess))
        live = [a for a in list(self.agents.values()) if (ws is None or a.ws == ws) and a.status != "dead" and a.id != aid]
        lines.append(f"Running agents: {len(live)}" + ("" if live else " (none)"))
        for a in live[:10]:
            lines.append(f"- {a.id} {a.provider} {a.status}" + (f" (child of {a.parent})" if a.parent else "")
                         + f": {a.goal or 'no goal declared'}" + (f" | paths: {', '.join(a.paths[:4])}" if a.paths else ""))
        if len(live) > 10:
            lines.append(f"(+{len(live) - 10} more)")
        if live:
            lines.append("Declared paths are advisory: nothing locks files.")
        return "\n".join(lines)

    def events(self, aid, since=0, n=100):
        rec = self._get(aid)
        with rec.lock:
            evs = [e for e in rec.events if e["seq"] > since]
        return evs[-n:]

    def tail(self, aid, n=15):
        rec = self._get(aid)
        out = []
        for e in list(rec.events)[-300:]:
            if e["type"] == "text":
                if out and out[-1]["type"] == "text":
                    out[-1]["text"] += e["text"]
                    continue
                out.append(dict(e))
            else:
                out.append(dict(e))
        return out[-n:]

    def result(self, aid):
        rec = self._get(aid)
        return {"status": rec.status, "turns": rec.turns, "usage": rec.usage}

    def shutdown(self):
        for a in list(self.agents.values()):
            try:
                a.adapter.kill()
            except Exception:
                pass
