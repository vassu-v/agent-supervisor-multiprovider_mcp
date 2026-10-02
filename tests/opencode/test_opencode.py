import os, sys, time, tempfile, threading, subprocess
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from orch.adapters.opencode import OpenCodeAdapter
MODEL = "opencode/big-pickle"
T0 = time.time()


class Rec:
    def __init__(s):
        s.ev = []
        s.cv = threading.Condition()

    def __call__(s, e):
        e = dict(e)
        e["t"] = round(time.time() - T0, 1)
        with s.cv:
            s.ev.append(e)
            s.cv.notify_all()

    def wait(s, pred, timeout=120, start=0):
        end = time.time() + timeout
        with s.cv:
            while time.time() < end:
                for i, e in enumerate(s.ev[start:], start):
                    if pred(e):
                        return i
                s.cv.wait(0.5)
        return None

    def results(s):
        return [e for e in s.ev if e["type"] == "result"]


def mk(cwd=None):
    r = Rec()
    a = OpenCodeAdapter("t", cwd or tempfile.mkdtemp(), MODEL, r)
    a.start()
    return a, r


def turn(a, r, text, mode="queue", timeout=120):
    n = len(r.results())
    a.send(text, mode)
    r.wait(lambda e: e["type"] == "result", timeout, len(r.ev)) if False else None
    end = time.time() + timeout
    while time.time() < end and len(r.results()) <= n:
        time.sleep(0.3)
    return r.results()[-1]["text"] if len(r.results()) > n else None


def t1_2():
    a, r = mk()
    try:
        print("1 reply:", turn(a, r, "Reply with exactly: PONG"))
        turn(a, r, "Remember the code word BANANA42.")
        print("2 memory:", turn(a, r, "What was the code word?"))
        print("  session:", a.session_id, "usage evs:", sum(e["type"] == "usage" for e in r.ev))
    finally:
        a.kill()


def t3():
    a, r = mk()
    try:
        a.send("Run this bash command: python -c \"import time;time.sleep(25)\" then say DONE1")
        i = r.wait(lambda e: e["type"] == "tool_start", 60)
        print("3 tool_start t=", r.ev[i]["t"])
        time.sleep(5)
        print("  mid sent t=", round(time.time() - T0, 1))
        a.send("Also, after that, say MIDMSG", "steer")
        r.wait(lambda e: e["type"] == "tool_end", 60)
        time.sleep(30)
        print("  tool_end t=", [e["t"] for e in r.ev if e["type"] == "tool_end"])
        print("  results:", [(e["t"], e["text"][:80]) for e in r.results()])
    finally:
        a.kill()


def pyprocs():
    o = subprocess.run(["powershell", "-c", "@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ? {$_.CommandLine -like '*sleep(120)*'}).Count"],
                       capture_output=True, text=True).stdout.strip()
    return o or "0"


def t4():
    a, r = mk()
    try:
        turn(a, r, "Remember the code word KIWI7.")
        a.send("Run this bash command: python -c \"import time;time.sleep(120)\" (timeout 200000 ms)")
        r.wait(lambda e: e["type"] == "tool_start", 60)
        time.sleep(3)
        print("4 sleepers before abort:", pyprocs())
        t = time.time()
        n = len(r.ev)
        a.interrupt()
        j = r.wait(lambda e: e["type"] == "result", 30, n)
        print("  abort->result in %.1fs:" % (time.time() - t), r.ev[j] if j is not None else None)
        time.sleep(3)
        print("  alive:", a.alive, "sleepers after abort:", pyprocs())
        print("  follow-up:", turn(a, r, "What was the code word? One word."))
    finally:
        a.kill()
        time.sleep(1)
        print("  sleepers after kill:", pyprocs())


def t5():
    d1, d2 = tempfile.mkdtemp(), tempfile.mkdtemp()
    a, ra = mk(d1)
    b, rb = mk(d2)
    try:
        for a_, n in ((a, "A"), (b, "B")):
            a_.send("Create a file named who.txt containing exactly %s using bash, then say OK." % n)
        ra.wait(lambda e: e["type"] == "result", 120)
        rb.wait(lambda e: e["type"] == "result", 120)
        for d, n in ((d1, "A"), (d2, "B")):
            p = os.path.join(d, "who.txt")
            print("5", n, open(p).read().strip() if os.path.exists(p) else "MISSING", sorted(os.listdir(d)))
    finally:
        a.kill()
        b.kill()


if __name__ == "__main__":
    for f in (t1_2, t3, t4, t5):
        f()
