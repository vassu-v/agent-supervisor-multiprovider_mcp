"""Static checks for orch/dashboard.html (no daemon needed)."""
import os
import re
import shutil
import subprocess
import tempfile
import unittest

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "orch", "dashboard.html")
# an interpolation is safe when it starts with one of these: esc() escaper, helpers named ui* that return
# already-escaped HTML, or encodeURIComponent for URLs
SAFE_START = re.compile(r"^\s*(esc\(|ui[A-Za-z0-9_]*|encodeURIComponent\(|CSS\.escape\(|q\()")


def interpolations(src):
    """Yield every `${...}` expression (brace-balanced), including ones nested inside other expressions."""
    i = 0
    while True:
        i = src.find("${", i)
        if i < 0:
            return
        depth, j = 1, i + 2
        while j < len(src) and depth:
            depth += {"{": 1, "}": -1}.get(src[j], 0)
            j += 1
        yield src[i + 2:j - 1]
        i += 2


class DashboardStatic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(PATH, encoding="utf-8") as f:
            cls.html = f.read()
        m = re.search(r"<script>(.*)</script>", cls.html, re.S)
        cls.script = m.group(1)

    def test_no_inline_handlers(self):
        low = self.html.lower()
        for bad in ("onclick=", "onerror=", "javascript:"):
            self.assertNotIn(bad, low)
        self.assertIsNone(re.search(r"<[^>]+\son[a-z]+\s*=", low), "inline on* handler attribute")

    def test_token_placeholder_once(self):
        self.assertEqual(self.html.count("__TOKEN__"), 1)
        self.assertIn('"Authorization":"Bearer "+TOKEN', self.html)

    def test_interpolations_escaped(self):
        bad = [e for e in interpolations(self.script) if not SAFE_START.match(e)]
        self.assertEqual(bad, [], "unescaped ${...} interpolations")

    def test_lanes_present(self):
        for s in ("green", "pink", "announcements", "open questions", "/api/board", "/api/workspaces"):
            self.assertIn(s, self.html)

    def test_no_external_requests(self):
        self.assertIsNone(re.search(r"(src|href)\s*=\s*[\"']https?://", self.html))

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_script_syntax(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "dash.js")
            with open(p, "w", encoding="utf-8") as f:
                f.write(self.script)
            r = subprocess.run([shutil.which("node"), "--check", p], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
