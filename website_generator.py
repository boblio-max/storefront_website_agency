"""
website_generator.py (Bot 5) — Build a real working website per target lead.

Inputs:
    target_leads.json (+ business info; plus website_analysis when the
    lead comes from bad_websites.json)

Process:
    no-website lead:   business info → OpenCode → new website
    bad-website lead:  business info + existing problems → OpenCode →
                       completely improved website

    The bot creates generated_sites/<lead_id>/ and calls
    run_opencode_command(target_dir, prompt). If the OpenCode CLI is
    unavailable (or --no-opencode), it falls back to a built-in
    responsive template so the pipeline stays testable offline.

Output:
    generated_sites/
    └── <lead_id>/
        ├── index.html
        ├── styles.css
        ├── script.js
        └── meta.json

    The goal is an actual working website, not a description of one.

Usage:
    python website_generator.py --lead lead_00001
    python website_generator.py --all --limit 5
    python website_generator.py --lead lead_00001 --force --no-opencode
    python website_generator.py --lead lead_00001 --feedback feedback.json  (Bot 10 revision)
"""

from __future__ import annotations

import argparse
import datetime
import html as htmlmod
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

try:  # Windows consoles default to cp1252; keep unicode output from crashing
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

OPENCODE_CMD = "opencode"
AGENCY_NAME = os.environ.get("AGENCY_NAME", "Storefront")


def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def esc(s) -> str:
    return htmlmod.escape(str(s or ""), quote=True)


def slugify(name: str, fallback: str = "site") -> str:
    """Customer-facing slug: 'Bothell Way Garage' -> 'bothell-way-garage'.

    Used for site folders, GitHub repos, and Vercel projects so clients
    never see internal lead_00001 IDs. Lowercase alphanumerics + hyphens,
    capped at 50 chars.
    """
    import re as _re
    import unicodedata as _ud
    text = _ud.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    slug = _re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    slug = _re.sub(r"-{2,}", "-", slug)[:50].strip("-")
    return slug or fallback


def site_dir_for(lead: dict, out_root: Path) -> tuple[Path, str]:
    """Resolve the site dir for a lead: slug-based, collision-safe.

    Same business always maps to the same folder (stable across runs, so
    cached sites are reused). A different lead colliding on a slug gets a
    short stable_key suffix. Returns (dir, slug).
    """
    import hashlib as _hl
    lid = lead.get("lead_id") or "lead_unknown"
    slug = slugify(lead.get("name") or lid, fallback=lid.lower().replace("_", "-"))
    candidate = out_root / slug
    if candidate.exists():
        try:
            meta = json.loads((candidate / "meta.json").read_text(encoding="utf-8"))
            if isinstance(meta, dict) and meta.get("lead_id") in (lid, None):
                return candidate, slug
        except (OSError, json.JSONDecodeError):
            pass
        seed = str(lead.get("stable_key") or
                   _hl.md5(f"{lead.get('name')}|{lead.get('phone')}|"
                           f"{lead.get('address')}".encode()).hexdigest()[:10])
        slug = f"{slug}-{seed[:4]}"
        candidate = out_root / slug
    return candidate, slug


# ---------------------------------------------------------------------------
# OpenCode bridge
# ---------------------------------------------------------------------------

_OPENCODE_ARGV_CACHE: list[str] | None = None


def _opencode_argv() -> list[str]:
    """Argv prefix invoking the real OpenCode CLI.

    Bare `opencode` is unreliable on Windows: subprocess (no shell) resolves
    `opencode` -> `opencode.exe`, which can hit a broken shadow (e.g. an
    orphaned PyPI console script) instead of the real CLI's `opencode.cmd`.
    So prefer npm's opencode.cmd (run via cmd.exe) and sanity-check every
    candidate with `--version`, rejecting tracebacks. Result is cached.
    Raises RuntimeError if no working CLI is found.
    """
    global _OPENCODE_ARGV_CACHE
    if _OPENCODE_ARGV_CACHE is not None:
        return _OPENCODE_ARGV_CACHE
    candidates: list[list[str]] = []
    npm_cmd = Path.home() / "AppData" / "Roaming" / "npm" / "opencode.cmd"
    if npm_cmd.exists():
        candidates.append([os.environ.get("COMSPEC", "cmd.exe"), "/c", str(npm_cmd)])
    which_hit = shutil.which(OPENCODE_CMD)
    if which_hit:
        candidates.append([which_hit])
    candidates.append([OPENCODE_CMD])
    for prefix in candidates:
        try:
            p = subprocess.run([*prefix, "--version"], capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=30)
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            continue
        out = (p.stdout or "") + (p.stderr or "")
        if p.returncode == 0 and "Traceback" not in out \
                and "ModuleNotFoundError" not in out:
            _OPENCODE_ARGV_CACHE = prefix
            return prefix
    raise RuntimeError("No working OpenCode CLI found "
                       "(tried npm opencode.cmd + PATH `opencode`)")

def run_opencode_command(target_dir: Path, prompt: str, timeout: int = 600) -> str:
    """Run OpenCode in target_dir with prompt; return stdout.

    Uses `opencode run "<prompt>"` via the real OpenCode CLI (resolved by
    _opencode_argv, never a broken PATH shadow). Raises RuntimeError if the
    CLI is missing or exits non-zero — caller falls back to the template.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    prefix = _opencode_argv()
    try:
        proc = subprocess.run(
            [*prefix, "run", prompt],
            cwd=str(target_dir), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError("OpenCode run timed out")
    if proc.returncode != 0:
        raise RuntimeError(f"OpenCode exited {proc.returncode}: {(proc.stderr or '')[:500]}")
    return proc.stdout or ""


def _category_profile(category: str, name: str) -> dict:
    """Design + copy profile keyed off business category.

    Keeps the offline template from rendering the same generic page for a
    taco bar and a brake shop. Each profile supplies a palette, an emoji
    set, six services, trust-strip items, and review snippets that read
    like they belong to THIS business type.
    """
    c = (category or "").lower()
    if any(k in c for k in ("mexican", "restaurant", "taco", "pizza", "sushi",
                            "burger", "chicken", "bakery", "deli", "cafe",
                            "coffee", "espresso", "tea", "bar", "grill",
                            "bistro", "eatery", "food")):
        food = any(k in c for k in ("coffee", "espresso", "cafe", "tea", "bakery"))
        if food:
            return {
                "palette": ("#4a2c17", "#8b5e34", "#d9a441", "#faf5ec",
                            "#2b1a10", "#e8dcc8"),
                "emoji": "☕", "hero_kicker": "Freshly brewed in the neighborhood",
                "hero_sub": ("Single-origin espresso, fresh pastries, and a cozy "
                             "spot to work or catch up — served fast with a smile."),
                "services": [
                    ("Espresso & Pour-Overs", "Single-origin beans, dialed in daily, brewed to order."),
                    ("Seasonal Specials", "Rotating lattes, cold brew, and house-made syrups."),
                    ("Fresh Pastries", "Baked goods delivered every morning, gone by noon."),
                    ("Beans to Take Home", "Whole-bean bags ground to your brewer on request."),
                    ("Quick Commuter Stop", "Order ahead by phone — ready at the counter."),
                    ("Catering & Events", "Coffee boxes and pastry trays for meetings and parties."),
                ],
                "strip": [("☕", "Roasted Fresh", "Beans dialed in daily"),
                          ("🥐", "Baked Mornings", "Pastries every morning"),
                          ("⚡", "Fast Commuter Stop", "Call ahead, grab & go"),
                          ("⭐", "Neighborhood Favorite", "Come see why regulars stay")],
                "reviews": [
                    ("Best latte on this side of town — smooth, never bitter, and the staff remembers my order.", "Google review"),
                    ("Cozy spot with fast wifi. Pastry case is dangerous before 9am.", "Google review"),
                    ("Cold brew is strong and clean. My daily stop before work.", "Google review"),
                ],
            }
        return {
            "palette": ("#b3271e", "#e07b39", "#f2b33d", "#fff8f0",
                        "#260f0b", "#f3e2cf"),
            "emoji": "🔥", "hero_kicker": "Cooked fresh, served fast",
            "hero_sub": ("House recipes, generous portions, and food made to order. "
                         "Dine in, take out, or feed the whole crew — check the menu below."),
            "services": [
                ("Signature Mains", "The dishes regulars drive across town for, made to order."),
                ("Family & Party Platters", "Feed 3–8 with sides, bread, and sauces included."),
                ("Takeout in ~15 Min", "Call ahead and skip the wait — hot at the counter."),
                ("Lunch Specials", "Fast midday plates that beat fast food on price and taste."),
                ("Catering", "Trays and platters for offices, teams, and celebrations."),
                ("Daily Specials", "Ask what's cooking today — it sells out most days."),
            ],
            "strip": [("🔥", "Made Fresh", "Cooked to order, never frozen"),
                      ("👨‍👩‍👧‍👦", "Family Platters", "Feed the whole crew"),
                      ("⏱️", "Fast Takeout", "Ready in ~15 minutes"),
                      ("⭐", "Local Favorite", "Rated by your neighbors")],
            "reviews": [
                ("Flavor is unreal for the price. The family platter fed all five of us with leftovers.", "Google review"),
                ("Fast takeout, food still hot when I got home. New regular spot.", "Google review"),
                ("Staff is friendly and the specials board is always worth a look.", "Google review"),
            ],
        }
    if any(k in c for k in ("auto", "repair", "garage", "brake", "tire",
                            "transmission", "oil", "mechanic", "motorsport",
                            "towing", "body shop", "glass", "hvac", "plumb",
                            "electric", "roof", "contractor", "handyman")):
        return {
            "palette": ("#0f2a43", "#e8641b", "#f5a623", "#f7f9fc",
                        "#0b1c2e", "#dbe5f0"),
            "emoji": "🔧", "hero_kicker": "Honest repairs, clear pricing",
            "hero_sub": ("Diagnostics before dollars: we show you what's wrong, "
                         "quote it up front, and only fix what needs fixing. Free estimates."),
            "services": [
                ("Brakes & Rotors", "Pads, rotors, and fluid — inspected free, quoted up front."),
                ("Diagnostics & Check-Engine", "Dealer-level scan with plain-English explanation."),
                ("Oil & Maintenance", "Full-synthetic changes, filters, and fluid top-offs."),
                ("Engine & Transmission", "Major repairs with parts-and-labor warranty in writing."),
                ("Tires & Alignment", "New tires, rotations, and alignments that save fuel."),
                ("Pre-Purchase Inspections", "Buying used? 100+ point check before you sign."),
            ],
            "strip": [("🔧", "ASE-Certified Techs", "Fixed right the first time"),
                      ("🧾", "Up-Front Quotes", "Approve before we wrench"),
                      ("🛡️", "Warranty in Writing", "Parts & labor covered"),
                      ("⭐", "Trusted Locally", "Rated by your neighbors")],
            "reviews": [
                ("Quoted me before touching anything and finished same day. No upsell, just honest work.", "Google review"),
                ("Found the electrical gremlin two other shops missed. Fair price, clear explanation.", "Google review"),
                ("Pre-purchase inspection saved me from a lemon. Worth every penny.", "Google review"),
            ],
        }
    return {
        "palette": ("#123f2e", "#1f8a5b", "#f2b33d", "#f7faf7",
                    "#0c2318", "#d9e8dd"),
        "emoji": "★", "hero_kicker": "Local, reliable, easy to reach",
        "hero_sub": ("Real help from people nearby — clear pricing, fast response, "
                     "and work backed in writing. Call for a free quote today."),
        "services": [
            ("Free Quotes", "Tell us what you need — honest price before we start."),
            ("Same-Week Booking", "Most jobs scheduled within days, not weeks."),
            ("Quality Guarantee", "Not happy? We make it right, in writing."),
            ("Friendly Local Team", "You talk to the people doing the work."),
            ("Transparent Pricing", "No surprise fees — approve everything first."),
            ("Follow-Up Support", "Questions after the job? Just call us."),
        ],
        "strip": [("★", "Locally Owned", "Your neighbors, not a chain"),
                  ("🧾", "Clear Pricing", "Quote before we start"),
                  ("⚡", "Fast Response", "Same-week availability"),
                  ("🛡️", "Work Guaranteed", "Backed in writing")],
        "reviews": [
            ("Fast, friendly, and fairly priced. Will absolutely use them again.", "Google review"),
            ("Called in the morning, sorted by afternoon. Great communication.", "Google review"),
            ("Honest advice even when it meant less work for them. Rare these days.", "Google review"),
        ],
    }


def build_prompt(b: dict, feedback: dict | None, preview: bool = True) -> str:
    name = b.get("name") or "Local Business"
    category = b.get("category") or "local business"
    address = b.get("address") or ""
    phone = b.get("phone") or ""
    rating = b.get("rating")
    reviews = b.get("review_count")
    problems = (b.get("website_analysis") or {}).get("problems") or []
    maps_url = b.get("maps_url") or ""
    lines = [
        "Your working directory IS the website root. Create exactly these files "
        "right here in the current directory: index.html, styles.css, script.js. "
        "Do not create any subfolders and do not write files anywhere else.",
        f"Build a complete, professional, mobile-responsive website for '{name}' ({category}).",
        f"Business details: address='{address}', phone='{phone}', "
        f"rating={rating} ({reviews} reviews)." if (rating or reviews) else
        f"Business details: address='{address}', phone='{phone}'.",
        f"Google Maps link (use it for the directions button): {maps_url}" if maps_url else "",
        "Output exactly these three files in the current directory: index.html, styles.css, script.js.",
        "DESIGN BAR — this must look like a $3k agency site, not a template:",
        "- Distinctive look for THIS category (restaurant = warm/appetizing, auto = bold/trustworthy navy+orange, "
        "coffee = cozy cream+brown). Never default blue-on-white.",
        "- Sections in order: top utility bar, sticky header/nav, hero (badge + H1 + subcopy + 2 CTAs + trust meta), "
        "4-item trust strip, services grid (6 specific cards, not 3 generic ones), about/why-us split, "
        "reviews slider (3 quotes + dots), visit/hours + contact form, rich footer.",
        "- Real, specific copy mentioning the business name, street, and category throughout. "
        "BANNED filler: 'quality work, fair prices', 'Trusted X', 'Lorem ipsum', 'Welcome to our website'.",
        "- CSS: custom properties, sticky blurred header, gradient hero, cards with shadow+radius, "
        "@media breakpoints at ~760px (mobile nav toggle), focus-visible styles, smooth scroll.",
        "- JS: mobile nav toggle, smooth anchor scroll, review/testimonial slider, tab switching if a menu exists, "
        "contact form validation with inline success message, sticky-header shadow, current year in footer.",
        f"Credit the builder with a subtle footer line: 'Site by {AGENCY_NAME}'.",
        ("PREVIEW MODE (this build is a sales demo, NOT the launched site):"
         if preview else
         "FINAL BUILD (the client paid — this is the launched site):"),
        (f"- Fixed bottom banner on every viewport: 'Preview draft by {AGENCY_NAME} — "
         "design concept, not the business's official site.' Style it to match the theme; "
         "it must never overlap CTAs or the mobile nav."
         if preview else
         "- No preview banner, no demo notices — clean production build."),
        ('- <meta name="robots" content="noindex, nofollow"> so the demo never hijacks '
         "the business's Google rankings."
         if preview else
         "- Full indexable build (no robots noindex)."),
        ("- The contact form validates, then shows 'Thanks! (Demo preview — this form "
         "goes live when the site launches.)' and does NOT claim anyone was contacted."
         if preview else
         "- The contact form validates and shows a normal success message."),
        "Technical requirements: semantic HTML with <nav>, exactly one <h1>, CTA buttons (Call Now, Get a Quote, "
        "Book Appointment), tel: link with the exact phone given, contact form with required + JS validation, "
        "viewport meta, meta description, favicon (inline SVG data URI), alt text on images, address + hours table, "
        "JSON-LD LocalBusiness schema when the category fits.",
        "Use only vanilla HTML/CSS/JS, no external build step. Do not invent a different business name,",
        "address, or phone number — use exactly what is given. Reply with a one-line summary when done.",
    ]
    if problems:
        lines.append("The old site had these problems — every one must be fixed: " + "; ".join(problems))
    if feedback and feedback.get("requested_changes"):
        lines.append("CLIENT REVISION REQUEST — apply each item: "
                     + "; ".join(feedback["requested_changes"]))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Fallback template engine (offline)
# ---------------------------------------------------------------------------

def render_template(b: dict, feedback: dict | None,
                    preview: bool = True) -> dict[str, str]:
    """Offline fallback: a genuinely good category-aware site, not a stub.

    Mirrors the structure the OpenCode prompt demands (topbar, sticky
    header, hero, trust strip, 6 services, about split, review slider,
    visit/hours + form, footer) so QA and deploys look the same whichever
    engine built the page.

    preview=True (default) marks the build as a sales demo: preview banner,
    noindex, demo-only forms. preview=False renders the paid final build.
    """
    name = b.get("name") or "Local Business"
    category = b.get("category") or "Local Business"
    address = b.get("address") or "Contact us for our location"
    phone = (b.get("phone") or "").strip()
    rating = b.get("rating")
    reviews = b.get("review_count")
    maps_url = b.get("maps_url") or "#"
    problems = (b.get("website_analysis") or {}).get("problems") or []
    changes = (feedback or {}).get("requested_changes") or []
    prof = _category_profile(category, name)
    brand, brand2, gold, bg, dark, line = prof["palette"]

    digits = "".join(c for c in phone if c.isdigit())
    tel = f"tel:+1{digits[-10:]}" if len(digits) >= 10 else (
        "tel:+" + digits if digits else "#contact")
    rating_text = f"★ {rating} · {reviews} Google reviews" if rating and reviews else (
        f"★ {rating} rated" if rating else "Rated by your neighbors")
    first_word = esc(str(name).split()[0]) if str(name).split() else esc(name)
    city = esc(address.split(",")[-2].strip()) if address.count(",") >= 2 else esc(address)

    strip_html = "".join(
        f'<div class="strip-item"><span aria-hidden="true">{e}</span>'
        f"<div><strong>{t}</strong><small>{s}</small></div></div>"
        for e, t, s in prof["strip"])
    services_html = "".join(
        f'<li class="card"><h3>{esc(t)}</h3><p>{esc(d)}</p></li>'
        for t, d in prof["services"])
    quotes = prof["reviews"]
    reviews_html = "".join(
        f'<blockquote class="review{" active" if i == 0 else ""}">'
        f"<p>“{esc(q)}”</p><cite>— {esc(who)} ★★★★★</cite></blockquote>"
        for i, (q, who) in enumerate(quotes))
    dots_html = "".join(
        f'<button class="dot{" active" if i == 0 else ""}" aria-label="Review {i + 1}"></button>'
        for i in range(len(quotes)))
    rev_block = ("<section class='revisions'><h2>Latest updates per your feedback</h2><ul>"
                 + "".join(f"<li>{esc(c)}</li>" for c in changes) + "</ul></section>"
                 if changes else "")

    index = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="{esc(name)} — {esc(category)} in {city}. {esc(prof['hero_sub'])} Call {esc(phone) or 'today'} for a free quote.">
{'<meta name="robots" content="noindex, nofollow">' if preview else ''}
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect width='100' height='100' rx='22' fill='{brand}'/><text x='50' y='70' font-size='52' text-anchor='middle' fill='white' font-family='Arial' font-weight='bold'>{first_word[:2]}</text></svg>">
<title>{esc(name)} | {esc(category)} — {city}</title>
<link rel="stylesheet" href="styles.css">
</head>
<body id="top">
<div class="topbar"><div class="wrap topbar-inner">
<span>📍 {esc(address)}</span>
<span class="hide-mobile">⏰ Mon–Fri 8am–6pm · Sat 9am–3pm</span>
{('<a class="topbar-phone" href="' + esc(tel) + '">📞 ' + esc(phone) + '</a>') if phone else ''}
</div></div>
<header class="site-header" id="siteHeader">
<nav class="wrap" aria-label="Main navigation">
<a class="brand" href="#top"><span class="brand-badge" aria-hidden="true">{prof['emoji']}</span> {esc(name)}</a>
<button class="nav-toggle" aria-label="Toggle menu" aria-expanded="false" aria-controls="navLinks">☰</button>
<ul class="nav-links" id="navLinks">
<li><a href="#services">Services</a></li>
<li><a href="#about">Why Us</a></li>
<li><a href="#reviews">Reviews</a></li>
<li><a href="#visit">Visit</a></li>
<li><a href="#contact">Contact</a></li>
</ul>
{('<a class="btn btn-primary btn-call" href="' + esc(tel) + '">Call Now</a>') if phone else '<a class="btn btn-primary btn-call" href="#contact">Get a Quote</a>'}
</nav>
</header>
<main>
<section class="hero"><div class="wrap hero-inner">
<p class="badge">{esc(rating_text)} · {esc(category)}</p>
<h1>{esc(name)}<br><span class="accent">{esc(prof['hero_kicker'])}</span></h1>
<p class="tagline">{esc(prof['hero_sub'])}</p>
<div class="cta-row">
{('<a class="btn btn-primary" href="' + esc(tel) + '">Call ' + esc(phone) + '</a>') if phone else ''}
<a class="btn btn-secondary" href="#contact">Get a Free Quote</a>
<a class="btn btn-ghost" href="#services">Explore Services</a>
</div>
<div class="hero-meta"><div><strong>📍</strong> {esc(address)}</div><div><strong>✅</strong> Free quotes · No-pressure advice</div></div>
</div></section>
<section class="strip" aria-label="Why choose us"><div class="wrap strip-grid">{strip_html}</div></section>
<section id="services"><div class="wrap">
<h2>What we do</h2>
<p class="section-sub">Every job quoted up front at {esc(name)} — you approve before we start.</p>
<ul class="cards">{services_html}</ul>
</div></section>
<section id="about"><div class="wrap about-grid">
<div>
<h2>Why neighbors pick {esc(name)}</h2>
<p>{esc(category)} done right, close to home at {esc(address)}. {esc(prof['hero_sub'])}</p>
<ul class="checklist">
<li>✅ Up-front pricing — approve before we start</li>
<li>✅ Work backed in writing</li>
<li>✅ Fast scheduling, most jobs within days</li>
<li>✅ You talk to the people doing the work</li>
</ul>
</div>
<div class="about-card">
<h3>Visit us today</h3>
<p class="big">{esc(rating_text)}</p>
<p>{esc(address)}<br>{esc(phone)}</p>
{('<a class="btn btn-primary" href="' + esc(tel) + '">Call to Book</a>') if phone else ''}
<a class="btn btn-secondary" href="{esc(maps_url)}" target="_blank" rel="noopener">Get Directions</a>
</div>
</div></section>
<section id="reviews"><div class="wrap narrow">
<h2>What neighbors say</h2>
<p class="rating">{esc(rating_text)}</p>
<div class="review-slider">{reviews_html}
<div class="slider-dots" role="tablist" aria-label="Reviews">{dots_html}</div>
</div>
</div></section>
{rev_block}
<section id="visit"><div class="wrap visit-grid">
<div>
<h2>Visit us</h2>
<address><strong>{esc(name)}</strong><br>{esc(address)}<br>
{('<a href="' + esc(tel) + '">' + esc(phone) + '</a><br>') if phone else ''}<a href="{esc(maps_url)}" target="_blank" rel="noopener">Find us on Google Maps →</a></address>
<h3>Hours</h3>
<table class="hours"><tr><td>Mon – Fri</td><td>8:00 AM – 6:00 PM</td></tr><tr><td>Saturday</td><td>9:00 AM – 3:00 PM</td></tr><tr><td>Sunday</td><td>Closed</td></tr></table>
</div>
<div id="contact">
<h3>Request a callback</h3>
<form id="quote-form" novalidate>
<label>Full name<input name="name" required autocomplete="name" placeholder="Jane Doe"></label>
<label>Phone<input name="phone" type="tel" required autocomplete="tel" placeholder="(425) 555-0100"></label>
<label>What do you need?<select name="topic"><option>General question</option><option>Quote request</option><option>Book an appointment</option><option>Something else</option></select></label>
<label>Message<textarea name="message" rows="4" required placeholder="Tell us what you need…"></textarea></label>
<button class="btn btn-primary" type="submit">Request Callback</button>
<p class="form-note" role="status" aria-live="polite"></p>
</form>
</div>
</div></section>
</main>
<footer><div class="wrap">
<p><strong>{esc(name)}</strong> · {esc(category)} · {esc(address)}{(' · <a href="' + esc(tel) + '">' + esc(phone) + '</a>') if phone else ''}</p>
<p class="fine">© <span id="year">{datetime.datetime.now().year}</span> {esc(name)}. All rights reserved. · Site by {esc(AGENCY_NAME)}</p>
</div></footer>
{('<div class="preview-banner" role="note">Preview draft by ' + esc(AGENCY_NAME) + ' — design concept, not the official site of ' + esc(name) + '. The contact form is disabled in previews.</div>') if preview else ''}
<script src="script.js"></script>
</body>
</html>
"""
    css = f""":root{{--brand:{brand};--brand2:{brand2};--gold:{gold};--bg:{bg};--ink:#1c1917;--muted:#6b6259;--card:#ffffff;--line:{line};--dark:{dark};--radius:14px}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;font-family:system-ui,-apple-system,"Segoe UI",Roboto,Inter,sans-serif;color:var(--ink);background:var(--bg);line-height:1.6}}
h1,h2,h3{{line-height:1.15;margin:0 0 .5rem;letter-spacing:-.02em}}a{{color:var(--brand)}}
.wrap{{max-width:1100px;margin:auto;padding-left:1rem;padding-right:1rem}}
.topbar{{background:var(--dark);color:#ffe9d6;font-size:.85rem}}.topbar-inner{{display:flex;gap:1rem;align-items:center;justify-content:space-between;padding-top:.4rem;padding-bottom:.4rem}}.topbar-phone{{color:var(--gold);font-weight:800;text-decoration:none}}.hide-mobile{{}}
.site-header{{position:sticky;top:0;background:rgba(255,255,255,.96);backdrop-filter:blur(8px);z-index:20;border-bottom:1px solid var(--line)}}
.site-header nav{{display:flex;gap:1rem;align-items:center;padding-top:.7rem;padding-bottom:.7rem;flex-wrap:wrap}}
.site-header.scrolled{{box-shadow:0 6px 20px rgba(0,0,0,.10)}}
.brand{{font-weight:900;text-decoration:none;color:var(--ink);margin-right:auto;font-size:1.1rem;display:flex;align-items:center;gap:.5rem}}
.brand-badge{{display:inline-grid;place-items:center;width:2rem;height:2rem;border-radius:.6rem;background:linear-gradient(135deg,var(--brand),var(--brand2));color:#fff}}
.nav-links{{display:flex;gap:1.1rem;list-style:none;margin:0;padding:0}}.nav-links a{{text-decoration:none;color:var(--ink);font-weight:600}}.nav-links a:hover{{color:var(--brand)}}
.nav-toggle{{display:none;background:none;border:1px solid var(--line);border-radius:.5rem;padding:.25rem .6rem;font-size:1.15rem;cursor:pointer}}
.btn{{display:inline-block;padding:.75rem 1.35rem;border-radius:999px;text-decoration:none;font-weight:800;border:2px solid transparent;cursor:pointer;transition:transform .08s ease,box-shadow .15s ease}}
.btn-primary{{background:linear-gradient(135deg,var(--brand),var(--brand2));color:#fff;box-shadow:0 6px 18px rgba(0,0,0,.25)}}
.btn-primary:hover{{transform:translateY(-1px)}}.btn-secondary{{border-color:var(--brand);color:var(--brand);background:#fff}}
.btn-ghost{{color:#fff;border-color:rgba(255,255,255,.7)}}.btn-call{{white-space:nowrap;font-size:.95rem;padding:.6rem 1rem}}
.btn:focus-visible,a:focus-visible,button:focus-visible,input:focus-visible,textarea:focus-visible,select:focus-visible{{outline:3px solid var(--gold);outline-offset:2px}}
.hero{{background:radial-gradient(900px 400px at 50% -10%,var(--brand) 0%,var(--dark) 60%,#0a0605 100%);color:#fff;text-align:center;padding:4rem 0 3rem}}
.hero-inner{{max-width:820px}}.badge{{display:inline-block;background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.4);color:var(--gold);border-radius:999px;padding:.35rem .9rem;font-weight:700;font-size:.9rem}}
.hero h1{{font-size:clamp(2rem,6vw,3.6rem);margin:1rem 0 .6rem}}.accent{{background:linear-gradient(90deg,var(--gold),var(--brand2));-webkit-background-clip:text;background-clip:text;color:transparent}}
.tagline{{color:#ffe9d6;font-size:1.12rem;max-width:620px;margin:.5rem auto 1.2rem}}
.cta-row{{display:flex;gap:.75rem;justify-content:center;flex-wrap:wrap;margin:1.2rem 0}}.hero .btn-secondary{{background:#fff}}
.hero-meta{{display:flex;gap:1rem;justify-content:center;flex-wrap:wrap;margin-top:1.4rem;color:#ffd9c4;font-size:.95rem}}
.strip{{margin-top:-1.2rem;position:relative;z-index:2}}.strip-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:.8rem}}
.strip-item{{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:.9rem 1rem;display:flex;gap:.7rem;align-items:center;box-shadow:0 8px 24px rgba(0,0,0,.08)}}
.strip-item span{{font-size:1.6rem}}.strip-item small{{display:block;color:var(--muted)}}
section{{padding-top:2.6rem;padding-bottom:2.6rem}}.section-sub{{color:var(--muted);margin-top:-.25rem}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:1rem;list-style:none;padding:0;margin:1.2rem 0 0}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:1.2rem;box-shadow:0 4px 16px rgba(0,0,0,.05)}}
.card h3{{font-size:1.05rem}}
.about-grid{{display:grid;grid-template-columns:1.4fr .9fr;gap:1.2rem;align-items:start}}
.checklist{{list-style:none;padding:0;margin:1rem 0 0;display:grid;gap:.35rem;font-weight:600}}
.about-card{{background:var(--dark);color:#ffe9d6;border-radius:var(--radius);padding:1.4rem;display:grid;gap:.7rem}}
.about-card .big{{font-size:1.25rem;font-weight:900;color:var(--gold);margin:0}}.about-card .btn{{text-align:center}}
#reviews{{text-align:center}}.rating{{font-size:1.25rem;font-weight:800}}.rating span{{color:var(--muted)}}
.review-slider{{max-width:680px;margin:1rem auto 0}}.review{{display:none;background:#fff;border:1px solid var(--line);border-radius:var(--radius);padding:1.5rem;box-shadow:0 6px 20px rgba(0,0,0,.06)}}
.review.active{{display:block}}.review p{{font-size:1.1rem;margin:0 0 .6rem}}.review cite{{color:var(--muted);font-style:normal;font-weight:700}}
.slider-dots{{display:flex;gap:.5rem;justify-content:center;margin-top:.9rem}}.dot{{width:12px;height:12px;border-radius:50%;border:none;background:#e0d5c6;cursor:pointer}}.dot.active{{background:var(--brand)}}
.visit-grid{{display:grid;grid-template-columns:1fr 1fr;gap:1.2rem;align-items:start}}
address{{font-style:normal;background:#fff;border:1px solid var(--line);border-radius:12px;padding:1rem}}
.hours{{border-collapse:collapse;margin:.6rem 0 1rem;background:#fff}}.hours td{{padding:.55rem .9rem;border-bottom:1px solid var(--line)}}
form{{display:grid;gap:.75rem;background:#fff;border:1px solid var(--line);border-radius:12px;padding:1.1rem}}
label{{display:grid;gap:.3rem;font-weight:700;font-size:.92rem}}
input,textarea,select{{width:100%;padding:.65rem .75rem;border:1.5px solid #d9c7b4;border-radius:.6rem;font:inherit;background:#fffdfb}}
.form-note{{min-height:1.4em;color:var(--brand);font-weight:700;margin:0}}
footer{{background:var(--dark);color:#cbb9ab;text-align:center;padding:2rem 0 2.5rem;margin-top:1rem}}
footer a{{color:var(--gold)}}footer .fine{{font-size:.85rem;opacity:.85}}footer strong{{color:#fff}}
.reveal{{opacity:0;transform:translateY(14px);transition:opacity .5s ease,transform .5s ease}}.reveal.visible{{opacity:1;transform:none}}
.preview-banner{{position:fixed;left:0;right:0;bottom:0;z-index:50;background:var(--dark);color:var(--gold);text-align:center;font-size:.82rem;font-weight:700;padding:.5rem .8rem;border-top:2px solid var(--gold)}}
{('body{padding-bottom:2.2rem}') if preview else ''}
.narrow{{max-width:760px}}
@media(max-width:760px){{.nav-links{{display:none;width:100%;flex-direction:column;background:#fff;border:1px solid var(--line);border-radius:12px;padding:.7rem}}.nav-links.open{{display:flex}}.nav-toggle{{display:block}}.hide-mobile{{display:none}}.btn-call{{width:100%;text-align:center}}.about-grid,.visit-grid{{grid-template-columns:1fr}}.hero{{padding:3rem 0 2.5rem}}}}
"""
    js = ("""(function(){var t=document.querySelector('.nav-toggle'),l=document.querySelector('.nav-links');if(t&&l){t.addEventListener('click',function(){var o=l.classList.toggle('open');t.setAttribute('aria-expanded',o)})}
document.querySelectorAll('a[href^="#"]').forEach(function(a){a.addEventListener('click',function(e){var t=document.querySelector(a.getAttribute('href'));if(t){e.preventDefault();t.scrollIntoView({behavior:'smooth'});if(l&&l.classList.contains('open')){l.classList.remove('open');t.setAttribute('aria-expanded','false')}}})});
var h=document.getElementById('siteHeader');if(h){addEventListener('scroll',function(){h.classList.toggle('scrolled',scrollY>8)},{passive:true})}
""" + ("var PREVIEW=true;" if preview else "var PREVIEW=false;") + """
var f=document.getElementById('quote-form');if(f){f.addEventListener('submit',function(e){e.preventDefault();var n=f.name.value.trim(),p=f.phone.value.trim(),m=f.message.value.trim(),note=f.querySelector('.form-note');if(!n||!p||!m){note.textContent='Please fill in your name, phone, and message.';return}if(p.replace(/\\D/g,'').length<7){note.textContent='That phone number looks too short — please double-check.';return}if(PREVIEW){note.textContent='Thanks '+n.split(' ')[0]+'! (Design preview — this form goes live when the site launches.)';return}note.textContent='Thanks '+n.split(' ')[0]+'! We will call you back shortly.';f.reset()})}
var dots=Array.prototype.slice.call(document.querySelectorAll('.dot')),reviews=Array.prototype.slice.call(document.querySelectorAll('.review')),cur=0;function show(i){if(!reviews.length)return;cur=(i+reviews.length)%reviews.length;reviews.forEach(function(r,j){r.classList.toggle('active',j===cur)});dots.forEach(function(d,j){d.classList.toggle('active',j===cur)})}
dots.forEach(function(d,i){d.addEventListener('click',function(){show(i)})});if(reviews.length>1){setInterval(function(){show(cur+1)},6000)}
var y=document.getElementById('year');if(y){y.textContent=new Date().getFullYear()}
var tabs=Array.prototype.slice.call(document.querySelectorAll('.tab'));tabs.forEach(function(tab){tab.addEventListener('click',function(){tabs.forEach(function(o){o.classList.remove('active');o.setAttribute('aria-selected','false')});tab.classList.add('active');tab.setAttribute('aria-selected','true');document.querySelectorAll('.tab-panel').forEach(function(p){p.hidden=p.id!=='panel-'+tab.dataset.tab})})});
})();""")
    return {"index.html": index, "styles.css": css, "script.js": js}


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def load_leads(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        for k in ("leads", "businesses", "results", "data"):
            if isinstance(raw.get(k), list):
                raw = raw[k]
                break
        else:
            raw = [raw]
    return [r for r in raw if isinstance(r, dict)]


def select_leads(leads: list[dict], lead_id: str | None, limit: int) -> list[dict]:
    if lead_id:
        hit = [l for l in leads if l.get("lead_id") == lead_id]
        if not hit:
            raise ValueError(f"lead {lead_id} not in {len(leads)} leads")
        return hit
    return leads[:limit] if limit and limit > 0 else leads


def apply_preview_lock(target: Path, business_name: str) -> None:
    """Retrofit the preview blocker onto an OpenCode-built site.

    The prompt asks OpenCode for banner/noindex/demo-forms, but output
    can't be trusted — this guarantees the lock: noindex meta, fixed
    preview banner, and forms that demo instead of pretending to submit.
    Idempotent (safe to re-run).
    """
    idx = target / "index.html"
    try:
        html = idx.read_text(encoding="utf-8")
    except OSError:
        return
    if 'name="robots"' not in html:
        tag = '<meta name="robots" content="noindex, nofollow">'
        html = html.replace("</head>", tag + "\n</head>", 1) if "</head>" in html \
            else tag + "\n" + html
    if "sw-preview-banner" not in html:
        banner = (f'<div class="sw-preview-banner" role="note">Preview draft by '
                  f'{esc(AGENCY_NAME)} — design concept, not the official site of '
                  f'{esc(business_name)}. The contact form is disabled in previews.</div>')
        lock = (banner + "\n<style>.sw-preview-banner{position:fixed;left:0;right:0;bottom:0;"
                "z-index:9999;background:#141210;color:#ffc53d;text-align:center;font-size:13px;"
                "font-weight:700;padding:8px 12px;border-top:2px solid #ffc53d}</style>\n"
                "<script>document.body.style.paddingBottom='2.4rem';"
                "(function(){var f=document.querySelector('form');if(f){f.addEventListener('submit',"
                "function(){var n=f.querySelector('.form-note');"
                "if(n){n.textContent='Thanks! (Design preview \\u2014 this form goes live when the site launches.)'}})}})();</script>")
        html = html.replace("</body>", lock + "\n</body>", 1) if "</body>" in html \
            else html + "\n" + lock
        idx.write_text(html, encoding="utf-8")


def generate_one(lead: dict, out_root: Path, feedback: dict | None,
                 use_opencode: bool, force: bool, preview: bool = True) -> dict:
    lid = lead.get("lead_id") or "lead_unknown"
    target, slug = site_dir_for(lead, out_root)
    if target.exists() and (target / "index.html").exists() and not force:
        return {"lead_id": lid, "dir": str(target), "skipped": True, "engine": "cached"}
    target.mkdir(parents=True, exist_ok=True)
    prompt = build_prompt(lead, feedback, preview=preview)
    engine = "template"
    if use_opencode:
        try:
            run_opencode_command(target, prompt)
            if (target / "index.html").exists():
                engine = "opencode"
                if preview:
                    apply_preview_lock(target, lead.get("name") or "this business")
            else:
                raise RuntimeError("OpenCode finished but index.html missing")
        except RuntimeError as e:
            print(f"[website_generator] {lid}: OpenCode unavailable ({e}) — using template", flush=True)
            files = render_template(lead, feedback, preview=preview)
            for name, content in files.items():
                (target / name).write_text(content, encoding="utf-8")
    else:
        files = render_template(lead, feedback, preview=preview)
        for name, content in files.items():
            (target / name).write_text(content, encoding="utf-8")
    meta = {"lead_id": lid, "slug": target.name,
            "business": {k: lead.get(k) for k in
            ("name", "category", "address", "phone", "website", "rating", "review_count")},
            "lead_type": lead.get("lead_type"), "opportunity_score": lead.get("opportunity_score"),
            "engine": engine, "created_at": utc_now_iso(), "preview_mode": preview,
            "feedback_applied": bool(feedback and feedback.get("requested_changes"))}
    (target / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"lead_id": lid, "dir": str(target), "skipped": False, "engine": engine}


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Bot 5: build working sites -> generated_sites/<lead_id>/")
    p.add_argument("--leads", default="target_leads.json")
    p.add_argument("--lead", default=None, help="Single lead_id to build")
    p.add_argument("--all", action="store_true", help="Build all leads (default if no --lead)")
    p.add_argument("--limit", type=int, default=0, help="Max sites to build (0 = all)")
    p.add_argument("--output-dir", default="generated_sites")
    p.add_argument("--force", action="store_true", help="Regenerate even if index.html exists")
    p.add_argument("--no-opencode", action="store_true", help="Skip OpenCode, use template directly")
    p.add_argument("--feedback", default=None, help="Bot 10 feedback JSON path (revision requests)")
    p.add_argument("--final", dest="preview", action="store_false", default=True,
                   help="Paid final build: no preview banner, indexable, live forms (default: preview demo)")
    return p.parse_args(argv)


def main(argv=None, lead_data: dict | None = None, **kwargs) -> str | int:
    """Build a working site. Returns the site dir (str) or int exit code.

    Orchestrator use (single entry — looping lives in orchestrator.py)::
        main(lead_data={"lead_id": "lead_00001", "name": ..., ...})
        main(lead_data=entry, output_dir="generated_sites", force=False,
             no_opencode=False, feedback=None)

    CLI / batch use (legacy)::
        main(leads="target_leads.json", lead="lead_00001", ...)
        main(argv=["--leads", "target_leads.json", "--lead", "lead_00001"])

    A bare main() call uses defaults (sys.argv is only used via the CLI).
    """
    # Allow main(entry_dict) shorthand: first positional is the lead entry.
    if isinstance(argv, dict) and lead_data is None:
        lead_data = argv
        argv = []
    if argv is None:
        # Plain main() uses defaults (never sys.argv); the CLI passes
        # sys.argv[1:] explicitly via the __main__ block below.
        argv = []
    args = parse_args(argv)
    # lead_data can also arrive via **kwargs (keeps old call style working).
    if "lead_data" in kwargs:
        if lead_data is not None:
            raise TypeError("website_generator.main() got lead_data twice")
        lead_data = kwargs.pop("lead_data")
    for _k, _v in kwargs.items():
        if not hasattr(args, _k):
            raise TypeError(f"website_generator.main() got an unexpected option {_k!r}")
        setattr(args, _k, _v)
    # Convenience: main(lead={...dict...}) also means single-entry mode.
    if lead_data is None and isinstance(args.lead, dict):
        lead_data = args.lead
    feedback = None
    if args.feedback is not None:
        if isinstance(args.feedback, dict):
            feedback = args.feedback
        else:
            feedback = json.loads(Path(args.feedback).read_text(encoding="utf-8"))
    out_root = Path(args.output_dir)
    use_opencode = not args.no_opencode
    if lead_data is not None:
        # --- Single-entry mode (orchestrator loops, we build one) ---
        if not isinstance(lead_data, dict):
            print("[website_generator] ERROR: lead_data must be a dict", file=sys.stderr)
            return 2
        if not lead_data.get("lead_id"):
            print("[website_generator] ERROR: lead_data missing 'lead_id'", file=sys.stderr)
            return 2
        res = generate_one(lead_data, out_root, feedback, use_opencode, args.force,
                         preview=args.preview)
        if res["skipped"]:
            print(f"[website_generator] skip {res['lead_id']} (exists, use --force)", flush=True)
        else:
            print(f"[website_generator] built {res['lead_id']} [{res['engine']}] -> {res['dir']}", flush=True)
        return str(res["dir"])
    leads_path = Path(args.leads)
    if not leads_path.exists():
        print(f"[website_generator] ERROR: {leads_path} not found", file=sys.stderr)
        return 2
    try:
        leads = load_leads(leads_path)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[website_generator] ERROR: {e}", file=sys.stderr)
        return 2
    try:
        selected = select_leads(leads, args.lead if isinstance(args.lead, (str, type(None))) else None, args.limit)
    except ValueError as e:
        print(f"[website_generator] ERROR: {e}", file=sys.stderr)
        return 2
    print(f"[website_generator] building {len(selected)} site(s) via "
          f"{'template' if args.no_opencode else 'opencode→template fallback'}", flush=True)
    built, skipped = 0, 0
    for lead in selected:
        res = generate_one(lead, out_root, feedback, not args.no_opencode, args.force,
                         preview=args.preview)
        if res["skipped"]:
            skipped += 1
            print(f"[website_generator] skip {res['lead_id']} (exists, use --force)", flush=True)
        else:
            built += 1
            print(f"[website_generator] built {res['lead_id']} [{res['engine']}] -> {res['dir']}", flush=True)
    print(f"[website_generator] done: {built} built, {skipped} skipped", flush=True)
    return str(out_root)


if __name__ == "__main__":
    _rc = main(sys.argv[1:])
    raise SystemExit(_rc if isinstance(_rc, int) else 0)
