"""Swarm test: many scripted fake agents hammering one workspace/directory, plus a small second workspace.

Run: python tests/test_swarm.py [--agents N] [--seed S]      (env SWARM_AGENTS / SWARM_SEED work too; defaults 20 / 1)

Asserts: every non-crashing agent settles idle, the crashing one is dead with dead/error auto posts, board ids are unique
and strictly increasing, no digest ever contains the recipient's own posts or another workspace's posts, no post is
delivered twice to the same agent, per-agent turn counts are bounded (no wake storms), AGENTS.md is created once per
directory and intact, no core_error/board_error audit entries, and /api/health + /api/list still answer in < 1 s.
"""
import argparse
import collections
import json
import os
import random
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tests.harness import api, spawn_fake, start_daemon  # noqa: E402
from tests.test_board_e2e import (audit_file, autos, board, frame_items, http, info, mkrepo, send_script,  # noqa: E402
                                  settled, split_frame, ws_of)

OPTS = {"agents": int(os.environ.get("SWARM_AGENTS", "20")), "seed": int(os.environ.get("SWARM_SEED", "1"))}
SLACK = 3                       # constant allowance per agent in the turn bound


def read_jsonl_tolerant(path):
    """-> (records, n_corrupt). Several adapters append to one shared file; count (do not hide) torn lines."""
    good, bad = [], 0
    if not os.path.exists(path):
        return good, bad
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                good.append(json.loads(line))
            except ValueError:
                bad += 1
    return good, bad


class Swarm(unittest.TestCase):
    def setUp(self):
        self.n, self.seed = OPTS["agents"], OPTS["seed"]
        self.rng = random.Random(self.seed)
        self.home = tempfile.mkdtemp(prefix="sy-swarm-home-")
        os.makedirs(os.path.join(self.home, "orch"))
        with open(os.path.join(self.home, "orch", "config.json"), "w") as f:
            json.dump({"max_concurrent": 500}, f)
        self.tmp = tempfile.mkdtemp(prefix="sy-swarm-")
        self.d = start_daemon(home=self.home)

    def tearDown(self):
        self.d.stop()
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def spawn(self, script, cwd, aid, **kw):
        st, r = spawn_fake(self.d, script, cwd, id=aid, opts={"step_delay": 0.005}, **kw)
        self.assertEqual(st, 200, r)

    def test_swarm(self):
        t0 = time.time()
        rng, seed = self.rng, self.seed
        r1, r2 = mkrepo(self.tmp, "swarm_one"), mkrepo(self.tmp, "swarm_two")
        d1, d2 = os.path.join(r1, "shared"), os.path.join(r2, "shared")
        ws1, ws2 = ws_of(self.d, r1), ws_of(self.d, r2)
        ids1 = [f"s{seed}w1n{i}" for i in range(self.n)]
        ids2 = [f"s{seed}w2n{i}" for i in range(3)]
        crash = rng.choice(ids1)
        candidates = [a for a in ids1 if a != crash]
        parents = rng.sample(candidates, min(2, len(candidates)))
        askers = set(rng.sample(candidates, max(1, self.n // 6)))
        sends = collections.Counter()

        def script(aid, k):
            s = []
            if aid in askers:
                s.append(http("POST", "/api/ask", {"text": f"question from {aid}: which file holds config?"}))
            s.append({"random_walk": {"seed": seed * 100003 + k, "steps": rng.randint(3, 9)}})
            if aid == crash:
                s.append({"crash": True})
            return s

        for k, aid in enumerate(ids1):
            self.spawn(script(aid, k), d1, aid)
        for k, aid in enumerate(ids2):
            self.spawn(script(aid, 1000 + k), d2, aid)
        kids = {}
        for p in parents:                       # children announce `done` -> their parent is woken (or told when idle)
            for j in range(2):
                cid = f"{p}k{j}"
                kids[cid] = p
                self.spawn([{"random_walk": {"seed": seed * 7 + j, "steps": rng.randint(1, 4)}},
                            http("POST", "/api/announce", {"text": f"child {cid} finished", "kind": "done"})],
                           d1, cid, parent=p)
        everyone = ids1 + ids2 + list(kids)
        for aid in everyone:
            settled(self.d, aid, status=("dead",) if aid == crash else ("idle",), timeout=120)

        # answer phase: an agent with spare rate budget answers each open question
        posts1 = board(self.d, ws1)
        count = collections.Counter(p["sender"] for p in posts1)
        for q in [p for p in posts1 if p["kind"] == "question" and p["status"] == "open"]:
            pool = [a for a in ids1 if a != crash and "agent:" + a != q["sender"] and count["agent:" + a] < 5]
            if not pool:
                continue
            who = rng.choice(pool)
            count["agent:" + who] += 1
            sends[who] += 1
            send_script(self.d, who, [http("POST", "/api/answer", {"id": q["id"], "text": f"{who} says: config.toml"})])
        for aid in everyone:
            if aid != crash:
                settled(self.d, aid, timeout=120)
        # let any wake triggered by the last answers land, then settle again
        time.sleep(0.3)
        for aid in everyone:
            if aid != crash:
                settled(self.d, aid, timeout=60)

        # ---------------------------------------------------------------- assertions
        posts1, posts2 = board(self.d, ws1), board(self.d, ws2)
        st = {aid: info(self.d, aid) for aid in everyone}
        for aid in everyone:
            self.assertEqual(st[aid]["status"], "dead" if aid == crash else "idle", aid)
        self.assertTrue(autos(posts1, "dead", crash), "crashed agent has a dead auto post")
        for posts in (posts1, posts2):
            ids = [p["id"] for p in posts]
            self.assertEqual(ids, sorted(set(ids)), "board ids unique and strictly increasing")
        self.assertFalse({p["id"] for p in posts1} & {p["id"] for p in posts2})
        self.assertTrue(all(p["ws"] == ws1 for p in posts1) and all(p["ws"] == ws2 for p in posts2))
        id_ws = {p["id"]: ws1 for p in posts1} | {p["id"]: ws2 for p in posts2}
        sender_of = {p["id"]: p["sender"] for p in posts1 + posts2}

        inbox1, bad1 = read_jsonl_tolerant(os.path.join(d1, ".fake_inbox.jsonl"))
        inbox2, bad2 = read_jsonl_tolerant(os.path.join(d2, ".fake_inbox.jsonl"))
        seen = collections.defaultdict(list)
        frames = 0
        for recs, ws in ((inbox1, ws1), (inbox2, ws2)):
            for m in recs:
                fr, _ = split_frame(m["text"])
                if not fr:
                    continue
                frames += 1
                for pid, sender, _tag in frame_items(fr):
                    self.assertNotEqual(sender, "agent:" + m["agent"], f"{m['agent']} was shown its own post #{pid}")
                    self.assertEqual(id_ws.get(pid), ws, f"post #{pid} delivered to {m['agent']} in another workspace")
                    self.assertEqual(sender_of.get(pid), sender, f"post #{pid} rendered with a different sender")
                    seen[m["agent"]].append(pid)
        dups = {a: [p for p, c in collections.Counter(v).items() if c > 1] for a, v in seen.items()}
        dups = {a: v for a, v in dups.items() if v}
        self.assertFalse(dups, f"posts delivered twice to the same agent: {dups}")

        allposts = posts1 + posts2
        overs = []
        for aid in everyone:
            my_kids = [c for c, p in kids.items() if p == aid]
            child_terminal = sum(1 for p in allposts if
                                 (p["kind"] == "done" and p["sender"] in ["agent:" + c for c in my_kids]) or
                                 (p["kind"] == "auto" and p.get("event") in ("stopped", "dead", "error")
                                  and p.get("about") in my_kids))
            answers = sum(1 for p in allposts if p["kind"] == "question" and p["sender"] == "agent:" + aid
                          and p["status"] == "answered")
            bound = 1 + child_terminal + answers + sends[aid] + SLACK
            if st[aid]["turns"] > bound:
                overs.append((aid, st[aid]["turns"], bound))
        self.assertFalse(overs, f"wake storm: (agent, turns, bound) {overs}")

        for dd in (d1, d2):
            p = os.path.join(dd, "AGENTS.md")
            self.assertTrue(os.path.exists(p))
            with open(p, encoding="utf-8") as f:
                txt = f.read()
            self.assertTrue(txt.startswith("# AGENTS.md"), txt[:200])
            self.assertEqual(txt.count("# AGENTS.md"), 1, "AGENTS.md written more than once")
            self.assertEqual(txt.count("## Agent notes"), 1)
        self.assertEqual(sorted(os.listdir(self.tmp)), ["swarm_one", "swarm_two"])

        errs = [a for a in audit_file(self.d) if a.get("kind") in ("core_error", "board_error")]
        self.assertFalse(errs, f"daemon errors in audit: {errs[:5]}")

        for path in ("/api/health", "/api/list", "/api/list?all=1&tree=1"):
            t = time.time()
            s, _ = api(self.d, "GET", path)
            dt = time.time() - t
            self.assertEqual(s, 200)
            self.assertLess(dt, 1.0, f"{path} took {dt:.2f}s")

        turns = [st[a]["turns"] for a in everyone]
        print(f"\n[swarm] seed={seed} agents={len(everyone)} (ws1 {len(ids1)}+{len(kids)} kids, ws2 {len(ids2)}) "
              f"crash={crash} posts ws1={len(posts1)} ws2={len(posts2)} questions="
              f"{sum(1 for p in allposts if p['kind'] == 'question')} answered="
              f"{sum(1 for p in allposts if p['kind'] == 'question' and p['status'] == 'answered')} "
              f"deliveries={len(inbox1) + len(inbox2)} frames={frames} torn_inbox_lines={bad1 + bad2} "
              f"turns max={max(turns)} mean={sum(turns) / len(turns):.2f} secs={time.time() - t0:.1f}", flush=True)
        if bad1 + bad2:
            print(f"[swarm] WARNING: {bad1 + bad2} torn line(s) in the shared .fake_inbox.jsonl (fake adapter appends "
                  f"from many threads to one file); those deliveries were not checked", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--agents", type=int, default=OPTS["agents"])
    ap.add_argument("--seed", type=int, default=OPTS["seed"])
    a, rest = ap.parse_known_args()
    OPTS.update(agents=a.agents, seed=a.seed)
    unittest.main(argv=[sys.argv[0]] + rest, verbosity=2)
