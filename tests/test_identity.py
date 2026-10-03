import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from orch import identity as I  # noqa: E402

DENIED = ["/api/resolve", "/api/provider", "/api/send", "/api/interrupt", "/api/escalations", "/api/audit"]


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class TokenTests(unittest.TestCase):
    def test_mint_verify(self):
        r = I.TokenRegistry()
        t = r.mint("a1")
        self.assertEqual(len(t), 48)
        int(t, 16)
        self.assertEqual(r.verify(t), "a1")

    def test_unique_tokens(self):
        r = I.TokenRegistry()
        self.assertEqual(len({r.mint("a%d" % i) for i in range(200)}), 200)

    def test_revoke(self):
        r = I.TokenRegistry()
        t = r.mint("a1")
        t2 = r.mint("a2")
        self.assertTrue(r.revoke("a1"))
        self.assertIsNone(r.verify(t))
        self.assertEqual(r.verify(t2), "a2")
        self.assertFalse(r.revoke("a1"))
        self.assertFalse(r.revoke("nope"))

    def test_remint_invalidates_old(self):
        r = I.TokenRegistry()
        old = r.mint("a1")
        new = r.mint("a1")
        self.assertIsNone(r.verify(old))
        self.assertEqual(r.verify(new), "a1")

    def test_revoke_all(self):
        r = I.TokenRegistry()
        ts = [r.mint("a%d" % i) for i in range(5)]
        r.revoke_all()
        self.assertTrue(all(r.verify(t) is None for t in ts))

    def test_never_accepted_after_revoke_even_remint_other(self):
        r = I.TokenRegistry()
        t = r.mint("a1")
        r.revoke("a1")
        r.mint("a1")
        self.assertIsNone(r.verify(t))

    def test_bad_tokens_rejected(self):
        r = I.TokenRegistry()
        t = r.mint("a1")
        for bad in (None, "", " ", 0, b"x", [], t[:-1], t[:10], t + "0", t.upper() if t != t.upper() else t + "x",
                    " " + t, t + "\n", t[:47] + "é", "0" * 48, 123456):
            self.assertIsNone(r.verify(bad), repr(bad))
        self.assertIsNone(I.TokenRegistry().verify(t))

    def test_mint_requires_aid(self):
        r = I.TokenRegistry()
        for bad in ("", None, 5):
            with self.assertRaises(ValueError):
                r.mint(bad)

    def test_plain_tokens_not_stored(self):
        r = I.TokenRegistry()
        t = r.mint("a1")
        self.assertNotIn(t, r._by_hash)
        self.assertNotIn(t, repr(vars(r)))

    def test_timing_safe_path_used(self):
        r = I.TokenRegistry()
        t = r.mint("a1")
        with mock.patch.object(I.hmac, "compare_digest", wraps=I.hmac.compare_digest) as cd:
            self.assertEqual(r.verify(t), "a1")
            self.assertTrue(cd.called)
        # a token that doesn't exist must not be compared against a real token by plain ==
        self.assertIsNone(r.verify("f" * 48))

    def test_compare_digest_failure_rejects(self):
        r = I.TokenRegistry()
        t = r.mint("a1")
        with mock.patch.object(I.hmac, "compare_digest", return_value=False):
            self.assertIsNone(r.verify(t))

    def test_threaded(self):
        r = I.TokenRegistry()
        errs = []

        def w(n):
            try:
                for i in range(100):
                    aid = "t%d-%d" % (n, i)
                    t = r.mint(aid)
                    assert r.verify(t) == aid
                    r.revoke(aid)
                    assert r.verify(t) is None
            except Exception as e:  # noqa
                errs.append(e)
        th = [threading.Thread(target=w, args=(n,)) for n in range(10)]
        [x.start() for x in th]
        [x.join() for x in th]
        self.assertEqual(errs, [])


class ScopeTests(unittest.TestCase):
    def test_allowed_exact_set(self):
        self.assertEqual(len(I.AGENT_ALLOWED), 19)
        for p in ("/api/spawn", "/api/stop", "/api/board", "/api/declare", "/api/health"):
            self.assertTrue(I.agent_allowed(p), p)

    def test_denied(self):
        for p in DENIED:
            self.assertFalse(I.agent_allowed(p), p)
            self.assertNotIn(p, I.AGENT_ALLOWED)

    def test_query_ignored_but_tricks_rejected(self):
        self.assertTrue(I.agent_allowed("/api/list?ws=x&all=1"))
        for p in ("/api/list/", "/api/List", "/API/list", "/api/list/../resolve", "/api/listx", " /api/list",
                  "/api/%6Cist", "//api/list", "", None, 5, "/api/resolve?x=/api/list", "/api/list\x00/api/resolve",
                  "/api/list#/api/resolve"[:0] or "/api"):
            self.assertFalse(I.agent_allowed(p), repr(p))

    def test_frozen(self):
        with self.assertRaises(AttributeError):
            I.AGENT_ALLOWED.add("/api/audit")


class IdentityStringTests(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(I.identity_string("agent", "a1"), "agent:a1")
        self.assertEqual(I.identity_string("session", "cursor", "win1"), "session:cursor/win1")
        self.assertEqual(I.identity_string("session", "cursor"), "session:cursor")
        self.assertEqual(I.identity_string("external"), "external")

    def test_spoofing(self):
        s = I.identity_string("session", "agent:a1", "x/../agent:a2")
        self.assertTrue(s.startswith("session:"))
        self.assertEqual(s.count(":"), 1)
        self.assertEqual(s.count("/"), 1)
        s = I.identity_string("agent", "a1\nagent:a2")
        self.assertNotIn("\n", s)
        self.assertEqual(s.count(":"), 1)
        self.assertEqual(I.identity_string("session", "", ""), "session:unknown")
        self.assertEqual(I.identity_string("agent", None), "agent:unknown")
        self.assertLessEqual(len(I.identity_string("session", "x" * 500, "y" * 500)), 8 + 40 + 1 + 40)

    def test_unknown_kind(self):
        for k in ("admin", "", None, "Agent"):
            with self.assertRaises(ValueError):
                I.identity_string(k, "x")


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.c = FakeClock()
        self.r = I.SessionRegistry(clock=self.c)

    def test_hello(self):
        s = self.r.hello("claude", "L", "ws-1234abcd", 42)
        self.assertEqual(len(s["id"]), 32)
        int(s["id"], 16)
        self.assertEqual((s["client"], s["label"], s["workspace"], s["pid"]), ("claude", "L", "ws-1234abcd", 42))
        self.assertNotEqual(s["id"], self.r.hello("claude")["id"])

    def test_bad_pid_dropped(self):
        for p in ("12", True, 1.5, None):
            self.assertIsNone(self.r.hello("c", pid=p)["pid"])

    def test_returned_dict_is_copy(self):
        s = self.r.hello("c")
        s["client"] = "evil"
        self.assertEqual(self.r.get(s["id"])["client"], "c")

    def test_expiry_and_touch(self):
        s = self.r.hello("c")
        self.c.t += 119
        self.assertTrue(self.r.touch(s["id"]))
        self.c.t += 119
        self.assertEqual(len(self.r.list_live()), 1)  # touched, still live
        self.c.t += 2
        self.assertEqual(self.r.list_live(), [])
        self.assertFalse(self.r.touch(s["id"]))  # expired stays dead
        self.assertIsNone(self.r.get(s["id"]))

    def test_exact_boundary_is_live(self):
        s = self.r.hello("c")
        self.c.t += 120
        self.assertIsNotNone(self.r.get(s["id"]))
        self.c.t += 0.01
        self.assertIsNone(self.r.get(s["id"]))

    def test_list_live_explicit_now(self):
        s = self.r.hello("c")
        self.assertEqual(len(self.r.list_live(now=self.c.t + 50)), 1)
        self.assertEqual(self.r.list_live(now=self.c.t + 500), [])

    def test_touch_unknown(self):
        for bad in ("nope", None, 5, ""):
            self.assertFalse(self.r.touch(bad))
            self.assertIsNone(self.r.get(bad))

    def test_drop(self):
        s = self.r.hello("c")
        self.assertTrue(self.r.drop(s["id"]))
        self.assertFalse(self.r.drop(s["id"]))
        self.assertIsNone(self.r.get(s["id"]))

    def test_hammer_20_threads(self):
        errs, ids = [], []
        lock = threading.Lock()

        def w(n):
            try:
                for i in range(100):
                    s = self.r.hello("c%d" % n, "l%d" % i, "ws", n)
                    assert self.r.touch(s["id"])
                    assert self.r.get(s["id"])["client"] == "c%d" % n
                    self.r.list_live()
                    with lock:
                        ids.append(s["id"])
                    if i % 3 == 0:
                        assert self.r.drop(s["id"])
            except Exception as e:  # noqa
                errs.append(e)
        th = [threading.Thread(target=w, args=(n,)) for n in range(20)]
        [x.start() for x in th]
        [x.join() for x in th]
        self.assertEqual(errs, [])
        self.assertEqual(len(set(ids)), 2000)
        self.assertEqual(len(self.r.list_live()), 2000 - 20 * 34)


class HierarchyTests(unittest.TestCase):
    def chain(self, n, status="busy"):
        a = {"a1": {"parent": None, "status": status}}
        for i in range(2, n + 1):
            a["a%d" % i] = {"parent": "a%d" % (i - 1), "status": status}
        return a

    def test_depth(self):
        a = self.chain(3)
        self.assertEqual([I.depth(a, "a1"), I.depth(a, "a2"), I.depth(a, "a3")], [1, 2, 3])
        self.assertEqual(I.depth(a, "ghost"), 0)

    def test_depth_orphan_and_cycle(self):
        a = {"x": {"parent": "gone", "status": "idle"}}
        self.assertEqual(I.depth(a, "x"), 1)
        a = {"p": {"parent": "q"}, "q": {"parent": "p"}}
        self.assertEqual(I.depth(a, "p"), 2)  # terminates

    def test_depth_limit(self):
        a = self.chain(3)
        ok, why = I.can_spawn(a, "a3")
        self.assertFalse(ok)
        self.assertIn("depth", why)
        self.assertTrue(I.can_spawn(a, "a2")[0])
        self.assertTrue(I.can_spawn(a, "a1")[0])
        self.assertFalse(I.can_spawn(a, "a2", max_depth=2)[0])

    def test_child_limit_counts_only_live(self):
        a = {"p": {"parent": None, "status": "idle"}}
        for i in range(5):
            a["c%d" % i] = {"parent": "p", "status": "busy" if i % 2 else "idle"}
        ok, why = I.can_spawn(a, "p")
        self.assertFalse(ok)
        self.assertIn("child", why)
        a["c0"]["status"] = "dead"
        self.assertTrue(I.can_spawn(a, "p")[0])
        self.assertEqual(len(I.live_children(a, "p")), 4)
        a["c1"]["status"] = "stopped"
        a["c2"]["status"] = "error"
        self.assertEqual(len(I.live_children(a, "p")), 2)

    def test_dead_children_dont_block_many_spawns(self):
        a = {"p": {"parent": None, "status": "idle"}}
        for i in range(50):
            a["d%d" % i] = {"parent": "p", "status": "dead"}
        self.assertTrue(I.can_spawn(a, "p")[0])

    def test_unknown_or_dead_caller(self):
        a = self.chain(1)
        self.assertFalse(I.can_spawn(a, "ghost")[0])
        self.assertFalse(I.can_spawn(a, None)[0])
        a["a1"]["status"] = "dead"
        self.assertFalse(I.can_spawn(a, "a1")[0])

    def test_custom_max_children(self):
        a = {"p": {"parent": None, "status": "idle"}, "c": {"parent": "p", "status": "idle"}}
        self.assertFalse(I.can_spawn(a, "p", max_children=1)[0])
        self.assertTrue(I.can_spawn(a, "p", max_children=2)[0])


if __name__ == "__main__":
    unittest.main()
