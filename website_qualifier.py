"""
website_qualifier.py (Bot 3) — Find businesses with BAD websites.

Pipeline:
    with_websites.json
            ↓
    Visit each website
            ↓
    Analyze (heuristics, no paid API):
      • Visual design / Modernity
      • Mobile responsiveness
      • Navigation / UX
      • Performance
      • Readability
      • Business information
      • Calls-to-action
      • Contact / booking functionality
      • Overall professionalism
            ↓
    Is the website good?
           ↙       ↘
         YES        NO (score < --threshold)
          ↓          ↓
       discard    keep → bad_websites.json

Each kept entry retains the original business fields plus:
    {
      "name": "Joe's Auto Repair",
      ...original fields...,
      "website_analysis": {
        "score": 38,
        "verdict": "bad",
        "problems": ["Outdated visual design", ...],
        "strengths": [...],
        "details": {...},
        "checked_at": "2026-09-07T...",
        "final_url": "https://..."
      }
    }

This bot does NOT generate a website. It only answers:
"Does this business's existing site look good enough, or is it a
potential website client?" Its output feeds Bot 5 alongside
without_websites.json.

Usage:
    pip install requests beautifulsoup4
    python website_qualifier.py with_websites.json
    python website_qualifier.py with_websites.json --output bad_websites.json --threshold 60
    python website_qualifier.py --input with_websites.json --max 30 --workers 5
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

try:  # Windows consoles default to cp1252; keep unicode output from crashing
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

DEFAULT_THRESHOLD = 60
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/120.0.0.0 Safari/537.36")

CTA_PATTERNS = re.compile(
    r"\b(book|booking|schedule|appointment|call now|get (a )?quote|free estimate|"
    r"contact us|order online|shop now|sign up|get started)\b", re.I)
PHONE_PAT = re.compile(r"(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")
HOURS_PAT = re.compile(r"\b(open|hours|mon|tue|wed|thu|fri|sat|sun|am|pm)\b", re.I)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
BAD_EMAIL_SUBS = ("example.", "sentry", "wixpress", "schema.", "email-protection",
                  "noreply", "no-reply", "donotreply", "mailer-daemon", "postmaster@")
BAD_EMAIL_EXTS = {"png", "jpg", "jpeg", "gif", "svg", "css", "js",
                  "webp", "ico", "woff", "woff2"}
CONTACT_PATHS = ("/contact", "/contact-us", "/contactus", "/about", "/about-us")


def extract_emails(html: str, page_url: str) -> list[str]:
    """Harvest real contact emails from a page, best-first.

    mailto: links win; same-domain addresses beat generic gmail/yahoo ones.
    Junk (examples, assets, noreply, obfuscated) is dropped. Never invents.
    """
    if not html:
        return []
    try:
        site_domain = (urlparse(page_url).netloc or "").lower().removeprefix("www.")
    except Exception:  # noqa: BLE001
        site_domain = ""
    soup = BeautifulSoup(html, "html.parser")
    mailtos: list[str] = []
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if href.lower().startswith("mailto:"):
            addr = href[7:].split("?")[0].strip()
            if addr and "@" not in addr:
                continue
            if addr:
                mailtos.append(addr)
    cands: list[str] = []
    for m in EMAIL_RE.findall(html):
        e = m.strip().strip(".,;:").lower()
        if len(e) > 100 or not e or "@" not in e:
            continue
        local, _, domain = e.partition("@")
        if not local or not domain or "." not in domain:
            continue
        if any(s in e for s in BAD_EMAIL_SUBS):
            continue
        if local.split(".")[-1] in BAD_EMAIL_EXTS:
            continue  # asset filename parsed as address (logo.png@x.com)
        if domain.split(".")[-1] in BAD_EMAIL_EXTS:
            continue
        cands.append(e)
    ordered = list(dict.fromkeys(mailtos + cands))  # dedupe, mailtos first
    same = [e for e in ordered if site_domain and e.endswith("@" + site_domain)]
    rest = [e for e in ordered if e not in same]
    return same + rest


def enrich_email(business: dict, fetched: dict, timeout: int) -> dict:
    """Return business with a harvested 'email' when it has none.

    Tries the fetched homepage first, then /contact-style pages. Never
    overwrites an email collected upstream; never fabricates one.
    """
    if (business.get("email") or "").strip():
        return business
    base = (fetched.get("final_url") or business.get("website") or "").strip()
    url = normalize_url(base)
    if not url:
        return business
    try:
        root = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    except Exception:  # noqa: BLE001
        return business
    pages = [fetched.get("html") or ""]
    if not extract_emails(pages[0], url):
        for path in CONTACT_PATHS:
            sub = fetch_site(root + path, timeout=min(timeout, 10))
            if sub["ok"] and sub["html"]:
                pages.append(sub["html"])
                if extract_emails(sub["html"], sub["final_url"]):
                    break
    for html in pages:
        found = extract_emails(html, url)
        if found:
            out = dict(business)
            out["email"] = found[0]
            out["email_source"] = "website_harvest"
            return out
    return business


def normalize_url(raw: str) -> str | None:
    if not raw or not isinstance(raw, str):
        return None
    u = raw.strip()
    if not u or u.lower() in {"null", "none", "n/a", "na", "-", "nan"}:
        return None
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    try:
        p = urlparse(u)
        if not p.netloc or "." not in p.netloc:
            return None
        return u
    except Exception:  # noqa: BLE001
        return None


def load_businesses(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        for key in ("businesses", "results", "data", "items"):
            if isinstance(raw.get(key), list):
                raw = raw[key]
                break
        else:
            raw = [raw]
    if not isinstance(raw, list):
        raise ValueError(f"{path} must contain a JSON list")
    return [r for r in raw if isinstance(r, dict)]


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def fetch_site(url: str, timeout: int = 20) -> dict:
    """GET url, return {ok, html, status, final_url, load_ms, size_kb, https, error}."""
    t0 = time.monotonic()
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT},
                         timeout=timeout, allow_redirects=True)
        load_ms = int((time.monotonic() - t0) * 1000)
        html = r.text if r.encoding or r.content else ""
        return {
            "ok": 200 <= r.status_code < 400 and bool(html.strip()),
            "html": html,
            "status": r.status_code,
            "final_url": str(r.url),
            "load_ms": load_ms,
            "size_kb": round(len(r.content) / 1024, 1),
            "https": str(r.url).lower().startswith("https://"),
            "error": None if 200 <= r.status_code < 400 else f"HTTP {r.status_code}",
        }
    except requests.RequestException as e:
        return {"ok": False, "html": "", "status": None, "final_url": url,
                "load_ms": int((time.monotonic() - t0) * 1000),
                "size_kb": 0.0, "https": url.lower().startswith("https://"),
                "error": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------------------
# Analysis (10 dimensions x 10 pts = 100)
# ---------------------------------------------------------------------------

def analyze_html(url: str, html: str, load_ms: int, size_kb: float,
                 https: bool, status: int | None) -> dict:
    """Score page 0-100. Returns {score, problems, strengths, details}."""
    soup = BeautifulSoup(html or "", "html.parser")
    problems: list[str] = []
    strengths: list[str] = []
    details: dict = {}
    scores: dict[str, int] = {}

    raw_text = soup.get_text(separator=" ", strip=True)
    text_len = len(raw_text)
    html_lower = (html or "").lower()

    # -- 1. Visual design -------------------------------------------------
    outdated_signals = sum([
        "<font" in html_lower,
        "<center" in html_lower,
        "<marquee" in html_lower,
        "flash" in html_lower and ".swf" in html_lower,
        bool(soup.find("table") and soup.find("table").find("font")),
    ])
    inline_style_count = len(soup.find_all(style=True))
    css_links = len(soup.find_all("link", rel=re.compile(r"stylesheet", re.I)))
    modern_css_hints = bool(re.search(r"bootstrap|tailwind|flex|grid|css variables|@media", html_lower))
    s = 10
    if outdated_signals >= 2:
        s -= 6
    elif outdated_signals == 1:
        s -= 3
    if inline_style_count > 40:
        s -= 3
    if css_links == 0 and not modern_css_hints:
        s -= 2
    s = max(0, min(10, s))
    scores["visual_design"] = s
    details.update({"outdated_signals": outdated_signals, "inline_styles": inline_style_count,
                    "css_links": css_links})
    if s <= 4:
        problems.append("Outdated visual design")
    elif s >= 8:
        strengths.append("Clean, modern visual presentation")

    # -- 2. Modernity ------------------------------------------------------
    year_marks = re.findall(r"(?:©|copyright)[^\d]{0,10}(19|20)\d{2}", html_lower)
    years = [int(m[1] + m[0] if False else 0) for m in []]  # placeholder
    years = [int(y) for y in re.findall(r"(?:©|copyright)[^\d]{0,10}((?:19|20)\d{2})", html_lower)]
    current_year = datetime.datetime.now().year
    stale_copyright = bool(years and max(years) < current_year - 3)
    has_meta_charset = soup.find("meta", charset=True) is not None
    has_doctype_modern = bool(re.match(r"\s*<!doctype html>", (html or "")[:200], re.I))
    s = 10
    if stale_copyright:
        s -= 4
    if not has_meta_charset:
        s -= 2
    if not has_doctype_modern:
        s -= 2
    if "wordpress 4" in html_lower or "joomla 2" in html_lower or "frontpage" in html_lower:
        s -= 3
    s = max(0, min(10, s))
    scores["modernity"] = s
    details.update({"copyright_years": years, "stale_copyright": stale_copyright})
    if s <= 4:
        problems.append("Site looks unmaintained / outdated technology")

    # -- 3. Mobile responsiveness -------------------------------------------
    viewport = soup.find("meta", attrs={"name": re.compile(r"^viewport$", re.I)})
    media_queries = len(re.findall(r"@media", html_lower))
    responsive_hints = bool(re.search(r"bootstrap|tailwind|viewport|mobile|responsive", html_lower))
    s = 10
    if viewport is None:
        s -= 6
    if media_queries == 0 and not responsive_hints:
        s -= 3
    s = max(0, min(10, s))
    scores["mobile_responsiveness"] = s
    details.update({"has_viewport": viewport is not None, "media_queries": media_queries})
    if s <= 4:
        problems.append("Poor mobile responsiveness")

    # -- 4. Navigation / UX --------------------------------------------------
    nav = soup.find("nav")
    links = soup.find_all("a", href=True)
    internal_links = [a for a in links if (a.get("href") or "").strip() not in ("", "#")]
    headings = soup.find_all(["h1", "h2"])
    s = 10
    if nav is None and len(internal_links) < 3:
        s -= 6
    elif nav is None:
        s -= 2
    if len(internal_links) == 0:
        s -= 4
    if not headings:
        s -= 2
    if len(soup.find_all("h1")) > 1:
        s -= 1
    s = max(0, min(10, s))
    scores["navigation_ux"] = s
    details.update({"has_nav": nav is not None, "link_count": len(internal_links)})
    if s <= 4:
        problems.append("Difficult navigation")
    elif s >= 8:
        strengths.append("Clear navigation structure")

    # -- 5. Performance ------------------------------------------------------
    imgs = soup.find_all("img")
    scripts = soup.find_all("script", src=True)
    s = 10
    if load_ms > 8000:
        s -= 6
    elif load_ms > 4000:
        s -= 3
    elif load_ms > 2500:
        s -= 1
    if size_kb > 3000:
        s -= 3
    elif size_kb > 1500:
        s -= 1
    if len(imgs) > 40 or len(scripts) > 20:
        s -= 2
    s = max(0, min(10, s))
    scores["performance"] = s
    details.update({"load_ms": load_ms, "size_kb": size_kb,
                    "images": len(imgs), "scripts": len(scripts)})
    if s <= 4:
        problems.append("Slow / heavy page load")

    # -- 6. Readability ------------------------------------------------------
    words = len(raw_text.split())
    h1 = soup.find("h1")
    title = (soup.title.string.strip() if soup.title and soup.title.string else "")
    s = 10
    if words < 100:
        s -= 5
    elif words < 250:
        s -= 2
    if not h1:
        s -= 3
    if not title or len(title) < 5:
        s -= 2
    s = max(0, min(10, s))
    scores["readability"] = s
    details.update({"words": words, "has_h1": h1 is not None, "title": title[:120]})
    if s <= 4:
        problems.append("Thin or poorly structured content")

    # -- 7. Business information ---------------------------------------------
    has_phone = bool(PHONE_PAT.search(raw_text)) or bool(soup.find("a", href=re.compile(r"^tel:", re.I)))
    has_address = bool(soup.find(attrs={"itemtype": re.compile(r"LocalBusiness|PostalAddress", re.I)})) \
        or bool(re.search(r"\b\d{1,5}\s+[A-Z][a-z]+\s+(St|Street|Ave|Avenue|Rd|Road|Blvd|Way|Dr|Drive)\b", raw_text)) \
        or bool(re.search(r"\b[A-Z]{2}\s+\d{5}\b", raw_text))
    has_hours = bool(HOURS_PAT.search(raw_text))
    found = sum([has_phone, has_address, has_hours])
    s = {0: 1, 1: 4, 2: 7, 3: 10}[found]
    scores["business_info"] = s
    details.update({"has_phone": has_phone, "has_address": has_address, "has_hours": has_hours})
    if s <= 4:
        problems.append("Missing basic business info (phone / address / hours)")
    elif s == 10:
        strengths.append("Complete business information")

    # -- 8. Calls-to-action ---------------------------------------------------
    buttons = soup.find_all(["button", "a"], string=re.compile(r".+"))
    cta_hits = CTA_PATTERNS.findall(raw_text)
    tel_links = len(soup.find_all("a", href=re.compile(r"^tel:", re.I)))
    s = 10
    if not cta_hits and tel_links == 0:
        s = 2
    elif len(cta_hits) <= 1 and tel_links == 0:
        s = 5
    elif len(cta_hits) >= 3 or tel_links >= 1:
        s = 10
    else:
        s = 7
    scores["cta"] = s
    details.update({"cta_hits": len(cta_hits), "tel_links": tel_links})
    if s <= 5:
        problems.append("Weak call-to-action")
    elif s >= 9:
        strengths.append("Strong calls-to-action")

    # -- 9. Contact / booking -------------------------------------------------
    forms = soup.find_all("form")
    contact_link = soup.find("a", href=re.compile(r"contact|book|schedule|appointment|quote", re.I))
    map_embed = bool(re.search(r"google\.com/maps|openstreetmap|mapbox|maps\.googleapis", html_lower))
    s = 10
    if not forms and contact_link is None and tel_links == 0:
        s = 2
    elif not forms and contact_link is None:
        s = 5
    elif forms and (contact_link is not None or tel_links > 0):
        s = 10
    else:
        s = 7
    s = max(0, min(10, s))
    scores["contact_booking"] = s
    details.update({"forms": len(forms), "has_contact_link": contact_link is not None,
                    "map_embed": map_embed})
    if s <= 5:
        problems.append("No clear contact / booking path")
    elif s >= 9:
        strengths.append("Easy contact / booking options")

    # -- 10. Professionalism / trust ------------------------------------------
    meta_desc = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    favicon = soup.find("link", rel=re.compile(r"icon", re.I))
    social = bool(re.search(r"facebook\.com|instagram\.com|linkedin\.com|yelp\.com", html_lower))
    s = 10
    if not https:
        s -= 4
    if meta_desc is None or not (meta_desc.get("content") or "").strip():
        s -= 2
    if favicon is None:
        s -= 1
    if status is not None and status != 200:
        s -= 2
    s = max(0, min(10, s))
    scores["professionalism"] = s
    details.update({"https": https, "has_meta_description": meta_desc is not None,
                    "has_favicon": favicon is not None, "social_links": social,
                    "http_status": status})
    if not https:
        problems.append("Not served over HTTPS")
    if s <= 4:
        problems.append("Unprofessional presentation (missing SEO basics / trust signals)")

    total = sum(scores.values())  # 0-100
    details["dimension_scores"] = scores
    # Dedupe problems while preserving order.
    seen: set[str] = set()
    problems = [p for p in problems if not (p in seen or seen.add(p))]
    seen_s: set[str] = set()
    strengths = [s_ for s_ in strengths if not (s_ in seen_s or seen_s.add(s_))]
    return {"score": total, "problems": problems, "strengths": strengths, "details": details}


def unreachable_analysis(url: str, error: str | None) -> dict:
    return {"score": 0,
            "problems": ["Website unreachable" + (f": {error}" if error else ""),
                         "No clear contact / booking path",
                         "Weak call-to-action"],
            "strengths": [],
            "details": {"fetch_error": error, "final_url": url}}


# ---------------------------------------------------------------------------
# Per-business worker
# ---------------------------------------------------------------------------

def qualify_one(business: dict, timeout: int) -> dict | None:
    """Return enriched business if BAD, else None. Never raises."""
    raw_site = (business.get("website") or "")
    url = normalize_url(raw_site) if isinstance(raw_site, str) else None
    if not url:
        return None  # no usable URL — belongs to without_websites.json, not here
    fetched = fetch_site(url, timeout=timeout)
    checked_at = utc_now_iso()
    if not fetched["ok"]:
        analysis = unreachable_analysis(fetched["final_url"], fetched["error"])
    else:
        a = analyze_html(fetched["final_url"], fetched["html"],
                         fetched["load_ms"], fetched["size_kb"],
                         fetched["https"], fetched["status"])
        analysis = {**a, "final_url": fetched["final_url"]}
        analysis["checked_at"] = checked_at
        return_enriched = enrich_email(dict(business), fetched, timeout)
        return_enriched["website_analysis"] = {
            "score": a["score"],
            "verdict": None,  # set by caller via threshold
            "problems": a["problems"],
            "strengths": a["strengths"],
            "details": a["details"],
            "checked_at": checked_at,
            "final_url": fetched["final_url"],
        }
        return return_enriched
    if not fetched["ok"]:
        enriched = dict(business)
        enriched["website_analysis"] = {
            "score": 0,
            "verdict": None,
            "problems": analysis["problems"],
            "strengths": [],
            "details": {**analysis["details"], "load_ms": fetched["load_ms"],
                        "size_kb": fetched["size_kb"], "http_status": fetched["status"]},
            "checked_at": checked_at,
            "final_url": fetched["final_url"],
        }
        return enriched
    return None  # unreachable safety


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Bot 3: visit sites in with_websites.json, keep the BAD ones -> bad_websites.json")
    p.add_argument("input", nargs="?", default="with_websites.json",
                   help="Input JSON (default: with_websites.json)")
    p.add_argument("--input", "-i", dest="input_opt", default=None)
    p.add_argument("--output", "-o", default="bad_websites.json",
                   help="Output for BAD websites (default: bad_websites.json)")
    p.add_argument("--threshold", "-t", type=int, default=DEFAULT_THRESHOLD,
                   help=f"Scores BELOW this are bad (default: {DEFAULT_THRESHOLD})")
    p.add_argument("--max", type=int, default=0, help="Max businesses to check (0 = all)")
    p.add_argument("--workers", type=int, default=5, help="Parallel fetch workers (default: 5)")
    p.add_argument("--timeout", type=int, default=20, help="Per-site timeout seconds (default: 20)")
    p.add_argument("--good-output", default=None,
                   help="Optional: also write GOOD sites here (otherwise discarded)")
    p.add_argument("--skip-unreachable", action="store_true",
                   help="Skip unreachable sites instead of marking them bad")
    return p.parse_args(argv)


def main(argv: list[str] | None = None, **kwargs) -> str | int:
    """Visit sites, keep the BAD ones. Returns the output path (str) or int exit code.

    Orchestrator use: ``main(input="with_websites.json", output="bad_websites.json",
    threshold=60)`` — any keyword matching an argparse option overrides the
    default. A bare main() call uses defaults (sys.argv is only used via the CLI).
    """
    if argv is None:
        # Plain main() uses defaults (never sys.argv); the CLI passes
        # sys.argv[1:] explicitly via the __main__ block below.
        argv = []
    args = parse_args(argv)
    for _k, _v in kwargs.items():
        if not hasattr(args, _k):
            raise TypeError(f"website_qualifier.main() got an unexpected option {_k!r}")
        setattr(args, _k, _v)
    in_path = Path(args.input_opt or args.input)
    out_path = Path(args.output)

    if not in_path.exists():
        print(f"[website_qualifier] ERROR: input not found: {in_path}", file=sys.stderr)
        return 2
    try:
        businesses = load_businesses(in_path)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[website_qualifier] ERROR reading {in_path}: {e}", file=sys.stderr)
        return 2

    if args.max and args.max > 0:
        businesses = businesses[:args.max]

    print(f"[website_qualifier] checking {len(businesses)} site(s), "
          f"threshold={args.threshold} (score < threshold = bad)", flush=True)

    bad: list[dict] = []
    good: list[dict] = []
    unverifiable: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        future_map = {ex.submit(qualify_one, b, args.timeout): b for b in businesses}
        done = 0
        for fut in as_completed(future_map):
            done += 1
            orig = future_map[fut]
            try:
                enriched = fut.result()
            except Exception as e:  # noqa: BLE001 — never let one site kill the run
                print(f"[website_qualifier] WARN {orig.get('website')}: {e}", file=sys.stderr)
                continue
            if enriched is None:
                continue
            analysis = enriched.get("website_analysis", {})
            fetch_error = analysis.get("details", {}).get("fetch_error") or ""
            # Bot-blocked (403/429) means a site EXISTS but refused our bot —
            # it is unverifiable, not a bad-website lead. Never mark it bad.
            if fetch_error in ("HTTP 403", "HTTP 429"):
                analysis["verdict"] = "unverifiable"
                enriched["website_analysis"] = analysis
                unverifiable.append(enriched)
                print(f"[{done}/{len(businesses)}] SKIP bot-blocked ({fetch_error}) "
                      f"{orig.get('name')} {orig.get('website')}", flush=True)
                continue
            if fetch_error and args.skip_unreachable:
                print(f"[{done}/{len(businesses)}] SKIP unreachable {orig.get('website')}", flush=True)
                continue
            score = int(analysis.get("score", 0))
            if score < args.threshold:
                analysis["verdict"] = "bad"
                enriched["website_analysis"] = analysis
                bad.append(enriched)
                print(f"[{done}/{len(businesses)}] BAD ({score}) {orig.get('name')} {orig.get('website')}", flush=True)
            else:
                analysis["verdict"] = "good"
                enriched["website_analysis"] = analysis
                good.append(enriched)
                print(f"[{done}/{len(businesses)}] good ({score}) {orig.get('name')}", flush=True)

    # Keep output stable: worst first.
    bad.sort(key=lambda r: r.get("website_analysis", {}).get("score", 0))
    out_path.write_text(json.dumps(bad, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.good_output:
        Path(args.good_output).write_text(json.dumps(good, ensure_ascii=False, indent=2) + "\n",
                                          encoding="utf-8")
        print(f"[website_qualifier] good sites -> {args.good_output} ({len(good)})", flush=True)
    print(f"[website_qualifier] input: {in_path} ({len(businesses)} checked)", flush=True)
    print(f"[website_qualifier] bad -> {out_path} ({len(bad)}) | good discarded ({len(good)}) "
          f"| unverifiable bot-blocked skipped ({len(unverifiable)})", flush=True)
    print("[website_qualifier] feed to Bot 5: without_websites.json + bad_websites.json", flush=True)
    return str(out_path)


if __name__ == "__main__":
    _rc = main(sys.argv[1:])
    raise SystemExit(_rc if isinstance(_rc, int) else 0)
