"""
website_generator.py (Bot 5) — Build a real working website per target lead.

v2: works out of the box with no external dependency required. OpenCode is
used when available (best output); if the CLI isn't installed or fails,
the script automatically falls back to a built-in template engine that is
no longer a generic stub — it's a category-aware, variant-based generator
with real typography, inline SVG icon system, layered hero art, and
per-lead visual variety (three distinct layouts per category, chosen
deterministically from the lead so re-runs are stable).

Inputs:
    target_leads.json (+ business info; plus website_analysis when the
    lead comes from bad_websites.json)

Process:
    no-website lead:   business info → OpenCode (if available) → new site
    bad-website lead:  business info + existing problems → OpenCode →
                       completely improved site
    OpenCode missing/failing → automatic fallback to the built-in
                       template engine (no flag required; use
                       --no-opencode to force template mode from the start,
                       or --require-opencode to disable the fallback and
                       fail loudly instead).

Output:
    generated_sites/
    └── <slug>/
        ├── index.html
        ├── styles.css
        ├── script.js
        └── meta.json

Usage:
    python website_generator.py --lead lead_00001
    python website_generator.py --all --limit 5
    python website_generator.py --lead lead_00001 --force --no-opencode
    python website_generator.py --lead lead_00001 --feedback feedback.json  (Bot 10 revision)
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import html as htmlmod
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path

import requests

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
    """Customer-facing slug: 'Bothell Way Garage' -> 'bothell-way-garage'."""
    import re as _re
    import unicodedata as _ud
    text = _ud.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    slug = _re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    slug = _re.sub(r"-{2,}", "-", slug)[:50].strip("-")
    return slug or fallback


def _stable_seed(lead: dict) -> int:
    """Deterministic integer seed for a lead — drives variant selection so
    the same business always gets the same layout across re-runs, but
    different businesses spread across the available variants."""
    key = str(lead.get("stable_key") or lead.get("lead_id") or
               f"{lead.get('name')}|{lead.get('phone')}|{lead.get('address')}")
    return int(hashlib.md5(key.encode("utf-8")).hexdigest()[:8], 16)


def site_dir_for(lead: dict, out_root: Path) -> tuple[Path, str]:
    """Resolve the site dir for a lead: slug-based, collision-safe."""
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
        seed = str(_stable_seed(lead))[:6]
        slug = f"{slug}-{seed}"
        candidate = out_root / slug
    return candidate, slug


# ---------------------------------------------------------------------------
# OpenCode bridge (best-effort — never the only path to a finished site)
# ---------------------------------------------------------------------------

_OPENCODE_ARGV_CACHE: list[str] | None | bool = False  # False = not probed yet, None = confirmed absent


def opencode_available() -> bool:
    try:
        _opencode_argv()
        return True
    except RuntimeError:
        return False


def _opencode_argv() -> list[str]:
    """Argv prefix invoking the real OpenCode CLI, or raise RuntimeError.

    Bare `opencode` is unreliable on Windows: subprocess (no shell) resolves
    `opencode` -> `opencode.exe`, which can hit a broken shadow instead of
    the real CLI's `opencode.cmd`. Prefer npm's native executable, then its
    cmd shim, and sanity-check every candidate with `--version`. Result is
    cached (including negative results, so we only probe once per run).
    """
    global _OPENCODE_ARGV_CACHE
    if _OPENCODE_ARGV_CACHE is not False:
        if _OPENCODE_ARGV_CACHE is None:
            raise RuntimeError("No working OpenCode CLI found (cached)")
        return _OPENCODE_ARGV_CACHE
    candidates: list[list[str]] = []
    npm_cmd = Path.home() / "AppData" / "Roaming" / "npm" / "opencode.cmd"
    npm_exe = npm_cmd.parent / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"
    if npm_exe.is_file():
        candidates.append([str(npm_exe)])
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
    _OPENCODE_ARGV_CACHE = None
    raise RuntimeError("No working OpenCode CLI found "
                       "(tried npm opencode.cmd + PATH `opencode`)")

DEFAULT_OPENCODE_MODEL = "opencode/big-pickle"

def run_opencode_command(target_dir: Path, prompt: str, timeout: int = 600) -> str:
    """Run OpenCode in target_dir with prompt; return stdout.

    Uses the build agent with the prompt on stdin to avoid Windows shell
    quoting and command-length limits. `--auto` is required: without it a
    headless run waits forever on tool-permission approvals (stdin is the
    prompt, there is no TTY to approve from). Raises RuntimeError on
    failure — callers decide whether to fall back.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    prefix = _opencode_argv()
    model = os.environ.get("AGENCY_OPENCODE_MODEL", "").strip() or DEFAULT_OPENCODE_MODEL
    command = [*prefix, "run", "--agent", "build", "--auto", "--model", model]
    print(f"[website_generator] OpenCode starting in {target_dir} "
          f"(model={model}, timeout={timeout}s)", flush=True)
    try:
        proc = subprocess.run(
            command, input=prompt,
            cwd=str(target_dir), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"OpenCode timed out after {timeout}s") from e
    except OSError as e:
        raise RuntimeError(f"Could not start OpenCode: {e}") from e
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "No diagnostic output").strip()
        raise RuntimeError(f"OpenCode exited {proc.returncode}: {detail[-2000:]}")
    return proc.stdout or ""


# ---------------------------------------------------------------------------
# Design system: fonts, icons, category profiles, layout variants
# ---------------------------------------------------------------------------

# Google Fonts pairings, one (display, body, google-fonts URL) per "mood".
FONT_PAIRINGS = {
    "warm_editorial": ("'Fraunces', serif", "'Inter', sans-serif",
        "https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,700;9..144,900&family=Inter:wght@400;500;600;700&display=swap"),
    "bold_industrial": ("'Archivo Black', sans-serif", "'Archivo', sans-serif",
        "https://fonts.googleapis.com/css2?family=Archivo+Black&family=Archivo:wght@400;500;600;700&display=swap"),
    "clean_modern": ("'Sora', sans-serif", "'Manrope', sans-serif",
        "https://fonts.googleapis.com/css2?family=Sora:wght@600;700;800&family=Manrope:wght@400;500;600;700&display=swap"),
    "friendly_rounded": ("'Poppins', sans-serif", "'Nunito Sans', sans-serif",
        "https://fonts.googleapis.com/css2?family=Poppins:wght@600;700;800&family=Nunito+Sans:wght@400;600;700&display=swap"),
    "elegant_serif": ("'Playfair Display', serif", "'Source Sans 3', sans-serif",
        "https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,600;0,700;0,800;1,500;1,600&family=Source+Sans+3:wght@400;500;600;700&display=swap"),
    "condensed_punch": ("'Anton', sans-serif", "'Inter', sans-serif",
        "https://fonts.googleapis.com/css2?family=Anton&family=Inter:wght@400;500;600;700&display=swap"),
    "modern_grotesk": ("'Space Grotesk', sans-serif", "'Inter', sans-serif",
        "https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Inter:wght@400;500;600;700&display=swap"),
}

# Small inline-SVG icon set (currentColor, 24x24 viewBox) — replaces emoji.
ICONS = {
    "pin": '<path d="M12 21s7-6.5 7-12a7 7 0 1 0-14 0c0 5.5 7 12 7 12Z"/><circle cx="12" cy="9" r="2.5"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "phone": '<path d="M4 5c0-.6.4-1 1-1h3l2 5-2 1.5a11 11 0 0 0 5 5L14.5 14l5 2v3c0 .6-.4 1-1 1C10.6 20 4 13.4 4 5Z"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "star": '<path d="M12 3.5l2.6 5.4 5.9.8-4.3 4.2 1 6-5.2-2.8-5.2 2.8 1-6-4.3-4.2 5.9-.8Z"/>',
    "shield": '<path d="M12 3l7 3v6c0 4.5-3 8-7 9-4-1-7-4.5-7-9V6Z"/><path d="M9 12l2 2 4-4"/>',
    "bolt": '<path d="M13 2 4 14h6l-1 8 9-12h-6Z"/>',
    "wrench": '<path d="M14.7 6.3a4 4 0 0 1-5.4 5.4L4 17l3 3 5.3-5.3a4 4 0 0 1 5.4-5.4l-3 3-2-2Z"/>',
    "flame": '<path d="M12 2s4 4 4 8a4 4 0 0 1-8 0c0-1 .3-2 1-3-1 3 0 5 3 5 2 0 3-2 3-4 0-3-3-5-3-8-3 2-5 6-5 9a5 5 0 0 0 10 0c0-5-5-7-5-7Z"/>',
    "coffee": '<path d="M4 9h13a3 3 0 0 1 0 6h-1"/><path d="M4 9v6a4 4 0 0 0 4 4h4a4 4 0 0 0 4-4V9"/><path d="M7 4c0 1-1 1-1 2M11 4c0 1-1 1-1 2"/>',
    "leaf": '<path d="M5 21c8 0 14-6 14-14V4h-3C8 4 3 10 3 18v3Z"/>',
    "heart": '<path d="M12 20s-7-4.4-9.5-8.8C.8 8 2 4.5 5.5 4a5 5 0 0 1 6.5 2 5 5 0 0 1 6.5-2c3.5.5 4.7 4 3 7.2C19 15.6 12 20 12 20Z"/>',
    "home": '<path d="M4 11 12 4l8 7"/><path d="M6 10v9h12v-9"/>',
    "scale": '<path d="M12 3v18M6 7h12M4 7l3 6a3 3 0 0 0 6 0L4 7Zm10 0l3 6a3 3 0 0 0 6 0l-3-6"/>',
    "sparkle": '<path d="M12 3l1.6 5.4L19 10l-5.4 1.6L12 17l-1.6-5.4L5 10l5.4-1.6Z"/>',
    "paw": '<circle cx="6" cy="9" r="1.6"/><circle cx="10.5" cy="6" r="1.6"/><circle cx="15" cy="6" r="1.6"/><circle cx="18.5" cy="9" r="1.6"/><path d="M12 12c-3 0-6 2-6 4.5S8 20 12 20s6-.9 6-3.5S15 12 12 12Z"/>',
    "dumbbell": '<path d="M4 12h16M4 9v6M20 9v6M7 7v10M17 7v10"/>',
    "camera": '<path d="M4 8h3l1.5-2h7L17 8h3v11H4Z"/><circle cx="12" cy="13.5" r="3.2"/>',
    "key": '<circle cx="8" cy="14" r="4"/><path d="M11 11l9-9M17 5l3 3M14 8l2 2"/>',
    "chart": '<path d="M4 20V10M11 20V4M18 20v-7"/>',
}


def _icon(name: str, size: int = 22) -> str:
    body = ICONS.get(name, ICONS["star"])
    return (f'<svg viewBox="0 0 24 24" width="{size}" height="{size}" fill="none" '
            f'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
            f'stroke-linejoin="round" aria-hidden="true">{body}</svg>')


# Film grain overlay shared by every template build (award-level texture).
_GRAIN_URI = ("data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' width='140' height='140'>"
              "<filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2'/>"
              "</filter><rect width='140' height='140' filter='url(%23n)' opacity='0.55'/></svg>")


def _texture_uri(icon: str) -> str:
    """Tileable SVG pattern data-URI giving each category its own art world
    (papel-picado dots for food, blueprint grid for trades, pinstripes for
    pros, ...). White motifs at low opacity — designed for dark heroes."""
    bodies = {
        "dots": "<circle cx='2' cy='2' r='1.5' fill='%23FFFFFF' fill-opacity='0.20'/>",
        "grid": ("<path d='M28 0H0V28' fill='none' stroke='%23FFFFFF' stroke-opacity='0.12'/>"
                 "<circle cx='0' cy='0' r='1.4' fill='%23FFFFFF' fill-opacity='0.22'/>"),
        "plus": ("<path d='M13 10h6M16 7v6' stroke='%23FFFFFF' stroke-opacity='0.16' stroke-width='1.6'/>"
                 "<circle cx='16' cy='16' r='1.2' fill='%23FFFFFF' fill-opacity='0.16'/>"),
        "diamond": ("<path d='M16 8l5 8-5 8-5-8Z' fill='none' stroke='%23FFFFFF' stroke-opacity='0.16'/>"),
        "pinstripe": "<path d='M0 32L32 0' stroke='%23FFFFFF' stroke-opacity='0.10' stroke-width='5'/>",
        "chevron": ("<path d='M0 16L16 0M0 32L32 0M16 32L32 16' stroke='%23FFFFFF' "
                    "stroke-opacity='0.10' stroke-width='2'/>"),
        "rings": ("<circle cx='16' cy='16' r='9' fill='none' stroke='%23FFFFFF' stroke-opacity='0.14'/>"
                  "<circle cx='16' cy='16' r='2' fill='%23FFFFFF' fill-opacity='0.18'/>"),
    }
    fam = {
        "dots": ("flame", "coffee", "star", "pin", "clock", "phone", "check", "shield"),
        "grid": ("wrench", "bolt", "home"),
        "plus": ("heart", "leaf"),
        "diamond": ("sparkle",),
        "pinstripe": ("scale", "chart", "key"),
        "chevron": ("dumbbell",),
        "rings": ("camera", "paw"),
    }
    body = bodies["dots"]
    for kind, icons in fam.items():
        if icon in icons:
            body = bodies[kind]
            break
    return ("data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' width='32' height='32'>"
            + body + "</svg>")


FOOD_CATEGORY_KEYS = ("coffee", "espresso", "cafe", "café", "tea", "bakery", "bagel",
                       "mexican", "restaurant", "taco", "pizza", "sushi", "burger",
                       "chicken", "deli", "bar", "grill", "bistro", "eatery", "food",
                       "bbq", "noodle", "thai", "italian")


def _is_food_category(category: str) -> bool:
    """True for restaurants/bars/cafes: order-and-visit language, never quotes."""
    c = (category or "").lower()
    return any(k in c for k in FOOD_CATEGORY_KEYS)


def _category_profile(category: str, name: str) -> dict:
    """Design + copy profile keyed off business category. Each profile
    supplies a palette, a font mood, an icon, six services, trust-strip
    items (icon-based, no emoji), and review snippets specific to the
    business type."""
    c = (category or "").lower()

    def profile(**kw):
        return kw

    if any(k in c for k in ("coffee", "espresso", "cafe", "café", "tea", "bakery", "bagel")):
        return profile(
            palette=("#4a2c17", "#8b5e34", "#d9a441", "#faf5ec", "#2b1a10", "#e8dcc8"),
            font="warm_editorial", icon="coffee",
            hero_kicker="Freshly brewed in the neighborhood",
            hero_sub=("Single-origin espresso, fresh pastries, and a cozy spot to work "
                       "or catch up — served fast with a smile."),
            services=[
                ("Espresso & Pour-Overs", "Single-origin beans, dialed in daily, brewed to order."),
                ("Seasonal Specials", "Rotating lattes, cold brew, and house-made syrups."),
                ("Fresh Pastries", "Baked goods delivered every morning, gone by noon."),
                ("Beans to Take Home", "Whole-bean bags ground to your brewer on request."),
                ("Quick Commuter Stop", "Order ahead by phone — ready at the counter."),
                ("Catering & Events", "Coffee boxes and pastry trays for meetings and parties."),
            ],
            strip=[("coffee", "Roasted Fresh", "Beans dialed in daily"),
                   ("clock", "Baked Mornings", "Pastries every morning"),
                   ("bolt", "Fast Commuter Stop", "Call ahead, grab & go"),
                   ("star", "Neighborhood Favorite", "Come see why regulars stay")],
            reviews=[
                ("Best latte on this side of town — smooth, never bitter, and the staff remembers my order.", "Google review"),
                ("Cozy spot with fast wifi. Pastry case is dangerous before 9am.", "Google review"),
                ("Cold brew is strong and clean. My daily stop before work.", "Google review"),
            ],
        )
    if any(k in c for k in ("mexican", "restaurant", "taco", "pizza", "sushi", "burger",
                            "chicken", "deli", "bar", "grill", "bistro", "eatery", "food",
                            "bbq", "noodle", "thai", "italian")):
        return profile(
            palette=("#b3271e", "#e07b39", "#f2b33d", "#fff8f0", "#260f0b", "#f3e2cf"),
            font="warm_editorial", icon="flame",
            hero_kicker="Cooked fresh, served fast",
            hero_sub=("House recipes, generous portions, and food made to order. "
                       "Dine in, take out, or feed the whole crew — see crowd favorites below."),
            services=[
                ("Signature Mains", "The dishes regulars drive across town for — entrees $14–$24, made to order."),
                ("Family & Party Platters", "Feed 3–8 with sides, bread, and sauces included — from $45."),
                ("Takeout in ~15 Min", "Call ahead and skip the wait — hot at the counter in about 15 minutes."),
                ("Lunch Specials", "Fast midday plates $11–$14 that beat fast food on price and taste."),
                ("Catering", "Trays and platters for offices, teams, and celebrations — quotes within a day."),
                ("Daily Specials", "Ask what's cooking today — it sells out most days."),
            ],
            icons=["flame", "heart", "clock", "star", "bolt", "coffee"],
            strip=[("flame", "Made Fresh", "Cooked to order, never frozen"),
                   ("heart", "Family Platters", "Feed the whole crew"),
                   ("clock", "Fast Takeout", "Ready in ~15 minutes"),
                   ("star", "Local Favorite", "Rated by your neighbors")],
            reviews=[
                ("Flavor is unreal for the price. The family platter fed all five of us with leftovers.", "Google review"),
                ("Fast takeout, food still hot when I got home. New regular spot.", "Google review"),
                ("Staff is friendly and the specials board is always worth a look.", "Google review"),
            ],
        )
    if any(k in c for k in ("auto", "repair", "garage", "brake", "tire", "transmission",
                            "oil change", "mechanic", "motorsport", "towing", "body shop", "glass")):
        return profile(
            palette=("#0f2a43", "#e8641b", "#f5a623", "#f7f9fc", "#0b1c2e", "#dbe5f0"),
            font="bold_industrial", icon="wrench",
            hero_kicker="Honest repairs, clear pricing",
            hero_sub=("Diagnostics before dollars: we show you what's wrong, quote it "
                       "up front, and only fix what needs fixing. Free estimates."),
            services=[
                ("Brakes & Rotors", "Pads, rotors, and fluid — inspected free, quoted up front."),
                ("Diagnostics & Check-Engine", "Dealer-level scan with plain-English explanation."),
                ("Oil & Maintenance", "Full-synthetic changes, filters, and fluid top-offs."),
                ("Engine & Transmission", "Major repairs with parts-and-labor warranty in writing."),
                ("Tires & Alignment", "New tires, rotations, and alignments that save fuel."),
                ("Pre-Purchase Inspections", "Buying used? 100+ point check before you sign."),
            ],
            strip=[("wrench", "ASE-Certified Techs", "Fixed right the first time"),
                   ("check", "Up-Front Quotes", "Approve before we wrench"),
                   ("shield", "Warranty in Writing", "Parts & labor covered"),
                   ("star", "Trusted Locally", "Rated by your neighbors")],
            reviews=[
                ("Quoted me before touching anything and finished same day. No upsell, just honest work.", "Google review"),
                ("Found the electrical gremlin two other shops missed. Fair price, clear explanation.", "Google review"),
                ("Pre-purchase inspection saved me from a lemon. Worth every penny.", "Google review"),
            ],
        )
    if any(k in c for k in ("hvac", "plumb", "electric", "roof", "contractor", "handyman",
                            "landscap", "lawn", "pest", "clean", "remodel", "paint", "flooring")):
        return profile(
            palette=("#0f3d2e", "#1f8a5b", "#f2b33d", "#f5faf6", "#0a2620", "#d8ebe0"),
            font="clean_modern", icon="home",
            hero_kicker="Reliable local pros, same-week service",
            hero_sub=("Licensed, insured, and easy to book. Straightforward quotes, "
                       "clean work, and a crew that shows up when they say they will."),
            services=[
                ("Free On-Site Estimates", "A real quote before anything is scheduled."),
                ("Emergency Calls", "Fast response for the jobs that can't wait."),
                ("Routine Maintenance", "Seasonal check-ups that catch problems early."),
                ("Repairs & Replacements", "Fixed right the first time, backed in writing."),
                ("New Installs", "Full installs with clean, code-compliant work."),
                ("Membership Plans", "Priority scheduling and discounted seasonal visits."),
            ],
            strip=[("shield", "Licensed & Insured", "Fully covered, every job"),
                   ("clock", "Same-Week Booking", "Fast scheduling, most weeks"),
                   ("check", "Up-Front Pricing", "No surprise fees"),
                   ("star", "Neighborhood Trusted", "Rated by your neighbors")],
            reviews=[
                ("Showed up on time, explained everything, and cleaned up after. Hiring them again.", "Google review"),
                ("Emergency call at 9pm and someone was here within the hour. Lifesavers.", "Google review"),
                ("Fair quote, no pressure to upsell. Exactly what they promised.", "Google review"),
            ],
        )
    if any(k in c for k in ("dental", "dentist", "orthodont", "clinic", "medical", "doctor",
                            "physical therapy", "chiropract", "urgent care", "pediatric")):
        return profile(
            palette=("#12406b", "#2f8fd1", "#7fd6c0", "#f5faff", "#0a2438", "#dceaf5"),
            font="clean_modern", icon="heart",
            hero_kicker="Care that puts you first",
            hero_sub=("A calm, modern practice with same-week appointments, transparent "
                       "pricing, and a team that actually listens."),
            services=[
                ("New Patient Exams", "A thorough first visit with no rushed appointments."),
                ("Preventive Care", "Cleanings and check-ups that catch issues early."),
                ("Same-Week Appointments", "Real availability, not a three-week wait."),
                ("Insurance & Payment Plans", "We help you understand costs before you commit."),
                ("Emergency Visits", "Set aside slots each day for urgent needs."),
                ("Family-Friendly Scheduling", "Book the whole household in one visit."),
            ],
            strip=[("heart", "Patient-First Care", "Unhurried appointments"),
                   ("clock", "Same-Week Booking", "Real availability"),
                   ("shield", "Insurance Friendly", "We handle the paperwork"),
                   ("star", "Highly Rated", "Rated by your neighbors")],
            reviews=[
                ("First practice that didn't rush me out the door. Explained every option clearly.", "Google review"),
                ("Got an appointment the same week for an urgent issue. Very grateful.", "Google review"),
                ("Front desk actually helped me understand my insurance coverage. Rare and appreciated.", "Google review"),
            ],
        )
    if any(k in c for k in ("salon", "spa", "barber", "beauty", "hair", "nail", "lash", "wax")):
        return profile(
            palette=("#5c2a4d", "#b5548a", "#f0c05a", "#fdf6f9", "#2a1224", "#f0dbe6"),
            font="elegant_serif", icon="sparkle",
            hero_kicker="Look and feel your best",
            hero_sub=("Skilled stylists, a relaxed atmosphere, and appointments that "
                       "actually start on time. Book online in under a minute."),
            services=[
                ("Signature Cuts & Color", "Precision cuts and custom color from senior stylists."),
                ("Blowouts & Styling", "Event-ready styling with lasting hold."),
                ("Skin & Facial Treatments", "Relaxing treatments tailored to your skin."),
                ("Nail Services", "Manicures and pedicures with a wide polish selection."),
                ("Bridal & Event Packages", "Full glam packages for your big day."),
                ("Membership Perks", "Priority booking and member-only pricing."),
            ],
            strip=[("sparkle", "Skilled Stylists", "Years of hands-on experience"),
                   ("clock", "On-Time Appointments", "Your time matters too"),
                   ("heart", "Relaxed Atmosphere", "A break from the everyday"),
                   ("star", "Client Favorite", "Rated by your neighbors")],
            reviews=[
                ("Left feeling like a new person. My stylist actually listened to what I wanted.", "Google review"),
                ("Appointment started on time and the whole visit felt unrushed and relaxing.", "Google review"),
                ("Best color I've had in years. Booking online was quick too.", "Google review"),
            ],
        )
    if any(k in c for k in ("law", "attorney", "legal", "accounting", "tax", "cpa",
                            "insurance agency", "financial", "notary")):
        return profile(
            palette=("#14213d", "#3a5a8c", "#c9a24b", "#f6f7f9", "#0b1526", "#dde3ec"),
            font="elegant_serif", icon="scale",
            hero_kicker="Straight answers, no jargon",
            hero_sub=("Clear guidance for a real problem, from people who explain your "
                       "options in plain English before you decide anything."),
            services=[
                ("Free Consultations", "An honest first conversation before you commit."),
                ("Case & Document Review", "A careful look before you sign anything."),
                ("Ongoing Representation", "Ongoing support through every step of the process."),
                ("Transparent Fees", "Clear pricing structure explained up front."),
                ("Urgent Matters", "Priority scheduling for time-sensitive issues."),
                ("Local Expertise", "Deep familiarity with local courts and processes."),
            ],
            strip=[("scale", "Experienced Team", "Years handling cases like yours"),
                   ("check", "Clear Fee Structure", "No surprise billing"),
                   ("clock", "Responsive Communication", "You're never left waiting"),
                   ("star", "Client Trusted", "Rated by your neighbors")],
            reviews=[
                ("Explained everything in terms I could actually understand. Never felt talked down to.", "Google review"),
                ("Responded to every email within a day. Made a stressful process manageable.", "Google review"),
                ("Upfront about costs from the first call. No surprises on the bill.", "Google review"),
            ],
        )
    if any(k in c for k in ("gym", "fitness", "yoga", "pilates", "crossfit", "martial arts",
                            "personal train", "boxing", "climbing")):
        return profile(
            palette=("#1a1a1a", "#e63946", "#f4a261", "#f7f7f5", "#0d0d0d", "#e6e6e2"),
            font="condensed_punch", icon="dumbbell",
            hero_kicker="Real results, real community",
            hero_sub=("Coached workouts, a welcoming crew, and programming that scales "
                       "to every fitness level. Your first class is on us."),
            services=[
                ("Free Trial Class", "Try a full session before you commit to anything."),
                ("Group Classes", "Coached sessions scheduled throughout the day."),
                ("Personal Training", "1-on-1 programming built around your goals."),
                ("Open Gym Access", "Flexible hours for members who like to train solo."),
                ("Nutrition Coaching", "Guidance that pairs with your training plan."),
                ("Membership Plans", "Options for every schedule and budget."),
            ],
            strip=[("dumbbell", "Certified Coaches", "Real programming, not guesswork"),
                   ("heart", "Welcoming Community", "All levels genuinely welcome"),
                   ("clock", "Flexible Scheduling", "Classes throughout the day"),
                   ("star", "Member Favorite", "Rated by your neighbors")],
            reviews=[
                ("Walked in nervous and left feeling like part of the community. Coaches actually check your form.", "Google review"),
                ("Best group of coaches I've trained with. Programming keeps things interesting.", "Google review"),
                ("Flexible enough to fit around a chaotic work schedule. Never feel like a number.", "Google review"),
            ],
        )
    if any(k in c for k in ("real estate", "realtor", "property management", "mortgage")):
        return profile(
            palette=("#1b2a41", "#3f7d58", "#c9a24b", "#f7f7f4", "#0d1520", "#e2e6df"),
            font="elegant_serif", icon="key",
            hero_kicker="Local expertise, honest guidance",
            hero_sub=("Buying, selling, or renting nearby — get a straight answer on "
                       "value and timing before you make a move."),
            services=[
                ("Free Home Valuation", "A realistic number before you list."),
                ("Buyer Representation", "Someone in your corner through every offer."),
                ("Listing & Marketing", "Professional photos and real market exposure."),
                ("Property Management", "Hands-off ownership with responsive service."),
                ("Investment Consulting", "Guidance for building a rental portfolio."),
                ("Neighborhood Insights", "Local knowledge you won't get from a listing site."),
            ],
            strip=[("key", "Local Market Experts", "Years serving this area"),
                   ("chart", "Data-Driven Pricing", "Realistic numbers, not guesses"),
                   ("clock", "Responsive Communication", "Fast answers, every time"),
                   ("star", "Client Trusted", "Rated by your neighbors")],
            reviews=[
                ("Sold above asking within a week. Their pricing strategy was spot on.", "Google review"),
                ("Patient through a dozen showings and never made me feel rushed.", "Google review"),
                ("Knew the neighborhood better than any app could tell me.", "Google review"),
            ],
        )
    if any(k in c for k in ("pet", "vet", "dog", "cat", "groom", "boarding", "kennel")):
        return profile(
            palette=("#2b5d4d", "#e0895a", "#f2c14e", "#fbf7ef", "#153229", "#e5ddc9"),
            font="friendly_rounded", icon="paw",
            hero_kicker="Care your pet actually enjoys",
            hero_sub=("Gentle, experienced care close to home — from routine visits to "
                       "grooming days your pet will (mostly) look forward to."),
            services=[
                ("Wellness Exams", "Routine check-ups that catch issues early."),
                ("Grooming & Baths", "A full spa day, tailored to your pet's coat."),
                ("Boarding & Daycare", "A safe, supervised stay while you're away."),
                ("Vaccinations", "Up-to-date protection on a schedule that fits."),
                ("Dental Care", "Cleanings that keep more than just teeth healthy."),
                ("Emergency Slots", "Same-day openings held for urgent needs."),
            ],
            strip=[("paw", "Gentle Handling", "Calm, patient with every pet"),
                   ("heart", "Genuine Pet Lovers", "Staff who remember your pet's name"),
                   ("clock", "Same-Day Openings", "For the things that can't wait"),
                   ("star", "Pet Parent Favorite", "Rated by your neighbors")],
            reviews=[
                ("My anxious rescue actually relaxes here. That says everything.", "Google review"),
                ("Squeezed us in same-day when our dog wasn't acting right. So grateful.", "Google review"),
                ("Grooming lasts longer than anywhere else we've tried.", "Google review"),
            ],
        )
    if any(k in c for k in ("photo", "studio", "videograph", "design agency", "marketing")):
        return profile(
            palette=("#111111", "#6c5ce7", "#00d4c8", "#f7f7fb", "#0a0a0a", "#e6e6f0"),
            font="modern_grotesk", icon="camera",
            hero_kicker="Work that actually gets noticed",
            hero_sub=("Concept to final delivery, handled by people who care about the "
                       "small details that make work look genuinely professional."),
            services=[
                ("Discovery Call", "A quick conversation to scope the project right."),
                ("Concept & Direction", "A clear creative direction before production starts."),
                ("Full Production", "Shoot, edit, and delivery handled end to end."),
                ("Brand Packages", "Consistent visuals across every platform."),
                ("Fast Turnaround Options", "Rush delivery when deadlines are tight."),
                ("Ongoing Partnerships", "Retainer options for recurring content needs."),
            ],
            strip=[("camera", "Portfolio-Proven", "See the work before you book"),
                   ("clock", "Reliable Turnaround", "Deadlines that actually hold"),
                   ("sparkle", "Creative Direction Included", "Not just execution"),
                   ("star", "Client Trusted", "Rated by past clients")],
            reviews=[
                ("Delivered ahead of schedule and the quality exceeded what we pitched internally.", "Google review"),
                ("Understood our brand better than agencies twice their size.", "Google review"),
                ("Communication was clear from kickoff to final delivery. No surprises.", "Google review"),
            ],
        )
    # Generic local-services fallback
    return profile(
        palette=("#123f2e", "#1f8a5b", "#f2b33d", "#f7faf7", "#0c2318", "#d9e8dd"),
        font="clean_modern", icon="star",
        hero_kicker="Local, reliable, easy to reach",
        hero_sub=("Real help from people nearby — clear pricing, fast response, and "
                   "work backed in writing. Call for a free quote today."),
        services=[
            ("Free Quotes", "Tell us what you need — honest price before we start."),
            ("Same-Week Booking", "Most jobs scheduled within days, not weeks."),
            ("Quality Guarantee", "Not happy? We make it right, in writing."),
            ("Friendly Local Team", "You talk to the people doing the work."),
            ("Transparent Pricing", "No surprise fees — approve everything first."),
            ("Follow-Up Support", "Questions after the job? Just call us."),
        ],
        strip=[("star", "Locally Owned", "Your neighbors, not a chain"),
               ("check", "Clear Pricing", "Quote before we start"),
               ("bolt", "Fast Response", "Same-week availability"),
               ("shield", "Work Guaranteed", "Backed in writing")],
        reviews=[
            ("Fast, friendly, and fairly priced. Will absolutely use them again.", "Google review"),
            ("Called in the morning, sorted by afternoon. Great communication.", "Google review"),
            ("Honest advice even when it meant less work for them. Rare these days.", "Google review"),
        ],
    )


# ---------------------------------------------------------------------------
# Production hardening: SEO identity metadata, overflow guard, carousel ARIA,
# marquee handling, and optional real form endpoint. Applied to every built
# site (both engines) so these outcomes are never left to model luck.
# ---------------------------------------------------------------------------

# Production base URL template. Set AGENCY_BASE_URL to a stable domain that
# maps to each generated site (use {slug} as the site placeholder, e.g.
# "https://{slug}.example.com/"); otherwise default to the per-site Vercel URL.
AGENCY_BASE_URL = os.environ.get("AGENCY_BASE_URL", "").rstrip("/")


def _site_base_url(slug: str) -> str:
    if AGENCY_BASE_URL:
        return AGENCY_BASE_URL.replace("{slug}", slug) + \
            ("" if AGENCY_BASE_URL.endswith("/") else "/")
    return f"https://{slug}.vercel.app/"


def _schema_type(category: str) -> str:
    c = (category or "").lower()
    if any(k in c for k in ("restaurant", "bar", "cafe", "coffee", "bakery", "taco",
                            "pizza", "burger", "grill", "cantina", "taqueria", "eatery",
                            "food", "kitchen", "brewery", "nightclub")):
        return "Restaurant"
    if any(k in c for k in ("auto", "car", "tire", "mechanic", "repair", "garage",
                            "detailing", "body shop", "truck")):
        return "AutoRepair"
    if any(k in c for k in ("dent", "ortho")):
        return "Dentist"
    if any(k in c for k in ("salon", "barber", "hair", "spa", "nail", "beauty", "lash", "tattoo")):
        return "HealthAndBeautyBusiness"
    if any(k in c for k in ("plumb", "hvac", "electric", "roof", "landscap", "pest",
                            "clean", "janitor", "window", "remodel", "paint", "floor")):
        return "HomeAndConstructionBusiness"
    if any(k in c for k in ("veterinar", "vet ", "pet", "groom")):
        return "VeterinaryCare"
    if any(k in c for k in ("gym", "fitness", "yoga", "pilates", "crossfit", "martial")):
        return "HealthClub"
    if any(k in c for k in ("law", "legal", "attorney")):
        return "Attorney"
    if any(k in c for k in ("hotel", "inn", "motel", "lodg")):
        return "Hotel"
    if any(k in c for k in ("church", "religious", "temple", "mosque")):
        return "Church"
    return "LocalBusiness"


def _jsonld_data(b: dict, url: str) -> dict:
    """LocalBusiness JSON-LD built strictly from lead data (name, category,
    address, phone, website, rating). Hours are intentionally NOT invented
    here — many leads have no verified hours, and a wrong opening-hours
    block is worse than none."""
    address_raw = (b.get("address") or "").strip()
    addr: dict = {}
    if address_raw:
        fields = [f.strip() for f in address_raw.split(",") if f.strip()]
        if len(fields) >= 1:
            addr["streetAddress"] = fields[0]
        if len(fields) >= 2:
            addr["addressLocality"] = fields[1]
            rest = ", ".join(fields[2:])
            m = re.search(r"([A-Za-z]{2})\s+(\d{4,5}(?:-\d{4})?)\s*$", rest)
            if m:
                addr["addressRegion"] = m.group(1)
                addr["postalCode"] = m.group(2)
    phone_digits = "".join(ch for ch in (b.get("phone") or "") if ch.isdigit())
    name = b.get("name") or "Local Business"
    category = b.get("category") or ""
    data = {
        "@context": "https://schema.org",
        "@type": _schema_type(category),
        "name": name,
        "description": ((f"{category} serving the neighborhood at {address_raw}."
                         if category else f"Serving the neighborhood at {address_raw}.")),
        "image": url + "og-image.svg",
        "url": url,
    }
    if addr:
        data["address"] = {"@type": "PostalAddress", **addr}
    elif address_raw:
        data["address"] = address_raw
    if phone_digits:
        data["telephone"] = "+1" + phone_digits[-10:] if len(phone_digits) >= 10 else "+" + phone_digits
    if b.get("website"):
        data["sameAs"] = [str(b["website"])]
    if b.get("rating"):
        agg = {"@type": "AggregateRating", "ratingValue": str(b["rating"])}
        if b.get("review_count"):
            agg["reviewCount"] = str(b["review_count"])
        data["aggregateRating"] = agg
    return data


def _seo_head(b: dict, slug: str) -> str:
    """Canonical + Open Graph + Twitter card + JSON-LD block for <head>.
    Gap-filling: callers add only the pieces still missing from the page."""
    url = _site_base_url(slug)
    name = b.get("name") or "Local Business"
    category = b.get("category") or ""
    address = b.get("address") or ""
    phone = b.get("phone") or "us today"
    image = url + "og-image.svg"
    desc = f"{name} — {category} at {address}. Call {phone} for a free quote."
    if len(desc) > 200:
        desc = desc[:197].rstrip() + "..."
    head = [
        f'<link rel="canonical" href="{esc(url)}">',
        f'<meta property="og:type" content="website">',
        f'<meta property="og:site_name" content="{esc(name)}">',
        f'<meta property="og:url" content="{esc(url)}">',
        f'<meta property="og:title" content="{esc(name)} — {esc(category)}">',
        f'<meta property="og:description" content="{esc(desc)}">',
        f'<meta property="og:image" content="{esc(image)}">',
        f'<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{esc(name)}">',
        f'<meta name="twitter:description" content="{esc(desc)}">',
        f'<meta name="twitter:image" content="{esc(image)}">',
        f'<script type="application/ld+json">{json.dumps(_jsonld_data(b, url), ensure_ascii=False)}</script>',
    ]
    return "\n".join(head)


def _write_og_image(target: Path, b: dict) -> Path:
    """1200x630 composed share card: palette scene, ghost monogram, name in
    display scale, rating proof, gold rule, builder credit."""
    name = b.get("name") or "Local Business"
    category = b.get("category") or "Local Business"
    prof = _category_profile(category, name)
    brand, brand2, gold, bg, dark, _line = prof["palette"]
    initials = "".join(w[0] for w in str(name).split()[:2]).upper() or "LB"
    fs = 72 if len(name) <= 22 else (58 if len(name) <= 32 else 46)
    rating = b.get("rating")
    reviews = b.get("review_count")
    proof = (f"★ {rating} · {reviews} verified reviews" if rating and reviews
             else (f"★ {rating} rated" if rating else "Trusted local business"))
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">'
        f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="{dark}"/><stop offset=".55" stop-color="{brand}"/>'
        f'<stop offset="1" stop-color="{brand2}"/></linearGradient></defs>'
        f'<rect width="1200" height="630" fill="url(#g)"/>'
        f'<circle cx="1050" cy="80" r="320" fill="{gold}" opacity="0.16"/>'
        f'<circle cx="1050" cy="80" r="200" fill="none" stroke="{gold}" stroke-width="2" opacity="0.35"/>'
        f'<circle cx="80" cy="580" r="240" fill="#000000" opacity="0.14"/>'
        f'<text x="1020" y="480" font-family="Georgia, serif" font-size="340" font-weight="700" '
        f'fill="#ffffff" opacity="0.10" text-anchor="middle">{esc(initials)}</text>'
        f'<rect x="80" y="120" width="120" height="120" rx="26" fill="{gold}"/>'
        f'<text x="140" y="202" font-family="Georgia, serif" font-size="64" font-weight="700" '
        f'fill="{dark}" text-anchor="middle">{esc(initials)}</text>'
        f'<text x="80" y="330" font-family="Georgia, serif" font-size="{fs}" font-weight="700" '
        f'fill="#ffffff">{esc(name)}</text>'
        f'<rect x="80" y="360" width="120" height="6" fill="{gold}"/>'
        f'<text x="80" y="412" font-family="Arial, Helvetica, sans-serif" font-size="34" '
        f'fill="{gold}">{esc(proof)}</text>'
        f'<text x="80" y="470" font-family="Arial, Helvetica, sans-serif" font-size="30" '
        f'fill="#ffffff" opacity="0.85">{esc(category)}</text>'
        f'<text x="80" y="560" font-family="Arial, Helvetica, sans-serif" font-size="24" '
        f'fill="#ffffff" opacity="0.6">Site by {esc(AGENCY_NAME)}</text>'
        f'</svg>'
    )
    img = target / "og-image.svg"
    img.write_text(svg, encoding="utf-8")
    return img


# ---------------------------------------------------------------------------
# Real-photo pipeline: scrape the business's own site, download locally.
# Never hotlink — remote URLs rot, get blocked, and leak referrers. If no
# usable photos exist, builders fall back to crafted inline-SVG scenes.
# ---------------------------------------------------------------------------

PHOTO_MAX_FILES = 4
PHOTO_MIN_WIDTH = 600
PHOTO_MIN_BYTES = 15_000
PHOTO_MAX_BYTES = 450_000
_PHOTO_REJECT = ("logo", "icon", "sprite", "favicon", "pixel", "tracker",
                 "spacer", "blank", "placeholder", "avatar", "badge", "arrow",
                 "divider", "schema", ".svg", "1x1", "transparent")
_PHOTO_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) StorefrontBot/1.0"}


def _img_dimensions(data: bytes) -> tuple[int, int] | None:
    """Width/height from image bytes (PNG/JPEG/GIF/WebP), stdlib only."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
            return struct.unpack(">II", data[16:24])
        if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
            return struct.unpack("<HH", data[6:10])
        if data[:2] == b"\xff\xd8":
            i = 2
            while i + 4 < len(data):
                if data[i] != 0xFF:
                    break
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return w, h
                if marker in (0xD8, 0xD9) or (0xD0 <= marker <= 0xD7) or marker == 0x01:
                    i += 2
                    continue
                ln = struct.unpack(">H", data[i + 2:i + 4])[0]
                i += 2 + ln
            return None
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            if data[12:16] == b"VP8X" and len(data) >= 30:
                w = int.from_bytes(data[24:27], "little") + 1
                h = int.from_bytes(data[27:30], "little") + 1
                return w, h
            if data[12:16] == b"VP8L" and len(data) >= 25:
                bits = int.from_bytes(data[21:25], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            if data[12:16] == b"VP8 " and len(data) >= 30:
                w = int.from_bytes(data[26:28], "little") & 0x3FFF
                h = int.from_bytes(data[28:30], "little") & 0x3FFF
                return (w or None, h or None) if w and h else None
    except (struct.error, IndexError):
        return None
    return None


class _ImgHarvest(HTMLParser):
    """Collect og:image + img src/srcset/data-src URLs from a page."""

    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        d = dict(attrs)
        if tag == "meta" and (d.get("property") or "").lower() == "og:image" \
                and d.get("content"):
            self.urls.append(d["content"].strip())
        if tag == "img":
            for key in ("src", "data-src", "data-lazy-src"):
                if d.get(key):
                    self.urls.append(d[key].strip())
            # split on comma+whitespace only: CDN URLs (e.g. Wix) legally
            # contain bare commas inside transformation params
            for part in re.split(r",\s+", d.get("srcset") or ""):
                bits = part.strip().split()
                if bits:
                    self.urls.append(bits[0])


def _photo_candidates(page_url: str) -> list[str]:
    """Image URLs from the business's own page (og:image first)."""
    try:
        r = requests.get(page_url, headers=_PHOTO_UA, timeout=15)
        r.raise_for_status()
        if "text/html" not in (r.headers.get("Content-Type") or ""):
            return []
    except Exception:
        return []
    harvester = _ImgHarvest()
    try:
        harvester.feed(r.text[:500_000])
    except Exception:
        return []
    seen: list[str] = []
    for raw in harvester.urls:
        if not raw or raw.startswith("data:"):
            continue
        url = urllib.parse.urljoin(page_url, raw).split("#")[0]
        scheme = urllib.parse.urlparse(url).scheme
        if scheme not in ("http", "https"):
            continue
        low = url.lower()
        if any(t in low for t in _PHOTO_REJECT):
            continue
        if url not in seen:
            seen.append(url)
    return seen[:12]


def _download_photo(url: str) -> tuple[bytes, str] | None:
    """Fetch one image, capped in size. Returns (bytes, extension) or None."""
    try:
        with requests.get(url, headers=_PHOTO_UA, timeout=15, stream=True) as r:
            r.raise_for_status()
            ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            ext = {"image/jpeg": ".jpg", "image/png": ".png",
                   "image/webp": ".webp"}.get(ctype)
            if not ext:
                return None
            buf = bytearray()
            for chunk in r.iter_content(32_768):
                buf += chunk
                if len(buf) > PHOTO_MAX_BYTES + 1:
                    return None
            if len(buf) < PHOTO_MIN_BYTES:
                return None
            return bytes(buf), ext
    except Exception:
        return None


def collect_business_photos(lead: dict, target: Path,
                            max_photos: int = PHOTO_MAX_FILES) -> list[dict]:
    """Scrape the business's own website for real photos and save them flat
    into the site root as photo-hero.jpg / photo-1.jpg ... Roles assigned by
    size (largest landscape = hero). Reuses existing files (no re-download).
    Never raises — returns [] when nothing usable is found."""
    try:
        existing = sorted(target.glob("photo-*.*"))
        if existing and all(p.stat().st_size > 0 for p in existing):
            scored = []
            for p in existing:
                dims = _img_dimensions(p.read_bytes()[:100_000])
                if dims:
                    scored.append((dims[0] * dims[1], p, dims))
            scored.sort(reverse=True)
            out = []
            for i, (_, p, (w, h)) in enumerate(scored[:max_photos]):
                role = "hero" if i == 0 else ("about" if i == 1 else f"gallery-{i - 1}")
                out.append({"file": p.name, "width": w, "height": h, "role": role})
            if out:
                return out
        page = (lead.get("website") or "").strip()
        found: list[tuple[int, bytes, str, int, int]] = []
        if page.startswith(("http://", "https://")):
            for url in _photo_candidates(page):
                if len(found) >= max_photos:
                    break
                got = _download_photo(url)
                if not got:
                    continue
                data, ext = got
                dims = _img_dimensions(data)
                if not dims or dims[0] < PHOTO_MIN_WIDTH:
                    continue
                w, h = dims
                if any(d == data for _, d, _, _, _ in found):
                    continue  # same bytes under a different sized URL
                found.append((w * h, data, ext, w, h))
        if not found:
            # Fallback: Google Maps photos Bot 1 saved (thumbnails that pass
            # the same size/dedup validation — never hotlinked, always local).
            for url in (lead.get("photo_urls") or []):
                if len(found) >= max_photos:
                    break
                if not isinstance(url, str) or not url.startswith("http"):
                    continue
                got = _download_photo(url)
                if not got:
                    continue
                data, ext = got
                dims = _img_dimensions(data)
                if not dims or dims[0] < PHOTO_MIN_WIDTH:
                    continue
                w, h = dims
                if any(d == data for _, d, _, _, _ in found):
                    continue
                found.append((w * h, data, ext, w, h))
        # name AFTER sorting: largest landscape file is always photo-hero.*
        found.sort(key=lambda t: t[0], reverse=True)
        out = []
        for i, (_, data, ext, w, h) in enumerate(found):
            fname = f"photo-hero{ext}" if i == 0 else f"photo-{i}{ext}"
            (target / fname).write_bytes(data)
            role = "hero" if i == 0 else ("about" if i == 1 else f"gallery-{i - 1}")
            out.append({"file": fname, "width": w, "height": h, "role": role})
        return out
    except Exception:
        return []


_HARDENING_MARK = "sw-hardening"

_OVERFLOW_GUARD_HTML = (
    '<style id="sw-overflow-guard">'
    "html,body{max-width:100%;overflow-x:hidden;overflow-x:clip}"
    ".marquee,[class*=\"marquee\"]{overflow:hidden!important;max-width:100%}"
    ".preview-banner,[class*=\"preview-banner\"]{padding-bottom:env(safe-area-inset-bottom)}"
    "</style>"
)

_HARDENING_JS = r"""
(function(){
  /* site hardening — sw-hardening (auto-applied, never breaks the page) */
  try{
    function qs(s,c){return (c||document).querySelector(s)}
    function qsa(s,c){return Array.prototype.slice.call((c||document).querySelectorAll(s))}
    /* 1. Marquees are decorative duplicates -> hidden from assistive tech */
    qsa('.marquee, [class*="marquee"]').forEach(function(m){if(!m.hasAttribute('aria-hidden'))m.setAttribute('aria-hidden','true')});
    /* 2. External links: rel=noopener + clear "opens in a new tab" label */
    qsa('a[target="_blank"]').forEach(function(a){
      var rel=(a.getAttribute('rel')||'').split(/\s+/).filter(Boolean);
      if(rel.indexOf('noopener')<0){rel.push('noopener');a.setAttribute('rel',rel.join(' '))}
      if(!a.getAttribute('aria-label')&&a.href&&a.href.indexOf('google.com/maps')>=0)
        a.setAttribute('aria-label','Get directions in Google Maps (opens in a new tab)')
    });
    /* 3. Review carousel: pair dots + slides as tablist/tab/tabpanel */
    qsa('.review-slider, .reviews-slider, .slider, [class*="slider"]').forEach(function(slider){
      var panels=qsa('.review',slider), tabs=qsa('.dot',slider).filter(function(t){return t.tagName==='BUTTON'});
      if(!panels.length||!tabs.length)return;
      if(!slider.id)slider.id='sw-review-'+Math.random().toString(36).slice(2,8);
      function sync(){
        panels.forEach(function(p,i){
          var on=p.classList.contains('active');
          p.setAttribute('role','tabpanel');
          p.setAttribute('aria-labelledby',slider.id+'-tab-'+i);
          p.setAttribute('aria-hidden',on?'false':'true')
        });
        tabs.forEach(function(t,i){
          var on=t.classList.contains('active');
          t.setAttribute('role','tab');
          t.setAttribute('aria-selected',on?'true':'false');
          t.setAttribute('aria-controls',slider.id+'-panel-'+i);
          t.setAttribute('tabindex',on?'0':'-1');
          if(!t.id)t.id=slider.id+'-tab-'+i
        })
      }
      panels.forEach(function(p,i){if(!p.id)p.id=slider.id+'-panel-'+i});
      tabs.forEach(function(t,i){
        t.addEventListener('keydown',function(e){
          if(e.key==='ArrowRight'||e.key==='ArrowLeft'){
            e.preventDefault();
            var j=(e.key==='ArrowRight'?i+1:i-1+tabs.length)%tabs.length;
            tabs[j].focus();tabs[j].click()
          }
        })
      });
      sync();
      var mo=new MutationObserver(sync);
      mo.observe(slider,{subtree:true,attributes:true,attributeFilter:['class']})
    });
    /* 4. Real form endpoint: <meta name="form-endpoint" content="https://..."> */
    var ep=qs('meta[name="form-endpoint"]');
    if(ep&&ep.content){
      qsa('form').forEach(function(f){
        var done=false;
        f.addEventListener('submit',function(e){
          if(done)return;
          e.preventDefault();
          done=true;
          var data={};qsa('input,select,textarea',f).forEach(function(el){if(el.name)data[el.name]=el.value});
          var note=qs('.form-note,[class*="form-note"],[class*="success"]',f)||qs('.form-note,[class*="form-note"],[class*="success"]');
          fetch(ep.content,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})
            .then(function(r){if(!r.ok)throw new Error(String(r.status));return r.json()})
            .then(function(){f.reset();if(note)note.textContent='Thanks! Your request has been received — we will reply shortly.'})
            .catch(function(){done=false;if(note)note.textContent='Sorry, we could not send your request. Please call us directly.'})
        })
      })
    }
  }catch(err){}
})();
"""


def apply_site_hardening(target: Path, lead: dict) -> None:
    """Retrofit every built site so audit-critical items can never regress:
    canonical/OG/Twitter + JSON-LD, horizontal-overflow guard, marquee
    aria-hidden, labeled external links, carousel ARIA, and optional real
    form endpoints. Idempotent — safe to run on cached/rebuilt sites."""
    idx = target / "index.html"
    if not idx.is_file():
        return
    html = idx.read_text(encoding="utf-8")
    slug = target.name or (slugify(lead.get("name") or "site"))
    base = _site_base_url(slug)

    head_add = ""
    if 'rel="canonical"' in html:
        html = re.sub(r'<link rel="canonical" href="[^"]*"?>',
                      f'<link rel="canonical" href="{esc(base)}">', html, count=1)
    else:
        head_add += f'<link rel="canonical" href="{esc(base)}">\n'
    if 'property="og:url"' in html:
        html = re.sub(r'<meta property="og:url" content="[^"]*"?>',
                      f'<meta property="og:url" content="{esc(base)}">', html, count=1)
    else:
        head_add += f'<meta property="og:url" content="{esc(base)}">\n'
    if 'property="og:image"' in html:
        html = re.sub(r'<meta property="og:image" content="[^"]*"?>',
                      f'<meta property="og:image" content="{esc(base + "og-image.svg")}">', html, count=1)
    else:
        head_add += f'<meta property="og:image" content="{esc(base + "og-image.svg")}">\n'
    if 'property="og:title"' not in html:
        head_add += f'<meta property="og:title" content="{esc(lead.get("name") or "Local Business")}">\n'
        head_add += f'<meta property="og:type" content="website">\n'
    if 'name="twitter:card"' not in html:
        head_add += '<meta name="twitter:card" content="summary_large_image">\n'
    if 'application/ld+json' not in html:
        head_add += f'<script type="application/ld+json">{json.dumps(_jsonld_data(lead, base), ensure_ascii=False)}</script>\n'

    if _HARDENING_MARK not in html:
        head_add += _OVERFLOW_GUARD_HTML + "\n"
    if head_add:
        if "</head>" in html:
            html = html.replace("</head>", head_add + "</head>", 1)
        else:
            html = head_add + html
    if _HARDENING_MARK not in html:
        script = f'<script>{_HARDENING_JS}</script>'
        html = html.replace("</body>", script + "\n</body>", 1) if "</body>" in html \
            else html + "\n" + script

    _write_og_image(target, lead)
    idx.write_text(html, encoding="utf-8")


# ---------------------------------------------------------------------------
# OpenCode prompt (used when the CLI is available)
# ---------------------------------------------------------------------------

def build_prompt(b: dict, feedback: dict | None, preview: bool = True,
                 photos: list[dict] | None = None) -> str:
    name = b.get("name") or "Local Business"
    category = b.get("category") or "local business"
    address = b.get("address") or ""
    phone = b.get("phone") or ""
    rating = b.get("rating")
    reviews = b.get("review_count")
    problems = (b.get("website_analysis") or {}).get("problems") or []
    maps_url = b.get("maps_url") or ""
    photos = photos or []
    if photos:
        listed = "; ".join(
            f"{p['file']} ({p['width']}x{p['height']}, {p['role']})" for p in photos)
        photo_lines = [
            f"- REAL PHOTOS — already scraped from the business and saved in this directory: {listed}. "
            "You MUST feature them: hero shows the hero photo, about/gallery show the rest. Reference "
            "the exact local filenames (src=\"photo-hero.jpg\"). NEVER hotlink remote image URLs, NEVER "
            "invent stock-photo URLs, NEVER delete or rename the photo files. Every <img> gets width + "
            "height attributes, descriptive alt text, fetchpriority=\"high\" on the hero, loading=\"lazy\" "
            "below the fold. Inline SVG is for small icons ONLY — no fake drawn/illustrated hero scenes "
            "while real photos exist.",
        ]
    else:
        photo_lines = [
            "- No real photos could be obtained for this business — craft rich, specific inline-SVG scenes "
            "and textures for the visuals instead. NEVER hotlink remote images and NEVER invent "
            "stock-photo URLs (Unsplash, Pexels, placeholder services) — every visual must work offline.",
        ]
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
        "MISSION: deliver a PERFECT website — pixel-clean, fast, accessible, and beautiful "
        "enough to win design awards. 'Good enough' is failure. Every requirement below is "
        "verified by automated checks after your build; anything you skip will be flagged.",
        "DESIGN BAR — this must look like a $10k award-winning agency site that makes "
        "the viewer say wow on the first screen. Competent-and-bland FAILS this bar.",
        "- PERSONA: you are NOT a code generator — you are a senior UI/UX designer at a world-class "
        "studio, and this site goes into YOUR portfolio under YOUR name. Design like your reputation "
        "depends on it: opinionated, restrained, distinctive. Think like a designer first — who visits, "
        "what they need in 5 seconds, what to omit — and let the code serve those decisions. A fellow "
        "designer must never suspect a machine made this.",
        "- ANTI-AI-SLOP — the site must NOT look machine-made; each of these is a FAIL: purple/blue "
        "default gradients; centered hero with badge + headline + 3 identical buttons; identical "
        "3-column card grids with generic stroke icons; the stock section cadence ('Why Choose Us / Our "
        "Services / Testimonials / Get In Touch') — rename sections with the business's real voice; "
        "grey lorem-length paragraphs; Inter-or-system-font-everywhere; perfectly symmetrical, "
        "evenly-spaced, same-radius-everywhere sterility. INSTEAD: asymmetry and overlap, oversized "
        "editorial numerals, dramatic whitespace, texture and grain, off-grid details, and one surprising "
        "human touch per section (a hand-drawn underline, a sticker badge, a rotated kicker, a marquee "
        "with actual voice). Restraint + taste beats decoration. If it looks like every other AI-generated "
        "site, you failed.",
        "- ART DIRECTION, one bold concept for THIS business — commit fully. Examples: Mexican restaurant = "
        "dark moody cantina (deep ember + cream + gold) with a papel-picado SVG bunting motif and grain texture; "
        "auto shop = bold industrial navy+orange with blueprint-grid texture and stencil display type; "
        "coffee = cozy cream+brown with steam-swirl SVG curves. Never default blue-on-white, never #0b5fff. "
        "Finish with craft details: film-grain or SVG texture overlay, styled ::selection, custom scrollbar, "
        "layered ghost art in the hero.",
        "- TYPOGRAPHY: Google Fonts pairing — ONE expressive display face + ONE clean body face, never more. "
        "Fluid clamp() scale with viewport units, oversized hero H1 (cinematic, tight leading) with an "
        "accent-gradient phrase, eyebrow kickers with section numerals on every section.",
        "- HERO must be layered and dramatic: multi-stop gradient (linear-gradient and/or radial-gradient) + "
        "SVG pattern/texture overlay + badge + exactly one H1 + subcopy + 2 CTAs + trust meta row. "
        "A text-on-flat-color hero is a FAIL. Reserve hero space (min-height) so nothing shifts on load.",
        "- MOTION everywhere it counts: choreographed hero entrance, IntersectionObserver scroll reveals "
        "with staggered delays, a scrolling marquee strip "
        "(CSS keyframes), review slider with dots + setInterval auto-rotate, sticky-header shadow on scroll, "
        "scrollIntoView smooth anchors, card hover lifts, magnetic primary buttons, count-up stats (real "
        "numbers only). Animation is transform/opacity ONLY (GPU-composited, 60fps), driven by "
        "requestAnimationFrame with passive listeners, paused when offscreen. Respect prefers-reduced-motion "
        "everywhere — it must stop ALL motion. Paste this exact block into styles.css (it is "
        "machine-checked): @media (prefers-reduced-motion:reduce){*,*::before,*::after"
        "{animation-duration:.01ms!important;animation-iteration-count:1!important;"
        "transition-duration:.01ms!important;scroll-behavior:auto!important}} plus JS: "
        "if(matchMedia('(prefers-reduced-motion: reduce)').matches){/* skip auto-rotate/marquee */}.",
        "- ICONS: inline SVG only. NO emoji anywhere on the page (not in cards, not in buttons, not in the topbar).",
        *photo_lines,
        "- Sections in order, with these EXACT hooks (automated checks require them): div.topbar (address, hours, "
        "click-to-call), sticky header/nav, hero, trust strip, services grid of exactly 6 specific cards each "
        "using class=\"card\" (restaurant: menu-style cards with real dish names + prices; services: cards with "
        "price hints and turnaround times — never 3 generic ones), about/why-us split with a designed visual "
        "panel (gradient + pattern + big stat, never an empty box), reviews slider (3 quotes + dots, wrapper "
        "class containing 'review'), visit with hours <table> + reservation/quote form with id=\"quote-form\", "
        "CTA banner, rich footer.",
        "- COPY: 500+ words of real, specific copy. Business name 5+ times, street address and category woven "
        "through hero, services, about, and reviews. Concrete details (dishes, prices, hours, neighborhood) "
        "over adjectives. BANNED filler: 'quality work, fair prices', 'Trusted X', 'Lorem ipsum', "
        "'Welcome to our website', 'ask us about recent customer feedback'.",
        "- CSS SYSTEM: :root MUST define --brand, --brand2, --gold (plus --bg, --dark, plus a named "
        "body-text variable like --ink/--cream); sticky blurred header; "
        "cards with shadow+radius+hover; .btn-primary with background AND color; @media breakpoints at ~760px "
        "with working mobile nav toggle (.nav-toggle wired to .nav-links); focus-visible styles; scroll-behavior. "
        "Hidden reveal states ONLY behind a JS-added scope — copy this exactly: CSS "
        "'body.js .reveal{opacity:0;...}' (never a bare '.reveal{opacity:0}'), with "
        "document.body.classList.add('js') as the first JS line — content hidden with JS off is a FAIL.",
        "- CONTRAST IS NON-NEGOTIABLE (measured with a calculator, never eyeballed — "
        "the checker tests WHITE text on your --brand and --brand2 variables, so BOTH must be "
        "dark enough that white passes ≥4.5:1 on each: safe choices are deep shades like #7c2d12, "
        "#9a3412, #1c1917, never bright orange/pink/blue. Bright gradient buttons (ANY endpoint "
        "lighter than #999999) MUST use near-black text like #1c1917 — white text on bright "
        "orange/pink (#fff on #f97316, #fb9233, or similar) is an instant FAIL. Body text and "
        "secondary/muted text ≥4.5:1 on the page background; gold/decorative accents ≥3:1 on dark. "
        "Verify every pair with real contrast math before you finish.",
        "- JS SYSTEM: mobile nav toggle, scrollIntoView smooth anchors, review slider with setInterval auto-rotate + dots, "
        "quote-form validation (required fields, phone-length check) with inline success message, "
        "sticky-header shadow, current year in footer. Zero console errors or warnings. No unused CSS/JS.",
        "- PERFORMANCE BUDGET (hard limits): index.html <60KB, styles.css <40KB, script.js <25KB, page "
        "total <200KB. Google Fonts with display=swap + preconnect only; lazy-load below-fold media; "
        "zero layout shift (aspect-ratio or explicit dimensions on all media); scripts at end of body.",
        "- LAYOUT SAFETY: NO horizontal scrollbars at any viewport (test 320/375/768/1024/1280px). The scrolling "
        "marquee lives inside an overflow:hidden wrapper and its animated track can never widen the page "
        "(wrapper max-width:100%). Never fix a layout bug by putting overflow-x:hidden on body/html as the only measure.",
        "- A11Y CAROUSEL: dots are real tabs — role=\"tablist\" container, role=\"tab\" on dots, role=\"tabpanel\" on "
        "the quote cards, matching id / aria-controls / aria-selected / aria-labelledby, plus arrow-key navigation.",
        "- A11Y MARQUEE: decorative marquee copy is aria-hidden=\"true\" so screen readers never hear it repeated "
        "dozens of times; keep one semantic copy elsewhere on the page (e.g. the trust strip).",
        "- HONEST CTA LABELS: the reservation/quote form button must say what it really does — \"Request a "
        "reservation\" or \"Get a Quote\". Use \"Book Appointment\" only if the form truly confirms a booking; "
        "a preview/demo form must never imply a reservation was made.",
        "- CATEGORY-AWARE CTAS: food businesses (restaurant, cafe, bar, bakery, pizza, sushi, coffee, "
        "tacos, grill, deli, food of any kind) NEVER say \"Get a Quote\" or \"Free Quote\" — their CTAs are "
        "\"View Menu\", \"Call to Order\", \"See Tonight's Specials\". Quote language is for trades and "
        "home services only.",
        "- EXTERNAL LINKS: links that open in a new tab (Google Maps) get aria-label like \"Get directions in "
        "Google Maps (opens in a new tab)\" and rel=\"noopener\".",
        "- FOOTER HOURS: keep each day paired with its hours on a single line (e.g. \"Fri: 11 AM – 2 AM\"), no "
        "splitting a day and its hours across separate lines.",
        "- SEO/SHARING: include canonical, Open Graph and Twitter-card meta (og:title/og:description/og:image) and "
        "a JSON-LD LocalBusiness schema built ONLY from the given, verified data (name, address, phone, rating) — "
        "never invent hours, prices, or review quotes.",
        "- SIGNATURE MOMENT (required — bland-but-complete fails): execute ONE unforgettable, butter-smooth "
        "interaction — choreographed hero entrance, canvas particle/aurora hero, scroll-driven horizontal "
        "gallery, sticky stacking cards, count-up stats, or equivalent. It MUST ship with at least one of "
        "these literal hooks the checker looks for: id=\"heroAurora\" (canvas aurora), class=\"stack\" / "
        "sticky-stack (stacking cards), data-count (count-up stats), class with hero-ghost (layered ghost "
        "art), or horizontal-scroll (scroll-driven gallery). Smooth beats showy: transform/opacity "
        "only, 60fps, with reduced-motion and no-JS fallbacks. Name the moment you built in your reply.",
        "- TEXTURE CRAFT (required): the page MUST contain at least one of these literal markers — an "
        "SVG feTurbulence grain overlay, a styled ::selection rule, or a hero-ghost element. Bare flat "
        "surfaces with none of the three are a FAIL.",
        "- MOBILE EXCELLENCE: the design is judged on phones — thumb-zone CTAs, readable type at 360px, "
        "touch-swipe carousels, 44px+ tap targets, fast first paint, no layout shift.",
        "- DEVELOPER-GRADE CODE: clean semantic HTML with section comments, zero console errors, no unused "
        "CSS/JS, GPU-composited animation only.",
        f"Credit the builder with a subtle footer line: 'Site by {AGENCY_NAME}'.",
        ("PREVIEW MODE (this build is a sales demo, NOT the launched site):"
         if preview else
         "FINAL BUILD (the client paid — this is the launched site):"),
        (f"- PREVIEW BANNER: do NOT build your own banner, overlay, or watermark — the pipeline "
         f"injects the single official preview banner after your build. A second banner is a FAIL. "
         f"Just leave clean space for it (it is fixed to the viewport bottom)."
         if preview else
         "- No preview banner, no demo notices — clean production build."),
        ('- <meta name="robots" content="noindex, nofollow"> so the demo never hijacks '
         "the business's Google rankings."
         if preview else
         "- Full indexable build (no robots noindex)."),
        ("- The contact form validates, then shows 'Thanks! (Demo preview — this form "
         "goes live when the site launches.)' and does NOT claim anyone was contacted."
         if preview else
         "- The contact form posts to a real endpoint when <meta name='form-endpoint' "
         "content='https://...'> is present in <head>; otherwise it validates and shows a "
         "normal success message that sets response expectations, and never implies a "
         "booking was confirmed."),
        "Technical requirements: semantic HTML with <nav>, exactly one <h1>, CTA buttons (Call Now, Get a Quote, "
        "Book Appointment), tel: link with the exact phone given, form with id=\"quote-form\" (exact id, required) "
        "with required fields + JS validation, utility bar with class=\"topbar\" (exact class), service cards with "
        "class=\"card\" (exact class), :root defining --brand/--brand2/--gold (exact names), "
        "viewport meta, meta description, favicon (inline SVG data URI), alt text on images, address + hours <table>, "
        "JSON-LD LocalBusiness schema when the category fits.",
        "Use only vanilla HTML/CSS/JS, no external build step. Do not invent a different business name,",
        "address, or phone number — use exactly what is given.",
        "- SELF-REVIEW BEFORE YOU REPLY — verify each item, fix everything, then reply: (1) zero horizontal "
        "scroll at 320/375/768/1024/1280px; (2) zero console errors or warnings; (3) keyboard-only run — nav, "
        "slider arrows, form all reachable with visible focus; (4) with JavaScript disabled, ALL content is "
        "readable; (5) the literal string prefers-reduced-motion is in styles.css or script.js and stops ALL "
        "animation; (6) every link and button works (no dead "
        "'#' links except same-page anchors); (7) every text/background pair passes the contrast numbers above, "
        "including white text on --brand and on --brand2 (both ≥4.5:1, calculated not eyeballed); "
        "(8) exactly one <h1>, exactly 6 service cards, one signature moment executed flawlessly; "
        "(9) at least one signature hook (heroAurora, class=\"stack\", data-count, hero-ghost, "
        "horizontal-scroll) AND at least one texture marker (feTurbulence, ::selection, hero-ghost) "
        "is literally present in the files.",
        "IF YOU DO NOT SUCCEED IN MAKING IT PERFECT, tHE WORLD WILL END",
        "Reply with a one-line summary that names the signature moment you built.",
    ]
    if problems:
        lines.append("The old site had these problems — every one must be fixed: " + "; ".join(problems))
    if feedback and feedback.get("requested_changes"):
        lines.append("CLIENT REVISION REQUEST — apply each item: "
                     + "; ".join(feedback["requested_changes"]))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Fallback template engine (offline, no dependencies) — v2
# ---------------------------------------------------------------------------

LAYOUT_VARIANTS = ("centered-gradient", "split-panel", "editorial-asymmetric")


def render_template(b: dict, feedback: dict | None,
                    preview: bool = True, photos: list[dict] | None = None) -> dict[str, str]:
    """Offline, no-dependency generator producing a genuinely distinct,
    well-designed site per category *and* per business — not a single
    reused skin. Picks one of three hero/layout variants deterministically
    from the lead so results are stable across re-runs but visually varied
    across a batch."""
    name = b.get("name") or "Local Business"
    category = b.get("category") or "Local Business"
    address = b.get("address") or "Contact us for our location"
    phone = (b.get("phone") or "").strip()
    rating = b.get("rating")
    reviews = b.get("review_count")
    maps_url = b.get("maps_url") or ""
    if not maps_url:
        # Dead href="#" directions links ship otherwise — build a real
        # Google Maps search URL from the street address when we have one.
        addr = (b.get("address") or "").strip()
        if addr:
            from urllib.parse import quote_plus
            maps_url = ("https://www.google.com/maps/search/?api=1&query="
                        + quote_plus(addr))
        else:
            maps_url = "#"
    is_food = _is_food_category(category)
    menu_href = (b.get("menu_url") or "").strip() or "#services"
    menu_ext = menu_href.startswith("http")
    changes = (feedback or {}).get("requested_changes") or []
    prof = _category_profile(category, name)
    brand, brand2, gold, bg, dark, line = prof["palette"]
    font_display, font_body, font_url = FONT_PAIRINGS[prof["font"]]
    variant = LAYOUT_VARIANTS[_stable_seed(b) % len(LAYOUT_VARIANTS)]
    # Signature "wow" moment per site: aurora canvas hero, sticky-stacking
    # services, or stats band — deterministic per lead, varied per batch.
    WOW_MODULES = ("aurora", "stack", "stats")
    wow = WOW_MODULES[(_stable_seed(b) // len(LAYOUT_VARIANTS)) % len(WOW_MODULES)]
    texture = _texture_uri(prof["icon"])
    has_rating = bool(rating and reviews)
    photos = photos or []
    photo_hero = next((p for p in photos if p.get("role") == "hero"), None)
    photo_about = next((p for p in photos if p.get("role") == "about"), None)

    def _photo_img(p, cls, alt, eager=False):
        ld = ' fetchpriority="high"' if eager else ' loading="lazy"'
        return (f'<img class="{cls}" src="{esc(p["file"])}" width="{p["width"]}" '
                f'height="{p["height"]}" alt="{esc(alt)}"{ld}>')

    digits = "".join(c for c in phone if c.isdigit())
    tel = f"tel:+1{digits[-10:]}" if len(digits) >= 10 else (
        "tel:+" + digits if digits else "#contact")
    rating_text = f"{rating} · {reviews} Google reviews" if rating and reviews else (
        f"{rating} rated" if rating else "Rated by your neighbors")
    first_letters = "".join(w[0] for w in str(name).split()[:2]).upper() or "LB"
    city = esc(address.split(",")[-2].strip()) if address.count(",") >= 2 else esc(address)

    strip_html = "".join(
        f'<div class="strip-item"><span class="strip-icon" aria-hidden="true">{_icon(ic)}</span>'
        f"<div><strong>{esc(t)}</strong><small>{esc(s)}</small></div></div>"
        for ic, t, s in prof["strip"])
    svc_icons = prof.get("icons") or ["bolt", "star", "check", "clock", "shield", "pin"]
    services_html = "".join(
        f'<li class="card"><span class="card-icon" aria-hidden="true">{_icon(svc_icons[i % len(svc_icons)], 20)}</span>'
        f"<h3>{esc(t)}</h3><p>{esc(d)}</p></li>"
        for i, (t, d) in enumerate(prof["services"]))
    quotes = prof["reviews"]
    lead_quotes = b.get("reviews_list")
    if isinstance(lead_quotes, list):
        real = []
        for rq in lead_quotes:
            if not isinstance(rq, dict):
                continue
            text = (rq.get("text") or "").strip()
            if len(text) < 20:
                continue
            author = (rq.get("author") or "").strip() or "Google review"
            real.append((text[:300], author[:60]))
            if len(real) >= 3:
                break
        if real:
            # Real customer words beat invented ones — no fabricated reviews.
            quotes = real
    reviews_html = "".join(
        f'<blockquote id="review-p{i}" class="review{" active" if i == 0 else ""}" '
        f'role="tabpanel" aria-labelledby="review-tab{i}" '
        f'aria-hidden="{"false" if i == 0 else "true"}">'
        f'<div class="review-stars" aria-hidden="true">{_icon("star", 16) * 5}</div>'
        f"<p>“{esc(q)}”</p><cite>— {esc(who)}</cite></blockquote>"
        for i, (q, who) in enumerate(quotes))
    dots_html = "".join(
        f'<button type="button" id="review-tab{i}" class="dot{" active" if i == 0 else ""}" '
        f'role="tab" aria-selected="{"true" if i == 0 else "false"}" aria-controls="review-p{i}" '
        f'aria-label="Review {i + 1}"></button>'
        for i in range(len(quotes)))
    # Never render requested-changes publicly: the revisions block used to leak
    # internal dev notes ("Latest updates per your feedback") to visitors.
    # Feedback still flows to the build prompt + meta.json (feedback_applied).
    rev_block = ""
    if wow == "stack":
        services_list = ('<ol class="stack">' + "".join(
            f'<li class="card stack-card"><span class="stack-num">{i + 1:02d}</span>'
            f'<span class="card-icon" aria-hidden="true">{_icon(svc_icons[i % len(svc_icons)], 20)}</span>'
            f"<h3>{esc(t)}</h3><p>{esc(d)}</p></li>"
            for i, (t, d) in enumerate(prof["services"])) + "</ol>")
    else:
        services_list = f'<ul class="cards">{services_html}</ul>'
    stats_band = ""
    if has_rating:
        stats_band = (
            f'<section class="stats" aria-label="Ratings and reviews">'
            f'<div class="wrap stats-grid">'
            f'<div class="stat"><span class="stat-num" data-count="{esc(str(rating))}" '
            f'data-decimals="1">0</span>'
            f'<span class="stat-star" aria-hidden="true">' + _icon("star", 14) + '</span>'
            f'<span class="stat-label">Average rating</span></div>'
            f'<div class="stat"><span class="stat-num" data-count="{esc(str(reviews))}">0</span>'
            f'<span class="stat-label">Verified reviews</span></div>'
            f'<div class="stat"><span class="stat-num" data-count="{len(prof["services"])}">0</span>'
            f'<span class="stat-label">Signature services</span></div>'
            f'</div></section>')

    hero_badge = f'{_icon("star", 15)} {esc(rating_text)}'
    if photo_hero:
        hero_visual = (f'<div class="hero-visual">'
                       f'{_photo_img(photo_hero, "hero-photo", f"{name} — {category}", eager=True)}'
                       f'</div>')
    else:
        hero_visual = f'''<div class="hero-visual" aria-hidden="true">
  <div class="hero-visual-ring"></div>
  <div class="hero-visual-icon">{_icon(prof["icon"], 46)}</div>
</div>''' if variant != "centered-gradient" else ""

    hero_media = ""
    if wow == "aurora":
        hero_media += '<canvas class="hero-aurora" id="heroAurora" aria-hidden="true"></canvas>'
    hero_media += (f'<div class="hero-ghost" aria-hidden="true" data-parallax="0.12">'
                   f'{_icon(prof["icon"], 220)}</div>')

    hero_html = f'''<section class="hero hero--{variant}">{hero_media}<div class="wrap hero-inner">
  <div class="hero-copy">
    <p class="badge">{hero_badge}</p>
    <h1>{esc(name)} <span class="accent">{esc(prof['hero_kicker'])}</span></h1>
    <p class="tagline">{esc(prof['hero_sub'])}</p>
    <div class="cta-row">
      {('<a class="btn btn-primary" href="' + esc(tel) + '">' + _icon("phone", 16) + ' Call ' + esc(phone) + '</a>') if phone else '<a class="btn btn-primary" href="#contact">Contact Us</a>'}
      {('<a class="btn btn-secondary" href="' + esc(menu_href) + '"' + (' target="_blank" rel="noopener"' if menu_ext else '') + '>View Menu</a>' if is_food else '<a class="btn btn-secondary" href="#contact">Get a Free Quote</a>')}
      {('' if is_food else '<a class="btn btn-ghost" href="#services">Explore Services</a>')}
    </div>
    <div class="hero-meta">
      <div>{_icon("pin", 15)} {esc(address)}</div>
      {('<div>' + _icon("check", 15) + ' Dine in · Takeout · Catering</div>') if is_food else ('<div>' + _icon("check", 15) + ' Free quotes · No-pressure advice</div>')}
    </div>
  </div>
  {hero_visual}
</div></section>'''

    index = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="{esc(name)} — {esc(category)} in {city}. {esc(prof['hero_sub'])} {('Order takeout or join us tonight.' if is_food else ('Call ' + esc(phone) + ' for a free quote.' if phone else 'Call today for a free quote.'))}">
{'<meta name="robots" content="noindex, nofollow">' if preview else ''}
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{font_url}">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect width='100' height='100' rx='22' fill='{brand}'/><text x='50' y='66' font-size='40' text-anchor='middle' fill='white' font-family='Arial' font-weight='bold'>{esc(first_letters)}</text></svg>">
<title>{esc(name)} | {esc(category)} — {city}</title>
<link rel="stylesheet" href="styles.css">
</head>
<body id="top">
<div class="loader" id="loader" aria-hidden="true"><div class="loader-mark">{esc(first_letters)}</div></div>
<div class="progress" aria-hidden="true"><span id="progressBar"></span></div>
<div class="topbar"><div class="wrap topbar-inner">
<span>{_icon("pin", 14)} {esc(address)}</span>
    <span class="hide-mobile">{_icon("clock", 14)} {esc(b.get("hours") or ("Call for today's hours" if phone else "Message us for today's hours"))}</span>
{('<a class="topbar-phone" href="' + esc(tel) + '">' + _icon("phone", 14) + ' ' + esc(phone) + '</a>') if phone else ''}
</div></div>
<header class="site-header" id="siteHeader">
<nav class="wrap" aria-label="Main navigation">
<a class="brand" href="#top"><span class="brand-badge" aria-hidden="true">{_icon(prof["icon"], 18)}</span> {esc(name)}</a>
<button class="nav-toggle" aria-label="Toggle menu" aria-expanded="false" aria-controls="navLinks">
  <span></span><span></span><span></span>
</button>
<ul class="nav-links" id="navLinks">
<li><a href="#services">{('Menu' if is_food else 'Services')}</a></li>
<li><a href="#about">Why Us</a></li>
<li><a href="#reviews">Reviews</a></li>
<li><a href="#visit">Visit</a></li>
<li><a href="#contact">Contact</a></li>
</ul>
{('<a class="btn btn-primary btn-call" href="' + esc(tel) + '">' + _icon("phone", 15) + ' Call ' + esc(phone) + '</a>') if phone else ('<a class="btn btn-primary btn-call" href="#contact">Contact Us</a>' if is_food else '<a class="btn btn-primary btn-call" href="#contact">Get a Quote</a>')}
</nav>
</header>
<main>
{hero_html}
<div class="marquee" aria-hidden="true"><div class="marquee-track">
{("&nbsp;•&nbsp; " + esc(name) + " ") * 2}
</div></div>
<section class="strip" aria-label="Why choose us"><div class="wrap strip-grid">{strip_html}</div></section>
{stats_band}
<section id="services"><div class="wrap">
<p class="eyebrow"><span class="secnum" aria-hidden="true">01</span>{('What we serve' if is_food else 'What we offer')}</p>
<h2>{('Crowd favorites' if is_food else 'What we do')}</h2>
<p class="section-sub">{('Made fresh at ' + esc(name) + ' — dine in, take out, or feed the whole crew.' if is_food else 'Every job quoted up front at ' + esc(name) + ' — you approve before we start.')}</p>
{services_list}
</div></section>
<section id="about"><div class="wrap about-grid">
<div>
<p class="eyebrow"><span class="secnum" aria-hidden="true">02</span>Why us</p>
<h2>Why neighbors pick {esc(name)}</h2>
<p>{('The neighborhood spot for ' + esc(category) + ' at ' + esc(address) + ' — quick counter, warm dining room, and everything made to order.' if is_food else (esc(category) + ' done right, close to home at ' + esc(address) + '. ' + esc(prof['hero_sub'])))}</p>
{(_photo_img(photo_about, "about-photo", f"Inside {name}") if photo_about else "")}
<ul class="checklist">
{('<li>' + _icon("check", 16) + ' Recipes made from scratch, served fast</li>'
'<li>' + _icon("check", 16) + ' Takeout ready in about 15 minutes</li>'
'<li>' + _icon("check", 16) + ' Family platters and catering for events</li>'
'<li>' + _icon("check", 16) + ' Friendly crew — dine in or grab and go</li>') if is_food else ('<li>' + _icon("check", 16) + ' Up-front pricing — approve before we start</li>'
'<li>' + _icon("check", 16) + ' Work backed in writing</li>'
'<li>' + _icon("check", 16) + ' Fast scheduling, most jobs within days</li>'
'<li>' + _icon("check", 16) + ' You talk to the people doing the work</li>')}
</ul>
</div>
<div class="about-card">
<span class="about-card-icon" aria-hidden="true">{_icon(prof["icon"], 30)}</span>
<h3>Visit us today</h3>
<p class="big">{(esc(phone) if phone else esc(rating_text))}</p>
<p>{esc(address)}</p>
{('<a class="btn btn-primary" href="' + esc(tel) + '">Call to Order</a>') if phone and is_food else (('<a class="btn btn-primary" href="' + esc(tel) + '">Call to Book</a>') if phone else '')}
<a class="btn btn-secondary" href="{esc(maps_url)}" target="_blank" rel="noopener" aria-label="Get directions in Google Maps (opens in a new tab)">Get Directions</a>
</div>
</div></section>
<section id="reviews"><div class="wrap narrow">
<p class="eyebrow"><span class="secnum" aria-hidden="true">03</span>Reviews</p>
<h2>What neighbors say</h2>
<p class="rating">{esc(rating_text)}</p>
<div class="review-slider" aria-roledescription="carousel"><div class="review-panels">{reviews_html}</div>
<div class="slider-dots" id="reviewTabs" role="tablist" aria-label="Reviews">{dots_html}</div>
</div>
</div></section>
{rev_block}
<section class="cta-banner"><div class="wrap cta-banner-inner">
<h2>{('Hungry? Come taste it fresh.' if is_food else 'Ready to get started?')}</h2>
<p>{('Join us at ' + esc(name) + ' tonight — dine in, take out, or cater your next event.' if is_food else 'Reach out to ' + esc(name) + ' today — free quotes, fast answers, no pressure.')}</p>
<div class="cta-row">
{('<a class="btn btn-primary" href="' + esc(tel) + '">' + _icon("phone", 16) + ' Call ' + esc(phone) + '</a>') if phone else ''}
{('<a class="btn btn-secondary" href="#visit">Plan Your Visit</a>' if is_food else '<a class="btn btn-secondary" href="#contact">Request a Callback</a>')}
</div>
</div></section>
<section id="visit"><div class="wrap visit-grid">
<div>
<p class="eyebrow"><span class="secnum" aria-hidden="true">04</span>Visit</p>
<h2>Visit us</h2>
<address><strong>{esc(name)}</strong><br>{esc(address)}<br>
{('<a href="' + esc(tel) + '">' + esc(phone) + '</a><br>') if phone else ''}<a href="{esc(maps_url)}" target="_blank" rel="noopener" aria-label="Get directions in Google Maps (opens in a new tab)">Find us on Google Maps →</a></address>
<h3>Hours</h3>
    {('<table class="hours">' + "".join(f"<tr><td>{esc(d)}</td><td>{esc(h)}</td></tr>" for d, h in b.get("hours_table")) + "</table>") if isinstance(b.get("hours_table"), list) and b.get("hours_table") else (('<p>' + esc(b.get("hours")) + '</p>') if (b.get("hours") or "").strip() else ('<p>Hours vary by day — call ' + (('<a href="' + esc(tel) + '">' + esc(phone) + '</a>') if phone else 'us') + ' for today&apos;s hours.</p>' if phone else '<p>Hours vary by day — send us a message below and we&apos;ll reply with today&apos;s hours.</p>'))}
</div>
<div id="contact">
<h3>{('Get in touch' if is_food else 'Request a callback')}</h3>
<form id="quote-form" novalidate>
<label>Full name<input name="name" required autocomplete="name" placeholder="Jane Doe"></label>
<label>Phone<input name="phone" type="tel" required autocomplete="tel" placeholder="(425) 555-0100"></label>
<label class="hp" aria-hidden="true"><span>Leave this field empty</span><input name="hp" tabindex="-1" autocomplete="off"></label>
<label>What do you need?<select name="topic"><option>General question</option>{('<option>Table reservation</option><option>Takeout order</option><option>Catering & events</option>' if is_food else '<option>Quote request</option><option>Book an appointment</option>')}<option>Something else</option></select></label>
<label>Message<textarea name="message" rows="4" required placeholder="Tell us what you need…"></textarea></label>
<button class="btn btn-primary" type="submit">{('Send Message' if is_food else 'Request Callback')}</button>
<p class="form-note" role="status" aria-live="polite"></p>
</form>
</div>
</div></section>
</main>
<footer><div class="wrap">
<p><strong>{esc(name)}</strong> · {esc(category)} · {esc(address)}{(' · <a href="' + esc(tel) + '">' + esc(phone) + '</a>') if phone else ''}</p>
<p class="fine">© <span id="year">{datetime.datetime.now().year}</span> {esc(name)}. All rights reserved. · Site by {esc(AGENCY_NAME)}</p>
</div></footer>
{('<div class="preview-banner" role="note">' + _icon("shield", 14) + ' Preview draft by ' + esc(AGENCY_NAME) + ' — design concept, not the official site of ' + esc(name) + '. The contact form is disabled in previews.</div>') if preview else ''}
<script src="script.js"></script>
</body>
</html>
"""

    variant_css = {
        "centered-gradient": f"""
.hero--centered-gradient{{background:radial-gradient(1100px 480px at 50% -12%,{brand} 0%,{dark} 62%,#070504 100%);text-align:center}}
.hero--centered-gradient .hero-inner{{flex-direction:column;align-items:center}}
.hero--centered-gradient .hero-copy{{max-width:820px}}
.hero--centered-gradient .cta-row,.hero--centered-gradient .hero-meta{{justify-content:center}}
""",
        "split-panel": f"""
.hero--split-panel{{background:linear-gradient(160deg,{dark} 0%,{brand} 130%)}}
.hero--split-panel .hero-inner{{display:grid;grid-template-columns:1.15fr .85fr;align-items:center;gap:2rem;text-align:left}}
.hero--split-panel .cta-row,.hero--split-panel .hero-meta{{justify-content:flex-start}}
.hero--split-panel .hero-visual{{position:relative;justify-self:center;width:min(280px,80%);aspect-ratio:1}}
.hero--split-panel .hero-visual-ring{{position:absolute;inset:0;border-radius:50%;border:2px dashed rgba(255,255,255,.35);animation:spin 24s linear infinite}}
.hero--split-panel .hero-visual-icon{{position:absolute;inset:0;display:grid;place-items:center;color:{gold};background:radial-gradient(circle,rgba(255,255,255,.12),transparent 70%)}}
@media(max-width:820px){{.hero--split-panel .hero-inner{{grid-template-columns:1fr;text-align:center}}.hero--split-panel .cta-row,.hero--split-panel .hero-meta{{justify-content:center}}}}
""",
        "editorial-asymmetric": f"""
.hero--editorial-asymmetric{{background:linear-gradient(135deg,{dark} 0%,{brand} 55%,{brand2} 100%);position:relative;overflow:hidden}}
.hero--editorial-asymmetric::before{{content:"";position:absolute;right:-8%;top:-20%;width:60%;height:140%;background:conic-gradient(from 90deg,{gold},transparent 40%);opacity:.18;transform:rotate(12deg)}}
.hero--editorial-asymmetric .hero-inner{{display:grid;grid-template-columns:1.3fr .7fr;align-items:end;gap:2rem;text-align:left;position:relative;z-index:1}}
.hero--editorial-asymmetric h1{{font-size:clamp(2.2rem,7vw,4.2rem)}}
.hero--editorial-asymmetric .cta-row,.hero--editorial-asymmetric .hero-meta{{justify-content:flex-start}}
.hero--editorial-asymmetric .hero-visual{{justify-self:end}}
.hero--editorial-asymmetric .hero-visual-icon{{color:{gold}}}
@media(max-width:820px){{.hero--editorial-asymmetric .hero-inner{{grid-template-columns:1fr;text-align:center}}.hero--editorial-asymmetric .cta-row,.hero--editorial-asymmetric .hero-meta{{justify-content:center}}}}
""",
    }[variant]

    css = f""":root{{--brand:{brand};--brand2:{brand2};--gold:{gold};--bg:{bg};--ink:#1c1917;--muted:#6b6259;--card:#ffffff;--line:{line};--dark:{dark};--radius:16px;--font-display:{font_display};--font-body:{font_body}}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}
body{{margin:0;font-family:var(--font-body);color:var(--ink);background:var(--bg);line-height:1.65;overflow-x:hidden;overflow-x:clip;max-width:100%}}
h1,h2,h3{{font-family:var(--font-display);line-height:1.1;margin:0 0 .5rem;letter-spacing:-.01em}}
a{{color:var(--brand)}}
.wrap{{max-width:1120px;margin:auto;padding-left:1.1rem;padding-right:1.1rem}}
.eyebrow{{text-transform:uppercase;letter-spacing:.14em;font-size:.78rem;font-weight:800;color:var(--brand2);margin:0 0 .35rem}}
.topbar{{background:var(--dark);color:#f3ece2;font-size:.85rem}}
.topbar-inner{{display:flex;gap:1rem;align-items:center;justify-content:space-between;padding-top:.4rem;padding-bottom:.4rem}}
.topbar-inner span,.topbar-phone{{display:inline-flex;align-items:center;gap:.4rem}}
.topbar-phone{{color:var(--gold);font-weight:800;text-decoration:none;white-space:nowrap}}
.hide-mobile{{}}
.site-header{{position:sticky;top:0;background:rgba(255,255,255,.94);backdrop-filter:blur(10px);z-index:30;border-bottom:1px solid var(--line);transition:box-shadow .2s ease}}
.site-header nav{{display:flex;gap:1rem;align-items:center;padding-top:.7rem;padding-bottom:.7rem;flex-wrap:wrap}}
.site-header.scrolled{{box-shadow:0 8px 24px rgba(0,0,0,.10)}}
.brand{{font-family:var(--font-display);font-weight:800;text-decoration:none;color:var(--ink);margin-right:auto;font-size:1.15rem;display:flex;align-items:center;gap:.55rem}}
.brand-badge{{display:inline-grid;place-items:center;width:2.1rem;height:2.1rem;border-radius:.7rem;background:linear-gradient(135deg,var(--brand),var(--brand2));color:#fff}}
.nav-links{{display:flex;gap:1.2rem;list-style:none;margin:0;padding:0}}
.nav-links a{{text-decoration:none;color:var(--ink);font-weight:600;font-size:.95rem}}
.nav-links a:hover{{color:var(--brand)}}
.nav-toggle{{display:none;flex-direction:column;gap:4px;background:none;border:1px solid var(--line);border-radius:.5rem;padding:.5rem .6rem;cursor:pointer}}
.nav-toggle span{{display:block;width:18px;height:2px;background:var(--ink)}}
.btn{{display:inline-flex;align-items:center;gap:.45rem;padding:.75rem 1.35rem;border-radius:999px;text-decoration:none;font-weight:800;font-size:.95rem;border:2px solid transparent;cursor:pointer;transition:transform .12s ease,box-shadow .15s ease}}
.btn-primary{{background:linear-gradient(135deg,var(--brand),var(--brand2));color:#fff;box-shadow:0 8px 22px rgba(0,0,0,.25)}}
.btn-primary:hover{{transform:translateY(-2px)}}
.btn-secondary{{border-color:var(--brand);color:var(--brand);background:#fff}}
.btn-secondary:hover{{transform:translateY(-2px)}}
.btn-ghost{{color:#fff;border-color:rgba(255,255,255,.7)}}
.btn-call{{white-space:nowrap;font-size:.92rem;padding:.6rem 1rem}}
.btn:focus-visible,a:focus-visible,button:focus-visible,input:focus-visible,textarea:focus-visible,select:focus-visible{{outline:3px solid var(--gold);outline-offset:2px}}
.hero{{color:#fff;padding:4.2rem 0 3.2rem;position:relative}}
.hero-inner{{display:flex}}
.badge{{display:inline-flex;align-items:center;gap:.4rem;background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.4);color:var(--gold);border-radius:999px;padding:.35rem .95rem;font-weight:700;font-size:.88rem}}
.hero h1{{font-size:clamp(2.1rem,6vw,3.7rem);margin:1rem 0 .6rem}}
.accent{{background:linear-gradient(90deg,var(--gold),var(--brand2));-webkit-background-clip:text;background-clip:text;color:transparent}}
.tagline{{color:#f3e9dc;font-size:1.12rem;max-width:640px;margin:.5rem 0 1.2rem}}
.cta-row{{display:flex;gap:.75rem;flex-wrap:wrap;margin:1.2rem 0}}
.hero .btn-secondary{{background:#fff}}
.hero-meta{{display:flex;gap:1.2rem;flex-wrap:wrap;margin-top:1.4rem;color:#f0dfcc;font-size:.95rem}}
.hero-meta div{{display:flex;align-items:center;gap:.4rem}}
.hero-visual-ring{{animation-name:spin}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
.marquee{{background:var(--dark);color:var(--gold);overflow:hidden;white-space:nowrap;padding:.55rem 0;font-weight:800;letter-spacing:.06em;font-size:.85rem;max-width:100%}}
.marquee-track{{display:inline-block;animation:scroll-left 22s linear infinite}}
@keyframes scroll-left{{from{{transform:translateX(0)}}to{{transform:translateX(-50%)}}}}
.strip{{margin-top:-1.2rem;position:relative;z-index:2}}
.strip-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:.8rem}}
.strip-item{{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:.95rem 1.05rem;display:flex;gap:.75rem;align-items:center;box-shadow:0 10px 26px rgba(0,0,0,.08)}}
.strip-icon{{display:grid;place-items:center;width:2.4rem;height:2.4rem;border-radius:.7rem;background:color-mix(in srgb, var(--brand) 12%, white);color:var(--brand);flex:none}}
.strip-item small{{display:block;color:var(--muted)}}
section{{padding-top:2.8rem;padding-bottom:2.8rem}}
.section-sub{{color:var(--muted);margin-top:-.25rem}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:1rem;list-style:none;padding:0;margin:1.2rem 0 0}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:1.3rem;box-shadow:0 6px 20px rgba(0,0,0,.05);transition:transform .15s ease,box-shadow .15s ease}}
.card:hover{{transform:translateY(-4px);box-shadow:0 14px 30px rgba(0,0,0,.10)}}
.card-icon{{display:inline-grid;place-items:center;width:2.3rem;height:2.3rem;border-radius:.65rem;background:color-mix(in srgb, var(--brand) 12%, white);color:var(--brand);margin-bottom:.6rem}}
.card h3{{font-size:1.05rem}}
.about-grid{{display:grid;grid-template-columns:1.4fr .9fr;gap:1.3rem;align-items:start}}
.checklist{{list-style:none;padding:0;margin:1rem 0 0;display:grid;gap:.5rem;font-weight:600}}
.checklist li{{display:flex;align-items:center;gap:.5rem}}
.checklist svg{{color:var(--brand);flex:none}}
.about-card{{background:linear-gradient(160deg,var(--dark),var(--brand));color:#f3e9dc;border-radius:var(--radius);padding:1.5rem;display:grid;gap:.7rem;position:relative;overflow:hidden}}
.about-card-icon{{color:var(--gold)}}
.about-card .big{{font-size:1.25rem;font-weight:900;color:var(--gold);margin:0}}
.about-card .btn{{text-align:center;justify-content:center}}
#reviews{{text-align:center}}
.rating{{font-size:1.2rem;font-weight:800}}
.narrow{{max-width:760px}}
.review-slider{{max-width:680px;margin:1rem auto 0}}
.review-panels{{position:relative}}
.review{{display:none;background:#fff;border:1px solid var(--line);border-radius:var(--radius);padding:1.6rem;box-shadow:0 8px 24px rgba(0,0,0,.06)}}
.review.active{{display:block}}
.review[aria-hidden="false"]{{display:block}}
.review-stars{{display:flex;gap:.15rem;justify-content:center;color:var(--gold);margin-bottom:.5rem}}
.review p{{font-size:1.1rem;margin:0 0 .6rem}}
.review cite{{color:var(--muted);font-style:normal;font-weight:700}}
.slider-dots{{display:flex;gap:.5rem;justify-content:center;margin-top:.9rem}}
.dot{{width:12px;height:12px;border-radius:50%;border:none;background:#e0d5c6;cursor:pointer}}
.dot.active{{background:var(--brand)}}
.cta-banner{{background:linear-gradient(120deg,var(--dark),var(--brand));color:#fff;text-align:center}}
.cta-banner-inner{{display:grid;gap:.6rem;justify-items:center}}
.cta-banner .cta-row{{justify-content:center}}
.visit-grid{{display:grid;grid-template-columns:1fr 1fr;gap:1.3rem;align-items:start}}
address{{font-style:normal;background:#fff;border:1px solid var(--line);border-radius:12px;padding:1rem}}
.hours{{border-collapse:collapse;margin:.6rem 0 1rem;background:#fff}}
.hours td{{padding:.55rem .9rem;border-bottom:1px solid var(--line)}}
form{{display:grid;gap:.75rem;background:#fff;border:1px solid var(--line);border-radius:12px;padding:1.15rem}}
label{{display:grid;gap:.3rem;font-weight:700;font-size:.92rem}}
input,textarea,select{{width:100%;padding:.65rem .75rem;border:1.5px solid #d9c7b4;border-radius:.6rem;font:inherit;background:#fffdfb}}
.form-note{{min-height:1.4em;color:var(--brand);font-weight:700;margin:0}}
.hp{{position:absolute!important;left:-9999px!important;width:1px;height:1px;overflow:hidden;opacity:0}}
footer{{background:var(--dark);color:#cbb9ab;text-align:center;padding:2.2rem 0 2.6rem;margin-top:1rem}}
footer a{{color:var(--gold)}}
footer .fine{{font-size:.85rem;opacity:.85}}
footer strong{{color:#fff}}
.reveal{{opacity:0;transform:translateY(16px);transition:opacity .5s ease,transform .5s ease}}
.reveal.visible{{opacity:1;transform:none}}
.preview-banner{{position:fixed;left:0;right:0;bottom:0;z-index:50;background:var(--dark);color:var(--gold);text-align:center;font-size:.82rem;font-weight:700;padding:.55rem .8rem;border-top:2px solid var(--gold);display:flex;gap:.4rem;align-items:center;justify-content:center}}
{('body{padding-bottom:2.4rem}') if preview else ''}
{variant_css}
::selection{{background:var(--gold);color:#1c1917}}
::-webkit-scrollbar{{width:11px}}::-webkit-scrollbar-track{{background:var(--bg)}}
::-webkit-scrollbar-thumb{{background:linear-gradient(var(--brand),var(--brand2));border-radius:8px}}
body::after{{content:"";position:fixed;inset:0;background-image:url("{_GRAIN_URI}");opacity:.05;pointer-events:none;z-index:5}}
h2{{font-size:clamp(1.7rem,3.6vw,2.5rem)}}
.secnum{{font-family:var(--font-display);color:var(--brand2);margin-right:.55rem;font-size:.9em;font-weight:800}}
.eyebrow{{display:flex;align-items:center}}
.eyebrow::after{{content:"";height:1px;width:54px;background:var(--line);margin-left:.7rem}}
#reviews .eyebrow{{justify-content:center}}
.hero{{overflow:hidden}}
.hero::before{{content:"";position:absolute;inset:0;background-image:url("{texture}");opacity:.6;pointer-events:none}}
.hero-inner{{position:relative;z-index:1}}
.hero-aurora{{position:absolute;inset:0;width:100%;height:100%;opacity:.55;pointer-events:none}}
.hero-ghost{{position:absolute;right:-24px;bottom:-64px;color:#fff;opacity:.07;pointer-events:none;line-height:0}}
.hero-photo{{width:100%;height:100%;object-fit:cover;border-radius:18px;display:block;box-shadow:0 20px 50px rgba(0,0,0,.35)}}
.hero--split-panel .hero-photo{{border-radius:50%;aspect-ratio:1}}
.hero--centered-gradient .hero-visual{{width:min(640px,100%);margin-top:1.6rem}}
.hero--editorial-asymmetric .hero-visual{{width:100%;max-width:380px}}
.about-photo{{width:100%;border-radius:var(--radius);margin:1rem 0 .4rem;display:block;box-shadow:0 10px 26px rgba(0,0,0,.10)}}
.hero h1{{font-size:clamp(2.6rem,7.2vw,4.6rem);letter-spacing:-.02em}}
body.js .hero-copy>*{{opacity:0;transform:translateY(26px)}}
body.js.loaded .hero-copy>*{{opacity:1;transform:none;transition:opacity .7s ease,transform .7s cubic-bezier(.2,.7,.2,1)}}
body.js.loaded .hero-copy>*:nth-child(1){{transition-delay:.05s}}
body.js.loaded .hero-copy>*:nth-child(2){{transition-delay:.14s}}
body.js.loaded .hero-copy>*:nth-child(3){{transition-delay:.24s}}
body.js.loaded .hero-copy>*:nth-child(4){{transition-delay:.34s}}
body.js.loaded .hero-copy>*:nth-child(5){{transition-delay:.44s}}
.wmask{{display:inline-block;overflow:hidden;vertical-align:bottom;padding-bottom:.08em;margin-bottom:-.08em}}
.w{{display:inline-block;transform:translateY(110%);transition:transform .6s cubic-bezier(.2,.7,.2,1)}}
.wmask.in .w{{transform:none}}
.loader{{display:none}}
body.js .loader{{display:grid;position:fixed;inset:0;z-index:100;background:var(--dark);place-items:center;transition:opacity .45s ease,visibility .45s}}
body.js.loaded .loader{{opacity:0;visibility:hidden}}
.loader-mark{{font-family:var(--font-display);font-weight:800;font-size:2.4rem;color:var(--gold);border:2px solid var(--gold);border-radius:1rem;padding:.6rem 1.3rem;animation:pulse 1s ease infinite}}
@keyframes pulse{{50%{{opacity:.35}}}}
.progress{{position:fixed;top:0;left:0;right:0;height:3px;z-index:70}}
.progress span{{display:block;height:100%;transform:scaleX(0);transform-origin:0 50%;background:linear-gradient(90deg,var(--gold),var(--brand2))}}
.stats{{background:var(--dark);color:#fff;padding:2.1rem 0}}
.stats-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:1rem;text-align:center;align-items:center}}
.stat-num{{font-family:var(--font-display);font-weight:800;font-size:clamp(2rem,5vw,3rem);color:var(--gold)}}
.stat-star{{color:var(--gold);margin-left:.25rem;vertical-align:super}}
.stat-label{{display:block;color:#e9dcc9;font-size:.9rem;margin-top:.15rem}}
.stack{{list-style:none;margin:1.2rem 0 0;padding:0;display:grid;gap:1rem}}
.stack-card{{position:sticky;top:92px;background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:1.6rem;box-shadow:0 12px 30px rgba(0,0,0,.08);display:grid;grid-template-columns:auto 1fr;gap:.3rem 1rem;align-items:start}}
.stack-card:nth-child(2){{top:102px}}.stack-card:nth-child(3){{top:112px}}
.stack-card:nth-child(4){{top:122px}}.stack-card:nth-child(5){{top:132px}}
.stack-card:nth-child(6){{top:142px}}
.stack-card.card:hover{{transform:none}}
.stack-num{{font-family:var(--font-display);font-weight:800;font-size:2.4rem;line-height:1;color:transparent;-webkit-text-stroke:1.5px var(--brand);grid-row:span 2}}
.stack-card h3{{margin-top:.35rem}}
@media(max-width:760px){{.stats-grid{{grid-template-columns:1fr;gap:1.4rem}}.stack-card{{top:84px}}.hero-ghost{{right:-60px;opacity:.05}}}}
@media (prefers-reduced-motion:reduce){{body.js .hero-copy>*{{opacity:1;transform:none;transition:none}}body.js .loader,.loader{{display:none}}.w{{transform:none;transition:none}}.hero-aurora{{display:none}}.marquee-track{{animation:none}}}}
@media(max-width:760px){{
  .nav-links{{display:none;width:100%;flex-direction:column;background:#fff;border:1px solid var(--line);border-radius:12px;padding:.7rem}}
  .nav-links.open{{display:flex}}
  .nav-toggle{{display:flex}}
  .hide-mobile{{display:none}}
  .btn-call{{width:100%;justify-content:center}}
  .about-grid,.visit-grid{{grid-template-columns:1fr}}
  .hero{{padding:3.2rem 0 2.6rem}}
}}
"""

    js = ("""(function(){
var t=document.querySelector('.nav-toggle'),l=document.querySelector('.nav-links');
if(t&&l){t.addEventListener('click',function(){var o=l.classList.toggle('open');t.setAttribute('aria-expanded',o)})}
document.querySelectorAll('a[href^="#"]').forEach(function(a){a.addEventListener('click',function(e){
  var tgt=document.querySelector(a.getAttribute('href'));
  if(tgt){e.preventDefault();tgt.scrollIntoView({behavior:'smooth'});
    if(l&&l.classList.contains('open')){l.classList.remove('open');t.setAttribute('aria-expanded','false')}}
})});
var h=document.getElementById('siteHeader');
if(h){addEventListener('scroll',function(){h.classList.toggle('scrolled',scrollY>8)},{passive:true})}
""" + ("var PREVIEW=true;" if preview else "var PREVIEW=false;") + """
var f=document.getElementById('quote-form');
function formNote(f){return f.querySelector('.form-note')}
if(f){f.addEventListener('submit',function(e){
  e.preventDefault();
  var n=f.name.value.trim(),p=f.phone.value.trim(),m=f.message.value.trim(),note=formNote(f);
  if(!n||!p||!m){note.textContent='Please fill in your name, phone, and message.';return}
  if(p.replace(/\\D/g,'').length<7){note.textContent='That phone number looks too short — please double-check.';return}
  if(f.hp&&f.hp.value){note.textContent='Thanks '+n.split(' ')[0]+'!';f.reset();return}
  if(PREVIEW){note.textContent='Thanks '+n.split(' ')[0]+'! (Design preview — this form goes live when the site launches.)';return}
  var ep=document.querySelector('meta[name="form-endpoint"]');
  if(ep&&ep.content){
    var data={name:n,phone:p,message:m};
    if(f.topic)data.topic=f.topic.value;
    data.hp=f.hp?f.hp.value:'';
    fetch(ep.content,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})
      .then(function(r){if(!r.ok)throw new Error(r.status);return r.json()})
      .then(function(){note.textContent='Thanks '+n.split(' ')[0]+'! We received your request and will reply shortly.';f.reset()})
      .catch(function(){note.textContent='Sorry — we could not send your request. Please call us directly.'});
    return;
  }
  note.textContent='Thanks '+n.split(' ')[0]+'! We received your request and will reply shortly.';f.reset()
})}
var dots=Array.prototype.slice.call(document.querySelectorAll('.dot')),
    reviews=Array.prototype.slice.call(document.querySelectorAll('.review')),cur=0;
function show(i){if(!reviews.length)return;cur=(i+reviews.length)%reviews.length;
  reviews.forEach(function(r,j){
    var on=j===cur;r.classList.toggle('active',on);
    r.setAttribute('aria-hidden',on?'false':'true')
  });
  dots.forEach(function(d,j){
    var on=j===cur;d.classList.toggle('active',on);
    if(d.hasAttribute('aria-selected'))d.setAttribute('aria-selected',on?'true':'false');
    if(d.hasAttribute('aria-controls'))d.setAttribute('tabindex',on?'0':'-1')
  })}
dots.forEach(function(d,i){d.addEventListener('click',function(){show(i)})});
var tabs=document.querySelector('.slider-dots');
if(tabs){tabs.addEventListener('keydown',function(e){
  if(e.key!=='ArrowRight'&&e.key!=='ArrowLeft')return;
  e.preventDefault();
  var idx=dots.indexOf(document.activeElement);if(idx<0)return;
  show((e.key==='ArrowRight'?idx+1:idx-1+dots.length)%dots.length);dots[idx<dots.length-1?idx+1:0].focus()
})}
var sliderEl=document.querySelector('.review-slider'),autoTimer=null;
function stopAuto(){if(autoTimer){clearInterval(autoTimer);autoTimer=null}}
function startAuto(){if(reviews.length>1&&!prefersReducedMotion){if(autoTimer)stopAuto();autoTimer=setInterval(function(){show(cur+1)},6000)}}
if(sliderEl){sliderEl.addEventListener('mouseenter',stopAuto);sliderEl.addEventListener('mouseleave',startAuto)}
var prefersReducedMotion=matchMedia('(prefers-reduced-motion: reduce)').matches;
var y=document.getElementById('year');if(y){y.textContent=new Date().getFullYear()}
startAuto();
if('IntersectionObserver' in window && !prefersReducedMotion){
  var obs=new IntersectionObserver(function(entries){
    entries.forEach(function(en){if(en.isIntersecting){en.target.classList.add('visible');obs.unobserve(en.target)}})
  },{threshold:.12});
  document.querySelectorAll('.card, .strip-item, .about-card, .review').forEach(function(el){
    el.classList.add('reveal');obs.observe(el)
  });
} else {
  document.querySelectorAll('.card, .strip-item, .about-card, .review').forEach(function(el){el.classList.add('reveal','visible')});
}
/* motion v2 — award-tier entrances, split reveals, magnetic CTAs, progress,
   parallax, count-ups, aurora canvas. All gated on reduced-motion + no-JS. */
document.body.classList.add('js');
var reduceMotion=matchMedia('(prefers-reduced-motion: reduce)').matches;
function pageLoaded(){document.body.classList.add('loaded')}
if(document.readyState==='complete'){pageLoaded()}
else{addEventListener('load',pageLoaded);setTimeout(pageLoaded,2500)}
if(!reduceMotion&&'IntersectionObserver' in window){
  var swObs=new IntersectionObserver(function(es){es.forEach(function(en){
    if(en.isIntersecting){en.target.classList.add('in');swObs.unobserve(en.target)}})},{threshold:.4});
  document.querySelectorAll('main h2').forEach(function(h){
    var words=h.textContent.trim().split(/\\s+/);
    if(words.length<2)return;
    h.setAttribute('aria-label',h.textContent.trim());
    h.innerHTML=words.map(function(w,i){return '<span class="wmask" aria-hidden="true"><span class="w" style="transition-delay:'+(i*45)+'ms">'+w+'</span></span>'}).join(' ');
    Array.prototype.forEach.call(h.querySelectorAll('.wmask'),function(m){swObs.observe(m)});
  });
}
if(!reduceMotion&&matchMedia('(pointer:fine)').matches){
  document.querySelectorAll('.hero .btn-primary, .cta-banner .btn-primary').forEach(function(btn){
    btn.addEventListener('pointermove',function(e){
      var r=btn.getBoundingClientRect();
      btn.style.transform='translate('+((e.clientX-(r.left+r.width/2))*.18)+'px,'+((e.clientY-(r.top+r.height/2))*.28)+'px)';
    });
    btn.addEventListener('pointerleave',function(){
      btn.style.transition='transform .25s ease';btn.style.transform='';
      setTimeout(function(){btn.style.transition=''},260);
    });
  });
}
var pBar=document.getElementById('progressBar');
var plx=Array.prototype.slice.call(document.querySelectorAll('[data-parallax]'));
var ticking=false;
function onScroll2(){ticking=false;
  if(pBar){var max=document.documentElement.scrollHeight-innerHeight;
    pBar.style.transform='scaleX('+(max>0?scrollY/max:0)+')'}
  if(!reduceMotion){plx.forEach(function(el){
    el.style.transform='translateY('+(scrollY*parseFloat(el.getAttribute('data-parallax')))+'px)'})}
}
addEventListener('scroll',function(){if(!ticking){ticking=true;requestAnimationFrame(onScroll2)}},{passive:true});
onScroll2();
var counters=document.querySelectorAll('[data-count]');
function runCount(el){
  var target=parseFloat(el.getAttribute('data-count'));
  var dec=parseInt(el.getAttribute('data-decimals')||'0',10);
  if(reduceMotion){el.textContent=target.toFixed(dec);return}
  var t0=null,dur=1400;
  function step(t){if(!t0)t0=t;var p=Math.min((t-t0)/dur,1);
    el.textContent=(target*(1-Math.pow(1-p,3))).toFixed(dec);
    if(p<1)requestAnimationFrame(step)}
  requestAnimationFrame(step);
}
if(counters.length){
  if('IntersectionObserver' in window){
    var cObs=new IntersectionObserver(function(es){es.forEach(function(en){
      if(en.isIntersecting){runCount(en.target);cObs.unobserve(en.target)}})},{threshold:.5});
    counters.forEach(function(c){cObs.observe(c)});
  }else{counters.forEach(runCount)}
}
var aurora=document.getElementById('heroAurora');
if(aurora&&!reduceMotion){
  var actx=aurora.getContext('2d'),AW,AH,parts=[],running=true;
  function sizeAurora(){var r=aurora.parentElement.getBoundingClientRect();
    AW=aurora.width=Math.max(1,Math.round(r.width));AH=aurora.height=Math.max(1,Math.round(r.height))}
  sizeAurora();addEventListener('resize',sizeAurora);
  var cs=getComputedStyle(document.documentElement);
  var brandC=(cs.getPropertyValue('--brand')||'#ffffff').trim();
  var goldC=(cs.getPropertyValue('--gold')||'#ffffff').trim();
  for(var pi=0;pi<46;pi++){parts.push({x:Math.random(),y:Math.random(),
    r:1+Math.random()*2.6,s:.0004+Math.random()*.0012,o:.15+Math.random()*.5,hue:Math.random()<.5?0:1})}
  function drawAurora(){
    if(!running)return;
    actx.clearRect(0,0,AW,AH);
    var t=Date.now()*.0002;
    var g1x=AW*(.25+.15*Math.sin(t)),g1y=AH*(.3+.1*Math.cos(t*1.3));
    var g=actx.createRadialGradient(g1x,g1y,0,g1x,g1y,AW*.4);
    g.addColorStop(0,goldC);g.addColorStop(1,'rgba(0,0,0,0)');
    actx.globalAlpha=.28;actx.fillStyle=g;actx.fillRect(0,0,AW,AH);
    var g2x=AW*(.8+.12*Math.cos(t*.8)),g2y=AH*(.7+.12*Math.sin(t*1.1));
    var g2=actx.createRadialGradient(g2x,g2y,0,g2x,g2y,AW*.35);
    g2.addColorStop(0,brandC);g2.addColorStop(1,'rgba(0,0,0,0)');
    actx.fillStyle=g2;actx.fillRect(0,0,AW,AH);
    parts.forEach(function(p){
      p.y-=p.s;if(p.y<-.02){p.y=1.02;p.x=Math.random()}
      actx.globalAlpha=p.o;actx.fillStyle=p.hue?goldC:'#ffffff';
      actx.beginPath();actx.arc(p.x*AW,p.y*AH,p.r,0,6.283);actx.fill();
    });
    actx.globalAlpha=1;
    requestAnimationFrame(drawAurora);
  }
  if('IntersectionObserver' in window){
    new IntersectionObserver(function(es){es.forEach(function(en){
      var was=running;running=en.isIntersecting;if(running&&!was)drawAurora()})}).observe(aurora);
  }
  drawAurora();
}
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
    # The prompt forbids self-added banners, but older builds have them:
    # remove any non-pipeline preview banner so exactly one ever ships.
    html = re.sub(r'<div class="preview-banner"[^>]*>.*?</div>\s*', "", html,
                  flags=re.S)
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


def _opencode_output_defects(target: Path) -> list[str]:
    """Reject known-bad OpenCode output before it ships (mojibake check).

    Returns a list of defect descriptions (empty = clean). Currently flags
    Unicode replacement chars (U+FFFD from undecodable model output) in
    index.html — the `90-bangkok` class of failure where the shipped
    `<title>`/meta contained mojibake. Missing/empty files are checked by
    the caller; this covers content defects.
    """
    defects: list[str] = []
    try:
        html = (target / "index.html").read_text(encoding="utf-8")
    except OSError:
        return ["index.html unreadable"]
    if "\ufffd" in html:
        defects.append("index.html contains U+FFFD replacement chars (mojibake)")
    return defects


def _opencode_autofix(target: Path) -> list[str]:
    """Repair machine-checkable OpenCode misses in place (no model round-trip).

    Models intermittently skip prompt requirements that qa_bot verifies
    statically; fixing them here (same philosophy as apply_site_hardening)
    is cheaper and more reliable than another full generation:
    - prefers-reduced-motion kill-switch appended when absent from CSS+JS.
    - bare `.reveal{opacity:0}` rules rescoped to `body.js .reveal` (plus the
      `js` class bootstrap in script.js) so content never hides with JS off.
    - a ::selection rule plus an SVG-grain overlay appended when the page has
      no texture craft at all (feTurbulence / ::selection / hero-ghost).
    Returns the applied fix names (empty = output was already clean).
    Brand-contrast and signature-moment failures still need the model, so
    they stay prompt + QA-retry responsibilities.
    """
    fixed: list[str] = []
    try:
        css_p, js_p, html_p = target / "styles.css", target / "script.js", target / "index.html"
        css, js = css_p.read_text(encoding="utf-8"), js_p.read_text(encoding="utf-8")
        html = html_p.read_text(encoding="utf-8")
    except OSError:
        return fixed
    html_low = html.lower()

    if "prefers-reduced-motion" not in css and "prefers-reduced-motion" not in js.lower():
        css += ("\n@media (prefers-reduced-motion:reduce){*,*::before,*::after"
                "{animation-duration:.01ms!important;animation-iteration-count:1!important;"
                "transition-duration:.01ms!important;scroll-behavior:auto!important}}\n")
        fixed.append("reduced-motion")

    if (re.search(r'class="[^"]*\breveal\b', html_low) and ".reveal" in css
            and "opacity:0" in css.replace(" ", "") and "body.js" not in css):
        def _scope(m: "re.Match") -> str:
            sel, body = m.group(1), m.group(2)
            if ".reveal" in sel and "body.js" not in sel:
                sel = re.sub(r"\.reveal\b", "body.js .reveal", sel)
            return sel + "{" + body + "}"
        css = re.sub(r"([^{}]+)\{([^{}]*opacity\s*:\s*0[^{}]*)\}", _scope, css)
        if "body.js" in css and 'classList.add(' not in js:
            js = "document.body.classList.add('js');\n" + js
        fixed.append("reveal-scope")

    if ("feTurbulence" not in css and "feTurbulence" not in html
            and "::selection" not in css and "hero-ghost" not in html):
        css += ("\n::selection{background:#ffc53d;color:#1c1917}\n"
                'body::after{content:"";position:fixed;inset:0;pointer-events:none;opacity:.05;'
                'background-image:url("data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' '
                'width=\'120\' height=\'120\'%3E%3Cfilter id=\'n\'%3E%3CfeTurbulence type=\'fractalNoise\' '
                'baseFrequency=\'0.9\' numOctaves=\'2\'/%3E%3C/filter%3E%3Crect width=\'120\' height=\'120\' '
                'filter=\'url(%23n)\' opacity=\'0.6\'/%3E%3C/svg%3E")}\n')
        fixed.append("texture")

    if fixed:
        try:
            css_p.write_text(css, encoding="utf-8")
            js_p.write_text(js, encoding="utf-8")
        except OSError:
            return []
    return fixed


def _prompt_sha(prompt: str) -> str:
    """Short stable hash of the build prompt for meta.json diagnosability."""
    import hashlib
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]


def generate_one(lead: dict, out_root: Path, feedback: dict | None,
                 use_opencode: bool, force: bool, preview: bool = True,
                 require_opencode: bool = False) -> dict:
    lid = lead.get("lead_id") or "lead_unknown"
    target, slug = site_dir_for(lead, out_root)
    required = ("index.html", "styles.css", "script.js")

    # Decide the actual engine up front. "opencode" is only attempted if the
    # CLI is genuinely reachable; otherwise we go straight to the template
    # engine so a fresh checkout with no CLI installed still works.
    want_opencode = use_opencode and opencode_available()
    if use_opencode and not want_opencode and require_opencode:
        raise RuntimeError("OpenCode CLI not found and --require-opencode was set")
    engine = "opencode" if want_opencode else "template"

    try:
        cached_meta = json.loads((target / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cached_meta = {}
    try:
        reports = json.loads((target / "qa_report.json").read_text(encoding="utf-8"))
        last_qa = reports[-1] if isinstance(reports, list) and reports else {}
    except (OSError, ValueError):
        last_qa = {}
    if not isinstance(last_qa, dict):
        last_qa = {}
    if (not force and not feedback and isinstance(cached_meta, dict)
            and cached_meta.get("engine") == engine
            and cached_meta.get("preview_mode") == preview
            and last_qa.get("passed") is not False
            and all((target / name).is_file() and (target / name).stat().st_size
                    for name in required)):
        apply_site_hardening(target, lead)
        return {"lead_id": lid, "dir": str(target), "skipped": True, "engine": engine}

    target.mkdir(parents=True, exist_ok=True)
    photos = collect_business_photos(lead, target)
    if photos:
        print(f"[website_generator] {len(photos)} real photo(s): "
              + ", ".join(p["file"] for p in photos), flush=True)
    prompt = build_prompt(lead, feedback, preview=preview, photos=photos)
    last_issues = last_qa.get("issues") or []
    if last_qa.get("passed") is False:
        prompt += "\nPREVIOUS QA FAILED — fix these issues: " + json.dumps(
            last_issues, ensure_ascii=False)
    elif last_issues:
        # Passed, but with warnings: self-heal on the next rebuild instead of
        # carrying defects forever (passed results are cached, so this only
        # fires on forced/feedback/polish rebuilds).
        prompt += ("\nPREVIOUS QA NOTES — the last build passed, but fix these "
                   "remaining issues too: " + json.dumps(last_issues, ensure_ascii=False))

    if want_opencode:
        before = {name: (target / name).read_bytes() if (target / name).is_file() else None
                  for name in required}
        (target / "meta.json").write_text(json.dumps({
            "lead_id": lid, "slug": target.name, "engine": "opencode-incomplete",
            "preview_mode": preview}, indent=2), encoding="utf-8")
        try:
            run_opencode_command(target, prompt)
            missing = [name for name in required
                       if not (target / name).is_file() or not (target / name).stat().st_size]
            if missing:
                raise RuntimeError("OpenCode did not produce required files: " + ", ".join(missing))
            if all((target / name).read_bytes() == before[name] for name in required):
                raise RuntimeError("OpenCode did not change any website files; refusing stale output")
            bad = _opencode_output_defects(target)
            if bad:
                raise RuntimeError("OpenCode output failed validation: " + "; ".join(bad))
        except RuntimeError as e:
            if require_opencode:
                raise
            print(f"[website_generator] OpenCode failed ({e}); "
                  f"falling back to the built-in template engine.", flush=True)
            engine = "template"
            files = render_template(lead, feedback, preview=preview, photos=photos)
            for name, content in files.items():
                (target / name).write_text(content, encoding="utf-8")
        else:
            fixes = _opencode_autofix(target)
            if fixes:
                print(f"[website_generator] opencode autofix: {', '.join(fixes)}",
                      flush=True)
            if preview:
                apply_preview_lock(target, lead.get("name") or "this business")
    else:
        files = render_template(lead, feedback, preview=preview, photos=photos)
        for name, content in files.items():
            (target / name).write_text(content, encoding="utf-8")

    # Template engine has no banner of its own: lock previews here so every
    # outreach build ships banner + demo forms (opencode path locks itself).
    if preview and engine == "template":
        apply_preview_lock(target, lead.get("name") or "this business")

    # Guarantee canonical/OG/Twitter/JSON-LD + overflow guard + carousel ARIA
    # for every site, whichever engine produced it (idempotent).
    apply_site_hardening(target, lead)

    meta = {"lead_id": lid, "slug": target.name,
            "photos": [p["file"] for p in photos],
            "business": {k: lead.get(k) for k in
            ("name", "category", "address", "phone", "website", "rating", "review_count")},
            "lead_type": lead.get("lead_type"), "opportunity_score": lead.get("opportunity_score"),
            "engine": engine, "created_at": utc_now_iso(), "preview_mode": preview,
            "model": os.environ.get("AGENCY_OPENCODE_MODEL", "").strip() or DEFAULT_OPENCODE_MODEL,
            "prompt_sha": _prompt_sha(prompt),
            "feedback_applied": bool(feedback and feedback.get("requested_changes"))}
    (target / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"lead_id": lid, "dir": str(target), "skipped": False, "engine": engine}


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Bot 5: build working sites -> generated_sites/<slug>/")
    p.add_argument("--leads", default="target_leads.json")
    p.add_argument("--lead", default=None, help="Single lead_id to build")
    p.add_argument("--all", action="store_true", help="Build all leads (default if no --lead)")
    p.add_argument("--limit", type=int, default=0, help="Max sites to build (0 = all)")
    p.add_argument("--output-dir", default="generated_sites")
    p.add_argument("--force", action="store_true", help="Regenerate even if index.html exists")
    p.add_argument("--no-opencode", action="store_true",
                   help="Skip OpenCode entirely, use the built-in template engine directly")
    p.add_argument("--require-opencode", action="store_true",
                   help="Disable automatic template fallback; fail loudly if OpenCode is unavailable or errors")
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
    By default, if OpenCode isn't installed or a build fails, the script
    automatically falls back to the built-in template engine rather than
    stopping the run — pass require_opencode=True / --require-opencode to
    disable that and fail loudly instead.
    """
    if isinstance(argv, dict) and lead_data is None:
        lead_data = argv
        argv = []
    if argv is None:
        argv = []
    args = parse_args(argv)
    if "lead_data" in kwargs:
        if lead_data is not None:
            raise TypeError("website_generator.main() got lead_data twice")
        lead_data = kwargs.pop("lead_data")
    for _k, _v in kwargs.items():
        if not hasattr(args, _k):
            raise TypeError(f"website_generator.main() got an unexpected option {_k!r}")
        setattr(args, _k, _v)
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
        if not isinstance(lead_data, dict):
            print("[website_generator] ERROR: lead_data must be a dict", file=sys.stderr)
            return 2
        if not lead_data.get("lead_id"):
            print("[website_generator] ERROR: lead_data missing 'lead_id'", file=sys.stderr)
            return 2
        res = generate_one(lead_data, out_root, feedback, use_opencode, args.force,
                         preview=args.preview, require_opencode=args.require_opencode)
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

    engine_note = "template (explicit --no-opencode)" if args.no_opencode else \
                  ("opencode (falls back to template automatically)"
                   if not args.require_opencode else "opencode (required, no fallback)")
    print(f"[website_generator] building {len(selected)} site(s) via {engine_note}", flush=True)
    built, skipped = 0, 0
    for lead in selected:
        res = generate_one(lead, out_root, feedback, not args.no_opencode, args.force,
                         preview=args.preview, require_opencode=args.require_opencode)
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
