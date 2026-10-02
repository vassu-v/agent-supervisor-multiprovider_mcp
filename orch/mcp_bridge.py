"""MCP stdio server exposing Switchyard as tools. Add to any MCP client:
  command: python   args: ["<repo>/orch/mcp_bridge.py"]
It is a thin bridge to the running daemon (agentctl.py serve) so many clients share ONE fleet."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import agentctl as cli  # noqa: E402

for _s in (sys.stdin, sys.stdout):                 # Windows defaults to a legacy codepage; JSON-RPC is UTF-8
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

S = {"type": "string"}
MODES = ["queue", "steer", "interrupt"]
TIERS = "trivial | bulk | standard | hard | review"

# name -> (description, properties, required, daemon path, fixed/derived-args hook)
TOOLS = {
    "agent_spawn": (
        "Start a coding agent working in directory `cwd`. It runs with permission prompts off, confined to that directory, so give "
        "each agent its own directory. The daemon writes an AGENTS.md (shared notes) into it if missing. `cwd` is created if its "
        "parent exists. provider='auto' (default) routes by `tier`: " + TIERS + " (default standard; tasks mentioning auth, secrets, "
        "payment, production or delete are bumped to `hard`, which routes to the costly Claude track and marks the result as "
        "needing review). Name a provider+model explicitly to control cost. Returns the agent id and the routing decision; poll "
        "agent_list / agent_tail, the agent works in the background.",
        {"task": {**S, "description": "What the agent should do. Give a verifiable outcome."},
         "cwd": {**S, "description": "Directory the agent works in (absolute or relative to the daemon)."},
         "provider": {**S, "description": "auto | agy | claude | opencode | codex. Must be currently available (see providers_list)."},
         "model": {**S, "description": "Exact model id from models_list (or an alias such as 'sonnet'). Omit to let routing choose."},
         "tier": {**S, "description": "Only used with provider=auto. " + TIERS},
         "id": {**S, "description": "Optional name for the agent; generated if omitted."}},
        ["task", "cwd"], "/api/spawn"),
    "agent_send": (
        "Send a message to a running agent. mode='queue' (default): delivered after its current turn finishes. mode='steer': injected "
        "mid-turn where the provider supports it (claude, codex); otherwise it is queued and the reply says so. mode='interrupt': "
        "cancel the current turn now and deliver this message as the new direction; the session and its context are kept and "
        "already-queued messages still run afterwards. Agents act on any text they receive, so never forward untrusted text.",
        {"id": S, "msg": S, "mode": {**S, "enum": MODES}}, ["id", "msg"], "/api/send"),
    "agent_list": (
        "List all agents (a JSON array). Fields: id, provider, model, cwd, tier, status (starting|busy|idle|dead), turns, queued, "
        "usage (input/output/cache_read tokens), restarts, caps, needs_review (hard-tier work awaiting your verification), "
        "stopped_by, last_text. Stopped agents stay listed as dead.", {}, [], "/api/list"),
    "agent_tail": ("The last N normalised events of an agent (text, tool calls, results, guard decisions): a live view of what it is doing.",
                   {"id": S, "n": {"type": "integer", "description": "default 15"}}, ["id"], "/api/tail"),
    "agent_result": ("Every turn of an agent: the prompt, the response, status and duration, plus token usage.", {"id": S}, ["id"], "/api/result"),
    "agent_interrupt": ("Cancel the agent's current turn without sending anything new. Session and context are kept (providers without "
                        "a native cancel restart the process on the same conversation; the reply says which).", {"id": S}, ["id"], "/api/interrupt"),
    "agent_stop": ("Stop an agent for good. A reason is REQUIRED and is logged with who stopped it. Use it on an agent that is doing "
                   "something harmful or that you no longer need.", {"id": S, "reason": S}, ["id", "reason"], "/api/stop"),
    "escalations": ("Pending escalations: risky actions the guard paused an agent on (git push, external POSTs, writes outside its "
                    "directory, secrets...). Each needs an allow or deny via escalation_resolve.", {}, [], "/api/escalations"),
    "escalation_resolve": ("Decide an escalation. decision='allow' tells the agent to continue; 'deny' tells it not to and to choose a "
                           "safe alternative. This is how a paused agent gets unblocked.",
                           {"escalation": S, "decision": {**S, "enum": ["allow", "deny"]}, "note": S}, ["escalation", "decision"], "/api/resolve"),
    "route_preview": ("Show which provider and model a task would be routed to (and why) without starting anything.",
                      {"task": S, "tier": {**S, "description": TIERS}}, ["task"], "/api/route"),
    "providers_list": ("Which providers are ready, switched off, not installed or need login. Small by default; "
                       "include_models=true embeds every model list (can be large).",
                       {"include_models": {"type": "boolean"}}, [], "/api/providers"),
    "provider_set": ("Switch a provider on or off. A provider that is off is never routed to and agents are told it is unavailable.",
                     {"name": {**S, "enum": ["agy", "claude", "opencode", "codex"]}, "enabled": {"type": "boolean"}},
                     ["name", "enabled"], "/api/provider"),
    "models_list": ("Models a provider reports right now (discovered live from its CLI, cached ~10 min; not hardcoded). Output is "
                    "bounded: use `filter` (substring, e.g. 'free' or 'flash') and `limit` (default 50); the reply gives total/matched/truncated. "
                    "refresh=true forces a re-query.",
                    {"provider": S, "filter": S, "limit": {"type": "integer"}, "refresh": {"type": "boolean"}}, [], "/api/models"),
}


def briefing():
    try:
        return ("Switchyard fleet: runs coding agents from several providers behind one interface.\n"
                "Provider availability right now (discovered, not assumed):\n" + cli.call("/api/summary")["text"] + "\n"
                "Cost: opencode models whose id ends in '-free' are free; treat claude (and the `hard` tier, which routes to it) and "
                "codex as the costly tracks unless the user says otherwise. Prefer provider='auto' with tier bulk/standard for routine "
                "work. Escalations (risky actions the guard paused) need your allow/deny. Use models_list for exact model ids.")
    except Exception:
        return "Switchyard daemon is not running. Start it: python agentctl.py serve (from the Switchyard repo)."


def reply(i, result=None, error=None):
    m = {"jsonrpc": "2.0", "id": i}
    m["error" if error else "result"] = error or result
    sys.stdout.write(json.dumps(m) + "\n")
    sys.stdout.flush()


def text_result(i, obj, is_error=False):
    reply(i, {"content": [{"type": "text", "text": obj if isinstance(obj, str) else json.dumps(obj, indent=1)}], "isError": is_error})


def call_tool(i, name, args):
    if name not in TOOLS:
        return reply(i, error={"code": -32602, "message": f"unknown tool {name!r}; have {sorted(TOOLS)}"})
    desc, props, required, path = TOOLS[name]
    missing = [r for r in required if args.get(r) in (None, "")]
    if missing:
        return text_result(i, f"missing required argument(s): {', '.join(missing)}", True)
    for k, spec in props.items():
        if k in args and "enum" in spec and args[k] not in spec["enum"]:
            return text_result(i, f"{k} must be one of {spec['enum']} (got {args[k]!r})", True)
    args = dict(args)
    if name == "providers_list":
        args["models"] = "1" if args.pop("include_models", False) else "0"
    try:
        out = cli.call(path, **args)
    except Exception as e:
        return text_result(i, f"Switchyard daemon unreachable ({e!r}). Start it: python agentctl.py serve", True)
    text_result(i, out, isinstance(out, dict) and "error" in out)


def handle(msg):
    if not isinstance(msg, dict):
        return reply(None, error={"code": -32600, "message": "batches / non-object requests are not supported"})
    m, i = msg.get("method"), msg.get("id")
    if m == "initialize":
        reply(i, {"protocolVersion": (msg.get("params") or {}).get("protocolVersion", "2024-11-05"), "capabilities": {"tools": {}},
                  "serverInfo": {"name": "switchyard", "version": "0.2"}, "instructions": briefing()})
    elif m == "tools/list":
        reply(i, {"tools": [{"name": n, "description": d, "inputSchema": {"type": "object", "properties": p, "required": r}}
                            for n, (d, p, r, _) in TOOLS.items()]})
    elif m == "tools/call":
        params = msg.get("params") or {}
        call_tool(i, params.get("name"), params.get("arguments") or {})
    elif m == "ping" and i is not None:
        reply(i, {})
    elif i is not None:
        reply(i, error={"code": -32601, "message": f"unknown method {m}"})


for line in sys.stdin:
    if not line.strip():
        continue
    try:
        msg = json.loads(line)
    except ValueError:
        reply(None, error={"code": -32700, "message": "parse error"})
        continue
    try:
        handle(msg)
    except Exception as e:                           # one bad message must never take the bridge down
        reply(msg.get("id") if isinstance(msg, dict) else None, error={"code": -32603, "message": f"internal error: {e!r}"})
