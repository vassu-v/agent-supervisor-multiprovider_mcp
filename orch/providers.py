"""Provider registry: which providers are switched on, whether each is installed, and which models each one offers.

Models are DISCOVERED from the provider CLIs (cached with a TTL, refreshed in the background and on demand), never
hardcoded. Enabled/disabled lives in orch/config.json (user-local, hot-reloaded) or the SWITCHYARD_DISABLE env var."""
import fnmatch
import importlib
import json
import os
import re
import shutil
import subprocess
import threading
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(HERE, "orch", "config.json")
NAMES = ["agy", "claude", "opencode", "codex"]
LOCK = threading.RLock()
_PLOCK = {}                 # per-provider lock: only one discovery subprocess per provider at a time
_CFG_LAST = {"data": None}
CACHE = {}          # provider -> {"models": [...], "source": "live|static|none", "checked": ts, "error": str|None}
_REFRESHING = set()


# ------------------------------------------------------------------ config
def load_config():
    cfg = {"providers": {n: {"enabled": True} for n in NAMES}, "model_cache_ttl_s": 600}
    user = None
    try:
        with open(CONFIG, encoding="utf-8") as f:
            user = json.load(f)
        _CFG_LAST["data"] = user
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        user = _CFG_LAST["data"]               # unreadable or half-written file: keep the last good config, don't re-enable everything
    if user:
        for n, v in (user.get("providers") or {}).items():
            cfg["providers"].setdefault(n, {}).update(v)
        if "model_cache_ttl_s" in user:
            cfg["model_cache_ttl_s"] = user["model_cache_ttl_s"]
    for n in filter(None, (x.strip() for x in os.environ.get("SWITCHYARD_DISABLE", "").split(","))):
        cfg["providers"].setdefault(n, {})["enabled"] = False
    return cfg


def set_enabled(name, enabled):
    if name not in NAMES:
        raise ValueError(f"unknown provider {name!r}; have {NAMES}")
    try:
        with open(CONFIG, encoding="utf-8") as f:
            user = json.load(f)
    except (OSError, ValueError):
        user = {}
    user.setdefault("providers", {}).setdefault(name, {})["enabled"] = bool(enabled)
    tmp = CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(user, f, indent=2)
    os.replace(tmp, CONFIG)                    # atomic: a concurrent reader never sees a partial file
    _CFG_LAST["data"] = user
    return status(name)


def enabled(name):
    return bool(load_config()["providers"].get(name, {}).get("enabled", True))


# ------------------------------------------------------------------ binaries
def binary(name):
    """Path of the provider CLI, or None if it is not installed."""
    try:
        if name == "agy":
            from orch.adapters.agy import AGY
            return AGY if os.path.exists(AGY) else None
        if name == "opencode":
            from orch.adapters.opencode import _find_opencode
            return _find_opencode()
        if name == "codex":
            from orch.adapters.codex import _find_codex
            return _find_codex()
    except Exception:
        return None
    return shutil.which(name)


def adapter_importable(name):
    try:
        mod = importlib.import_module(f"orch.adapters.{name}")
        return any(n.endswith("Adapter") for n in dir(mod))
    except Exception:
        return False


# ------------------------------------------------------------------ discovery
def _run(cmd, timeout=40):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    return r.stdout


def _discover_agy(exe):
    out = _run([exe, "models"])
    models = []
    for line in out.splitlines():
        parts = line.rstrip().split("\t")
        if parts and parts[0].strip():
            models.append({"id": parts[0].strip(), "label": parts[1].strip() if len(parts) > 1 else parts[0].strip()})
    return models, "live"


def _discover_opencode(exe):
    out = _run([exe, "models"])
    return [{"id": l.strip(), "label": l.strip()} for l in out.splitlines() if "/" in l and " " not in l.strip()], "live"


def _discover_claude(exe):
    # Claude Code has no model-list command; these aliases are accepted by `--model` and always track the latest model.
    aliases = ["opus", "sonnet", "haiku"]
    extra = load_config()["providers"].get("claude", {}).get("extra_models", [])
    return [{"id": a, "label": f"{a} (alias)"} for a in aliases + list(extra)], "static"


def _discover_codex(exe):
    data = json.loads(_run([exe, "debug", "models"]))          # lists models; consumes no tokens
    return [{"id": m["slug"], "label": m.get("display_name") or m["slug"]}
            for m in data.get("models", []) if m.get("slug") and m.get("visibility", "list") != "hide"], "live"


DISCOVER = {"agy": _discover_agy, "opencode": _discover_opencode, "claude": _discover_claude, "codex": _discover_codex}


def _check_auth(name, exe, n_models):
    """True / False / None (unknown). Best effort: never raises, never starts a model turn."""
    try:
        if name == "claude":
            return bool(json.loads(_run([exe, "auth", "status"], 20) or "{}").get("loggedIn"))
        if name == "codex":
            r = subprocess.run([exe, "login", "status"], capture_output=True, text=True, encoding="utf-8", timeout=20)
            return "logged in" in (r.stdout + r.stderr).lower()
        if name == "opencode":
            return n_models > 0              # free models need no login; a populated list means it works
        if name == "agy":
            return True if n_models > 0 else None
    except Exception:
        return None
    return None


def refresh(name):
    exe = binary(name)
    rec = {"models": [], "source": "none", "checked": time.time(), "error": None, "auth": None}
    if not exe:
        rec["error"] = "not installed"
    else:
        try:
            rec["models"], rec["source"] = DISCOVER[name](exe)
        except Exception as e:
            rec["error"] = f"model discovery failed: {e!r}"
        rec["auth"] = _check_auth(name, exe, len(rec["models"]))
    with LOCK:
        CACHE[name] = rec
    return rec


def _fresh(name):
    rec = CACHE.get(name)
    if rec is None:
        return False
    ttl = load_config()["model_cache_ttl_s"]
    if rec["error"] or rec["auth"] is False:
        ttl = min(ttl, 30)                     # failures are re-checked soon: fixing a login or install is picked up quickly
    return (time.time() - rec["checked"]) < ttl


def models(name, force=False):
    if not force and _fresh(name):
        return CACHE[name]
    with _PLOCK.setdefault(name, threading.Lock()):
        if not force and _fresh(name):         # another caller refreshed while we waited
            return CACHE[name]
        return refresh(name)


def refresh_all_async():
    """Warm every enabled provider's model list without blocking the caller."""
    def work(n):
        try:
            models(n, force=True)
        finally:
            _REFRESHING.discard(n)
    for n in NAMES:
        if enabled(n) and n not in _REFRESHING:
            _REFRESHING.add(n)
            threading.Thread(target=work, args=(n,), daemon=True).start()


# ------------------------------------------------------------------ status / selection
def status(name, with_models=True):
    en = enabled(name)
    exe = binary(name)
    st = {"name": name, "enabled": en, "installed": bool(exe), "path": exe,
          "adapter": adapter_importable(name)}
    if not en:
        st["state"] = "disabled"
    elif not exe:
        st["state"] = "not_installed"
    elif not st["adapter"]:
        st["state"] = "no_adapter"
    else:
        st["state"] = "ready"
        if with_models:
            m = models(name)
            st.update(models=m["models"], models_source=m["source"], checked=m["checked"], error=m["error"],
                      logged_in=m["auth"])
            if m["auth"] is False:
                st["state"] = "needs_login"
            elif m["error"]:
                st["state"] = "degraded"
    return st


def all_status(with_models=True):
    return [status(n, with_models) for n in NAMES]


def usable():
    """Providers a task may actually be routed to."""
    out = []
    for n in NAMES:
        if status(n, with_models=False)["state"] not in ("ready", "degraded"):
            continue
        rec = CACHE.get(n)                    # cached login check only: never block routing on a slow CLI call
        if rec is not None and rec["auth"] is False:
            continue
        out.append(n)
    return out


def _natural_key(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def resolve_model(provider, pattern):
    """Resolve a policy pattern (glob, or alias) to a concrete discovered model id. None if nothing matches."""
    ms = [m["id"] for m in models(provider)["models"]]
    if not pattern or pattern == "default":
        return pattern or None
    if pattern == "*":               # "any model": let the provider pick its own default rather than guessing
        return "default"
    if not ms:
        return None
    exact = [m for m in ms if m == pattern]
    if exact:
        return exact[0]
    hits = sorted((m for m in ms if fnmatch.fnmatch(m, pattern)), key=_natural_key, reverse=True)
    return hits[0] if hits else None


def validate_model(provider, model):
    """-> (ok, suggestions). Only strict when the list came live from the provider."""
    rec = models(provider)
    if rec["source"] != "live" or not model:
        return True, []
    ids = [m["id"] for m in rec["models"]]
    if model in ids:
        return True, []
    close = [i for i in ids if model.split("/")[-1].lower()[:6] in i.lower()][:6] or ids[:6]
    return False, close


def summary_text():
    """Short human/agent-readable provider availability, used in MCP instructions and agent preambles."""
    lines = []
    for s in all_status(with_models=True):
        if s["state"] in ("ready", "degraded"):
            ids = [m["id"] for m in s.get("models", [])]
            lines.append(f"- {s['name']}: AVAILABLE ({len(ids)} models{', e.g. ' + ', '.join(ids[:3]) if ids else ''})")
        else:
            why = {"disabled": "switched off by the user", "not_installed": "not installed", "no_adapter": "adapter missing",
                   "needs_login": "installed but not logged in"}[s["state"]]
            lines.append(f"- {s['name']}: UNAVAILABLE ({why}) - do not route work to it")
    return "\n".join(lines)
