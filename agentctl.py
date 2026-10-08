"""CLI client for the orchestrator.  python agentctl.py <cmd> ...      (Windows: `py -3` also works)
Output is short and readable by default; add --json (anywhere before a bare `--`) for the full raw JSON.
A bare `--` ends the flags: everything after it is taken literally, e.g.  send a1 -- "--json"
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
  list [--ws ID] [--all] [--tree]                     agents as a table (default: everything; --tree indents children under parents)
  wait ID [--timeout SECONDS] [--until idle|done]     block until the agent has finished its work (idle, every turn answered, nothing queued; --until
                                                      idle and done are the same), then print its result;
                                                      exit 0 finished, 1 error/stopped/dead, 2 timeout.  spawn ... --wait = spawn, then wait
  spawn ... --cwd DIR                                 DIR is created if its parent exists (default: the current directory); spawn ... --wait [--timeout S]
  hook ID --on done|idle|error|any --run "<cmd>" [--repeat]   run a shell command when the agent reaches that state (once, unless --repeat)
  hooks                                               list hooks            unhook HOOKID     remove one
  --json                                              any command: print the raw JSON reply instead of the readable summary
Env: ORCH_URL (default http://127.0.0.1:$ORCH_PORT, port 8765), ORCH_TOKEN (default: orch/token.txt), ORCH_AGENT (caller id for audit),
     ORCH_WORKSPACE (default --ws; else the workspace of the current directory), SWITCHYARD_SESSION (session id header)."""
import difflib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
URL = os.environ.get("ORCH_URL") or "http://127.0.0.1:" + os.environ.get("ORCH_PORT", "8765")     # ORCH_PORT moves both serve and the client


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


def _request(req):
    try:
        return json.loads(urllib.request.urlopen(req, timeout=60).read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return json.loads(raw)
        except ValueError:
            return {"error": f"HTTP {e.code}: {raw[:200].decode('utf-8', 'replace')}"}


def _headers():
    h = {"Authorization": f"Bearer {token()}", "Content-Type": "application/json"}
    sid = SESSION_ID or os.environ.get("SWITCHYARD_SESSION")
    if sid:
        h["X-Switchyard-Session"] = sid
    return h


def call(_path, /, **kw):
    kw.setdefault("by", os.environ.get("ORCH_AGENT", "external"))
    return _request(urllib.request.Request(URL + _path, json.dumps(kw).encode(), _headers()))


def get(_path):
    return _request(urllib.request.Request(URL + _path, headers=_headers()))


BOOL_FLAGS = {"sandbox", "refresh", "full", "all", "tree", "json", "no-open", "wait", "repeat"}


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
        head = re.split(r"\s(?=\[|--)", usage, 1)[0].split()[1:]          # the positional words before the first option
        what = head[len(pos)] if len(pos) < len(head) else "an argument"
        raise CliError(f"missing {what} (usage: agentctl.py {usage})")
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


JSON_MODE = False
HOOK_ON = ("done", "idle", "error", "any")
COMMANDS = ("serve", "spawn", "list", "status", "tail", "result", "events", "send", "interrupt", "stop", "escalations", "resolve", "audit",
            "route", "providers", "models", "announce", "ask", "answer", "board", "declare", "who", "ws", "sessions", "dashboard",
            "wait", "hook", "hooks", "unhook")
FAILED = ("dead", "stopped", "error")


def clean(v, n=160):
    """One printable line: control characters become spaces, long text is cut with an ellipsis."""
    t = re.sub(r"[\x00-\x1f\x7f]+", " ", v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)).strip()
    return t if len(t) <= n else t[:n - 3] + "..."


def dim(t):
    return f"\x1b[2m{t}\x1b[0m" if sys.stdout.isatty() and not os.environ.get("NO_COLOR") else t


def ktok(n):
    n = int(n or 0)
    return str(n) if n < 1000 else (f"{n / 1000:.1f}k" if n < 1000000 else f"{n / 1000000:.1f}M")


def tokens(a):
    u = a.get("usage") or {}
    return ktok((u.get("input") or 0) + (u.get("output") or 0))


def pm(a):
    return f"{a.get('provider')}:{a.get('model')}"


def h_spawn(out, pos, kw):
    lines = [f"started {out['id']} on {pm(out)} (tier {out.get('tier')}) in {out.get('cwd')}"]
    route = out.get("route") or {}
    if route.get("review") and route.get("reasons"):
        r = route["reasons"]
        lines.append(dim("  review flagged: " + clean("; ".join(map(str, r)) if isinstance(r, list) else r, 300)))
    if out.get("effort_warning"):
        lines.append(dim("  " + clean(out["effort_warning"], 300)))
    return "\n".join(lines)


def h_list(rows, pos, kw):
    if not isinstance(rows, list):
        raise ValueError("not a list")
    if not rows:
        return "(no agents)"
    tree = kw.get("tree")
    cells = [("ID", "STATUS", "PROVIDER:MODEL", "TURNS", "TOKENS", "GOAL")]
    for a in rows:
        cells.append((("  " * int(a.get("depth", 0)) if tree else "") + str(a.get("id")), str(a.get("status")), pm(a),
                      str(a.get("turns")), tokens(a), clean(a.get("goal") or "", 50)))
    w = [max(len(r[i]) for r in cells) for i in range(5)]
    return "\n".join(("  ".join(r[i].ljust(w[i]) for i in range(5)) + "  " + r[5]).rstrip() for r in cells)


def h_status(a, pos, kw):
    lines = [f"{a['id']}  {a['status']}  {pm(a)} (tier {a.get('tier')})  turns {a.get('turns')}  tokens {tokens(a)}"
             + (f"  queued {a['queued']}" if a.get("queued") else "") + (f"  parent {a['parent']}" if a.get("parent") else "")]
    lines.append(f"  cwd {a.get('cwd')}   age {a.get('age_s')}s")
    if a.get("goal"):
        lines.append("  goal: " + clean(a["goal"]))
    if a.get("current_tool"):
        lines.append("  running tool: " + clean(a["current_tool"], 80))
    if a.get("needs_review"):
        lines.append("  needs review")
    if a.get("pending_escalation"):
        lines.append("  waiting on an escalation (see: agentctl.py escalations)")
    if a.get("stopped_by"):
        lines.append("  stopped: " + clean(a["stopped_by"]))
    if a.get("last_text"):
        lines.append("  last: " + clean(a["last_text"]))
    return "\n".join(lines)


def result_text(out):
    turns = [t for t in (out.get("turns_full") or out.get("turns") or []) if isinstance(t, dict)]
    if not turns:
        return ""
    t = turns[-1]                                      # only the newest turn: never show an older turn's text as its answer
    return t.get("response") or t.get("partial") or ""


def h_result(out, pos, kw):
    return result_text(out).strip() or f"(no result yet; agent is {out.get('status')})"


def h_tail(evs, pos, kw):
    if not isinstance(evs, list):
        raise ValueError("not a list")
    lines = []
    for e in evs:
        t = e.get("type")
        if t == "text":
            lines += [f"[text] {clean(x)}" for x in str(e.get("text", "")).splitlines() if x.strip()]
        elif t == "tool_start":
            lines.append(clean(f"[tool] {e.get('tool') or '?'}: {e.get('input', '')}"))
        elif t == "tool_end":
            lines.append(f"[tool {'done' if e.get('ok', True) else 'failed'}] {clean(e.get('tool') or '', 60)}".rstrip())
        elif t == "status":
            lines.append(f"[status] {clean(e.get('state', ''))}")
        elif t == "error":
            lines.append(clean(f"[error] {e.get('message', '')}"))
        elif t == "usage":
            continue
        else:
            lines.append(clean(f"[{t}] " + json.dumps({k: v for k, v in e.items() if k not in ("type", "ts", "seq")}, default=str)))
    return "\n".join(lines) or "(no events yet)"


def h_send(out, pos, kw):
    extra = "".join(f" ({out[k]})" for k in ("delivered", "how") if out.get(k)) + (f"  note: {out['note']}" if out.get("note") else "")
    return f"{pos[0]}: {out['result']}{extra}"


def h_stop(out, pos, kw):
    return f"stopped {pos[0]}" if out.get("result") == "stopped" else h_send(out, pos, kw)


def hook_line(h):
    return clean(f"{h.get('hook', '?')}  {h.get('id', '')}  on {h.get('on', '?')}  {'every time' if h.get('repeat') else 'once'}  run: {h.get('run', '')}")


def h_hook(out, pos, kw):
    return f"hook {out.get('hook', '?')}: when {pos[0]} reaches {kw['on']}, run: {clean(kw['run'], 100)} ({'every time' if kw.get('repeat') else 'once'})"


def h_hooks(out, pos, kw):
    rows = out.get("hooks") if isinstance(out, dict) else out
    if not isinstance(rows, list):
        raise ValueError("not a list")
    return "\n".join(hook_line(h) for h in rows) or "(no hooks)"


def h_unhook(out, pos, kw):
    r = out.get("result")
    return f"removed hook {pos[0]}" if not r or r in ("removed", "ok") else f"{pos[0]}: {r}"


HUMAN = {"spawn": h_spawn, "list": h_list, "status": h_status, "result": h_result, "tail": h_tail, "send": h_send,
         "stop": h_stop, "interrupt": h_send, "hook": h_hook, "hooks": h_hooks, "unhook": h_unhook}


def render(cmd, out, pos, kw):
    """The text to print for `out`: readable by default, raw JSON with --json or when a summary does not fit the reply."""
    if isinstance(out, str):
        return out
    if not JSON_MODE and cmd in HUMAN:
        try:
            return HUMAN[cmd](out, pos, kw)
        except Exception:
            pass
    return json.dumps(out, indent=1)


def cmd_wait(aid, kw):
    """Poll /api/status until the agent is idle (or finished). Prints the result; returns the exit code."""
    until = kw.get("until", "idle")
    if until not in ("idle", "done"):
        raise CliError("--until must be idle or done")
    timeout = kw.get("timeout")
    if timeout is not None:
        try:
            timeout = float(timeout)
        except (TypeError, ValueError):
            raise CliError(f"--timeout needs a number of seconds (got {timeout!r})")
    deadline = None if timeout is None else time.time() + timeout
    calm = 0                                    # consecutive polls on which the agent looked settled
    while True:
        info = chk(get("/api/status?id=" + urllib.parse.quote(str(aid))))
        st = info.get("status") or info.get("state")
        settled = (info.get("turns", 0) >= 1 and not info.get("queued") and all("response" in t for t in info.get("turns_full") or [])
                   if isinstance(info.get("turns"), int) else True)    # an adapter reports a start-time 'idle' before its first turn
        calm = calm + 1 if st == "idle" and settled else 0   # idle and done both mean: every turn answered, nothing queued
        if st in FAILED or calm >= 2:                       # twice in a row: the daemon sets idle a few ms before it delivers/queues follow-ups
            break
        if deadline is not None and time.time() >= deadline:
            if JSON_MODE:
                print(json.dumps(info, indent=1))
            print(f"error: timed out after {timeout:g}s waiting for {aid} (status {st})", file=sys.stderr)
            return 2
        time.sleep(0.4 if calm else 1)
    last = (info.get("turns_full") or [{}])[-1]
    turn_failed = last.get("ok") is False and last.get("stop") != "cancelled"      # the agent is idle again after a failed turn
    if JSON_MODE:
        print(json.dumps(info, indent=1))
    else:
        text = result_text(info).strip()
        if text:
            print(text)
        if st in FAILED:
            print(f"error: {aid} ended: {st}" + (f" ({clean(info['stopped_by'])})" if info.get("stopped_by") else ""), file=sys.stderr)
        elif turn_failed:
            print(f"error: {aid}'s last turn failed", file=sys.stderr)
    return 1 if st in FAILED or turn_failed else 0


def run(cmd, pos, kw):
    if cmd == "spawn":
        need(pos, 1, 'spawn "<task>" --cwd DIR [--provider P] [--model M] [--tier T] [--effort low|medium|high|xhigh|max] [--id ID] [--goal TEXT] [--paths a,b]')
        if kw.get("effort") is True:
            raise CliError("--effort needs a value: low|medium|high|xhigh|max")
        opts = {"sandbox": True} if kw.pop("sandbox", None) else None
        if "paths" in kw:
            kw["paths"] = csv(kw["paths"])
        for k in ("wait", "timeout", "until"):           # client-side flags: handled in main(), never sent to the daemon
            kw.pop(k, None)
        cwd = kw.pop("cwd", None)
        if cwd is True:
            raise CliError("--cwd needs a directory")
        cwd = os.path.abspath(cwd or os.getcwd())
        existed = os.path.isdir(cwd)
        out = call("/api/spawn", task=pos[0], cwd=cwd, opts=opts, **kw)
        if not existed and os.path.isdir(cwd) and not (isinstance(out, dict) and out.get("error")):
            print(f"created {cwd}", file=sys.stderr)
        return out
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
        return chk(call("/api/list", **q))
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
        return out if JSON_MODE else fmt_board(out)
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
    if cmd == "hook":
        need(pos, 1, 'hook ID --on done|idle|error|any --run "<command>" [--repeat]')
        on = kw.get("on")
        if on not in HOOK_ON:
            raise CliError(f"hook needs --on {'|'.join(HOOK_ON)}")
        if not kw.get("run") or kw["run"] is True:
            raise CliError('hook needs --run "<command>"')
        return call("/api/hook", id=pos[0], on=on, run=kw["run"], repeat=bool(kw.get("repeat")))
    if cmd == "hooks":
        return get("/api/hooks")
    if cmd == "unhook":
        need(pos, 1, "unhook HOOKID")
        return call("/api/unhook", hook=pos[0])
    return None


def main():
    global JSON_MODE
    argv = sys.argv[1:]
    cut = argv.index("--") if "--" in argv else len(argv)
    head, tail = argv[:cut], argv[cut + 1:]
    JSON_MODE = "--json" in head
    argv = [a for a in head if a != "--json"]
    try:
        sys.stdout.reconfigure(errors="replace")       # a non-UTF-8 console must never crash the printing
    except (AttributeError, ValueError):
        pass
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return
    cmd, (pos, kw) = argv[0], flags(argv[1:])
    pos += tail                                    # after a bare `--` every token is positional, even "--json"
    if cmd not in COMMANDS:
        near = difflib.get_close_matches(cmd, COMMANDS, n=1)
        print(f"unknown command {cmd!r}" + (f" - did you mean '{near[0]}'?" if near else ""), file=sys.stderr)
        print("run: python agentctl.py --help", file=sys.stderr)
        sys.exit(2)
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
        if cmd == "wait":
            need(pos, 1, "wait ID [--timeout SECONDS] [--until idle|done]")
            sys.exit(cmd_wait(pos[0], kw))
        wait = cmd == "spawn" and kw.get("wait")
        if cmd == "spawn" and not wait and ("timeout" in kw or "until" in kw):
            raise CliError("--timeout/--until only apply together with --wait")
        wkw = {k: kw[k] for k in ("timeout", "until") if k in kw}
        out = run(cmd, pos, kw)
        chk(out)
        if not (wait and JSON_MODE):
            print(render(cmd, out, pos, kw))
        if wait:
            sys.stdout.flush()
            sys.exit(cmd_wait(out["id"], wkw))
    except CliError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        sys.exit(130)
    except (urllib.error.URLError, OSError) as e:
        print(f"error: cannot reach the daemon at {URL} ({getattr(e, 'reason', e)}). Start it: python agentctl.py serve", file=sys.stderr)
        sys.exit(1)
    except (ValueError, KeyError, TypeError) as e:
        print(f"error: bad input or unexpected reply ({e!r})", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
