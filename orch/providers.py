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
HOME = os.path.abspath(os.environ.get("SWITCHYARD_HOME") or HERE)
CONFIG = os.path.join(HOME, "orch", "config.json")
NAMES = ["agy", "claude", "opencode", "codex"] + (["fake"] if os.environ.get("SWITCHYARD_FAKE") else [])
ADAPTER_MODULE = {"fake": "tests.fake.adapter"}
LOCK = threading.RLock()
_PLOCK = {}                 # per-provider lock: only one discovery subprocess per provider at a time
_CFG_LAST = {"data": None}
CACHE = {}          # provider -> {"models": [...], "source": "live|static|none", "checked": ts, "error": str|None}
_REFRESHING = set()


# ------------------------------------------------------------------ reasoning effort
# Unified effort value space, low to high. Each provider maps it onto what it really supports (nearest level, ties go
# DOWN); the result is reported as `effort_applied` (+ `effort_warning` when it differs or cannot be applied).
#   claude   : `--effort <level>` at launch. Native levels low|medium|high|xhigh|max -> identity.
#   agy      : effort is part of the MODEL ID (gemini-3.8-flash-low|medium|high): effort picks the sibling id. agy rejects
#              `--effort` for every model (verified), so the flag is never passed; models without a level have no effort.
#   codex    : turn/start `effort` (ReasoningEffort string). Levels are per model (debug models: supported_reasoning_levels,
#              e.g. gpt-5.5 = low..xhigh, gpt-5.6-luna = low..max); unified value -> nearest level the model supports.
#   opencode : `variant` on prompt_async; provider-specific (openai/*: low|medium|high|xhigh; anthropic/*: high|max;
#              google/*: low|high; other: low|medium|high). Unified value -> nearest variant for the model's provider.
EFFORTS = ("low", "medium", "high", "xhigh", "max")
_STD = list(EFFORTS)
EFFORT_STATIC = {"claude": _STD, "agy": _STD, "fake": _STD, "codex": ["low", "medium", "high", "xhigh"]}   # codex: fallback when the model is unknown
OPENCODE_VARIANTS = {"openai": ["low", "medium", "high", "xhigh"], "anthropic": ["high", "max"],
                     "google": ["low", "high"]}
OPENCODE_DEFAULT = ["low", "medium", "high"]
_AGY_SUFFIX = ("low", "medium", "high")


def check_effort(effort):
    """Normalise + validate a unified effort value. None/'' -> None. Raises ValueError otherwise."""
    if effort is None or effort == "":
        return None
    if not isinstance(effort, str) or effort.strip().lower() not in EFFORTS:
        raise ValueError(f"effort {effort!r} is invalid; valid values: {list(EFFORTS)}")
    return effort.strip().lower()


def nearest_level(effort, supported):
    """Nearest of `supported` to `effort` on the unified scale; ties go to the lower level."""
    r = EFFORTS.index(effort)
    best = min(supported, key=lambda x: (abs(EFFORTS.index(x) - r) if x in EFFORTS else 99, EFFORTS.index(x) if x in EFFORTS else 99))
    return best


def opencode_levels(model):
    return OPENCODE_VARIANTS.get((model or "").split("/", 1)[0].lower(), OPENCODE_DEFAULT) if model and "/" in model else OPENCODE_DEFAULT


def _model_efforts(provider, model_id):
    """Levels a provider/model accepts (from the discovered list when known, else the static table)."""
    if provider == "opencode":
        return opencode_levels(model_id)
    if provider == "codex" and model_id and model_id != "default":
        for m in (CACHE.get("codex") or {}).get("models", []):
            if m["id"] == model_id and m.get("efforts"):
                return list(m["efforts"])
    return list(EFFORT_STATIC.get(provider) or [])


def supported_efforts(provider, model=None):
    return _model_efforts(provider, model)


def map_effort(provider, model, effort):
    """-> {"effort": requested, "applied": provider value | None, "warning": str | None}. Never silent: a changed or
    impossible mapping is explained in `warning`. Raises ValueError for an invalid effort value."""
    effort = check_effort(effort)
    if effort is None:
        return {"effort": None, "applied": None, "warning": None}
    if provider == "agy" and model and model.rsplit("-", 1)[-1] in _AGY_SUFFIX:
        # agy rejects `--effort` together with a model id that already carries a level (verified: "conflicts with --effort").
        # For those models effort is chosen by picking the sibling id: gemini-3.8-flash-low / -medium / -high.
        base = model.rsplit("-", 1)[0]
        ids = {m["id"] for m in models("agy")["models"]}
        have = [lv for lv in ("low", "medium", "high") if f"{base}-{lv}" in ids] or [model.rsplit("-", 1)[-1]]
        applied = effort if effort in have else nearest_level(effort, have)
        warn = None if applied == effort else (f"agy encodes effort in the model id and offers {have} for {base}; "
                                               f"using {applied!r} for requested {effort!r}")
        return {"effort": effort, "applied": applied, "warning": warn, "model": f"{base}-{applied}", "via_model": True}
    if provider == "agy":
        # verified live: agy rejects --effort for EVERY model ("--effort is not supported for model ..."), so a model id without a
        # level suffix has no effort control at all; never pass the flag.
        return {"effort": effort, "applied": None,
                "warning": (f"agy takes reasoning effort only through the level in a gemini model id (-low/-medium/-high); "
                            f"{model or 'the default model'} has none, so {effort!r} was ignored")}
    if provider == "codex" and model and model != "default":
        models(provider)                        # make sure per-model levels are known (cached, no tokens)
    sup = _model_efforts(provider, model)
    if not sup:
        return {"effort": effort, "applied": None,
                "warning": f"provider {provider} does not support reasoning effort; {effort!r} was ignored"}
    applied = effort if effort in sup else nearest_level(effort, sup)
    warn = None
    if applied != effort:
        warn = f"{provider}{':' + model if model else ''} does not offer effort {effort!r} (offers {sup}); using nearest {applied!r}"
    return {"effort": effort, "applied": applied, "warning": warn}


# ------------------------------------------------------------------ config
def load_config():
    cfg = {"providers": {n: {"enabled": True} for n in NAMES}, "model_cache_ttl_s": 600, "max_concurrent": 20}
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
        for k in ("model_cache_ttl_s", "max_concurrent"):
            if k in user:
                cfg[k] = user[k]
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
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
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
        if name == "fake":
            return "<fake>"
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
        mod = importlib.import_module(ADAPTER_MODULE.get(name, f"orch.adapters.{name}"))
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
    ids = {m["id"] for m in models}
    for m in models:                              # effort levels exist only where a gemini id has level siblings
        suffix = m["id"].rsplit("-", 1)[-1]
        base = m["id"].rsplit("-", 1)[0]
        m["efforts"] = [lv for lv in ("low", "medium", "high") if f"{base}-{lv}" in ids] if suffix in _AGY_SUFFIX else []
    return models, "live"


def _discover_opencode(exe):
    out = _run([exe, "models"])
    return [{"id": l.strip(), "label": l.strip(), "efforts": opencode_levels(l.strip())}
            for l in out.splitlines() if "/" in l and " " not in l.strip()], "live"


def _discover_claude(exe):
    # Claude Code has no model-list command; these aliases are accepted by `--model` and always track the latest model.
    aliases = ["opus", "sonnet", "haiku"]
    extra = load_config()["providers"].get("claude", {}).get("extra_models", [])
    return [{"id": a, "label": f"{a} (alias)", "efforts": list(EFFORT_STATIC["claude"])} for a in aliases + list(extra)], "static"


def _discover_codex(exe):
    data = json.loads(_run([exe, "debug", "models"]))          # lists models; consumes no tokens
    return [{"id": m["slug"], "label": m.get("display_name") or m["slug"], "efforts": _codex_levels(m),
             "default_effort": m.get("default_reasoning_level")}
            for m in data.get("models", []) if m.get("slug") and m.get("visibility", "list") != "hide"], "live"


def _codex_levels(m):
    """Unified-scale levels from supported_reasoning_levels (codex-only extras such as 'ultra' are not part of the unified space)."""
    lv = [x.get("effort") if isinstance(x, dict) else x for x in (m.get("supported_reasoning_levels") or [])]
    lv = [x for x in lv if x in EFFORTS]
    return lv or list(EFFORT_STATIC["codex"])


def _discover_fake(exe):
    return [{"id": "fake-1", "label": "scripted fake", "efforts": list(EFFORT_STATIC["fake"])}], "live"


DISCOVER = {"fake": _discover_fake, "agy": _discover_agy, "opencode": _discover_opencode, "claude": _discover_claude, "codex": _discover_codex}


def _check_auth(name, exe, n_models):
    """True / False / None (unknown). Best effort: never raises, never starts a model turn."""
    try:
        if name == "fake":
            return True
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
