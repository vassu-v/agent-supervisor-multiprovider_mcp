"""CLI client for the orchestrator.  py -3.10 agentctl.py <cmd> ...
  serve                                   start the daemon (run as a background task)
  spawn "<task>" --cwd DIR [--provider agy|claude|codex|opencode|auto] [--model M] [--tier T] [--id ID] [--sandbox]
  list | status ID | tail ID [N] | result ID | events ID [SINCE]
  send ID "<msg>" [--mode queue|steer|interrupt]      interrupt ID
  stop ID --reason "<why>"                            escalations | resolve EID allow|deny [--note ".."] | audit | route "<task>"
  providers                                           which providers are ready / disabled / not installed
  providers disable|enable NAME                       switch a provider off or on (agents are told it is unavailable)
  models [PROVIDER] [--filter TEXT] [--limit N] [--refresh] [--full]   models each provider reports now (discovered, cached 10 min; default limit 50)
Env: ORCH_URL (default http://127.0.0.1:8765), ORCH_TOKEN (default: orch/token.txt), ORCH_AGENT (caller id for audit)."""
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
URL = os.environ.get("ORCH_URL", "http://127.0.0.1:8765")


def token():
    t = os.environ.get("ORCH_TOKEN")
    if t:
        return t
    try:
        home = os.path.abspath(os.environ.get("SWITCHYARD_HOME") or HERE)
        return open(os.path.join(home, "orch", "token.txt")).read().strip()
    except OSError:
        return ""


def call(path, **kw):
    kw.setdefault("by", os.environ.get("ORCH_AGENT", "external"))
    req = urllib.request.Request(URL + path, json.dumps(kw).encode(),
                                 {"Authorization": f"Bearer {token()}", "Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=60).read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


BOOL_FLAGS = {"sandbox", "refresh", "full", "all"}


def flags(args):
    pos, kw, i = [], {}, 0
    while i < len(args):
        if args[i].startswith("--"):
            k = args[i][2:]
            if k not in BOOL_FLAGS and i + 1 < len(args) and not args[i + 1].startswith("--"):
                kw[k] = args[i + 1]
                i += 2
            else:
                kw[k] = True
                i += 1
        else:
            pos.append(args[i])
            i += 1
    return pos, kw


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd, (pos, kw) = sys.argv[1], flags(sys.argv[2:])
    if cmd == "serve":
        sys.path.insert(0, HERE)
        from orch.server import main as serve
        return serve()
    if cmd == "spawn":
        opts = {"sandbox": True} if kw.pop("sandbox", None) else None
        out = call("/api/spawn", task=pos[0], cwd=kw.pop("cwd", os.getcwd()), opts=opts, **kw)
    elif cmd == "send":
        out = call("/api/send", id=pos[0], msg=pos[1], mode=kw.get("mode", "queue"))
    elif cmd == "stop":
        out = call("/api/stop", id=pos[0], reason=kw.get("reason", ""))
    elif cmd == "resolve":
        out = call("/api/resolve", escalation=pos[0], decision=pos[1], note=kw.get("note", ""))
    elif cmd == "route":
        out = call("/api/route", task=pos[0], **kw)
    elif cmd == "providers":
        if len(pos) >= 2 and pos[0] in ("enable", "disable"):
            out = call("/api/provider", name=pos[1], enabled=(pos[0] == "enable"))
        else:
            out = [{k: s.get(k) for k in ("name", "state", "installed", "enabled", "models_source", "error")} |
                   {"models": len(s.get("models", []))} for s in call("/api/providers")]
    elif cmd == "models":
        q = {"refresh": bool(kw.get("refresh")), "limit": kw.get("limit", 50)}
        if kw.get("filter"):
            q["filter"] = kw["filter"]
        out = call("/api/models", **({"provider": pos[0]} if pos else {}), **q)
        if not kw.get("full") and "error" not in out:
            out = {n: {"enabled": v["enabled"], "source": v["source"], "error": v["error"], "total": v["total"],
                       "matched": v["matched"], "truncated": v["truncated"], "models": [m["id"] for m in v["models"]]}
                   for n, v in out.items()}
    elif cmd in ("list", "escalations", "audit"):
        out = call("/api/" + cmd, **kw)
    elif cmd in ("status", "result", "interrupt"):
        out = call("/api/" + cmd, id=pos[0])
    elif cmd in ("tail", "events"):
        out = call("/api/" + cmd, id=pos[0], **({"n": pos[1]} if cmd == "tail" and len(pos) > 1 else
                                                 {"since": pos[1]} if len(pos) > 1 else {}))
    else:
        print(__doc__)
        return
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
