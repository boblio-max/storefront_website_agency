"""
qa_bot.py (Bot 6) — Independently judge whether a Bot 5 site is deployable.

Input:
    path to one site folder (str), e.g. "generated_sites/lead_00001"
    (index.html + styles.css + script.js + meta.json)

Inspects (critical, independent of Bot 5):
    visual quality · desktop layout · mobile layout · navigation ·
    buttons/links · forms · content accuracy · responsiveness ·
    accessibility · performance · broken elements · professionalism

Output:
    <site_dir>/qa_report.json — run history (list, latest last):
    [{"passed": true, "score": 94, "issues": [], ...}, ...]

    Re-runs append; the file is created on first run.

If it fails:
    {"passed": false, "score": 71,
     "issues": ["Mobile navigation overlaps hero content", ...], ...}

A failed QA stops the pipeline (exit code 1) — no automatic retry loop.

Two gates:
  Gate 1 (structural, always): deterministic checks — files, responsive
    CSS, nav/forms, content accuracy, contrast, SEO identity. Fast.
  Gate 2 (reviewer, opencode): a fresh `opencode run` instance whose only
    job is taste — hierarchy, typography, spacing, color restraint, copy
    quality, mobile feel. Its issues merge into the report (prefixed
    [reviewer]) so Bot 5 fixes them on rebuild. Skipped with --no-reviewer
    or when the CLI is unreachable (structural verdict stands, report notes
    the abstention).

Usage:
    python qa_bot.py generated_sites/lead_00001
    python qa_bot.py --site generated_sites/lead_00001
    python qa_bot.py generated_sites/lead_00001 --no-reviewer
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from bs4 import BeautifulSoup

try:  # Windows consoles default to cp1252; keep unicode output from crashing
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

PASS_THRESHOLD = 80


def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _hex_to_rgb(h: str) -> tuple[float, float, float] | None:
    h = h.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) not in (6, 8) or any(c not in "0123456789abcdefABCDEF" for c in h[:6]):
        return None
    v = int(h[:6], 16)
    return ((v >> 16 & 255) / 255.0, (v >> 8 & 255) / 255.0, (v & 255) / 255.0)


def _rel_lum(rgb: tuple[float, float, float]) -> float:
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _contrast(a: str, b: str) -> float | None:
    ra, rb = _hex_to_rgb(a), _hex_to_rgb(b)
    if not ra or not rb:
        return None
    la, lb = _rel_lum(ra), _rel_lum(rb)
    hi, lo = (la, lb) if la >= lb else (lb, la)
    return (hi + 0.05) / (lo + 0.05)


def _root_var(css: str, name: str) -> str | None:
    m = re.search(r"--" + re.escape(name) + r"\s*:\s*([^;{}]+)", css)
    if not m:
        return None
    h = re.search(r"#[0-9a-fA-F]{3,8}", m.group(1))
    return h.group(0) if h else None


def _body_text_color(css: str) -> str | None:
    """The color actually used for body copy: body{color: ...}, resolving
    var(--x). Falls back to common text-variable names; None if unknowable."""
    m = re.search(r"(?<![\w-])body\s*\{([^}]*)\}", css)
    if m:
        c = re.search(r"(?<![\w-])color\s*:\s*([^;}]+)", m.group(1))
        if c:
            val = c.group(1).strip()
            vm = re.match(r"var\(\s*--([\w-]+)", val)
            if vm:
                v = _root_var(css, vm.group(1))
                if v:
                    return v
            h = re.search(r"#[0-9a-fA-F]{3,8}", val)
            if h:
                return h.group(0)
    for cand in ("ink", "cream", "text", "fg", "body", "foreground", "copy"):
        v = _root_var(css, cand)
        if v:
            return v
    return None


def load_lead_from_site(site_dir: Path) -> dict:
    """Load lead context from <site_dir>/meta.json (written by Bot 5).

    Returns a lead-like dict with at least ``lead_id`` plus ``name``/``phone``
    when available, so content-accuracy checks work without target_leads.json.
    """
    fallback = {"lead_id": site_dir.name}
    try:
        raw = json.loads((site_dir / "meta.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback
    if not isinstance(raw, dict):
        return fallback
    lead: dict = {"lead_id": raw.get("lead_id") or site_dir.name}
    business = raw.get("business")
    if isinstance(business, dict):
        for k in ("name", "category", "address", "phone", "website",
                  "rating", "review_count"):
            if business.get(k) is not None:
                lead[k] = business[k]
    return lead


DEFAULT_REVIEWER_MODEL = "opencode/big-pickle"
REVIEW_TIMEOUT = 180


def _reviewer_model() -> str:
    return (os.environ.get("AGENCY_QA_MODEL", "").strip()
            or os.environ.get("AGENCY_OPENCODE_MODEL", "").strip()
            or DEFAULT_REVIEWER_MODEL)


def reviewer_available() -> bool:
    """True when a working OpenCode CLI exists for the reviewer gate."""
    try:
        from website_generator import opencode_available
        return opencode_available()
    except Exception:  # noqa: BLE001
        return False


def run_reviewer_command(site_dir: Path, prompt: str, timeout: int = REVIEW_TIMEOUT) -> str:
    """Run a fresh opencode reviewer instance in site_dir; return stdout.

    Raises RuntimeError on any failure — callers treat it as abstention.
    """
    from website_generator import _opencode_argv
    site_dir.mkdir(parents=True, exist_ok=True)
    prefix = _opencode_argv()
    model = _reviewer_model()
    command = [*prefix, "run", "--agent", "build", "--auto", "--model", model]
    print(f"[qa_bot] reviewer starting in {site_dir} "
          f"(model={model}, timeout={timeout}s)", flush=True)
    try:
        proc = subprocess.run(
            command, input=prompt,
            cwd=str(site_dir), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"reviewer timed out after {timeout}s") from e
    except OSError as e:
        raise RuntimeError(f"could not start reviewer: {e}") from e
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "no diagnostic output").strip()
        raise RuntimeError(f"reviewer exited {proc.returncode}: {detail[-2000:]}")
    return proc.stdout or ""


def build_review_prompt(lead: dict, structural: dict) -> str:
    """Reviewer brief: taste only (structure already passed Gate 1)."""
    name = lead.get("name") or lead.get("lead_id") or "this business"
    category = lead.get("category") or "local business"
    struct_issues = structural.get("issues") or []
    return f"""You are a senior UI/UX design critic judging a website like an
Awwwards juror. The site for "{name}" ({category}) lives in this directory:
read index.html, styles.css, and script.js IN FULL before judging.

Structural checks already passed — your job is TASTE ONLY:
- Visual hierarchy: does the eye land on the business name, then the offer,
  then one clear CTA? Or does everything shout at once?
- Typography: display + body pairing, sizes, line length, rhythm. Any
  system-font fallback smell, awkward wraps, oversized paragraphs?
- Spacing: section padding, card gutters, alignment. Anything cramped or
  swimming in whitespace?
- Color restraint: does the palette feel designed for THIS business, or
  generic gradient soup? Max 2 accent colors used with discipline?
- Copy quality: specific (streets, dishes, services, prices, hours) or
  adjective-stuffed filler? No lorem ipsum, no "welcome to our website".
- Mobile feel (infer from CSS): will this hold together at 360px? Hero
  type scale, nav collapse, tap targets.
- One-visit test: after 10 seconds, could a visitor name the business,
  what it does, and how to contact it?

IMPORTANT CONTEXT — this is a locked outreach preview, not the final site:
a preview banner, noindex tag, and demo-only form messaging ("goes live
when the site launches") are INTENTIONAL. Never flag them as issues.

Context: structural gate found {len(struct_issues)} issue(s):
{json.dumps(struct_issues[:10], ensure_ascii=False) or "none"}.

Reply with EXACTLY one JSON block and nothing else:
```json
{{"verdict": "pass" | "fail", "issues": ["concrete fix", ...], "praise": ["..."]}}
```
Rules: verdict "fail" REQUIRES at least one concrete issue (file + element +
fix). Never invent pages or images. Be strict but fair: pass genuinely good
work, fail the forgettable. Maximum 6 issues, most important first.
"""


def parse_review_output(out: str) -> dict | None:
    """Extract the reviewer verdict JSON; None when unparseable (abstain)."""
    if not (out or "").strip():
        return None
    m = re.search(r"```json\s*(\{.*?\})\s*```", out, re.S)
    blob = m.group(1) if m else out.strip()
    if not m:
        start, end = blob.find("{"), blob.rfind("}")
        if start < 0 or end <= start:
            return None
        blob = blob[start:end + 1]
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("verdict") not in ("pass", "fail"):
        return None
    issues = data.get("issues") or []
    if not isinstance(issues, list):
        return None
    issues = [str(i).strip() for i in issues if str(i).strip()][:6]
    if data["verdict"] == "fail" and not issues:
        return None  # fail with no fixes is unactionable — abstain
    praise = data.get("praise") or []
    praise = [str(p).strip() for p in praise if str(p).strip()][:4] \
        if isinstance(praise, list) else []
    return {"verdict": data["verdict"], "issues": issues, "praise": praise}


def review_with_opencode(site_dir: Path, lead: dict, structural: dict,
                         timeout: int = REVIEW_TIMEOUT) -> dict:
    """Gate 2: fresh reviewer instance. Never raises; abstains on failure.

    Returns {"engine": "opencode"|"abstained"|"skipped", "model": str,
    "issues": [...], "praise": [...], "verdict": ...}.
    """
    model = _reviewer_model()
    if not reviewer_available():
        print("[qa_bot] reviewer skipped (no OpenCode CLI) — structural verdict stands",
              flush=True)
        return {"engine": "skipped", "model": model, "issues": [],
                "praise": [], "verdict": "abstain"}
    prompt = build_review_prompt(lead, structural)
    out = ""
    parsed = None
    try:
        out = run_reviewer_command(site_dir, prompt, timeout=timeout)
        parsed = parse_review_output(out)
        if parsed is None:
            # One retry with an explicit format nudge — models sometimes
            # bury the verdict in prose on the first attempt.
            print("[qa_bot] reviewer reply unparseable — retrying once", flush=True)
            out = run_reviewer_command(
                site_dir, prompt + "\nReply with ONLY the JSON block, no other text.",
                timeout=timeout)
            parsed = parse_review_output(out)
    except RuntimeError as e:
        print(f"[qa_bot] reviewer abstained ({e}) — structural verdict stands", flush=True)
        return {"engine": "abstained", "model": model, "issues": [],
                "praise": [], "verdict": "abstain", "error": str(e)}
    parsed = parse_review_output(out)
    if parsed is None:
        print("[qa_bot] reviewer abstained (unparseable verdict) — structural verdict stands",
              flush=True)
        return {"engine": "abstained", "model": model, "issues": [],
                "praise": [], "verdict": "abstain",
                "raw_snippet": (out or "")[:300]}
    print(f"[qa_bot] reviewer verdict: {parsed['verdict']} "
          f"({len(parsed['issues'])} issue(s))", flush=True)
    return {"engine": "opencode", "model": model, **parsed}


def check_site(site_dir: Path, lead: dict, threshold: int) -> dict:
    issues: list[str] = []
    recommendations: list[str] = []
    details: dict = {}
    score = 100
    lid = lead.get("lead_id") or site_dir.name

    def penalize(points: int, issue: str, recommendation: str = ""):
        nonlocal score
        score -= points
        issues.append(issue)
        if recommendation:
            recommendations.append(recommendation)

    index = site_dir / "index.html"
    css_p = site_dir / "styles.css"
    js_p = site_dir / "script.js"
    for f, w in ((index, 25), (css_p, 8), (js_p, 5)):
        if not f.exists():
            penalize(w, f"Missing required file: {f.name}",
                     f"Bot 5 must generate {f.name} before deploy")

    html = index.read_text(encoding="utf-8", errors="replace") if index.exists() else ""
    css = css_p.read_text(encoding="utf-8", errors="replace") if css_p.exists() else ""
    js = js_p.read_text(encoding="utf-8", errors="replace") if js_p.exists() else ""
    soup = BeautifulSoup(html, "html.parser") if html else None
    details["bytes"] = {"html": len(html), "css": len(css), "js": len(js),
                        "total_kb": round((len(html) + len(css) + len(js)) / 1024, 1)}
    local_imgs = [p for p in site_dir.glob("photo-*.*")
                  if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")]
    details["bytes"]["images_kb"] = round(sum(p.stat().st_size for p in local_imgs) / 1024, 1)
    details["bytes"]["n_images"] = len(local_imgs)

    if soup is None:
        return {"lead_id": lid, "passed": False, "score": 0, "issues": issues,
                "recommendations": recommendations, "checked_at": utc_now_iso(),
                "site_dir": str(site_dir), "details": details}

    # -- Document basics ------------------------------------------------
    if not re.match(r"\s*<!doctype html>", html[:200], re.I):
        penalize(4, "Missing or invalid <!DOCTYPE html>", "Start index.html with <!DOCTYPE html>")
    html_tag = soup.find("html")
    if not html_tag or not (html_tag.get("lang") or "").strip():
        penalize(3, "Missing lang attribute on <html>", 'Add lang="en" for accessibility/SEO')
    title = (soup.title.string.strip() if soup.title and soup.title.string else "")
    if len(title) < 5:
        penalize(4, "Missing/empty <title>", "Add a descriptive title with the business name")
    meta_desc = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    if not meta_desc or not (meta_desc.get("content") or "").strip():
        penalize(3, "Missing meta description", "Add <meta name='description'> for SEO")
    h1s = soup.find_all("h1")
    if len(h1s) == 0:
        penalize(5, "No <h1> heading", "Add exactly one <h1> with the business name")
    elif len(h1s) > 1:
        penalize(2, "Multiple <h1> headings", "Keep a single <h1>; use <h2> for sections")

    # -- Responsive / mobile ---------------------------------------------
    viewport = soup.find("meta", attrs={"name": re.compile(r"^viewport$", re.I)})
    if not viewport:
        penalize(10, "No viewport meta — mobile layout will break",
                 "Add <meta name='viewport' content='width=device-width, initial-scale=1'>")
    if not re.search(r"@media", css):
        penalize(8, "No responsive CSS breakpoints", "Add @media queries for small screens")
        recommendations.append("Verify the mobile nav toggle works at 360px width")
    if "nav-toggle" in html and "open" not in js and "toggle" not in js.lower():
        penalize(4, "Mobile navigation overlaps hero content (toggle has no JS handler)",
                 "Wire .nav-toggle to open/close .nav-links in script.js")

    # -- Navigation / buttons / links --------------------------------------
    nav = soup.find("nav")
    links = soup.find_all("a", href=True)
    if not nav:
        penalize(5, "Missing <nav> landmark", "Wrap main links in <nav aria-label='Main navigation'>")
    if len(links) < 3:
        penalize(4, "Too few navigable links", "Add Services / About / Reviews / Contact links")
    if not soup.find("a", href=re.compile(r"^tel:", re.I)):
        penalize(5, "No tel: call link", "Add a Call Now button with href='tel:+1...'")
    cta_text = soup.get_text(" ", strip=True)
    if not re.search(r"call|quote|book|schedule|appointment|contact", cta_text, re.I):
        penalize(4, "CTA button has insufficient prominence", "Add a visible Call/Quote/Book button above the fold")

    # -- Forms ---------------------------------------------------------------
    forms = soup.find_all("form")
    if not forms:
        penalize(5, "No contact/quote form", "Add a contact form with name/phone/message + validation")
    else:
        for f in forms:
            inputs = f.find_all(["input", "textarea"])
            unlabeled = [i for i in inputs if not (i.get("aria-label") or i.get("id"))
                         and not f.find("label")]
            if unlabeled:
                penalize(2, "Form inputs missing labels", "Associate each input with a <label>")
                break
            if not any(i.has_attr("required") for i in inputs):
                penalize(1, "Form has no required-field validation", "Mark required fields + JS check")
                break

    # -- Content accuracy (vs lead data) ---------------------------------------
    name = (lead.get("name") or "").strip()
    if name and name.lower() not in soup.get_text(" ", strip=True).lower():
        penalize(6, f"Business name '{name}' not found in page content",
                 "Use the exact business name from target_leads.json — do not invent one")
    phone = "".join(c for c in (lead.get("phone") or "") if c.isdigit())
    if phone and phone[-7:] not in re.sub(r"\D", "", soup.get_text()):
        penalize(4, "Business phone number missing from page", "Render the exact lead phone number")

    # -- Accessibility -----------------------------------------------------------
    imgs_no_alt = [i for i in soup.find_all("img") if not (i.get("alt") or "").strip()]
    if imgs_no_alt:
        penalize(3, f"{len(imgs_no_alt)} image(s) missing alt text", "Add descriptive alt attributes")
    if re.search(r"<button(?![^>]*aria-label)[^>]*>\s*</button>", html):
        penalize(2, "Empty <button> element", "Give every button visible text")
    m = re.search(r"\.btn-primary\s*\{([^}]*)\}", css)
    if m and not ("background" in m.group(1) and "color" in m.group(1)):
        penalize(2, "CTA button has insufficient contrast definition",
                 "Set both background and color on .btn-primary (contrast ≥ 4.5:1)")

    # -- Performance ---------------------------------------------------------------
    if details["bytes"]["total_kb"] > 500:
        penalize(4, f"Page weight {details['bytes']['total_kb']}KB is heavy",
                 "Compress images, drop unused CSS/JS")
    if len(re.findall(r"<img", html, re.I)) > 15:
        penalize(2, "Many unoptimized images", "Lazy-load below-fold images (loading='lazy')")
    if details["bytes"]["images_kb"] > 1500:
        penalize(3, f"Local photos total {details['bytes']['images_kb']}KB — too heavy",
                 "Recompress photos (each ≤450KB, total ≤1.5MB)")
    for p in local_imgs:
        if p.stat().st_size > 500 * 1024:
            penalize(2, f"Photo {p.name} is {round(p.stat().st_size / 1024)}KB (cap 500KB)",
                     "Recompress this photo before deploy")
            break
    if soup:
        hotlinked = [i.get("src", "") for i in soup.find_all("img", src=True)
                     if re.match(r"https?://", (i.get("src") or "").strip(), re.I)]
        if hotlinked:
            penalize(3, f"Hotlinked remote image(s): {hotlinked[0][:80]}",
                     "Download real photos into the site folder — never hotlink (they rot/get blocked)")
        no_alt = [i for i in soup.find_all("img") if not (i.get("alt") or "").strip()]
        if no_alt:
            penalize(2, f"{len(no_alt)} image(s) missing alt text",
                     "Describe every photo with a real alt attribute")

    # -- Broken local elements -------------------------------------------------------
    for tag, attr in (("a", "href"), ("img", "src"), ("link", "href"), ("script", "src")):
        for el in soup.find_all(tag, **{attr: True}):
            ref = (el.get(attr) or "").strip()
            if not ref or ref.startswith(("#", "http", "https:", "mailto:", "tel:", "data:")):
                continue
            if not (site_dir / ref.split("?")[0].split("#")[0]).exists():
                penalize(3, f"Broken {tag} reference: {ref}", f"Fix or remove the dead '{ref}' link")
                break

    # -- Design quality (agency bar — generic templates fail here) -----------
    body_text = soup.get_text(" ", strip=True) if soup else ""
    words = len(re.findall(r"[A-Za-z0-9']+", body_text))
    details["words"] = words
    n_sections = len(soup.find_all("section")) if soup else 0
    details["sections"] = n_sections
    if n_sections < 5:
        penalize(8, f"Only {n_sections} <section> blocks — looks like a stub",
                 "Ship topbar/header/hero/trust-strip/services/about/reviews/visit/footer (7+ sections)")
    if words < 400:
        penalize(6, f"Only ~{words} words of copy — too thin to sell anything",
                 "Write 400+ words of specific copy (services, about, reviews, hours)")
    n_cards = len(soup.select(".cards li, .card, .stack-card, .cap-list li, .price-card, .svc-list li")) if soup else 0
    if n_cards < 4:
        penalize(6, f"Only {n_cards} service cards — looks unfinished",
                 "Offer 6 specific service cards for this category, not 3 generic ones")
    filler = [p for p in ("quality work, fair prices",
                          "lorem ipsum", "welcome to our website",
                          "ask us about recent customer feedback")
              if p in body_text.lower()]
    if filler:
        penalize(7, f"Generic filler copy: {', '.join(filler)}",
                 "Replace filler with specific copy (street, services, prices, hours)")
    name_hits = body_text.lower().count(name.lower()) if name else 0
    first = (name.strip().split() or [""])[0].lower() if name else ""
    first_hits = body_text.lower().count(first) if len(first) > 2 else 0
    if name and name_hits < 3 and first_hits < 4:
        penalize(4, f"Business name appears only {name_hits}x — feels templated",
                 "Mention the business name in hero, about, and footer at minimum")
    cat = (lead.get("category") or "").strip().lower()
    cat_words = [w for w in re.findall(r"[a-z]{4,}", cat)]
    if cat and len(cat) > 3 and cat not in body_text.lower() \
            and not any(w in body_text.lower() for w in cat_words):
        penalize(3, f"Category '{lead.get('category')}' never mentioned in copy",
                 "Weave the category into hero subcopy and service descriptions")
    if not any(v in css for v in ("--brand", "--matcha", "--primary", "--accent")) \
            or "#0b5fff" in css.lower():
        penalize(5, "Default/generic brand styling (template blue or no theme)",
                 "Define a category palette via --brand/--brand2/--gold custom properties")
    if "linear-gradient" not in css and "radial-gradient" not in css:
        penalize(3, "Flat, unstyled hero — no gradient or visual hierarchy",
                 "Style the hero with a gradient, badge, and layered CTA row")
    if ".topbar" not in html and "topbar" not in css:
        penalize(2, "No utility top bar (address/hours/phone strip)",
                 "Add a topbar with address, hours, and click-to-call")
    if "review-slider" not in html and "review" not in html.lower():
        penalize(3, "No reviews/testimonials section", "Add a 3-quote review slider with dots")
    js_low = js.lower()
    html_low = html.lower()
    has_nav_js = ("nav-toggle" in html_low or "nav-toggle" in js_low
                  or "toggle" in js_low or "menu" in js_low)
    if not has_nav_js:
        penalize(2, "JS missing mobile nav toggle",
                 "Implement a menu toggle in script.js")
    if "quote-form" not in html_low and "quote-form" not in js_low \
            and "inquiry-form" not in html_low and "inquiry-form" not in js_low:
        penalize(2, "JS missing contact form handler",
                 "Implement contact form handler in script.js")
    if "scrollintoview" not in js_low and "scroll-behavior" not in css.lower():
        penalize(2, "JS missing smooth anchor scrolling",
                 "Implement smooth anchor scrolling in script.js")
    if "setinterval" not in js_low and "slider" not in js_low and "review" not in html.lower():
        penalize(2, "No interactive review slider behavior",
                 "Auto-rotate testimonials + dot navigation in script.js")
    if "<table" not in html.lower() and "hour" not in body_text.lower():
        penalize(2, "No hours table", "Add an hours table in the visit section")

    # -- SEO / sharing identity ---------------------------------------------
    canonical_ok = any(
        lk.get("rel") and "canonical" in [str(r).lower() for r in lk.get("rel")]
        for lk in soup.find_all("link"))
    if not canonical_ok:
        penalize(2, "No canonical URL", "Add <link rel='canonical'> so shares/SEO point to one URL")
    if not soup.find("meta", attrs={"property": re.compile(r"^og:", re.I)}):
        penalize(2, "No Open Graph metadata",
                 "Add og:title/og:description/og:image so link previews render correctly")
    if not soup.find("meta", attrs={"name": re.compile(r"^twitter:card$", re.I)}):
        penalize(1, "No Twitter card metadata", "Add name='twitter:card' meta")
    ld = soup.find("script", attrs={"type": re.compile(r"^application/ld\+json$", re.I)})
    if not ld:
        penalize(2, "No JSON-LD structured data",
                 "Add a LocalBusiness/Restaurant JSON-LD block (name, address, phone)")
    if "overflow-x:clip" not in css and "overflow-x:hidden" not in css \
            and "overflow-x:clip" not in html_low and "overflow-x:hidden" not in html_low:
        penalize(3, "No layout overflow guard — page may scroll horizontally",
                 "Add overflow-x:clip on html/body AND keep the marquee in an overflow:hidden wrapper")
    if "marquee" in html_low and 'class="marquee"' in html and "aria-hidden" not in html_low:
        penalize(1, "Marquee duplicates not hidden from screen readers",
                 "Set aria-hidden='true' on the decorative marquee")

    # -- Review carousel ARIA -----------------------------------------------
    tablist = soup.find(attrs={"role": "tablist"})
    tabpanels = soup.find_all(attrs={"role": "tabpanel"})
    if tablist and not tabpanels:
        penalize(2, "Review slider tabs have no tab panels",
                 "Pair each dot (role=tab) with a quote card (role=tabpanel + aria-labelledby)")
    if tabpanels and not soup.find(attrs={"role": "tab"}):
        penalize(2, "Review tab panels have no tab controls",
                 "Give the dot controls role='tab' with aria-controls pointing at each panel")

    # -- Award-tier design rubric (Awwwards bar: Design 40, Usability 30) ----
    brand = _root_var(css, "brand") or "#000000"
    brand2 = _root_var(css, "brand2") or brand
    gold = _root_var(css, "gold") or "#000000"
    bg = _root_var(css, "bg") or "#ffffff"
    dark = _root_var(css, "dark") or "#000000"
    ink = _body_text_color(css)
    muted = _root_var(css, "muted")
    btn_bg = brand if (_contrast("#ffffff", brand) or 0) >= \
        (_contrast("#ffffff", brand2) or 0) else brand2
    c_btn = _contrast("#ffffff", btn_bg)
    if c_btn is not None and c_btn < 4.5:
        penalize(3, f"Button text contrast {c_btn:.1f}:1 on brand (needs 4.5:1)",
                 "Darken --brand/--brand2 until white button text passes WCAG AA")
    if ink is not None:
        c_body = _contrast(ink, bg)
        if c_body is not None and c_body < 4.5:
            penalize(3, f"Body text contrast {c_body:.1f}:1 (needs 4.5:1)",
                     "Fix body text/background pairing until it passes WCAG AA")
    c_gold = _contrast(gold, dark)
    if c_gold is not None and c_gold < 3.0:
        penalize(2, f"Gold accent contrast {c_gold:.1f}:1 on dark (needs 3:1)",
                 "Brighten --gold until accents pass 3:1 on dark surfaces")
    c_muted = _contrast(muted, bg) if muted else None
    if c_muted is not None and c_muted < 4.5:
        penalize(2, f"Secondary text contrast {c_muted:.1f}:1 (needs 4.5:1)",
                 "Darken --muted until secondary copy passes WCAG AA")
    if not re.search(r"font-size:\s*clamp\([^)]*vw", css):
        penalize(2, "No fluid display typography (clamp + viewport units)",
                 "Set the hero H1 with clamp() + vw so display type scales cinematically")
    fams = set()
    for link in soup.find_all("link", href=True):
        m = re.search(r"family=([^:&]+)", link["href"])
        if m:
            fams.add(m.group(1).lower())
    if len(fams) > 3:
        penalize(1, f"{len(fams)} font families loaded — pick two at most",
                 "Limit Google Fonts to one display + one body family")
    if "prefers-reduced-motion" not in css and "prefers-reduced-motion" not in js_low:
        penalize(2, "Motion ignores prefers-reduced-motion",
                 "Gate every animation/auto-rotate on prefers-reduced-motion")
    if (re.search(r"class=\"[^\"]*\breveal\b", html_low) and ".reveal" in css
            and "opacity:0" in css.replace(" ", "") and "body.js" not in css):
        penalize(3, "Scroll reveals hide content when JS is off",
                 "Scope hidden initial states behind a JS-added class (e.g. body.js)")
    wow_markers = ("heroAurora" in html, 'class="stack"' in html,
                   "data-count" in html, "hero-ghost" in html,
                   "horizontal-scroll" in html, "sticky-stack" in html)
    if not any(wow_markers):
        penalize(2, "No signature moment — competent but forgettable",
                 "Add ONE: aurora/canvas hero, sticky-stacking cards, count-up stats, "
                 "or scroll-driven horizontal gallery")
    if "feTurbulence" not in css and "feTurbulence" not in html \
            and "::selection" not in css and "hero-ghost" not in html:
        penalize(1, "No texture craft (grain, selection color, ghost art)",
                 "Add film grain, ::selection styling, or layered ghost art")

    day_only = re.compile(
        r"^(mon(day)?|tue(sday)?|wed(nesday)?|thu(rsday)?|fri(day)?|"
        r"sat(urday)?|sun(day)?)\s*:?\s*$", re.I)
    split_days = [p.get_text(strip=True) for p in soup.find_all("p")
                  if day_only.match(p.get_text(strip=True) or "")]
    if split_days:
        penalize(2, f"Footer hours split across lines ({', '.join(split_days)})",
                 "Keep each day paired with its hours on a single line, e.g. 'Fri: 11 AM – 2 AM'")

    score = max(0, min(100, score))
    passed = score >= threshold
    if not passed:
        recommendations.append("Fix the issues above in Bot 5, then re-run QA (no auto-loop)")
    return {"lead_id": lid, "passed": passed, "score": score, "issues": issues,
            "recommendations": sorted(set(recommendations)), "checked_at": utc_now_iso(),
            "site_dir": str(site_dir), "details": details}


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Bot 6: QA one generated site (fail stops pipeline)")
    p.add_argument("site", nargs="?", default=None,
                   help="Path to generated_sites/<lead_id>")
    p.add_argument("--site", dest="site_opt", default=None,
                   help="Same as positional site (CLI convenience)")
    p.add_argument("--threshold", "-t", type=int, default=PASS_THRESHOLD)
    p.add_argument("--output", "-o", default=None,
                   help="Report path (default: <site>/qa_report.json)")
    p.add_argument("--no-reviewer", action="store_true",
                   help="Skip Gate 2 (opencode taste review); structural checks only")
    p.add_argument("--review-timeout", type=int, default=REVIEW_TIMEOUT,
                   help="Seconds for the reviewer instance (default: 180)")
    return p.parse_args(argv)


def append_report(path: Path, report: dict) -> list[dict]:
    """Append report to qa history file; create it if missing.

    File shape is always a list (latest last). Legacy single-dict files
    are migrated to [old, new]. Corrupt/empty files are reset to [report].
    Returns the full history list.
    """
    history: list[dict] = []
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = None
        if isinstance(existing, list):
            history = [r for r in existing if isinstance(r, dict)]
        elif isinstance(existing, dict):
            history = [existing]
    history.append(report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    return history


def main(argv=None, site_path: str | Path | None = None, **kwargs) -> str | int:
    """QA one generated site. Returns qa_report.json path (str) on pass, int otherwise.

    Orchestrator use: ``main("generated_sites/lead_00001")`` or
    ``main(site_path="generated_sites/lead_00001")`` — a single folder-path
    string. Optional overrides: ``threshold``, ``output``.
    A failed gate returns 1; missing input returns 2.
    """
    # --- Normalize single-string input -----------------------------------
    # Allow main("generated_sites/lead_00001") shorthand.
    if isinstance(argv, (str, Path)) and site_path is None:
        site_path = argv
        argv = []
    if argv is None:
        argv = []
    args = parse_args(argv)
    # site_path can also arrive via **kwargs (orchestrator style).
    if "site_path" in kwargs:
        if site_path is not None:
            raise TypeError("qa_bot.main() got site_path twice")
        site_path = kwargs.pop("site_path")
    # Legacy alias: main(site="...").
    if "site" in kwargs:
        if site_path is not None:
            raise TypeError("qa_bot.main() got site_path twice")
        site_path = kwargs.pop("site")
    for _k, _v in kwargs.items():
        if not hasattr(args, _k):
            raise TypeError(f"qa_bot.main() got an unexpected option {_k!r}")
        setattr(args, _k, _v)
    site_str = site_path if site_path is not None else (args.site or args.site_opt)
    if not site_str:
        print("[qa_bot] ERROR: pass the site folder path, e.g. "
              'main("generated_sites/lead_00001")', file=sys.stderr)
        return 2
    site_dir = Path(site_str)
    if not site_dir.exists() or not site_dir.is_dir():
        print(f"[qa_bot] ERROR: site not found: {site_dir}", file=sys.stderr)
        return 2
    report = check_site(site_dir, load_lead_from_site(site_dir), args.threshold)
    # Gate 2 (taste): only when structure passed — no point having a critic
    # review a site missing files. Reviewer issues merge into the report so
    # Bot 5 fixes them on rebuild; each costs 2 points (cap 10).
    reviewer: dict = {"engine": "skipped", "model": _reviewer_model(),
                      "issues": [], "praise": [], "verdict": "abstain"}
    if report.get("passed") and not args.no_reviewer:
        reviewer = review_with_opencode(site_dir, load_lead_from_site(site_dir),
                                        report, timeout=args.review_timeout)
        tagged = [f"[reviewer] {i}" for i in reviewer.get("issues", [])]
        if tagged:
            report["issues"].extend(tagged)
            report["score"] = max(0, report["score"] - min(10, 2 * len(tagged)))
            if reviewer.get("verdict") == "fail":
                report["passed"] = False
                report.setdefault("recommendations", []).append(
                    "Fix the [reviewer] taste issues above in Bot 5, then re-run QA")
    report["reviewer"] = reviewer
    out_path = Path(args.output) if args.output else (site_dir / "qa_report.json")
    append_report(out_path, report)
    # Always mirror inside the site folder for the pipeline.
    if out_path.resolve() != (site_dir / "qa_report.json").resolve():
        append_report(site_dir / "qa_report.json", report)
    status = "PASS" if report["passed"] else "FAIL"
    print(f"[qa_bot] {status} score={report['score']} {report['lead_id']} -> {out_path}", flush=True)
    for i in report["issues"]:
        print(f"  - {i}", flush=True)
    if not report["passed"]:
        print("[qa_bot] FAILED QA stops the pipeline (fix in Bot 5, re-run QA)", file=sys.stderr)
        return 1
    return str(out_path)


if __name__ == "__main__":
    _rc = main(sys.argv[1:])
    raise SystemExit(_rc if isinstance(_rc, int) else 0)
