"""CLI client for the orchestrator.  py -3.10 agentctl.py <cmd> ...
  serve                                   start the daemon (run as a background task)
  spawn "<task>" --cwd DIR [--provider agy|claude|codex|opencode|auto] [--model M] [--tier T] [--effort low|medium|high|xhigh|max] [--id ID] [--sandbox]
  list | status ID | tail ID [N] | result ID | events ID [SINCE]
  send ID "<msg>" [--mode queue|steer|interrupt]      interrupt ID
  stop ID --reason "<why>"                            escalations | resolve EID allow|deny [--note ".."] | audit | route "<task>"
  providers                                           which providers are ready / disabled / not installed
  providers disable|enable NAME                       switch a provider off or on (agents are told it is unavailable)
  models [PROVIDER] [--filter TEXT] [--limit N] [--refresh] [--full]   models each provider reports now (discovered, cached 10 min; default limit 50)
  spawn ... also takes [--goal TEXT] [--paths a,b]     (declared goal and advisory paths, shown to peers)
  announce "<text>" [--kind started|done|changed|blocked|info|handoff] [--paths a,b] [--ws ID]
  ask "<text>" [--ws ID]                              answer ID "<text>"
  board [--ws ID] [--since N] [--kind K[,K]] [--json] shared board: announcements and open questions
  declare --goal "..." [--paths a,b] [--id AID]       set your goal/paths (agents: your own id is implied)
  who [--ws ID]                                       the workspace briefing        ws [PATH]   resolve a path to its workspace
  sessions                                            attached clients
  dashboard [--no-open]                               print (and open in the browser) http://127.0.0.1:PORT/ui/#t=TOKEN
  list [--ws ID] [--all] [--tree]                     agents (default: everything; --tree indents children under parents)
Env: ORCH_URL (default http://127.0.0.1:8765), ORCH_TOKEN (default: orch/token.txt), ORCH_AGENT (caller id for audit),
     ORCH_WORKSPACE (default --ws; else the workspace of the current directory), SWITCHYARD_SESSION (session id header)."""
import json
import os
import sys
import time
import urllib.error
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


SESSION_ID = None                                  # set by the MCP bridge; the CLI uses env SWITCHYARD_SESSION


def call(_path, /, **kw):
    kw.setdefault("by", os.environ.get("ORCH_AGENT", "external"))
    h = {"Authorization": f"Bearer {token()}", "Content-Type": "application/json"}
    sid = SESSION_ID or os.environ.get("SWITCHYARD_SESSION")
    if sid:
        h["X-Switchyard-Session"] = sid
    req = urllib.request.Request(URL + _path, json.dumps(kw).encode(), h)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=60).read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return json.loads(raw)
        except ValueError:
            return {"error": f"HTTP {e.code}: {raw[:200].decode('utf-8', 'replace')}"}


BOOL_FLAGS = {"sandbox", "refresh", "full", "all", "tree", "json", "no-open"}


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


class CliError(Exception):
    pass


KINDS = ("started", "done", "changed", "blocked", "info", "handoff")


def need(pos, n, usage):
    if len(pos) < n:
        raise CliError("usage: agentctl.py " + usage)
    return pos


def csv(v):
    if v is True:
        raise CliError("--paths needs a value, e.g. --paths src/a.py,docs/")
    return [x.strip() for x in str(v).split(",") if x.strip()]


def chk(out):
    if isinstance(out, dict) and out.get("error"):
        raise CliError(str(out["error"]))
    return out


def default_ws(kw):
    ws = kw.get("ws")
    if ws is True:
        raise CliError("--ws needs a workspace id")
    if ws:
        return ws
    ws = os.environ.get("ORCH_WORKSPACE")
    if ws:
        return ws
    w = chk(call("/api/workspace", path=os.getcwd()))
    return w["id"]


def hhmm(ts):
    try:
        return time.strftime("%H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError, OverflowError, OSError):
        return "--:--"


def fmt_board(b):
    lines = [f"open questions: {b.get('open_questions', 0)}   last post: #{b.get('last', 0)}"]
    for p in b.get("posts", []):
        extra = ""
        if p.get("kind") == "question":
            extra = f" ({p.get('status')}{', stale' if p.get('stale') else ''})"
        if p.get("reply_to"):
            extra += f" re #{p['reply_to']}"
        lines.append(f"#{p['id']} [{p.get('kind', '?')}] {p.get('sender', '?')} {hhmm(p.get('ts'))}  {p.get('text', '')}{extra}")
    return "\n".join(lines)


def fmt_list(rows, tree):
    if not isinstance(rows, list):
        return json.dumps(rows, indent=1)
    out = []
    for a in rows:
        pad = "  " * int(a.get("depth", 0)) if tree else ""
        out.append(f"{pad}{a.get('id')}  {a.get('provider')}  {a.get('status')}" + (f"  parent={a['parent']}" if a.get("parent") else "")
                   + (f"  goal: {a['goal']}" if a.get("goal") else ""))
    return "\n".join(out) or "(no agents)"


def run(cmd, pos, kw):
    if cmd == "spawn":
        need(pos, 1, 'spawn "<task>" --cwd DIR [--provider P] [--model M] [--tier T] [--effort low|medium|high|xhigh|max] [--id ID] [--goal TEXT] [--paths a,b]')
        if kw.get("effort") is True:
            raise CliError("--effort needs a value: low|medium|high|xhigh|max")
        opts = {"sandbox": True} if kw.pop("sandbox", None) else None
        if "paths" in kw:
            kw["paths"] = csv(kw["paths"])
        return call("/api/spawn", task=pos[0], cwd=kw.pop("cwd", os.getcwd()), opts=opts, **kw)
    if cmd == "send":
        need(pos, 2, 'send ID "<msg>" [--mode queue|steer|interrupt]')
        return call("/api/send", id=pos[0], msg=pos[1], mode=kw.get("mode", "queue"))
    if cmd == "stop":
        need(pos, 1, 'stop ID --reason "<why>"')
        return call("/api/stop", id=pos[0], reason=kw.get("reason", ""))
    if cmd == "resolve":
        need(pos, 2, 'resolve EID allow|deny [--note ".."]')
        return call("/api/resolve", escalation=pos[0], decision=pos[1], note=kw.get("note", ""))
    if cmd == "route":
        need(pos, 1, 'route "<task>"')
        return call("/api/route", task=pos[0], **kw)
    if cmd == "providers":
        if len(pos) >= 2 and pos[0] in ("enable", "disable"):
            return call("/api/provider", name=pos[1], enabled=(pos[0] == "enable"))
        return [{k: s.get(k) for k in ("name", "state", "installed", "enabled", "models_source", "error")} |
                {"models": len(s.get("models", []))} for s in call("/api/providers")]
    if cmd == "models":
        q = {"refresh": bool(kw.get("refresh")), "limit": kw.get("limit", 50)}
        if kw.get("filter"):
            q["filter"] = kw["filter"]
        out = call("/api/models", **({"provider": pos[0]} if pos else {}), **q)
        if not kw.get("full") and "error" not in out:
            out = {n: {"enabled": v["enabled"], "source": v["source"], "error": v["error"], "total": v["total"],
                       "matched": v["matched"], "truncated": v["truncated"], "models": [m["id"] for m in v["models"]]}
                   for n, v in out.items()}
        return out
    if cmd == "list":
        q = {}
        if kw.get("all"):
            q["all"] = True
        if kw.get("ws"):
            if kw["ws"] is True:
                raise CliError("--ws needs a workspace id")
            q["ws"] = kw["ws"]
        if kw.get("tree"):
            q["tree"] = True
        out = chk(call("/api/list", **q))
        return fmt_list(out, True) if kw.get("tree") else out
    if cmd in ("escalations", "audit"):
        return call("/api/" + cmd, **kw)
    if cmd in ("status", "result", "interrupt"):
        need(pos, 1, f"{cmd} ID")
        return call("/api/" + cmd, id=pos[0])
    if cmd in ("tail", "events"):
        need(pos, 1, f"{cmd} ID [{'N' if cmd == 'tail' else 'SINCE'}]")
        return call("/api/" + cmd, id=pos[0], **({"n": pos[1]} if cmd == "tail" and len(pos) > 1 else
                                                  {"since": pos[1]} if len(pos) > 1 else {}))
    if cmd == "announce":
        need(pos, 1, 'announce "<text>" [--kind started|done|changed|blocked|info|handoff] [--paths a,b] [--ws ID]')
        kind = kw.get("kind", "info")
        if kind not in KINDS:
            raise CliError(f"--kind must be one of {', '.join(KINDS)} (got {kind!r})")
        body = {"text": pos[0], "kind": kind, "ws": default_ws(kw)}
        if "paths" in kw:
            body["paths"] = csv(kw["paths"])
        return call("/api/announce", **body)
    if cmd == "ask":
        need(pos, 1, 'ask "<text>" [--ws ID]')
        return call("/api/ask", text=pos[0], ws=default_ws(kw))
    if cmd == "answer":
        need(pos, 2, 'answer ID "<text>"')
        if not pos[0].lstrip("#").isdigit():
            raise CliError(f"answer: ID must be a post number (got {pos[0]!r})")
        return call("/api/answer", id=int(pos[0].lstrip("#")), text=pos[1])
    if cmd == "board":
        q = {"ws": default_ws(kw)}
        if "since" in kw:
            if not str(kw["since"]).isdigit():
                raise CliError(f"--since must be a post number (got {kw['since']!r})")
            q["since"] = int(kw["since"])
        if kw.get("kind"):
            if kw["kind"] is True:
                raise CliError("--kind needs a value")
            q["kind"] = kw["kind"]
        out = chk(call("/api/board", **q))
        return out if kw.get("json") else fmt_board(out)
    if cmd == "declare":
        if not kw.get("goal") and "paths" not in kw:
            raise CliError('usage: agentctl.py declare --goal "..." [--paths a,b] [--id AID]')
        body = {}
        if kw.get("goal"):
            body["goal"] = kw["goal"]
        if "paths" in kw:
            body["paths"] = csv(kw["paths"])
        aid = kw.get("id") or os.environ.get("ORCH_AGENT")
        if not aid or aid is True:
            raise CliError("declare needs --id AID (agents: ORCH_AGENT is used)")
        return call("/api/declare", id=aid, **body)
    if cmd == "who":
        return chk(call("/api/briefing", ws=default_ws(kw)))["text"]
    if cmd == "ws":
        return chk(call("/api/workspace", path=os.path.abspath(pos[0] if pos else os.getcwd())))
    if cmd == "sessions":
        return call("/api/sessions")
    return None


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd, (pos, kw) = sys.argv[1], flags(sys.argv[2:])
    if cmd == "dashboard":                         # the token travels in the URL fragment, which browsers never send to a server
        from urllib.parse import urlparse
        port = urlparse(URL).port or 8765
        link = f"http://127.0.0.1:{port}/ui/#t={token()}"
        print(link)
        if not kw.get("no-open"):
            import webbrowser
            webbrowser.open(link)
        return
    if cmd == "serve":
        sys.path.insert(0, HERE)
        from orch.server import main as serve
        return serve()
    try:
        out = run(cmd, pos, kw)
        if out is None:
            print(__doc__)
            return
        chk(out)
    except CliError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    except (urllib.error.URLError, OSError) as e:
        print(f"error: cannot reach the daemon at {URL} ({getattr(e, 'reason', e)}). Start it: python agentctl.py serve", file=sys.stderr)
        sys.exit(1)
    except (ValueError, KeyError, TypeError) as e:
        print(f"error: bad input or unexpected reply ({e!r})", file=sys.stderr)
        sys.exit(1)
    print(out if isinstance(out, str) else json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
