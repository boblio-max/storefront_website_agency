"""tests.py — smoke test for Bot 7 (deployment_manager). Run: python tests.py"""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import website_generator as wg
from deployment_manager import create_github_repo

TARGET = Path(r"C:\Users\smile\OneDrive\Documents\GitHub\website_agency\websites_folder")

SKIP_MARKERS = ("not found on PATH", "gh repo create failed", "'gh'")


def ensure_fixture(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    if not any(p for p in folder.iterdir() if p.name != ".git"):
        (folder / "index.html").write_text(
            "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<title>Test site</title></head><body><h1>Test site</h1></body></html>",
            encoding="utf-8")


def test_create_github_repo() -> int:
    ensure_fixture(TARGET)
    try:
        url = create_github_repo(str(TARGET))
    except RuntimeError as e:
        msg = str(e)
        if any(m in msg for m in SKIP_MARKERS):
            print(f"[tests] SKIP (gh unavailable): {msg}", flush=True)
            return 0
        print(f"[tests] FAIL: {msg}", flush=True)
        return 1
    print(f"[tests] PASS: repo -> {url}", flush=True)
    return 0


class WebsiteGeneratorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.lead = {"lead_id": "lead_test", "name": "Example Cafe", "category": "Cafe"}

    def build(self, use_opencode=True, force=False, **kwargs):
        return wg.generate_one(self.lead, self.root, None, use_opencode, force, **kwargs)

    def write_site(self, target, prompt):
        for name, content in {"index.html": "<html><head></head><body>Custom cafe</body></html>",
                              "styles.css": "body { color: brown; }",
                              "script.js": "document.title = 'Cafe';"}.items():
            (target / name).write_text(content, encoding="utf-8")
        return "Done"

    def test_failure_falls_back_to_template(self):
        with patch.object(wg, "opencode_available", return_value=True), \
                patch.object(wg, "run_opencode_command", side_effect=RuntimeError("unavailable")):
            result = self.build()
            self.assertEqual(result["engine"], "template")
            html = (Path(result["dir"]) / "index.html").read_text(encoding="utf-8")
            self.assertIn('class="topbar"', html)
            self.assertIn('id="quote-form"', html)

    def test_require_opencode_raises(self):
        with patch.object(wg, "opencode_available", return_value=True), \
                patch.object(wg, "run_opencode_command", side_effect=RuntimeError("boom")):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                self.build(require_opencode=True)
        with patch.object(wg, "opencode_available", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "--require-opencode"):
                self.build(require_opencode=True)

    def test_explicit_template_and_cached_template_upgrade(self):
        with patch.object(wg, "run_opencode_command") as run:
            result = self.build(use_opencode=False)
            self.assertEqual(result["engine"], "template")
            run.assert_not_called()
        with patch.object(wg, "run_opencode_command", side_effect=self.write_site) as run:
            result = self.build()
            self.assertEqual(result["engine"], "opencode")
            self.assertFalse(result["skipped"])
            run.assert_called_once()
            self.assertTrue(self.build()["skipped"])
            run.assert_called_once()

    def test_noop_falls_back_to_template(self):
        self.build(use_opencode=False)
        with patch.object(wg, "opencode_available", return_value=True), \
                patch.object(wg, "run_opencode_command", return_value="Done"):
            result = self.build()
            self.assertEqual(result["engine"], "template")
        with patch.object(wg, "opencode_available", return_value=True), \
                patch.object(wg, "run_opencode_command", return_value="Done"):
            with self.assertRaisesRegex(RuntimeError, "did not change"):
                self.build(require_opencode=True)

    def test_missing_output_falls_back(self):
        with patch.object(wg, "opencode_available", return_value=True), \
                patch.object(wg, "run_opencode_command", return_value="Done"):
            result = self.build()
            self.assertEqual(result["engine"], "template")
            self.assertTrue((Path(result["dir"]) / "index.html").is_file())
        with tempfile.TemporaryDirectory() as fresh, \
                patch.object(wg, "opencode_available", return_value=True), \
                patch.object(wg, "run_opencode_command", return_value="Done"):
            with self.assertRaisesRegex(RuntimeError, "required files"):
                wg.generate_one(self.lead, Path(fresh), None, True, False,
                                require_opencode=True)

    def test_template_engine_has_qa_hooks_and_no_emoji(self):
        lead = dict(self.lead, phone="(425) 555-0100",
                    address="1 Main St, Bothell, WA 98011")
        files = wg.render_template(lead, None, preview=True)
        html, css = files["index.html"], files["styles.css"]
        for hook in ('class="topbar"', 'id="quote-form"', 'class="card"',
                     "<table", "<nav", "tel:"):
            self.assertIn(hook, html)
        for var in ("--brand", "--brand2", "--gold"):
            self.assertIn(var, css)
        self.assertNotRegex(html, "[\U0001F300-\U0001FAFF\u2600-\u27BF]")

    def test_template_variants_are_stable_and_varied(self):
        variants = set()
        for i in range(6):
            lead = dict(self.lead, lead_id=f"lead_{i:05d}",
                        stable_key=f"key-{i}", name=f"Cafe {i}")
            files = wg.render_template(lead, None)
            m = re.search(r"hero--([a-z-]+)", files["index.html"])
            self.assertIsNotNone(m)
            variants.add(m.group(1))
            again = wg.render_template(lead, None)
            self.assertIn(f"hero--{m.group(1)}", again["index.html"])
        self.assertGreater(len(variants), 1)

    def test_failed_qa_is_not_cached_and_is_in_prompt(self):
        with patch.object(wg, "run_opencode_command", side_effect=self.write_site):
            result = self.build()
        target = Path(result["dir"])
        (target / "qa_report.json").write_text(json.dumps([
            {"passed": False, "issues": ["Missing navigation"]}]), encoding="utf-8")
        with patch.object(wg, "run_opencode_command", side_effect=self.write_site) as run:
            self.assertFalse(self.build()["skipped"])
            self.assertIn("Missing navigation", run.call_args.args[1])

    def test_bridge_passes_prompt_via_stdin_and_selects_build_agent(self):
        prompt = 'Business "Cafe"\nBuild <nav> & responsive CSS'
        proc = subprocess.CompletedProcess([], 0, stdout="Done", stderr="")
        with patch.object(wg, "_opencode_argv", return_value=["opencode"]), \
                patch.dict(wg.os.environ, {"AGENCY_OPENCODE_MODEL": "provider/model"}), \
                patch.object(wg.subprocess, "run", return_value=proc) as run:
            self.assertEqual(wg.run_opencode_command(self.root, prompt), "Done")
            self.assertEqual(run.call_args.args[0],
                             ["opencode", "run", "--agent", "build", "--auto",
                              "--model", "provider/model"])
            self.assertEqual(run.call_args.kwargs["input"], prompt)

    def test_bridge_defaults_to_fast_model_with_auto_approve(self):
        proc = subprocess.CompletedProcess([], 0, stdout="Done", stderr="")
        env = {k: v for k, v in wg.os.environ.items() if k != "AGENCY_OPENCODE_MODEL"}
        with patch.object(wg, "_opencode_argv", return_value=["opencode"]), \
                patch.dict(wg.os.environ, env, clear=True), \
                patch.object(wg.subprocess, "run", return_value=proc) as run:
            wg.run_opencode_command(self.root, "Build")
            self.assertEqual(run.call_args.args[0],
                             ["opencode", "run", "--agent", "build", "--auto",
                              "--model", wg.DEFAULT_OPENCODE_MODEL])

    def test_native_launcher_is_preferred(self):
        proc = subprocess.CompletedProcess([], 0, stdout="1.18.31", stderr="")
        with patch.object(wg, "_OPENCODE_ARGV_CACHE", False), \
                patch.object(wg.Path, "is_file", return_value=True), \
                patch.object(wg.subprocess, "run", return_value=proc) as run:
            command = wg._opencode_argv()
            self.assertEqual(len(command), 1)
            self.assertTrue(command[0].endswith("opencode.exe"))
            self.assertEqual(run.call_args.args[0], [*command, "--version"])

    def test_failed_build_is_retried_not_cached(self):
        with patch.object(wg, "opencode_available", return_value=True), \
                patch.object(wg, "run_opencode_command", side_effect=RuntimeError("failed")):
            result = self.build()
            self.assertEqual(result["engine"], "template")
        with patch.object(wg, "run_opencode_command", side_effect=self.write_site) as run:
            result = self.build()
            self.assertEqual(result["engine"], "opencode")
            self.assertFalse(result["skipped"])
            run.assert_called_once()

    def test_bridge_errors_propagate(self):
        cases = [subprocess.TimeoutExpired("opencode", 1), OSError("missing")]
        with patch.object(wg, "_opencode_argv", return_value=["opencode"]):
            for error in cases:
                with self.subTest(error=error), \
                        patch.object(wg.subprocess, "run", side_effect=error):
                    with self.assertRaises(RuntimeError):
                        wg.run_opencode_command(self.root, "Build")
            proc = subprocess.CompletedProcess([], 1, stdout="Authentication failed", stderr="")
            with patch.object(wg.subprocess, "run", return_value=proc):
                with self.assertRaisesRegex(RuntimeError, "Authentication failed"):
                    wg.run_opencode_command(self.root, "Build")


if __name__ == "__main__":
    gh = shutil.which("gh") or next(
        (c for c in (r"C:\Program Files\GitHub CLI\gh.exe",
                     r"C:\Program Files (x86)\GitHub CLI\gh.exe")
         if Path(c).exists()), None)
    print(f"[tests] gh: {gh or 'MISSING'}", flush=True)
    rc = test_create_github_repo()
    print(f"[tests] {'done (pass/skip)' if rc == 0 else 'FAILED'}", flush=True)
    raise SystemExit(rc)
