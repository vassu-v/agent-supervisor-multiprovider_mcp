"""Identity, sessions and hierarchy helpers (0.3 design). Python 3.10 stdlib only."""
import hashlib
import hmac
import re
import secrets
import threading
import time
import uuid

AGENT_ALLOWED = frozenset({
    "/api/health", "/api/summary", "/api/providers", "/api/models", "/api/route", "/api/list",
    "/api/status", "/api/tail", "/api/result", "/api/briefing", "/api/workspace", "/api/sessions",
    "/api/board", "/api/announce", "/api/ask", "/api/answer", "/api/declare", "/api/stop",
    "/api/spawn",
})

TOKEN_HEX_LEN = 48  # secrets.token_hex(24)
SESSION_TTL = 120.0
DEAD_STATUSES = frozenset({"dead", "stopped", "error"})


def agent_allowed(path):
    """Exact, case-sensitive match on the path (query string ignored)."""
    if not isinstance(path, str):
        return False
    return path.split("?", 1)[0] in AGENT_ALLOWED


def _h(token):
    return hashlib.sha256(token.encode("utf-8")).digest()


class TokenRegistry:
    """token -> aid, held in memory. Only SHA-256 digests are stored; lookup is by digest, so the
    time of a dict probe depends on the digest (unpredictable to an attacker), not on a shared
    prefix with a real token; the hit is then confirmed with hmac.compare_digest."""

    def __init__(self):
        self._lock = threading.Lock()
        self._by_hash = {}   # digest -> aid
        self._by_aid = {}    # aid -> digest

    def mint(self, aid):
        if not aid or not isinstance(aid, str):
            raise ValueError("aid required")
        token = secrets.token_hex(24)
        d = _h(token)
        with self._lock:
            old = self._by_aid.pop(aid, None)   # one live token per agent
            if old is not None:
                self._by_hash.pop(old, None)
            self._by_hash[d] = aid
            self._by_aid[aid] = d
        return token

    def verify(self, token):
        if not isinstance(token, str) or len(token) != TOKEN_HEX_LEN:
            return None
        d = _h(token)
        with self._lock:
            aid = self._by_hash.get(d)
            if aid is None:
                return None
            stored = self._by_aid.get(aid)
        if stored is not None and hmac.compare_digest(stored, d):
            return aid
        return None

    def revoke(self, aid):
        with self._lock:
            d = self._by_aid.pop(aid, None)
            if d is not None:
                self._by_hash.pop(d, None)
                return True
        return False

    def revoke_all(self):
        with self._lock:
            self._by_hash.clear()
            self._by_aid.clear()


_SAFE = re.compile(r"[^A-Za-z0-9._ -]")


def _part(s, default):
    s = _SAFE.sub("_", str(s if s is not None else "")).strip()[:40]
    return s or default


def identity_string(kind, name=None, label=None):
    """agent:<aid> | session:<client>/<label> | external. Components are sanitised so a client
    cannot forge another identity (no ':' or '/' survives inside a component)."""
    if kind == "external":
        return "external"
    if kind == "agent":
        return "agent:" + _part(name, "unknown")
    if kind == "session":
        c = _part(name, "unknown")
        return "session:%s/%s" % (c, _part(label, "")) if label not in (None, "") else "session:" + c
    raise ValueError("unknown identity kind: %r" % (kind,))


class SessionRegistry:
    def __init__(self, clock=time.time, ttl=SESSION_TTL):
        self._clock = clock
        self._ttl = ttl
        self._lock = threading.Lock()
        self._s = {}

    def _expired(self, s, now):
        return now - s["last_seen"] > self._ttl

    def hello(self, client, label=None, workspace=None, pid=None):
        now = self._clock()
        if isinstance(pid, bool) or not isinstance(pid, int):
            pid = None
        sess = {
            "id": uuid.uuid4().hex,
            "client": _part(client, "unknown"),
            "label": _part(label, "") if label else "",
            "workspace": workspace,
            "pid": pid,
            "created": now,
            "last_seen": now,
        }
        with self._lock:
            self._s[sess["id"]] = sess
            return dict(sess)

    def touch(self, session_id):
        now = self._clock()
        with self._lock:
            s = self._s.get(session_id) if isinstance(session_id, str) else None
            if s is None:
                return False
            if self._expired(s, now):
                del self._s[session_id]
                return False
            s["last_seen"] = now
            return True

    def get(self, session_id):
        now = self._clock()
        with self._lock:
            s = self._s.get(session_id) if isinstance(session_id, str) else None
            if s is None:
                return None
            if self._expired(s, now):
                del self._s[session_id]
                return None
            return dict(s)

    def list_live(self, now=None):
        now = self._clock() if now is None else now
        with self._lock:
            for k in [k for k, s in self._s.items() if self._expired(s, now)]:
                del self._s[k]
            return [dict(s) for s in self._s.values()]

    def drop(self, session_id):
        with self._lock:
            return self._s.pop(session_id, None) is not None


# ---- hierarchy (plain dict {aid: {'parent':..., 'status':...}}) ----

def depth(agents, aid):
    """Top-level agent = 1. Missing parent record or a cycle stops the walk. Unknown aid = 0."""
    d, seen, cur = 0, set(), aid
    while cur in agents and cur not in seen:
        seen.add(cur)
        d += 1
        cur = agents[cur].get("parent")
    return d


def live_children(agents, aid):
    return [k for k, v in agents.items()
            if v.get("parent") == aid and v.get("status") not in DEAD_STATUSES]


def can_spawn(agents, caller_aid, max_children=5, max_depth=3):
    if caller_aid not in agents:
        return False, "unknown caller %r" % (caller_aid,)
    if agents[caller_aid].get("status") in DEAD_STATUSES:
        return False, "caller is not live"
    if depth(agents, caller_aid) + 1 > max_depth:
        return False, "hierarchy depth limit %d reached" % max_depth
    n = len(live_children(agents, caller_aid))
    if n >= max_children:
        return False, "child limit reached (%d live, max %d)" % (n, max_children)
    return True, ""
