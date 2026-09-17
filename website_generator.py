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
        "https://fonts.googleapis.com/css2?family=Playfair+Display:wght@600;700;800&family=Source+Sans+3:wght@400;500;600;700&display=swap"),
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
                       "Dine in, take out, or feed the whole crew — check the menu below."),
            services=[
                ("Signature Mains", "The dishes regulars drive across town for, made to order."),
                ("Family & Party Platters", "Feed 3–8 with sides, bread, and sauces included."),
                ("Takeout in ~15 Min", "Call ahead and skip the wait — hot at the counter."),
                ("Lunch Specials", "Fast midday plates that beat fast food on price and taste."),
                ("Catering", "Trays and platters for offices, teams, and celebrations."),
                ("Daily Specials", "Ask what's cooking today — it sells out most days."),
            ],
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
            font="bold_industrial", icon="dumbbell",
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
            font="clean_modern", icon="camera",
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
# OpenCode prompt (used when the CLI is available)
# ---------------------------------------------------------------------------

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
        "DESIGN BAR — this must look like a $10k award-winning agency site that makes "
        "the viewer say wow on the first screen. Competent-and-bland FAILS this bar.",
        "- ART DIRECTION, one bold concept for THIS business — commit fully. Examples: Mexican restaurant = "
        "dark moody cantina (deep ember + cream + gold) with a papel-picado SVG bunting motif and grain texture; "
        "auto shop = bold industrial navy+orange with blueprint-grid texture and stencil display type; "
        "coffee = cozy cream+brown with steam-swirl SVG curves. Never default blue-on-white, never #0b5fff.",
        "- TYPOGRAPHY: Google Fonts pairing (one expressive display face + one clean body face), fluid clamp() "
        "scale, oversized hero H1 with an accent-gradient phrase, eyebrow kickers on every section.",
        "- HERO must be layered and dramatic: multi-stop gradient (linear-gradient and/or radial-gradient) + "
        "SVG pattern/texture overlay + badge + exactly one H1 + subcopy + 2 CTAs + trust meta row. "
        "A text-on-flat-color hero is a FAIL.",
        "- MOTION everywhere it counts: IntersectionObserver scroll reveals, a scrolling marquee strip "
        "(CSS keyframes), review slider with dots + setInterval auto-rotate, sticky-header shadow on scroll, "
        "scrollIntoView smooth anchors, card hover lifts. Respect prefers-reduced-motion.",
        "- ICONS: inline SVG only. NO emoji anywhere on the page (not in cards, not in buttons, not in the topbar).",
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
        "- CSS: :root MUST define --brand, --brand2, --gold (plus --bg, --dark); sticky blurred header; "
        "cards with shadow+radius+hover; .btn-primary with background AND color; @media breakpoints at ~760px "
        "with working mobile nav toggle (.nav-toggle wired to .nav-links); focus-visible styles; scroll-behavior.",
        "- JS: mobile nav toggle, scrollIntoView smooth anchors, review slider with setInterval auto-rotate + dots, "
        "quote-form validation (required fields, phone-length check) with inline success message, "
        "sticky-header shadow, current year in footer.",
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
        "Book Appointment), tel: link with the exact phone given, form with id=\"quote-form\" (exact id, required) "
        "with required fields + JS validation, utility bar with class=\"topbar\" (exact class), service cards with "
        "class=\"card\" (exact class), :root defining --brand/--brand2/--gold (exact names), "
        "viewport meta, meta description, favicon (inline SVG data URI), alt text on images, address + hours <table>, "
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
# Fallback template engine (offline, no dependencies) — v2
# ---------------------------------------------------------------------------

LAYOUT_VARIANTS = ("centered-gradient", "split-panel", "editorial-asymmetric")


def render_template(b: dict, feedback: dict | None,
                    preview: bool = True) -> dict[str, str]:
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
    maps_url = b.get("maps_url") or "#"
    changes = (feedback or {}).get("requested_changes") or []
    prof = _category_profile(category, name)
    brand, brand2, gold, bg, dark, line = prof["palette"]
    font_display, font_body, font_url = FONT_PAIRINGS[prof["font"]]
    variant = LAYOUT_VARIANTS[_stable_seed(b) % len(LAYOUT_VARIANTS)]

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
    services_html = "".join(
        f'<li class="card"><span class="card-icon" aria-hidden="true">{_icon(prof["icon"], 20)}</span>'
        f"<h3>{esc(t)}</h3><p>{esc(d)}</p></li>"
        for t, d in prof["services"])
    quotes = prof["reviews"]
    reviews_html = "".join(
        f'<blockquote class="review{" active" if i == 0 else ""}">'
        f'<div class="review-stars" aria-hidden="true">{_icon("star", 16) * 5}</div>'
        f"<p>“{esc(q)}”</p><cite>— {esc(who)}</cite></blockquote>"
        for i, (q, who) in enumerate(quotes))
    dots_html = "".join(
        f'<button class="dot{" active" if i == 0 else ""}" aria-label="Review {i + 1}"></button>'
        for i in range(len(quotes)))
    rev_block = ("<section class='revisions'><h2>Latest updates per your feedback</h2><ul>"
                 + "".join(f"<li>{esc(c)}</li>" for c in changes) + "</ul></section>"
                 if changes else "")

    hero_badge = f'{_icon("star", 15)} {esc(rating_text)} · {esc(category)}'
    hero_visual = f'''<div class="hero-visual" aria-hidden="true">
  <div class="hero-visual-ring"></div>
  <div class="hero-visual-icon">{_icon(prof["icon"], 46)}</div>
</div>''' if variant != "centered-gradient" else ""

    hero_html = f'''<section class="hero hero--{variant}"><div class="wrap hero-inner">
  <div class="hero-copy">
    <p class="badge">{hero_badge}</p>
    <h1>{esc(name)}<br><span class="accent">{esc(prof['hero_kicker'])}</span></h1>
    <p class="tagline">{esc(prof['hero_sub'])}</p>
    <div class="cta-row">
      {('<a class="btn btn-primary" href="' + esc(tel) + '">' + _icon("phone", 16) + ' Call ' + esc(phone) + '</a>') if phone else ''}
      <a class="btn btn-secondary" href="#contact">Get a Free Quote</a>
      <a class="btn btn-ghost" href="#services">Explore Services</a>
    </div>
    <div class="hero-meta">
      <div>{_icon("pin", 15)} {esc(address)}</div>
      <div>{_icon("check", 15)} Free quotes · No-pressure advice</div>
    </div>
  </div>
  {hero_visual}
</div></section>'''

    index = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="{esc(name)} — {esc(category)} in {city}. {esc(prof['hero_sub'])} Call {esc(phone) or 'today'} for a free quote.">
{'<meta name="robots" content="noindex, nofollow">' if preview else ''}
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{font_url}">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect width='100' height='100' rx='22' fill='{brand}'/><text x='50' y='66' font-size='40' text-anchor='middle' fill='white' font-family='Arial' font-weight='bold'>{esc(first_letters)}</text></svg>">
<title>{esc(name)} | {esc(category)} — {city}</title>
<link rel="stylesheet" href="styles.css">
</head>
<body id="top">
<div class="topbar"><div class="wrap topbar-inner">
<span>{_icon("pin", 14)} {esc(address)}</span>
<span class="hide-mobile">{_icon("clock", 14)} Mon–Fri 8am–6pm · Sat 9am–3pm</span>
{('<a class="topbar-phone" href="' + esc(tel) + '">' + _icon("phone", 14) + ' ' + esc(phone) + '</a>') if phone else ''}
</div></div>
<header class="site-header" id="siteHeader">
<nav class="wrap" aria-label="Main navigation">
<a class="brand" href="#top"><span class="brand-badge" aria-hidden="true">{_icon(prof["icon"], 18)}</span> {esc(name)}</a>
<button class="nav-toggle" aria-label="Toggle menu" aria-expanded="false" aria-controls="navLinks">
  <span></span><span></span><span></span>
</button>
<ul class="nav-links" id="navLinks">
<li><a href="#services">Services</a></li>
<li><a href="#about">Why Us</a></li>
<li><a href="#reviews">Reviews</a></li>
<li><a href="#visit">Visit</a></li>
<li><a href="#contact">Contact</a></li>
</ul>
{('<a class="btn btn-primary btn-call" href="' + esc(tel) + '">' + _icon("phone", 15) + ' Call Now</a>') if phone else '<a class="btn btn-primary btn-call" href="#contact">Get a Quote</a>'}
</nav>
</header>
<main>
{hero_html}
<div class="marquee" aria-hidden="true"><div class="marquee-track">
{("&nbsp;•&nbsp; " + esc(name) + " &nbsp;•&nbsp; " + esc(category) + " ") * 6}
</div></div>
<section class="strip" aria-label="Why choose us"><div class="wrap strip-grid">{strip_html}</div></section>
<section id="services"><div class="wrap">
<p class="eyebrow">What we offer</p>
<h2>What we do</h2>
<p class="section-sub">Every job quoted up front at {esc(name)} — you approve before we start.</p>
<ul class="cards">{services_html}</ul>
</div></section>
<section id="about"><div class="wrap about-grid">
<div>
<p class="eyebrow">Why us</p>
<h2>Why neighbors pick {esc(name)}</h2>
<p>{esc(category)} done right, close to home at {esc(address)}. {esc(prof['hero_sub'])}</p>
<ul class="checklist">
<li>{_icon("check", 16)} Up-front pricing — approve before we start</li>
<li>{_icon("check", 16)} Work backed in writing</li>
<li>{_icon("check", 16)} Fast scheduling, most jobs within days</li>
<li>{_icon("check", 16)} You talk to the people doing the work</li>
</ul>
</div>
<div class="about-card">
<span class="about-card-icon" aria-hidden="true">{_icon(prof["icon"], 30)}</span>
<h3>Visit us today</h3>
<p class="big">{esc(rating_text)}</p>
<p>{esc(address)}<br>{esc(phone)}</p>
{('<a class="btn btn-primary" href="' + esc(tel) + '">Call to Book</a>') if phone else ''}
<a class="btn btn-secondary" href="{esc(maps_url)}" target="_blank" rel="noopener">Get Directions</a>
</div>
</div></section>
<section id="reviews"><div class="wrap narrow">
<p class="eyebrow">Reviews</p>
<h2>What neighbors say</h2>
<p class="rating">{esc(rating_text)}</p>
<div class="review-slider">{reviews_html}
<div class="slider-dots" role="tablist" aria-label="Reviews">{dots_html}</div>
</div>
</div></section>
{rev_block}
<section class="cta-banner"><div class="wrap cta-banner-inner">
<h2>Ready to get started?</h2>
<p>Reach out to {esc(name)} today — free quotes, fast answers, no pressure.</p>
<div class="cta-row">
{('<a class="btn btn-primary" href="' + esc(tel) + '">' + _icon("phone", 16) + ' Call ' + esc(phone) + '</a>') if phone else ''}
<a class="btn btn-secondary" href="#contact">Request a Callback</a>
</div>
</div></section>
<section id="visit"><div class="wrap visit-grid">
<div>
<p class="eyebrow">Visit</p>
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
body{{margin:0;font-family:var(--font-body);color:var(--ink);background:var(--bg);line-height:1.65}}
h1,h2,h3{{font-family:var(--font-display);line-height:1.1;margin:0 0 .5rem;letter-spacing:-.01em}}
a{{color:var(--brand)}}
.wrap{{max-width:1120px;margin:auto;padding-left:1.1rem;padding-right:1.1rem}}
.eyebrow{{text-transform:uppercase;letter-spacing:.14em;font-size:.78rem;font-weight:800;color:var(--brand2);margin:0 0 .35rem}}
.topbar{{background:var(--dark);color:#f3ece2;font-size:.85rem}}
.topbar-inner{{display:flex;gap:1rem;align-items:center;justify-content:space-between;padding-top:.4rem;padding-bottom:.4rem}}
.topbar-inner span,.topbar-phone{{display:inline-flex;align-items:center;gap:.4rem}}
.topbar-phone{{color:var(--gold);font-weight:800;text-decoration:none}}
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
.marquee{{background:var(--dark);color:var(--gold);overflow:hidden;white-space:nowrap;padding:.55rem 0;font-weight:800;letter-spacing:.06em;font-size:.85rem}}
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
.review{{display:none;background:#fff;border:1px solid var(--line);border-radius:var(--radius);padding:1.6rem;box-shadow:0 8px 24px rgba(0,0,0,.06)}}
.review.active{{display:block}}
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
footer{{background:var(--dark);color:#cbb9ab;text-align:center;padding:2.2rem 0 2.6rem;margin-top:1rem}}
footer a{{color:var(--gold)}}
footer .fine{{font-size:.85rem;opacity:.85}}
footer strong{{color:#fff}}
.reveal{{opacity:0;transform:translateY(16px);transition:opacity .5s ease,transform .5s ease}}
.reveal.visible{{opacity:1;transform:none}}
.preview-banner{{position:fixed;left:0;right:0;bottom:0;z-index:50;background:var(--dark);color:var(--gold);text-align:center;font-size:.82rem;font-weight:700;padding:.55rem .8rem;border-top:2px solid var(--gold);display:flex;gap:.4rem;align-items:center;justify-content:center}}
{('body{padding-bottom:2.4rem}') if preview else ''}
{variant_css}
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
if(f){f.addEventListener('submit',function(e){
  e.preventDefault();
  var n=f.name.value.trim(),p=f.phone.value.trim(),m=f.message.value.trim(),note=f.querySelector('.form-note');
  if(!n||!p||!m){note.textContent='Please fill in your name, phone, and message.';return}
  if(p.replace(/\\D/g,'').length<7){note.textContent='That phone number looks too short — please double-check.';return}
  if(PREVIEW){note.textContent='Thanks '+n.split(' ')[0]+'! (Design preview — this form goes live when the site launches.)';return}
  note.textContent='Thanks '+n.split(' ')[0]+'! We will call you back shortly.';f.reset()
})}
var dots=Array.prototype.slice.call(document.querySelectorAll('.dot')),
    reviews=Array.prototype.slice.call(document.querySelectorAll('.review')),cur=0;
function show(i){if(!reviews.length)return;cur=(i+reviews.length)%reviews.length;
  reviews.forEach(function(r,j){r.classList.toggle('active',j===cur)});
  dots.forEach(function(d,j){d.classList.toggle('active',j===cur)})}
dots.forEach(function(d,i){d.addEventListener('click',function(){show(i)})});
if(reviews.length>1){setInterval(function(){show(cur+1)},6000)}
var y=document.getElementById('year');if(y){y.textContent=new Date().getFullYear()}
var prefersReduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
if('IntersectionObserver' in window && !prefersReduced){
  var obs=new IntersectionObserver(function(entries){
    entries.forEach(function(en){if(en.isIntersecting){en.target.classList.add('visible');obs.unobserve(en.target)}})
  },{threshold:.12});
  document.querySelectorAll('.card, .strip-item, .about-card, .review').forEach(function(el){
    el.classList.add('reveal');obs.observe(el)
  });
} else {
  document.querySelectorAll('.card, .strip-item, .about-card, .review').forEach(function(el){el.classList.add('reveal','visible')});
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
        return {"lead_id": lid, "dir": str(target), "skipped": True, "engine": engine}

    target.mkdir(parents=True, exist_ok=True)
    prompt = build_prompt(lead, feedback, preview=preview)
    if last_qa.get("passed") is False:
        prompt += "\nPREVIOUS QA FAILED — fix these issues: " + json.dumps(
            last_qa.get("issues", []), ensure_ascii=False)

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
        except RuntimeError as e:
            if require_opencode:
                raise
            print(f"[website_generator] OpenCode failed ({e}); "
                  f"falling back to the built-in template engine.", flush=True)
            engine = "template"
            files = render_template(lead, feedback, preview=preview)
            for name, content in files.items():
                (target / name).write_text(content, encoding="utf-8")
        else:
            if preview:
                apply_preview_lock(target, lead.get("name") or "this business")
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
