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

Usage:
    python qa_bot.py generated_sites/lead_00001
    python qa_bot.py --site generated_sites/lead_00001
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
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
    n_cards = len(soup.select(".cards li, .card")) if soup else 0
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
    if "--brand" not in css or "#0b5fff" in css.lower():
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
    if "quote-form" not in html_low and "quote-form" not in js_low:
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
