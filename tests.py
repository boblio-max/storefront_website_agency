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
        for hook in ('class="topbar"', 'id="quote-form"', "<nav", "tel:"):
            self.assertIn(hook, html)
        # honest hours: no invented table without hours data, real table with it
        self.assertNotIn("<table", html)
        self.assertIn("Call for today", html)
        with_hours = dict(lead, hours_table=[("Mon – Fri", "8:00 AM – 6:00 PM")])
        self.assertIn("<table", wg.render_template(with_hours, None)["index.html"])
        # food leads speak order-and-visit, never quotes
        food = dict(lead, category="Pizza restaurant")
        food_html = wg.render_template(food, None)["index.html"]
        for phrase in ("View Menu", "Crowd favorites", "Call to Order",
                       "google.com/maps/search"):
            self.assertIn(phrase, food_html)
        for phrase in ("Get a Free Quote", "Every job quoted up front",
                       "Request a Callback", 'href="#"'):
            self.assertNotIn(phrase, food_html)
        # service cards ship as grid cards and/or sticky-stack cards
        self.assertTrue('class="card"' in html or "stack-card" in html)
        for var in ("--brand", "--brand2", "--gold"):
            self.assertIn(var, css)
        self.assertNotRegex(html, "[\U0001F300-\U0001FAFF\u2600-\u27BF]")

    def test_hardening_applies_seo_overflow_aria_to_built_site(self):
        lead = dict(self.lead, phone="(425) 555-0100", rating=4.7,
                    review_count=12, address="1 Main St, Bothell, WA 98011",
                    category="Cafe")
        result = wg.generate_one(lead, self.root, None, use_opencode=False,
                                 force=True, preview=True)
        target = Path(result["dir"])
        html = (target / "index.html").read_text(encoding="utf-8")
        css = (target / "styles.css").read_text(encoding="utf-8")
        for marker in ('rel="canonical"', 'property="og:title"',
                       'name="twitter:card"', "application/ld+json",
                       "sw-overflow-guard", "sw-hardening", "og-image.svg"):
            self.assertIn(marker, html)
        self.assertIn("overflow-x:clip", css)
        self.assertTrue((target / "og-image.svg").is_file())
        ld = re.search(r'<script type="application/ld\+json">(.*?)</script>',
                       html, re.S)
        self.assertIsNotNone(ld)
        data = json.loads(ld.group(1))
        self.assertEqual(data["@type"], "Restaurant")
        self.assertEqual(data["telephone"], "+14255550100")
        self.assertEqual(data["aggregateRating"]["ratingValue"], "4.7")
        # carousel ARIA + marquee hidden by default in the hardened template
        self.assertIn('role="tabpanel"', html)
        self.assertIn('role="tab"', html)
        self.assertIn('aria-controls="review-p0"', html)
        # idempotency: re-hardening must not change the file
        before = html
        wg.apply_site_hardening(target, lead)
        self.assertEqual(before, (target / "index.html").read_text(encoding="utf-8"))
        # final (non-preview) builds keep the SEO identity too
        result2 = wg.generate_one(lead, self.root, None, use_opencode=False,
                                  force=True, preview=False)
        html2 = (Path(result2["dir"]) / "index.html").read_text(encoding="utf-8")
        self.assertIn('rel="canonical"', html2)
        self.assertIn("application/ld+json", html2)
        self.assertNotIn("sw-preview-banner", html2)
        self.assertNotIn("Demo preview", html2)

    def test_hardening_retrofits_cached_site(self):
        result = wg.generate_one(self.lead, self.root, None, use_opencode=False,
                                 force=True, preview=True)
        target = Path(result["dir"])
        # simulate a site that lacks SEO identity (e.g. older OpenCode build)
        html = (target / "index.html").read_text(encoding="utf-8")
        html = re.sub(r'<link rel="canonical"[^>]*>', "", html, count=1)
        html = re.sub(r'<meta property="og:title"[^>]*>', "", html, count=1)
        (target / "index.html").write_text(html, encoding="utf-8")
        wg.apply_site_hardening(target, self.lead)
        html2 = (target / "index.html").read_text(encoding="utf-8")
        self.assertIn('rel="canonical"', html2)
        self.assertIn('property="og:title"', html2)

    def test_award_tier_craft_in_template_build(self):
        lead = dict(self.lead, phone="(425) 555-0100", rating=4.7,
                    review_count=12, address="1 Main St, Bothell, WA 98011",
                    category="Cafe")
        files = wg.render_template(lead, None, preview=True)
        html, css, js = files["index.html"], files["styles.css"], files["script.js"]
        # entrance system with no-JS fallback
        self.assertIn('id="loader"', html)
        self.assertIn('id="progressBar"', html)
        self.assertIn("body.js", css)
        self.assertIn("pageLoaded", js)
        self.assertIn("prefers-reduced-motion", js)
        # art worlds: grain + category texture + ghost art
        self.assertIn("feTurbulence", css)
        self.assertIn("data:image/svg+xml", css)
        self.assertIn("hero-ghost", html)
        # signature moment present (one of three deterministic modules)
        self.assertTrue('id="heroAurora"' in html or 'class="stack"' in html
                        or "data-count" in html)
        # fluid display type + section numerals + custom selection
        self.assertRegex(css, r"font-size:\s*clamp\([^)]*vw")
        self.assertIn("secnum", html)
        self.assertIn("::selection", css)
        # split-text + magnetic + count-up + aurora JS modules
        for marker in ("wmask", "pointermove", "data-count", "heroAurora",
                       "requestAnimationFrame"):
            self.assertIn(marker, js)
        # rating proof flows into stats band + upgraded og share card
        self.assertIn('data-count="4.7"', html)
        og = wg._write_og_image(Path(self.root), lead)
        og_svg = og.read_text(encoding="utf-8")
        self.assertIn("verified reviews", og_svg)
        self.assertIn('font-size="72"', og_svg)
        # prompt demands the signature moment + mobile + dev-grade code
        prompt = wg.build_prompt(lead, None, preview=True)
        for marker in ("NON-NEGOTIABLE OUTPUT CONTRACT", "DESIGN PERSONA",
                       "senior UI/UX designer", "ART DIRECTION",
                       "COPYWRITING RULES", "MANDATORY STRUCTURE",
                       "FINAL SELF-AUDIT",
                       "do NOT build your own banner",
                       "body.js .reveal",
                       "quote-form",
                       "prefers-reduced-motion",
                       "index.html < 60KB"):
            self.assertIn(marker, prompt)

    def test_photo_pipeline_uses_real_photos_not_drawn(self):
        import struct

        def fake_jpeg(w, h, pad):
            body = (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
                    + b"\xff\xc0\x00\x0b\x08" + struct.pack(">HH", h, w) + b"\x03\x01\x22\x00")
            return body + b"\x00" * pad

        big_a = fake_jpeg(1600, 1000, 30000)
        big_b = fake_jpeg(1200, 800, 30000) + b"\x01" * 9000
        tiny = fake_jpeg(100, 100, 100)
        self.assertEqual(wg._img_dimensions(big_a), (1600, 1000))
        self.assertIsNone(wg._img_dimensions(b"junk"))

        def fake_candidates(url):
            return ["https://demo.test/hero.jpg", "https://demo.test/logo-big.png",
                    "https://demo.test/tiny.jpg", "https://demo.test/interior.jpg",
                    "https://demo.test/hero.jpg"]  # duplicate on purpose

        def fake_download(url):
            if "logo" in url:
                return None
            if "tiny" in url:
                return tiny, ".jpg"
            if "interior" in url:
                return big_b, ".jpg"
            return big_a, ".jpg"

        lead = dict(self.lead, website="https://demo.test/")
        with patch.object(wg, "_photo_candidates", side_effect=fake_candidates), \
                patch.object(wg, "_download_photo", side_effect=fake_download):
            photos = wg.collect_business_photos(lead, Path(self.root))
        # tiny skipped (<600px), duplicate collapsed, hero named by size rank
        self.assertEqual([p["file"] for p in photos], ["photo-hero.jpg", "photo-1.jpg"])
        self.assertEqual(photos[0]["role"], "hero")
        self.assertEqual((photos[0]["width"], photos[0]["height"]), (1600, 1000))

        # end to end: files on disk, meta records them, template shows them
        with patch.object(wg, "_photo_candidates", side_effect=fake_candidates), \
                patch.object(wg, "_download_photo", side_effect=fake_download):
            result = wg.generate_one(lead, self.root, None, use_opencode=False,
                                     force=True, preview=True)
        target = Path(result["dir"])
        self.assertTrue((target / "photo-hero.jpg").is_file())
        meta = json.loads((target / "meta.json").read_text(encoding="utf-8"))
        self.assertIn("photo-hero.jpg", meta["photos"])
        html = (target / "index.html").read_text(encoding="utf-8")
        self.assertIn('src="photo-hero.jpg"', html)
        self.assertIn('fetchpriority="high"', html)
        self.assertIn('src="photo-1.jpg"', html)
        # prompt branches: photos listed + hotlink ban; empty -> SVG fallback line
        prompt = wg.build_prompt(lead, None, preview=True, photos=photos)
        self.assertIn("photo-hero.jpg (1600x1000, hero)", prompt)
        self.assertIn("NEVER hotlink", prompt)
        bare = wg.build_prompt(lead, None, preview=True, photos=[])
        self.assertIn("NEVER hotlink", bare)
        self.assertIn("inline-SVG scenes", bare)

    def test_design_rubric_helpers_and_template_passes_qa(self):
        import qa_bot
        self.assertEqual(qa_bot._contrast("#ffffff", "#000000"), 21.0)
        self.assertGreater(qa_bot._contrast("#ffffff", "#b3271e") or 0, 4.5)
        self.assertIsNone(qa_bot._contrast("#ffffff", "not-a-color"))
        self.assertEqual(qa_bot._root_var(":root{--brand:#123456;}", "brand"), "#123456")
        self.assertIsNone(qa_bot._root_var(":root{--brand:#123456;}", "gold"))
        # body{color} resolution beats var-name guessing on dark themes
        dark_css = (":root{--ink:#241a10;--cream:#f4ead6;--bg:#14100d;}"
                    "body{margin:0;color:var(--cream);background:var(--bg);}")
        self.assertEqual(qa_bot._body_text_color(dark_css), "#f4ead6")
        self.assertIsNone(qa_bot._body_text_color("body{margin:0}"))
        lead = dict(self.lead, phone="(425) 555-0100", rating=4.7,
                    review_count=12, address="1 Main St, Bothell, WA 98011",
                    category="Cafe")
        result = wg.generate_one(lead, self.root, None, use_opencode=False,
                                 force=True, preview=True)
        report = qa_bot.check_site(Path(result["dir"]), lead, threshold=80)
        self.assertTrue(report["passed"], report["issues"])
        self.assertGreaterEqual(report["score"], 90)

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


class OpenCodeAutofixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, html="x", css="x", js="x"):
        (self.root / "index.html").write_text(html, encoding="utf-8")
        (self.root / "styles.css").write_text(css, encoding="utf-8")
        (self.root / "script.js").write_text(js, encoding="utf-8")

    def test_prompt_names_qa_gates_verbatim(self):
        lead = {"lead_id": "lead_x", "name": "Cafe X", "category": "Cafe"}
        prompt = wg.build_prompt(lead, None, preview=True)
        for marker in ("NON-NEGOTIABLE OUTPUT CONTRACT",
                       "DESIGN PERSONA",
                       "senior UI/UX designer",
                       "body.js .reveal",
                       "classList.add('js')",
                       "prefers-reduced-motion",
                       "quote-form",
                       "FINAL SELF-AUDIT",
                       "do NOT build your own banner",
                       "MANDATORY STRUCTURE"):
            self.assertIn(marker, prompt)

    def test_reduced_motion_injected_when_missing(self):
        self.write(css="body{color:#111}", js="console.log(1)")
        self.assertEqual(wg._opencode_autofix(self.root), ["reduced-motion", "texture"])
        css = (self.root / "styles.css").read_text(encoding="utf-8")
        self.assertIn("prefers-reduced-motion", css)

    def test_reveal_rescoped_and_js_bootstrapped(self):
        self.write(html='<div class="reveal">hi</div>',
                   css=".reveal{opacity:0;transform:none}",
                   js="console.log(1)")
        fixed = wg._opencode_autofix(self.root)
        self.assertIn("reveal-scope", fixed)
        css = (self.root / "styles.css").read_text(encoding="utf-8")
        self.assertIn("body.js .reveal", css)
        self.assertNotIn(".reveal{opacity:0", css.replace("body.js .reveal", ""))
        js = (self.root / "script.js").read_text(encoding="utf-8")
        self.assertIn("classList.add('js')", js)

    def test_clean_output_is_untouched(self):
        self.write(html='<div class="stack" data-count="5">hi</div>',
                   css=("body.js .reveal{opacity:0}::selection{background:#000}"
                        "@media (prefers-reduced-motion:reduce){*{animation:none}}"),
                   js="document.body.classList.add('js')")
        before = {(p.name): p.read_bytes() for p in self.root.iterdir()}
        self.assertEqual(wg._opencode_autofix(self.root), [])
        after = {(p.name): p.read_bytes() for p in self.root.iterdir()}
        self.assertEqual(before, after)


class EmailConfigTests(unittest.TestCase):
    def test_smtp_config_none_without_password(self):
        import email_generator as eg
        with patch.dict(eg.os.environ, {}, clear=False):
            eg.os.environ.pop("AGENCY_SMTP_PASS", None)
            self.assertIsNone(eg.smtp_config())

    def test_smtp_config_bad_port_falls_back(self):
        import email_generator as eg
        with patch.dict(eg.os.environ, {"AGENCY_SMTP_PASS": "x", "AGENCY_SMTP_PORT": "bad"}):
            self.assertEqual(eg.smtp_config()["port"], 587)

    def test_imap_config_none_without_password(self):
        import response_feedback_manager as rfm
        with patch.dict(rfm.os.environ, {}, clear=False):
            rfm.os.environ.pop("AGENCY_IMAP_PASS", None)
            self.assertIsNone(rfm.imap_config())

    def test_imap_config_bad_port_falls_back(self):
        import response_feedback_manager as rfm
        with patch.dict(rfm.os.environ, {"AGENCY_IMAP_PASS": "x", "AGENCY_IMAP_PORT": "bad"}):
            self.assertEqual(rfm.imap_config()["port"], 993)

    def test_suppression_and_resend_guards(self):
        import email_generator as eg
        hist = [{"lead_id": "lead_1", "direction": "outgoing",
                 "purpose": "initial_outreach"},
                {"lead_id": "lead_2", "direction": "incoming",
                 "body": "please unsubscribe me"}]
        self.assertTrue(eg.already_sent(hist, "lead_1", "initial_outreach"))
        self.assertFalse(eg.already_sent(hist, "lead_1", "follow_up"))
        self.assertTrue(eg.is_suppressed(hist, "lead_2"))
        self.assertFalse(eg.is_suppressed(hist, "lead_1"))

    def test_mojibake_output_rejected(self):
        target = self.temp_target()
        (target / "index.html").write_text("<title>caf\ufffd</title>", encoding="utf-8")
        (target / "styles.css").write_text("body{}", encoding="utf-8")
        (target / "script.js").write_text("1", encoding="utf-8")
        self.assertTrue(wg._opencode_output_defects(target))
        (target / "index.html").write_text("<title>cafe</title>", encoding="utf-8")
        self.assertEqual(wg._opencode_output_defects(target), [])

    def temp_target(self):
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        return Path(d)


class PaymentWalletTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = str(Path(self.temp.name) / "payments.json")

    def test_invoice_is_idempotent_per_lead(self):
        import payment_manager as pm
        a = pm.create_invoice("lead_x", 49900, "USD", ["card"], self.ledger)
        b = pm.create_invoice("lead_x", 49900, "USD", ["card"], self.ledger)
        self.assertEqual(a["invoice_id"], b["invoice_id"])
        self.assertEqual(a["status"], "open")

    def test_pay_flow_and_balance(self):
        import payment_manager as pm
        rec = pm.create_invoice("lead_y", 10000, "USD", ["bank", "crypto"], self.ledger)
        self.assertFalse(pm.is_paid(self.ledger, "lead_y"))
        pm.mark_paid(rec["invoice_id"], "bank", "ZELLE-1", self.ledger)
        self.assertTrue(pm.is_paid(self.ledger, "lead_y"))
        bal = pm.ledger_balance(self.ledger)
        self.assertEqual(bal["paid_cents"], 10000)
        self.assertEqual(bal["paid_by_method_cents"]["bank"], 10000)

    def test_instructions_never_leak_secrets(self):
        import payment_manager as pm
        rec = pm.create_invoice("lead_z", 5000, "USD", ["card", "crypto"], self.ledger)
        with patch.dict(pm.os.environ, {"AGENCY_STRIPE_KEY": "sk_test_1234567890",
                                        "AGENCY_CRYPTO_ADDRESSES": '{"BTC":"bc1qtest"}'}):
            text = pm.pay_instructions(rec)
            self.assertIn("bc1qtest", text)  # watch-only address is public
            report = pm.payout_report(self.ledger)
            self.assertNotIn("sk_test_1234567890", report)
            self.assertIn(pm.mask_secret("sk_test_1234567890"), report)

    def test_bad_input_rejected(self):
        import payment_manager as pm
        with self.assertRaises(ValueError):
            pm.create_invoice("", 100, "USD", None, self.ledger)
        with self.assertRaises(ValueError):
            pm.create_invoice("lead_q", -5, "USD", None, self.ledger)
        with self.assertRaises(LookupError):
            pm.mark_paid("inv_nope_01", "card", "", self.ledger)
        with self.assertRaises(ValueError):
            pm.mark_paid("inv_nope_01", "cash", "", self.ledger)


class ResponseFeedbackTests(unittest.TestCase):
    def test_buying_intent_is_interested(self):
        import response_feedback_manager as rfm
        for body in ("Perfect, we'll take it! What's the price and how do we pay?",
                     "Looks great — send me the invoice and let's start."):
            self.assertEqual(rfm.classify(body)[0], "interested")
        self.assertEqual(rfm.classify("No thanks, not interested")[0], "not_interested")


class QAReviewerTests(unittest.TestCase):
    def test_parse_verdicts(self):
        import qa_bot
        good = '```json\n{"verdict": "pass", "issues": [], "praise": ["nice hero"]}\n```'
        self.assertEqual(qa_bot.parse_review_output(good)["verdict"], "pass")
        bad = '{"verdict": "fail", "issues": ["Hero H1 wraps awkwardly — tighten clamp()"]}'
        parsed = qa_bot.parse_review_output("noise " + bad + " noise")
        self.assertEqual(parsed["verdict"], "fail")
        self.assertEqual(len(parsed["issues"]), 1)
        self.assertIsNone(qa_bot.parse_review_output("looks fine to me"))
        self.assertIsNone(qa_bot.parse_review_output('{"verdict": "fail", "issues": []}'))

    def test_reviewer_fail_blocks_pass(self):
        import qa_bot
        with tempfile.TemporaryDirectory() as d:
            site = Path(d) / "s"
            (site / "x").mkdir(parents=True)
            (site / "x" / "index.html").write_text("<html></html>", encoding="utf-8")
            fake_report = {"lead_id": "s", "passed": True, "score": 100,
                           "issues": [], "recommendations": []}
            with patch.object(qa_bot, "check_site", return_value=fake_report), \
                 patch.object(qa_bot, "review_with_opencode",
                              return_value={"engine": "opencode", "model": "m",
                                            "verdict": "fail",
                                            "issues": ["taste issue"], "praise": []}):
                rc = qa_bot.main(site_path=str(site / "x"), output=str(Path(d) / "r.json"))
                self.assertEqual(rc, 1)

    def test_reviewer_abstain_keeps_structural_pass(self):
        import qa_bot
        with tempfile.TemporaryDirectory() as d:
            site = Path(d) / "s"
            (site / "x").mkdir(parents=True)
            (site / "x" / "index.html").write_text("<html></html>", encoding="utf-8")
            fake_report = {"lead_id": "s", "passed": True, "score": 100,
                           "issues": [], "recommendations": []}
            with patch.object(qa_bot, "check_site", return_value=fake_report), \
                 patch.object(qa_bot, "review_with_opencode",
                              return_value={"engine": "abstained", "model": "m",
                                            "verdict": "abstain",
                                            "issues": [], "praise": []}):
                rc = qa_bot.main(site_path=str(site / "x"), output=str(Path(d) / "r.json"))
                self.assertTrue(isinstance(rc, str))

    def test_no_reviewer_flag_skips_gate2(self):
        import qa_bot
        with tempfile.TemporaryDirectory() as d:
            site = Path(d) / "s"
            (site / "x").mkdir(parents=True)
            (site / "x" / "index.html").write_text("<html></html>", encoding="utf-8")
            fake_report = {"lead_id": "s", "passed": True, "score": 100,
                           "issues": [], "recommendations": []}
            with patch.object(qa_bot, "check_site", return_value=fake_report), \
                 patch.object(qa_bot, "review_with_opencode",
                              side_effect=AssertionError("must not run")):
                rc = qa_bot.main(argv=["--no-reviewer"], site_path=str(site / "x"),
                                 output=str(Path(d) / "r.json"))
                self.assertTrue(isinstance(rc, str))


class ScraperDeepTests(unittest.TestCase):
    def test_parse_hours_lines(self):
        import maps_scraper as ms
        rows = ms.parse_hours_lines([
            "Monday: 9:00 AM – 5:00 PM", "Tue 9am-5pm", "Sunday: Closed",
            "Monday s", "not a day row", "", "x" * 100])
        self.assertEqual(rows, [["Monday", "9:00 AM – 5:00 PM"],
                                ["Tuesday", "9am-5pm"], ["Sunday", "Closed"]])

    def test_pair_hours_texts(self):
        import maps_scraper as ms
        rows = ms.pair_hours_texts(["Monday", "5:00 AM – 11:00 PM",
                                    "Tuesday", "5:00 AM – 11:00 PM",
                                    "Some review text about delivery"])
        self.assertEqual(rows, [["Monday", "5:00 AM – 11:00 PM"],
                                ["Tuesday", "5:00 AM – 11:00 PM"]])
        self.assertEqual(ms.pair_hours_texts(["Hello world"]), [])

    def test_summarize_hours(self):
        import maps_scraper as ms
        self.assertEqual(ms.summarize_hours(
            [["Monday", "9 AM – 5 PM"], ["Tuesday", "9 AM – 5 PM"]]),
            "Mon–Tue 9 AM – 5 PM")
        self.assertIsNone(ms.summarize_hours([]))

    def test_parse_price_level(self):
        import maps_scraper as ms
        self.assertEqual(ms.parse_price_level("Price: $$"), 2)
        self.assertEqual(ms.parse_price_level("$"), 1)
        self.assertIsNone(ms.parse_price_level("No price here"))
        self.assertIsNone(ms.parse_price_level(None))

    def test_parse_coords(self):
        import maps_scraper as ms
        self.assertEqual(ms.parse_coords_from_url(
            "https://www.google.com/maps/place/X/@47.61,-122.33,15z"),
            {"lat": 47.61, "lng": -122.33})
        self.assertEqual(ms.parse_coords_from_url("https://x/!3d47.5!4d-122.1"),
                         {"lat": 47.5, "lng": -122.1})
        self.assertIsNone(ms.parse_coords_from_url("https://example.com"))

    def test_template_uses_scraped_menu_reviews_hours(self):
        lead = {"lead_id": "lead_deep", "name": "Deep Pizza",
                "category": "Pizza restaurant", "phone": "(425) 555-0100",
                "address": "1 Main St, Bothell, WA 98011",
                "menu_url": "https://deep.example/menu",
                "hours_table": [["Monday", "11 AM – 10 PM"]],
                "reviews_list": [{"text": "The blistered margherita is the best pie in town, full stop.",
                                  "author": "Sam R."}]}
        files = wg.render_template(lead, None, preview=True)
        html = files["index.html"]
        self.assertIn('href="https://deep.example/menu"', html)
        self.assertIn("blistered margherita", html)
        self.assertIn("Sam R.", html)
        self.assertIn("<table", html)
        self.assertIn("11 AM", html)

    def test_template_falls_back_to_maps_photos(self):
        import struct

        def fake_jpeg(w, h, pad):
            body = (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
                    + b"\xff\xc0\x00\x0b\x08" + struct.pack(">HH", h, w) + b"\x03\x01\x22\x00")
            return body + b"\x00" * pad

        big = fake_jpeg(1600, 1000, 30000)
        lead = {"lead_id": "lead_mapic", "name": "No Site Cafe",
                "category": "Cafe", "website": None,
                "photo_urls": ["https://lh3.googleusercontent.com/p/photo1=w1600"]}
        with patch.object(wg, "_download_photo", return_value=(big, ".jpg")):
            with tempfile.TemporaryDirectory() as fresh:
                photos = wg.collect_business_photos(lead, Path(fresh))
        self.assertEqual([p["file"] for p in photos], ["photo-hero.jpg"])
        self.assertEqual(photos[0]["role"], "hero")


if __name__ == "__main__":
    gh = shutil.which("gh") or next(
        (c for c in (r"C:\Program Files\GitHub CLI\gh.exe",
                     r"C:\Program Files (x86)\GitHub CLI\gh.exe")
         if Path(c).exists()), None)
    print(f"[tests] gh: {gh or 'MISSING'}", flush=True)
    rc = test_create_github_repo()
    print(f"[tests] {'done (pass/skip)' if rc == 0 else 'FAILED'}", flush=True)
    raise SystemExit(rc)
