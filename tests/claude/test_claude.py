import os, sys, time, threading, subprocess
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from orch.adapters.claude import ClaudeAdapter

CWD = os.path.join(ROOT, "tests", "claude", "work")
os.makedirs(CWD, exist_ok=True)
ev, lock, t0 = [], threading.Lock(), time.time()
def emit(e):
    with lock: ev.append((round(time.time() - t0, 1), e))
a = ClaudeAdapter("ct", CWD, "claude-haiku-4-5-20251001", emit)
a.start()

def wait(kind, since, timeout=120, pred=lambda e: True):
    end = time.time() + timeout
    while time.time() < end:
        with lock:
            for i, (t, e) in enumerate(ev[since:], since):
                if e["type"] == kind and pred(e): return i + 1, t, e
        time.sleep(0.2)
    raise TimeoutError(kind)

def sleepers():
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
        "(Get-CimInstance Win32_Process | ? { $_.CommandLine -match 'time.sleep\\(40\\)' -and $_.Name -match 'python' }).Count"],
        capture_output=True, text=True).stdout.strip()
    return int(out or 0)

print("1 simple"); n = len(ev); a.send("Remember the word KIWI. Reply with only OK.")
n, t, e = wait("result", n); print("  ", repr(e["text"]), e["stop"], "session", a.session_id)
print("2 multi-turn + cache"); a.send("What word did I ask you to remember? One word.");
n, t, e = wait("result", n); print("  ", repr(e["text"]))
print("   usage events:", [x[1] for x in ev if x[1]["type"] == "usage"][-2:])
print("3 tool + steer"); m = len(ev)
a.send("Run this bash command and wait for it: python -c \"import time; time.sleep(25)\"  Then reply DONE.")
wait("tool_start", m); ts = time.time() - t0
a.send("Also, when finished, append the word STEERED after DONE.", "steer")
n2, t2, e2 = wait("result", m, 150); print("   tool started at", round(ts, 1), "result at", t2, repr(e2["text"]))
print("4 interrupt mid long command"); m = len(ev)
a.send("Run this bash command and wait for it: python -c \"import time; time.sleep(40)\"  Then reply DONE.")
wait("tool_start", m, 90); time.sleep(3); print("   sleepers before:", sleepers()); ti = time.time()
a.interrupt()
n3, t3, e3 = wait("result", m, 60); print("   result after", round(time.time() - ti, 1), "s stop=", e3["stop"], "alive=", a.alive,
      "restarted=", any(x[1].get("restarted") for x in ev[m:]))
time.sleep(2); print("   sleepers after:", sleepers())
a.send("What was the word I asked you to remember at the very start? One word.")
n4, t4, e4 = wait("result", len(ev) - 1 if False else m, 90, lambda e: e["stop"] == "end_turn")
print("   follow-up:", repr(e4["text"]))
a.kill(); print("killed; alive=", a.alive)
