"""Mock codex app-server (protocol per generated schema). Prompt keywords: 'slow' (long turn),
'approve' (emit approval request mid-turn)."""
import json, sys, threading, time

out_lock = threading.Lock()


def send(o):
    with out_lock:
        sys.stdout.write(json.dumps(o) + "\n")
        sys.stdout.flush()


def note(m, p):
    send({"jsonrpc": "2.0", "method": m, "params": p})


state = {"turn": 0, "active": None, "steers": [], "interrupt": False,
         "approval_ev": threading.Event(), "approval_res": None}
TH = "thread-1"


def run_turn(tid, text):
    note("turn/started", {"threadId": TH, "turn": {"id": tid, "items": [], "status": "inProgress"}})
    if "approve" in text:
        send({"jsonrpc": "2.0", "id": 9000, "method": "item/commandExecution/requestApproval",
              "params": {"threadId": TH, "turnId": tid, "itemId": "i-ap", "startedAtMs": 0, "command": "rm x"}})
        state["approval_ev"].wait(5)
        note("item/agentMessage/delta", {"threadId": TH, "turnId": tid, "itemId": "m",
                                         "delta": "approval=%s;" % json.dumps(state["approval_res"])})
    item = {"type": "commandExecution", "id": "c1", "command": "echo hi", "commandActions": [],
            "cwd": ".", "status": "inProgress"}
    note("item/started", {"threadId": TH, "turnId": tid, "startedAtMs": 0, "item": item})
    note("item/agentMessage/delta", {"threadId": TH, "turnId": tid, "itemId": "m", "delta": "hello "})
    item = dict(item, status="completed", exitCode=0, aggregatedOutput="hi\n")
    note("item/completed", {"threadId": TH, "turnId": tid, "item": item})
    n = 100 if "slow" in text else 2
    for _ in range(n):
        time.sleep(0.05)
        if state["interrupt"]:
            break
    status = "interrupted" if state["interrupt"] else "completed"
    state["interrupt"] = False
    tu = {"inputTokens": 10, "outputTokens": 5, "cachedInputTokens": 2, "reasoningOutputTokens": 0, "totalTokens": 15}
    note("thread/tokenUsage/updated", {"threadId": TH, "turnId": tid, "tokenUsage": {"last": tu, "total": tu}})
    note("item/agentMessage/delta", {"threadId": TH, "turnId": tid, "itemId": "m",
                                     "delta": "done" + "".join(" steer:" + s for s in state["steers"])})
    state["active"] = None
    note("turn/completed", {"threadId": TH, "turn": {"id": tid, "items": [], "status": status}})


for line in sys.stdin:
    m = json.loads(line)
    if "method" not in m:  # response to our server request
        if m.get("id") == 9000:
            state["approval_res"] = m.get("result")
            state["approval_ev"].set()
        continue
    meth, p, rid = m["method"], m.get("params") or {}, m.get("id")
    if rid is None:
        continue
    if meth == "initialize":
        send({"jsonrpc": "2.0", "id": rid, "result": {"userAgent": "mock"}})
    elif meth == "thread/start":
        send({"jsonrpc": "2.0", "id": rid, "result": {"thread": {"id": TH}, "model": "mock"}})
    elif meth == "turn/start":
        state["turn"] += 1
        tid = "turn-%d" % state["turn"]
        state["active"] = tid
        text = p["input"][0]["text"]
        send({"jsonrpc": "2.0", "id": rid, "result": {"turn": {"id": tid, "items": [], "status": "inProgress"}}})
        threading.Thread(target=run_turn, args=(tid, text), daemon=True).start()
    elif meth == "turn/steer":
        if p["expectedTurnId"] != state["active"]:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": "turn mismatch"}})
        else:
            state["steers"].append(p["input"][0]["text"])
            send({"jsonrpc": "2.0", "id": rid, "result": {"turnId": state["active"]}})
    elif meth == "turn/interrupt":
        state["interrupt"] = True
        send({"jsonrpc": "2.0", "id": rid, "result": {}})
    else:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": meth}})
