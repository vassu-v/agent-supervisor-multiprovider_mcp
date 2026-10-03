import os
import sys
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from orch import workspace as W  # noqa: E402


def mk(*parts):
    p = os.path.join(*parts)
    os.makedirs(p, exist_ok=True)
    return p


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="swy ws "))  # space in path
        self.env = {"USERPROFILE": os.path.join(self.tmp, "fakehome"), "HOME": os.path.join(self.tmp, "fakehome")}
        mk(self.env["HOME"])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def repo(self, name="proj"):
        r = mk(self.tmp, name)
        mk(r, ".git")
        return r

    def test_git_dir_and_subdir(self):
        r = self.repo()
        sub = mk(r, "a", "b")
        res = W.resolve(sub, self.env)
        self.assertEqual(os.path.normcase(res["root"]), os.path.normcase(r))
        self.assertEqual(res["subdir"], "a/b")
        self.assertEqual(res["name"], "proj")
        self.assertRegex(res["id"], r"^proj-[0-9a-f]{8}$")

    def test_root_itself_subdir_empty(self):
        r = self.repo()
        self.assertEqual(W.resolve(r, self.env)["subdir"], "")

    def test_git_file(self):
        r = mk(self.tmp, "wt")
        with open(os.path.join(r, ".git"), "w") as f:
            f.write("gitdir: elsewhere\n")
        sub = mk(r, "x")
        self.assertEqual(os.path.normcase(W.resolve(sub, self.env)["root"]), os.path.normcase(r))

    def test_nested_nearest_wins(self):
        outer = self.repo("outer")
        inner = mk(outer, "vendor", "inner")
        mk(inner, ".git")
        res = W.resolve(mk(inner, "src"), self.env)
        self.assertEqual(os.path.normcase(res["root"]), os.path.normcase(inner))
        self.assertNotEqual(res["id"], W.resolve(outer, self.env)["id"])

    def test_no_git_uses_dir_itself(self):
        d = mk(self.tmp, "plain", "deep")
        res = W.resolve(d, self.env)
        self.assertEqual(os.path.normcase(res["root"]), os.path.normcase(d))
        self.assertEqual(res["subdir"], "")

    def test_file_path(self):
        r = self.repo()
        f = os.path.join(mk(r, "src"), "m.py")
        open(f, "w").close()
        res = W.resolve(f, self.env)
        self.assertEqual(res["subdir"], "src")

    def test_nonexistent_with_existing_parent(self):
        r = self.repo()
        res = W.resolve(os.path.join(r, "newdir"), self.env)
        self.assertEqual(res["subdir"], "newdir")
        self.assertEqual(res["id"], W.resolve(r, self.env)["id"])

    def test_nonexistent_without_parent_refused(self):
        with self.assertRaises(ValueError):
            W.resolve(os.path.join(self.tmp, "nope", "nada"), self.env)

    def test_same_root_same_id_variants(self):
        r = self.repo()
        base = W.resolve(r, self.env)["id"]
        variants = [
            r + os.sep, r + "/", r.replace("\\", "/"),
            os.path.join(r, "x", ".."), os.path.join(r, ".", "."),
            r.swapcase() if os.name == "nt" else r,
            os.path.join(mk(r, "sub"), ".."),
        ]
        for v in variants:
            self.assertEqual(W.resolve(v, self.env)["id"], base, v)
        # from within too
        self.assertEqual(W.resolve(mk(r, "q"), self.env)["id"], base)

    def test_deterministic(self):
        r = self.repo()
        self.assertEqual(W.resolve(r, self.env), W.resolve(r, self.env))

    def test_different_roots_different_ids(self):
        a, b = self.repo("same"), os.path.join(mk(self.tmp, "o"), "same")
        mk(b, ".git")
        self.assertNotEqual(W.resolve(a, self.env)["id"], W.resolve(b, self.env)["id"])

    def test_home_refused(self):
        with self.assertRaises(ValueError):
            W.resolve(self.env["HOME"], self.env)

    def test_home_with_git_is_ignored_for_projects_under_it(self):
        # a dotfiles repo at the home dir must not swallow every project, nor make them unusable
        mk(self.env["HOME"], ".git")
        sub = mk(self.env["HOME"], "sub")
        r = W.resolve(sub, self.env)
        self.assertEqual(os.path.normcase(r["root"]), os.path.normcase(sub))
        with self.assertRaises(ValueError):                 # but home itself is still refused
            W.resolve(self.env["HOME"], self.env)

    def test_home_case_and_slash_variant_refused(self):
        h = self.env["HOME"]
        with self.assertRaises(ValueError):
            W.resolve(h.replace("\\", "/") + "/", self.env)

    def test_drive_and_fs_root_refused(self):
        anchor = os.path.splitdrive(self.tmp)[0] + os.sep
        with self.assertRaises(ValueError):
            W.resolve(anchor, self.env)
        if os.name == "nt":
            with self.assertRaises(ValueError):
                W.resolve(anchor.rstrip("\\"), self.env)  # "C:" is cwd-relative; must not slip through
        with self.assertRaises(ValueError):
            W.resolve(os.path.abspath(os.sep), self.env)

    @unittest.skipUnless(os.name == "nt", "UNC is Windows only")
    def test_unc_share_root_refused_and_subpath_ok(self):
        with self.assertRaises(ValueError):
            W.resolve("\\\\localhost\\nosuchshare", self.env)
        with self.assertRaises(ValueError):
            W.resolve("\\\\localhost\\nosuchshare\\", self.env)

    @unittest.skipUnless(os.name == "nt", "UNC is Windows only")
    def test_unc_admin_share_path(self):
        drive = os.path.splitdrive(self.tmp)[0]
        unc = "\\\\localhost\\" + drive[0] + "$" + self.tmp[len(drive):]
        if not os.path.isdir(unc):
            self.skipTest("admin share unavailable")
        r = self.repo("uncproj")
        res = W.resolve(unc + "\\uncproj", self.env)
        self.assertEqual(res["name"], "uncproj")

    def test_empty_none_bad(self):
        for bad in ("", "   ", None, 5, "a\x00b"):
            with self.assertRaises(ValueError):
                W.resolve(bad, self.env)

    def test_env_override(self):
        r = self.repo("ovr")
        other = mk(self.tmp, "other")
        env = dict(self.env, SWITCHYARD_WORKSPACE=r)
        res = W.resolve(other, env)
        self.assertEqual(res["name"], "ovr")
        self.assertEqual(res["id"], W.resolve(r, self.env)["id"])

    def test_env_override_empty_ignored(self):
        r = self.repo()
        env = dict(self.env, SWITCHYARD_WORKSPACE="  ")
        self.assertEqual(W.resolve(r, env)["name"], "proj")

    def test_env_override_to_home_refused(self):
        env = dict(self.env, SWITCHYARD_WORKSPACE=self.env["HOME"])
        with self.assertRaises(ValueError):
            W.resolve(self.repo(), env)

    def test_slug(self):
        self.assertEqual(W.slug("My Proj! v2"), "my-proj-v2")
        self.assertEqual(W.slug("!!!"), "ws")
        self.assertLessEqual(len(W.slug("x" * 200)), 40)
        res = W.resolve(self.repo("Weird  Name!"), self.env)
        self.assertRegex(res["id"], r"^weird-name-[0-9a-f]{8}$")

    # ---- contains ----
    def test_contains_basic(self):
        r = self.repo()
        self.assertTrue(W.contains(r, r))
        self.assertTrue(W.contains(r, r + os.sep))
        self.assertTrue(W.contains(r, os.path.join(r, "a", "b")))
        self.assertTrue(W.contains(r, os.path.join(r, "not", "yet", "there")))

    def test_contains_prefix_sibling_not_contained(self):
        r = self.repo("proj")
        evil = mk(self.tmp, "proj-evil")
        self.assertFalse(W.contains(r, evil))
        self.assertFalse(W.contains(r, os.path.join(evil, "x")))
        self.assertFalse(W.contains(r, r + "x"))

    def test_contains_dotdot_escape(self):
        r = self.repo("proj")
        mk(self.tmp, "proj-evil")
        self.assertFalse(W.contains(r, os.path.join(r, "..", "proj-evil")))
        self.assertFalse(W.contains(r, os.path.join(r, "..")))
        self.assertTrue(W.contains(r, os.path.join(r, "a", "..", "b")))

    def test_contains_case_and_slashes(self):
        r = self.repo("proj")
        if os.name == "nt":
            self.assertTrue(W.contains(r.upper(), os.path.join(r.lower(), "x")))
        self.assertTrue(W.contains(r.replace("\\", "/"), os.path.join(r, "x")))

    def test_contains_bad_input_false(self):
        r = self.repo()
        for bad in ("", None, "a\x00b"):
            self.assertFalse(W.contains(r, bad))
            self.assertFalse(W.contains(bad, r))

    @unittest.skipUnless(os.name == "nt", "drives are Windows only")
    def test_contains_other_drive_false(self):
        r = self.repo()
        other = "Z:\\" if not os.path.splitdrive(r)[0].upper().startswith("Z") else "Y:\\"
        self.assertFalse(W.contains(r, other + "x"))


if __name__ == "__main__":
    unittest.main()
