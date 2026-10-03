"""Switchyard QA runner: every offline suite in order, scripted fake provider only (no model turns, no cost).

    python tests/qa.py                 # everything, swarm with 3 distinct seeds
    python tests/qa.py --seeds 5       # swarm with 5 seeds
    python tests/qa.py --quick         # skip swarm and chaos
    python tests/qa.py --real          # reserved (real providers): not implemented

Writes logs/qa/<timestamp>.json (per suite: name, ok, seconds, returncode, tail of output), prints a table, and exits
non-zero if any suite failed. Suites whose file does not exist are reported as skipped.
"""
import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")
TAIL = 3000
TIMEOUT = 1200


def plan(seeds, quick):
    """-> [(name, argv relative to ROOT, required file)]"""
    py = sys.executable
    s = [(f, [py, os.path.join("tests", f)], f) for f in
         ("test_guard.py", "test_http_security.py", "test_board.py", "test_identity.py", "test_workspace.py")]
    s += [(m, [py, "-m", "unittest", "-v", f"tests.{m}"], m + ".py") for m in ("test_adapter_env", "test_fake_adapter")]
    s += [(f, [py, os.path.join("tests", f)], f) for f in ("test_identity_e2e.py", "test_board_e2e.py", "test_review_fixes.py", "test_stale_questions.py")]
    if not quick:
        base = int(time.time()) % 100000
        for k in range(seeds):
            seed = base + k * 7919                     # distinct seeds per run, printed so a failure can be replayed
            s.append((f"test_swarm.py seed={seed}", [py, os.path.join("tests", "test_swarm.py"), "--seed", str(seed)],
                      "test_swarm.py"))
        s.append(("test_chaos.py", [py, os.path.join("tests", "test_chaos.py")], "test_chaos.py"))
    s += [(f, [py, os.path.join("tests", f)], f) for f in ("test_cli_mcp.py", "test_dashboard_static.py")]
    return s


def run_suite(name, argv):
    t0 = time.time()
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    try:
        p = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=TIMEOUT)
        out, rc = (p.stdout or "") + (p.stderr or ""), p.returncode
    except subprocess.TimeoutExpired as e:
        out = ((e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")) + \
              f"\n[qa] TIMEOUT after {TIMEOUT}s"
        rc = -1
    return {"name": name, "ok": rc == 0, "returncode": rc, "seconds": round(time.time() - t0, 1), "tail": out[-TAIL:]}


def summary_line(tail):
    """Best-effort one-liner from unittest / custom script output."""
    for line in reversed(tail.splitlines()):
        line = line.strip()
        if line.startswith(("OK", "FAILED", "Ran ")) or "failure(s)" in line or line.startswith("[swarm]"):
            return line[:70]
    return ""


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seeds", type=int, default=3, help="number of distinct swarm seeds (default 3)")
    ap.add_argument("--quick", action="store_true", help="skip the swarm and chaos suites")
    ap.add_argument("--real", action="store_true", help="reserved: run against real providers")
    a = ap.parse_args()
    if a.real:
        print("--real: not implemented (QA uses the scripted fake provider only)")
        return 2
    results = []
    for name, argv, fname in plan(max(1, a.seeds), a.quick):
        if not os.path.exists(os.path.join(TESTS, fname)):
            results.append({"name": name, "ok": True, "skipped": True, "seconds": 0, "returncode": None,
                            "tail": "file not found: skipped"})
            print(f"  skip  {name} (missing)", flush=True)
            continue
        print(f"  run   {name} ...", end="", flush=True)
        r = run_suite(name, argv)
        results.append(r)
        print(f" {'ok' if r['ok'] else 'FAIL'} ({r['seconds']}s)", flush=True)

    os.makedirs(os.path.join(ROOT, "logs", "qa"), exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = os.path.join(ROOT, "logs", "qa", f"{stamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"ts": stamp, "python": sys.version.split()[0], "seeds": a.seeds, "quick": a.quick,
                   "suites": results}, f, indent=2)

    w = max(len(r["name"]) for r in results)
    print("\n" + "suite".ljust(w) + "  result  secs    summary")
    print("-" * (w + 60))
    for r in results:
        res = "skip" if r.get("skipped") else ("ok" if r["ok"] else "FAIL")
        print(f"{r['name'].ljust(w)}  {res:<6}  {r['seconds']:>6}  {summary_line(r['tail'])}")
    bad = [r for r in results if not r["ok"]]
    print(f"\n{len(results) - len(bad)}/{len(results)} suites passed; log: {path}")
    for r in bad:
        print(f"\n===== {r['name']} (rc={r['returncode']}) tail =====\n{r['tail'][-1500:]}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
