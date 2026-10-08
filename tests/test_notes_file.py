"""Shared notes live in <cwd>/.switchyard/AGENTS.md; the project's own AGENTS.md/CLAUDE.md are never touched."""
import os, sys, tempfile, unittest, shutil
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
import harness  # noqa: E402


class NotesFile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = harness.start_daemon()
        cls.base = tempfile.mkdtemp(prefix="notes-")

    @classmethod
    def tearDownClass(cls):
        cls.d.stop()
        shutil.rmtree(cls.base, ignore_errors=True)

    def test_existing_project_files_untouched(self):
        cwd = os.path.join(self.base, "proj"); os.makedirs(cwd)
        own = {"AGENTS.md": "# mine\nkeep me\n", "CLAUDE.md": "# claude\n"}
        for k, v in own.items():
            open(os.path.join(cwd, k), "w", encoding="utf-8", newline="").write(v)
        st, _ = harness.spawn_fake(self.d, [{"say": "hi"}], cwd, id="n1")
        self.assertEqual(st, 200)
        harness.wait_status(self.d, "n1", "idle")
        for k, v in own.items():
            self.assertEqual(open(os.path.join(cwd, k), encoding="utf-8", newline="").read(), v, k)
        notes = os.path.join(cwd, ".switchyard", "AGENTS.md")
        self.assertTrue(os.path.exists(notes))
        self.assertTrue(open(notes, encoding="utf-8").read().startswith("# AGENTS.md"))
        self.assertEqual(open(os.path.join(cwd, ".switchyard", ".gitignore")).read().strip(), "*")

    def test_clean_project_gets_no_root_agents_md(self):
        cwd = os.path.join(self.base, "clean")
        st, _ = harness.spawn_fake(self.d, [{"say": "hi"}], cwd, id="n2")
        self.assertEqual(st, 200)
        harness.wait_status(self.d, "n2", "idle")
        self.assertFalse(os.path.exists(os.path.join(cwd, "AGENTS.md")))
        self.assertTrue(os.path.exists(os.path.join(cwd, ".switchyard", "AGENTS.md")))


if __name__ == "__main__":
    unittest.main()
