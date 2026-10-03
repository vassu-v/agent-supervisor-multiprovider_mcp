"""Reasoning-effort tests. No model, no real agent: mapping table, validation, routing, adapter command/body
construction (mocked) and an end-to-end spawn through the fake provider.
Run: python tests/test_effort.py"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from orch import providers  # noqa: E402
from orch import core  # noqa: E402
from orch.adapters.agy import AgyAdapter  # noqa: E402
from orch.adapters.claude import ClaudeAdapter  # noqa: E402
from orch.adapters.codex import CodexAdapter  # noqa: E402
from orch.adapters.opencode import OpenCodeAdapter  # noqa: E402
from tests.harness import api, spawn_fake, start_daemon, wait_status  # noqa: E402

CODEX_MODELS = {"models": [{"id": "gpt-5.5", "efforts": ["low", "medium", "high", "xhigh"]},
                           {"id": "gpt-5.6-luna", "efforts": ["low", "medium", "high", "xhigh", "max"]}]}


class Mapping(unittest.TestCase):
    def test_validation(self):
        self.assertIsNone(providers.check_effort(None))
        self.assertIsNone(providers.check_effort(""))
        self.assertEqual(providers.check_effort(" HIGH "), "high")
        for bad in ("ultra", "medium-ish", 3, "minimal"):
            with self.assertRaises(ValueError):
                providers.check_effort(bad)

    def test_native_providers_are_identity(self):
        for prov in ("claude", "fake"):               # agy is separate: effort is expressed through the model id
            for e in providers.EFFORTS:
                m = providers.map_effort(prov, None, e)
                self.assertEqual((m["applied"], m["warning"]), (e, None), (prov, e))

    def test_agy_discovery_lists_only_real_effort_levels(self):
        out = ("gemini-3.8-flash-low\tL\ngemini-3.8-flash-medium\tM\n"
               "gemini-3.8-flash-high\tH\nclaude-sonnet-4-6\tS\n")
        with mock.patch.object(providers, "_run", return_value=out):
            ms, _ = providers._discover_agy("agy")
        by = {m["id"]: m["efforts"] for m in ms}
        self.assertEqual(by["gemini-3.8-flash-medium"], ["low", "medium", "high"])
        self.assertEqual(by["claude-sonnet-4-6"], [])

    def test_none_effort(self):
        self.assertEqual(providers.map_effort("claude", None, None), {"effort": None, "applied": None, "warning": None})

    def test_nearest_ties_go_down(self):
        self.assertEqual(providers.nearest_level("medium", ["low", "high"]), "low")
        self.assertEqual(providers.nearest_level("max", ["low", "medium", "high", "xhigh"]), "xhigh")
        self.assertEqual(providers.nearest_level("low", ["high", "max"]), "high")

    def test_opencode_variants_by_provider(self):
        m = providers.map_effort("opencode", "anthropic/claude-x", "low")
        self.assertEqual(m["applied"], "high")
        self.assertIn("nearest", m["warning"])
        self.assertEqual(providers.map_effort("opencode", "anthropic/claude-x", "max")["applied"], "max")
        self.assertEqual(providers.map_effort("opencode", "openai/gpt", "max")["applied"], "xhigh")
        self.assertEqual(providers.map_effort("opencode", "google/gemini", "medium")["applied"], "low")
        self.assertEqual(providers.map_effort("opencode", "opencode/big-pickle-free", "xhigh")["applied"], "high")
        self.assertEqual(providers.map_effort("opencode", None, "medium")["applied"], "medium")

    def test_codex_per_model_levels(self):
        with mock.patch.dict(providers.CACHE, {"codex": {"models": CODEX_MODELS["models"], "source": "live", "checked": 1e18,
                                                          "error": None, "auth": True}}):
            self.assertEqual(providers.map_effort("codex", "gpt-5.6-luna", "max")["applied"], "max")
            m = providers.map_effort("codex", "gpt-5.5", "max")
            self.assertEqual(m["applied"], "xhigh")
            self.assertIn("xhigh", m["warning"])
            self.assertEqual(providers.supported_efforts("codex", "gpt-5.5"), ["low", "medium", "high", "xhigh"])
        # unknown/default model: static fallback low..xhigh
        self.assertEqual(providers.map_effort("codex", None, "max")["applied"], "xhigh")

    def test_codex_discovery_reads_supported_levels(self):
        raw = {"models": [{"slug": "m1", "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}, {"effort": "ultra"}],
                           "default_reasoning_level": "low"}, {"slug": "m2"}]}
        with mock.patch.object(providers, "_run", return_value=json.dumps(raw)):
            ms, src = providers._discover_codex("codex")
        self.assertEqual(ms[0]["efforts"], ["low", "high"])       # 'ultra' is codex-only, not in the unified space
        self.assertEqual(ms[1]["efforts"], providers.EFFORT_STATIC["codex"])

    def test_provider_without_effort_support_warns(self):
        with mock.patch.dict(providers.EFFORT_STATIC, {"nope": []}):
            m = providers.map_effort("nope", None, "high")
        self.assertIsNone(m["applied"])
        self.assertIn("does not support", m["warning"])

    AGY_IDS = {"models": [{"id": i} for i in ("gemini-3.8-flash-low", "gemini-3.8-flash-medium", "gemini-3.8-flash-high",
                                              "gemini-3.1-pro-low", "gemini-3.1-pro-high")]}

    def test_agy_effort_is_chosen_by_the_sibling_model_id(self):
        # verified live: agy REJECTS `--effort` with a model id that already carries a level ("conflicts with --effort"),
        # so effort has to be expressed by picking gemini-3.8-flash-low / -medium / -high
        with mock.patch.object(providers, "models", return_value=self.AGY_IDS):
            m = providers.map_effort("agy", "gemini-3.8-flash-low", "high")
            self.assertEqual((m["applied"], m["model"], m["via_model"], m["warning"]), ("high", "gemini-3.8-flash-high", True, None))
            m = providers.map_effort("agy", "gemini-3.8-flash-high", "low")
            self.assertEqual((m["applied"], m["model"]), ("low", "gemini-3.8-flash-low"))
            m = providers.map_effort("agy", "gemini-3.8-flash-medium", "max")             # no level above high
            self.assertEqual((m["applied"], m["model"]), ("high", "gemini-3.8-flash-high"))
            self.assertIn("using 'high'", m["warning"])
            m = providers.map_effort("agy", "gemini-3.1-pro-high", "medium")              # pro has no medium sibling: nearest, tie to lower
            self.assertEqual((m["applied"], m["model"]), ("low", "gemini-3.1-pro-low"))
            self.assertIn("offers", m["warning"])

    def test_agy_model_without_a_level_has_no_effort_and_never_gets_the_flag(self):
        # verified live: "--effort is not supported for model claude-sonnet-4-6"
        for model in ("claude-sonnet-4-6", None):
            m = providers.map_effort("agy", model, "high")
            self.assertIsNone(m["applied"])
            self.assertIn("ignored", m["warning"])
            self.assertNotIn("via_model", m)

    def test_models_expose_efforts(self):
        self.assertEqual(providers._discover_claude("claude")[0][0]["efforts"], list(providers.EFFORTS))
        with mock.patch.object(providers, "_run", return_value="anthropic/c\nopenai/g\n"):
            ms, _ = providers._discover_opencode("oc")
        self.assertEqual(ms[0]["efforts"], ["high", "max"])
        self.assertEqual(ms[1]["efforts"], ["low", "medium", "high", "xhigh"])
        with mock.patch.object(providers, "_run", return_value="gemini-x\tGemini X\n"):
            ms, _ = providers._discover_agy("agy")
        self.assertEqual(ms[0]["efforts"], [], "a model id with no level has no effort control in agy")


class Routing(unittest.TestCase):
    POLICY = {"routing": {"default_tier": "standard", "bump_to_hard_keywords": [],
                          "tiers": {"standard": {"candidates": ["claude:opus@high", "agy:*flash*"]},
                                    "bad": {"candidates": ["claude:opus@turbo", "agy:*flash*@low"]}}}}

    def route(self, *a, **kw):
        with mock.patch.object(core, "load_policy", return_value=self.POLICY), \
             mock.patch.object(providers, "usable", return_value=["claude", "agy"]), \
             mock.patch.object(providers, "resolve_model", side_effect=lambda p, pat: {"opus": "opus"}.get(pat, "gemini-flash")), \
             mock.patch.object(providers, "status", return_value={"state": "ready"}), \
             mock.patch.object(providers, "validate_model", return_value=(True, [])):
            return core.Policy().route(*a, **kw)

    def test_parse_candidate(self):
        self.assertEqual(core.parse_candidate("claude:opus@high"), ("claude", "opus", "high"))
        self.assertEqual(core.parse_candidate("agy:*pro*high"), ("agy", "*pro*high", None))
        with self.assertRaises(ValueError):
            core.parse_candidate("claude:opus@turbo")

    def test_tier_candidate_supplies_effort(self):
        r = self.route("do it", available=["claude"])
        self.assertEqual((r["provider"], r["model"], r["effort"], r["effort_applied"]), ("claude", "opus", "high", "high"))
        self.assertIn("effort high", " ".join(r["reasons"]))

    def test_explicit_effort_overrides_candidate_under_auto(self):
        r = self.route("do it", provider="auto", effort="max")
        self.assertEqual((r["provider"], r["effort"], r["effort_applied"]), ("claude", "max", "max"))

    def test_explicit_provider_never_gets_routed_effort(self):
        r = self.route("do it", provider="agy", model="gemini-3.8-flash-high")
        self.assertEqual((r["provider"], r["model"], r["effort"], r["effort_applied"]), ("agy", "gemini-3.8-flash-high", None, None))
        r = self.route("do it", provider="agy", model="gemini-3.8-flash-medium", effort="xhigh")
        self.assertEqual((r["provider"], r["effort_applied"], r["model"]), ("agy", "high", "gemini-3.8-flash-high"))
        self.assertTrue(r["effort_via_model"], "agy effort is expressed through the model id, never as a --effort flag")

    def test_invalid_candidate_effort_is_skipped_and_invalid_request_effort_raises(self):
        r = self.route("x", tier="bad")
        self.assertEqual((r["provider"], r["effort"]), ("agy", "low"))
        self.assertTrue(any("opus@turbo" in x for x in r["reasons"]))
        with self.assertRaises(ValueError):
            self.route("x", effort="ultra")

    def test_route_docstring_states_the_rule(self):
        self.assertIn("ALWAYS honoured", core.Policy.route.__doc__)
        self.assertIn("never rerouted", core.Policy.route.__doc__)


def _popen_cmd(cls, effort, model="m", session=None):
    ad = cls("a1", os.getcwd(), model, lambda e: None, {"effort": effort} if effort else {})
    ad.session_id = session
    with mock.patch("subprocess.Popen") as popen, mock.patch("threading.Thread"):
        ad.start()
    return popen.call_args.args[0]


class Adapters(unittest.TestCase):
    def test_claude_cmd(self):
        cmd = _popen_cmd(ClaudeAdapter, "xhigh")
        self.assertEqual(cmd[cmd.index("--effort") + 1], "xhigh")
        self.assertNotIn("--effort", _popen_cmd(ClaudeAdapter, None))
        self.assertIn("--effort", _popen_cmd(ClaudeAdapter, "low", session="s1"))      # kept on --resume restarts

    def test_agy_cmd(self):
        cmd = _popen_cmd(AgyAdapter, "max")
        self.assertEqual(cmd[cmd.index("--effort") + 1], "max")
        self.assertNotIn("--effort", _popen_cmd(AgyAdapter, None))

    def test_opencode_body_has_variant(self):
        for eff, want in (("high", "high"), (None, None)):
            ad = OpenCodeAdapter("a1", os.getcwd(), "anthropic/claude-x", lambda e: None, {"effort": eff} if eff else {})
            ad._ready.set()
            ad.session_id = "ses1"
            ad._req = mock.Mock()
            ad.send("hello")
            method, path, body = ad._req.call_args.args
            self.assertTrue(path.endswith("/prompt_async"))
            self.assertEqual(body.get("variant"), want)
            self.assertEqual(body["model"], {"providerID": "anthropic", "modelID": "claude-x"})

    def test_codex_turn_start_params(self):
        for eff in ("max", None):
            ad = CodexAdapter("a1", os.getcwd(), "gpt-5.6-luna", lambda e: None, {"effort": eff} if eff else {})
            ad.session_id = "th1"
            ad._request = mock.Mock(return_value={"turn": {"id": "t1"}})
            ad._start_turn("hi")
            method, params = ad._request.call_args.args
            self.assertEqual(method, "turn/start")
            self.assertEqual(params.get("effort"), eff)
            self.assertEqual(params["threadId"], "th1")


class E2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = start_daemon()
        cls.tmp = tempfile.mkdtemp(prefix="sy-effort-")

    @classmethod
    def tearDownClass(cls):
        cls.d.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_spawn_reports_and_records_effort(self):
        cwd = os.path.join(self.tmp, "r1")
        st, r = spawn_fake(self.d, [{"say": "ok"}], cwd, id="eff-1", effort="high")
        self.assertEqual(st, 200, r)
        self.assertEqual((r["effort"], r["effort_applied"], r["effort_warning"]), ("high", "high", None))
        self.assertEqual(r["route"]["effort_applied"], "high")
        info = wait_status(self.d, "eff-1", "idle")
        self.assertEqual((info["effort"], info["effort_applied"]), ("high", "high"))
        with open(os.path.join(cwd, ".fake_inbox.jsonl"), encoding="utf-8") as f:
            self.assertEqual(json.loads(f.readline()).get("effort"), "high")           # adapter received the mapped value
        st, audit = api(self.d, "GET", "/api/audit")
        rec = [a for a in audit if a.get("kind") == "spawn" and a.get("agent") == "eff-1"][0]
        self.assertEqual((rec["effort"], rec["effort_applied"]), ("high", "high"))

    def test_spawn_without_effort_and_invalid_effort(self):
        st, r = spawn_fake(self.d, [{"say": "ok"}], os.path.join(self.tmp, "r2"), id="eff-2")
        self.assertEqual(st, 200, r)
        self.assertEqual((r["effort"], r["effort_applied"]), (None, None))
        st, r = spawn_fake(self.d, [{"say": "ok"}], os.path.join(self.tmp, "r3"), id="eff-3", effort="turbo")
        self.assertGreaterEqual(st, 400)
        self.assertIn("effort", json.dumps(r))
        self.assertNotEqual(api(self.d, "GET", "/api/status?id=eff-3")[0], 200)     # nothing was created

    def test_models_expose_efforts_and_route_preview(self):
        st, m = api(self.d, "GET", "/api/models?provider=fake")
        self.assertEqual(st, 200, m)
        self.assertEqual(m["fake"]["models"][0]["efforts"], list(providers.EFFORTS))
        st, r = api(self.d, "POST", "/api/route", {"task": "x", "provider": "fake", "effort": "max"})
        self.assertEqual(st, 200, r)
        self.assertEqual(r["effort_applied"], "max")


if __name__ == "__main__":
    unittest.main(verbosity=2)
