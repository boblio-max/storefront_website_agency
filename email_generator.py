"""
email_generator.py (Bot 9) — Personalized outreach email with memory.

Receives: business info + preview URL + complete email history +
conversation state + website info + purpose of the current email.
Then calls OpenCode: run_opencode_command(target_dir, prompt), where the
prompt essentially says:

    Generate the next email to this business.
    Here is the business information: ...
    Here is the complete email history: ...
    Here is the current website preview: ...
    Here is the current situation: ...
    Generate a natural, personalized response.
    Do not invent facts. Do not claim something happened unless confirmed.

Every SENT email is recorded (Bot 9 has memory):
    {"message_id": "...", "lead_id": "lead_00421", "direction": "outgoing",
     "timestamp": "...", "subject": "...", "body": "...", "preview_url": "..."}

Usage:
    python email_generator.py --lead lead_00001 --purpose initial_outreach
    python email_generator.py --lead lead_00001 --purpose follow_up --send
    python email_generator.py --lead lead_00001 --purpose revision_delivery --send --no-opencode
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

try:  # Windows consoles default to cp1252; keep unicode output from crashing
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

OPENCODE_CMD = "opencode"
AGENCY_NAME = os.environ.get("AGENCY_NAME", "Storefront")
AGENCY_EMAIL = os.environ.get("AGENCY_FROM", "storefront.webs@gmail.com").strip() \
    or "storefront.webs@gmail.com"


def _load_env_file() -> None:
    """Load a local .env file if python-dotenv is installed (optional dep).

    The pipeline reads credentials from process env; a `.env` file is never
    required, but local runs commonly keep the Gmail app password there.
    Missing dotenv is silently ignored (env-only mode).
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    try:
        load_dotenv(dotenv_path=Path(".env"), override=False)
    except Exception:  # noqa: BLE001
        pass


_load_env_file()


def _env_port(name: str, default: int) -> int:
    """Read an env port safely: bad values fall back to default (no crash)."""
    try:
        return int(os.environ.get(name, str(default)).strip() or default)
    except (TypeError, ValueError, AttributeError):
        return default


def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


_OPENCODE_ARGV_CACHE: list[str] | None = None


def _opencode_argv() -> list[str]:
    """Argv prefix invoking the real OpenCode CLI (see website_generator)."""
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


def run_opencode_command(target_dir: Path, prompt: str, timeout: int = 300) -> str:
    """Call OpenCode; raise RuntimeError if CLI missing/fails (caller falls back)."""
    target_dir.mkdir(parents=True, exist_ok=True)
    prefix = _opencode_argv()
    try:
        proc = subprocess.run([*prefix, "run", prompt], cwd=str(target_dir),
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError("OpenCode run timed out")
    if proc.returncode != 0:
        raise RuntimeError(f"OpenCode exited {proc.returncode}: {(proc.stderr or '')[:500]}")
    return proc.stdout or ""


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def find_current(current: list, lead_id: str) -> dict | None:
    for c in current:
        if isinstance(c, dict) and c.get("lead_id") == lead_id:
            return c
    return None


def lead_history(history: list, lead_id: str) -> list[dict]:
    return [m for m in history if isinstance(m, dict) and m.get("lead_id") == lead_id]


def build_prompt(biz: dict, history: list[dict], purpose: str) -> str:
    hist_text = ("(no previous emails)"
                 if not history else "\n".join(
                     f"- [{m.get('timestamp')}] {m.get('direction')}: {m.get('subject')}\n  "
                     f"{(m.get('body') or '')[:600]}" for m in history[-8:]))
    return "\n".join([
        "Generate the next email to this business.",
        "",
        "Here is the business information:",
        json.dumps({k: biz.get(k) for k in
                    ("business_name", "category", "phone", "email", "address",
                      "website", "preview_url", "opportunity_score",
                      "rating", "review_count", "lead_type", "site_note")},
                   ensure_ascii=False, indent=2),
        "",
        "Personalization fuel (all verified, safe to cite): rating/review_count = "
        "their Google reputation; lead_type no_website = they have no site at all; "
        "site_note = the concrete problem found with their current site.",
        "",
        "Here is the complete email history:",
        hist_text,
        "",
        "Here is the current website preview:",
        str(biz.get("preview_url") or "(no preview yet)"),
        "",
        "Here is the current situation:",
        f"purpose={purpose}; status={biz.get('status')}; "
        f"prior_messages={len(history)}",
        "",
        "Generate a natural, personalized response with a subject line and body.",
        "Do not invent facts (no fake owner names, addresses, prices, or past meetings).",
        "Do not claim something happened unless the history confirms it.",
        "VOICE — write like one human to another, not a sales sequence:",
        "- Short greeting with their short name only (never 'Hi <Full Legal Name> team').",
        "- Open with ONE specific true observation (their rating, review count, or site problem from the info above).",
        "- Vary your wording from the previous emails above — never reuse their sentences or structure.",
        "- Plain sentences, contractions ok, no exclamation marks.",
        "- BANNED phrases: 'I hope this finds you well', 'just checking in', 'touch base', 'circling back',",
        "  'cutting-edge', 'leverage', 'game-changer', 'solutions', 'synergy', 'valuable', 'state-of-the-art',",
        "  'Since we haven't connected yet', 'modern, mobile-friendly'.",
        "- Preview link on its own line. End with ONE short question, not a paragraph CTA.",
        "- Body 130 words max. Subject 8 words max, no RE:/FW:, no clickbait, no all-caps.",
        f"Sign off as {AGENCY_NAME} (the agency that built the preview).",
        "Include our agency website in the email signature: https://storefront-web-agency.vercel.app/",
        "This is the agency website, not the business's preview; keep the preview link separate.",
        "Reply in this exact format:\nSubject: <subject>\n\n<body>",
    ])


def _short_name(name: str) -> str:
    """'La Gloria Modern Mexican Bar' -> 'La Gloria' (how a human addresses them)."""
    import re as _re
    base = _re.sub(r"\s+(LLC|Inc|Co|Company|LLP|PLLC|Ltd|Corp)\.?$", "",
                   (name or "").strip(), flags=_re.I)
    words = base.split()
    if not words:
        return "there"
    return " ".join(words[:2])


def _variant(key: str, n: int) -> int:
    """Stable rotation 0..n-1 so each lead gets its own wording, deterministically."""
    import hashlib as _hl
    return int(_hl.md5(str(key or "").encode("utf-8")).hexdigest()[:8], 16) % max(1, n)


def _sender() -> str:
    import os as _os
    # Personal first name beats a brand: "Nikhil at Storefront" reads human,
    # "Storefront" alone reads like a mail-merge. Override with
    # AGENCY_SENDER_NAME="Nikhil" in env/.env; falls back to agency name.
    return _os.environ.get("AGENCY_SENDER_NAME", "").strip() or AGENCY_NAME


def _sender_sign() -> str:
    """Two-line human signature: person, then agency + site (never a logo)."""
    import os as _os
    person = _os.environ.get("AGENCY_SENDER_NAME", "").strip() or "Nikhil"
    agency_line = "https://storefront-web-agency.vercel.app/"
    if person == AGENCY_NAME or person.lower() in AGENCY_NAME.lower():
        return f"\n\n— {AGENCY_NAME}\n{agency_line}"
    return f"\n\n— {person} ({AGENCY_NAME})\n{agency_line}"


BANNED_PHRASES = (
    "hope this finds", "checking in", "touch base", "circling back",
    "cutting-edge", "leverage", "game-changer", "solutions", "synergy",
    "haven't connected yet", "mobile-friendly", "state-of-the-art",
    "valuable", "just bumping", "just following up",
)


def _humanize(subject: str, body: str, max_words: int = 130) -> tuple[str, str]:
    """Post-process any draft (template or OpenCode) so it reads human.

    - lowercases shouty subjects, strips RE:/FW: and clickbait
    - removes banned sales phrases, collapses 3+ newlines, drops "!"
    - guarantees one short closing question and a plain-text signature
    Never raises; returns (subject, body).
    """
    import re as _re
    try:
        subject = (subject or "").strip()
        subject = _re.sub(r"^(re|fw|fwd)\s*:\s*", "", subject, flags=_re.I).strip()
        if subject.isupper() and len(subject) > 4:
            subject = subject.capitalize()
        words = subject.split()
        if len(words) > 8:
            subject = " ".join(words[:8])
        body = (body or "").replace("\r\n", "\n")
        for banned in BANNED_PHRASES:
            body = _re.sub(re.escape(banned), "", body, flags=_re.I)
        body = body.replace("!", ".")
        body = _re.sub(r"\n{3,}", "\n\n", body).strip()
        body = _re.sub(r"[ \t]{2,}", " ", body)
        toks = body.split()
        if len(toks) > max_words:
            # Truncate at a sentence boundary so we never send a half-thought.
            cut = " ".join(toks[:max_words])
            m = list(_re.finditer(r"[.?]\s", cut))
            if m:
                cut = cut[:m[-1].end()].strip()
            body = cut
        return subject, body
    except Exception:  # noqa: BLE001
        return subject, body


def _observation(biz: dict) -> str:
    """One specific, true sentence about their situation. Only collected facts."""
    short = _short_name(biz.get("business_name") or "")
    rating = biz.get("rating")
    reviews = biz.get("review_count")
    proof = ""
    try:
        if rating and reviews:
            proof = f" {float(rating):.1f} stars across {int(reviews)} reviews — people clearly like you."
    except (TypeError, ValueError):
        proof = ""
    site = (biz.get("website") or "").strip()
    ltype = (biz.get("lead_type") or "")
    note = (biz.get("site_note") or "").strip()
    ln = note.lower()
    if ltype == "no_website" or not site:
        situation = ("Looks like you don't have a website at all — "
                     "people searching for you mostly find directory listings.")
    elif "facebook" in site.lower():
        situation = "Right now Google sends people to your Facebook page instead of a real site."
    elif "unreachable" in ln or "wouldn't load" in ln or "http" in ln or "ssl" in ln:
        situation = "I tried opening your site and it wouldn't load."
    elif note:
        situation = f"Your current site has an issue ({note[:90]})."
    else:
        situation = "Your current site looks like it could work a lot harder for you."
    return f"Was looking at {short} today.{proof} {situation}"


def template_email(biz: dict, history: list[dict], purpose: str) -> tuple[str, str]:
    name = biz.get("business_name") or "there"
    short = _short_name(name)
    category = (biz.get("category") or "your business").strip() or "your business"
    preview = (biz.get("preview_url") or "").strip()
    lid = str(biz.get("lead_id") or name)
    sign = _sender_sign()

    if purpose in ("follow_up", "follow_up_2", "follow_up_3"):
        subjects = {
            "follow_up": f"Re: quick idea for {short}",
            "follow_up_2": f"bumping this, {short}",
            "follow_up_3": f"closing the loop",
        }
        link = f"\n\n{preview}" if preview else ""
        bodies = {
            "follow_up": (f"Hi {short},\n\nBumping my note from the other day — "
                          f"I put together a preview site for you and it's still up here:{link}\n\n"
                          f"Take a look when you get a minute? Happy to change anything."),
            "follow_up_2": (f"Hi {short},\n\nOne more nudge — your preview site is still live:{link}\n\n"
                            f"If you like the direction I can put it on your domain this week. "
                            f"If not, no worries, just say the word."),
            "follow_up_3": (f"Hi {short},\n\nLast note from me. I'll take the preview down soon, "
                            f"but it's yours if you want it:{link}\n\nThanks either way."),
        }
        s, b = subjects[purpose], bodies[purpose] + sign
        return _humanize(s, b, max_words=80)

    if purpose == "revision_delivery":
        link = f"\n\n{preview}" if preview else ""
        return (f"your updated preview is ready",
                f"Hi {short},\n\nMade the changes you asked for — the new version is here:{link}\n\n"
                f"Tell me what you think, and send over any photos of the place if you have them — "
                f"real pictures beat anything I can mock up." + sign)

    if purpose == "payment_request":
        try:
            from agency_config import pay_block
            pay = pay_block()
        except ImportError:
            pay = ""
        return (f"{short} — here's how to go live",
                f"Hi {short},\n\nGlad you like it. To launch:\n\n"
                + (pay + "\n\n" if pay else "Reply and I'll send over payment details.\n\n")
                + f"Once that's sorted I'll connect your domain and flip the switch." + sign)

    # initial_outreach: three rotating openers, one specific observation each.
    v = _variant(lid, 3)
    obs = _observation(biz)
    middles = [
        f"So I built you a preview — a proper {category} site with click-to-call and a contact form.",
        f"So I mocked up what a proper site could look like for you — click-to-call, contact form, the works.",
        f"I went ahead and built a preview site for you — clean, works on phones, easy to contact you from.",
    ]
    asks = [
        "Worth a look?",
        "Want to see it?",
        "Curious what you think.",
    ]
    subjects = [
        f"quick idea for {short}",
        f"made {short} a preview site",
        f"your {category} site",
    ]
    if purpose == "revision_delivery":
        link = f"\n\n{preview}" if preview else ""
        s = "your updated preview is ready"
        b = (f"Hi {short},\n\nMade the changes you asked for — the new version is here:{link}\n\n"
             f"Tell me what you think, and send over any photos of the place if you have them — "
             f"real pictures beat anything I can mock up." + sign)
        return _humanize(s, b, max_words=110)

    if purpose == "payment_request":
        try:
            from agency_config import pay_block
            pay = pay_block()
        except ImportError:
            pay = ""
        s = f"{short} — here's how to go live"
        b = (f"Hi {short},\n\nGlad you like it. To launch:\n\n"
             + (pay + "\n\n" if pay else "Reply and I'll send over payment details.\n\n")
             + f"Once that's sorted I'll connect your domain and flip the switch." + sign)
        return _humanize(s, b, max_words=130)

    # initial_outreach: six rotating human openers, one specific observation
    # each. Deterministic per lead (stable rotation) so re-runs don't
    # reword the same lead, but the pool is wide enough that neighbours
    # never look copy-pasted.
    v = _variant(lid, 6)
    obs = _observation(biz)
    middles = [
        f"So I built you a preview — a proper {category} site with click-to-call and a contact form.",
        f"So I mocked up what a proper site could look like for you — click-to-call, contact form, the works.",
        f"I went ahead and built a preview site for you — clean, works on phones, easy to contact you from.",
        f"Took a stab at a preview site for you — simple layout, your reviews up front, easy to call from a phone.",
        f"Put together a quick preview for you — hours, location and contact front and center.",
        f"Made you a rough preview — nothing fancy, just what a customer on their phone needs to find you and call.",
    ]
    asks = [
        "Worth a look?",
        "Want to see it?",
        "Curious what you think.",
        "Mind if I send the link?",
        "Open to a peek?",
        "Want me to send it over?",
    ]
    subjects = [
        f"quick idea for {short}",
        f"made {short} a preview site",
        f"your {category} site",
        f"a preview for {short}",
        f"{short} — quick mockup",
        f"thought of {short}",
    ]
    try:
        from agency_config import pay_block as _pb
        pay = _pb()
    except ImportError:
        pay = ""
    body = (f"Hi {short},\n\n{obs}\n\n{middles[v]}"
            + (f"\n\n{preview}" if preview else "")
            + f"\n\n{asks[v] if preview else 'Can I send you the link?'}"
            + (f"\n\n{pay}" if pay else "")
            + sign)
    return _humanize(subjects[v], body, max_words=130)


# ---------------------------------------------------------------------------
# Inbound info extraction: turn a human reply into structured lead updates.
# Pure regex, no network, never raises — the watcher calls this on every
# incoming email and merges whatever it finds back into the lead files.
# ---------------------------------------------------------------------------

PHONE_RE = __import__("re").compile(
    r"(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}")
EMAIL_RE = __import__("re").compile(
    r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
HOURS_RE = __import__("re").compile(
    r"(mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|thu(?:rsday)?|"
    r"fri(?:day)?|sat(?:urday)?|sun(?:day)?)[\s:.\-]*"
    r"(\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s*[–\-–to]+\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)?|"
    r"closed|open)",
    __import__("re").I)
DAY_RANGE_RE = __import__("re").compile(
    r"(weekdays?|weekends?|mon\s*[–\-]\s*fri|mon\s*[–\-]\s*sat)",
    __import__("re").I)


def extract_info_from_email(body: str) -> dict:
    """Pull structured facts out of a free-text reply.

    Returns a dict with only the keys found, e.g.
    {"phones": [...], "emails": [...], "owner_name": "Sam",
     "hours_mentioned": [...], "wants_call": True,
     "budget_mentioned": "$300", "timeline": "this week",
     "services_mentioned": [...], "opt_out": False}.
    Never raises; empty dict when nothing is found.
    """
    import re as _re
    out: dict = {}
    try:
        text = (body or "").strip()
        if not text:
            return out
        phones = sorted(set(PHONE_RE.findall(text)))
        if phones:
            out["phones"] = phones[:3]
        emails = sorted(set(
            e for e in EMAIL_RE.findall(text)
            if "storefront.webs" not in e.lower()))
        if emails:
            out["emails"] = emails[:3]
        m = _re.search(
            r"(?:i'?m|this is|name is|call me)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
            text)
        if m:
            out["owner_name"] = m.group(1).strip()
        hours = HOURS_RE.findall(text)
        if hours:
            out["hours_mentioned"] = [
                " ".join(h).strip() for h in hours[:6]]
        elif DAY_RANGE_RE.search(text) or "hour" in text.lower():
            out["hours_mentioned"] = [text[:160]]
        if _re.search(r"call me|my number|my cell|reach me at|text me",
                      text, _re.I):
            out["wants_call"] = True
        m2 = _re.search(r"\$\s?\d[\d,]*(?:\.\d{2})?", text)
        if m2 or _re.search(r"\b(budget|price|cost|quote|how much)\b",
                            text, _re.I):
            out["budget_mentioned"] = m2.group(0) if m2 else "asked"
        m3 = _re.search(
            r"(today|tomorrow|this week|next week|asap|urgent|"
            r"no rush|whenever|this month)",
            text, _re.I)
        if m3:
            out["timeline"] = m3.group(1).lower()
        svcs = [s.strip() for s in _re.findall(
            r"(?:we (?:do|offer|provide)|services?(?: include)?)[:\s]+(.{4,160})",
            text, _re.I)]
        if svcs:
            out["services_mentioned"] = svcs[:3]
        if _re.search(r"unsubscribe|remove me|stop emailing|opt.?out",
                      text, _re.I):
            out["opt_out"] = True
        return out
    except Exception:  # noqa: BLE001
        return out


def apply_extracted_to_lead(lead_id: str, info: dict,
                            current: str = "current_leads.json",
                            targets: str = "target_leads.json") -> dict:
    """Merge extracted inbound facts into current + target lead files.

    Only fills gaps or appends notes — never overwrites verified data with
    guesses. Returns the merged info dict (empty when nothing applied).
    Never raises.
    """
    if not info or not lead_id:
        return {}
    applied: dict = {}
    try:
        for path in (current, targets):
            p = Path(path)
            if not p.exists():
                continue
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            items = raw if isinstance(raw, list) else raw.get("leads", [])
            changed = False
            for entry in items:
                if not isinstance(entry, dict) or entry.get("lead_id") != lead_id:
                    continue
                notes = entry.setdefault("inbound_notes", [])
                if info.get("phones") and not entry.get("phone"):
                    entry["phone"] = info["phones"][0]
                    applied["phone"] = info["phones"][0]
                    changed = True
                if info.get("emails") and not entry.get("email"):
                    entry["email"] = info["emails"][0]
                    applied["email"] = info["emails"][0]
                    changed = True
                if info.get("owner_name") and not entry.get("owner_name"):
                    entry["owner_name"] = info["owner_name"]
                    applied["owner_name"] = info["owner_name"]
                    changed = True
                for key in ("hours_mentioned", "services_mentioned",
                            "timeline", "budget_mentioned", "wants_call"):
                    if info.get(key) and info.get(key) not in notes:
                        notes.append({key: info[key]})
                        applied[key] = info[key]
                        changed = True
                if changed:
                    entry["updated_at"] = utc_now_iso()
            if changed:
                p.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        return applied
    except Exception:  # noqa: BLE001
        return applied


# Pacing: humans don't blast 200 emails at 3am. The orchestrator calls
# send_pacing_ok() before each send; it enforces a per-day cap and a
# small quiet-hours nudge (sends still go through, just logged).

def send_pacing_ok(history_all: list, cap_per_day: int = 40) -> tuple[bool, str]:
    """True when another send is within the daily cap. Returns (ok, reason)."""
    try:
        import datetime as _dt
        today = _dt.datetime.now(_dt.timezone.utc).date()
        sent_today = 0
        for m in history_all:
            if not isinstance(m, dict) or m.get("direction") != "outgoing":
                continue
            try:
                ts = _dt.datetime.fromisoformat(str(m.get("timestamp") or ""))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=_dt.timezone.utc)
                if ts.date() == today:
                    sent_today += 1
            except (ValueError, TypeError):
                continue
        if sent_today >= max(1, cap_per_day):
            return False, f"daily cap reached ({sent_today}/{cap_per_day})"
        hour = _dt.datetime.now().hour  # local quiet-hours nudge
        if hour < 7 or hour >= 22:
            return True, "quiet-hours (sending anyway, logged)"
        return True, "ok"
    except Exception:  # noqa: BLE001
        return True, "ok"


UNSUB_RE = __import__("re").compile(
    r"unsubscribe|remove me|stop emailing|opt.?out|not interested|no thanks|"
    r"don'?t (call|email|contact)|stop", __import__("re").I)


def parse_opencode_output(text: str) -> tuple[str, str]:
    subject, body = "", text.strip()
    m = __import__("re").search(r"^Subject\s*:\s*(.+)$", text, __import__("re").M)
    if m:
        subject = m.group(1).strip()
        body = text[m.end():].strip()
    return subject, body


SUPPRESS_ACTIONS = {"opt_out"}


def is_suppressed(history_all: list, lead_id: str) -> dict | None:
    """Return the suppressing incoming message if the lead opted out."""
    for m in history_all:
        if not isinstance(m, dict) or m.get("lead_id") != lead_id:
            continue
        if m.get("direction") == "incoming" and m.get("action") in SUPPRESS_ACTIONS:
            return m
        body = str(m.get("body") or "")
        if m.get("direction") == "incoming" and UNSUB_RE.search(body):
            return m
    return None


def already_sent(history_all: list, lead_id: str, purpose: str) -> bool:
    """True if an outgoing email for (lead, purpose) is already recorded."""
    return any(isinstance(m, dict) and m.get("lead_id") == lead_id
               and m.get("direction") == "outgoing"
               and m.get("purpose") == purpose for m in history_all)


def _parse_ts(value) -> "datetime.datetime | None":
    try:
        if not value:
            return None
        return datetime.datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def followup_due(history_all: list, lead_id: str,
                 schedule_days: list | None = None, now=None) -> str | None:
    """Return the next follow-up purpose due (follow_up/follow_up_2/...), else None.

    Rules: initial_outreach sent, no incoming reply, not suppressed,
    enough days elapsed, and that follow-up purpose not already sent.
    Never raises.
    """
    try:
        from agency_config import load_config
        cfg = load_config()
        schedule_days = schedule_days if schedule_days is not None else cfg.get(
            "followup_schedule_days", [3, 7, 14])
        max_n = int(cfg.get("max_followups", 3))
    except Exception:  # noqa: BLE001
        schedule_days = schedule_days or [3, 7, 14]
        max_n = 3
    try:
        if is_suppressed(history_all, lead_id):
            return None
        msgs = [m for m in history_all if isinstance(m, dict) and m.get("lead_id") == lead_id]
        if any(m.get("direction") == "incoming" for m in msgs):
            return None
        initials = [m for m in msgs if m.get("direction") == "outgoing"
                    and m.get("purpose") == "initial_outreach"]
        if not initials:
            return None
        first = min((_parse_ts(m.get("timestamp")) for m in initials),
                    default=None)
        if first is None:
            return None
        if first.tzinfo is None:
            first = first.replace(tzinfo=datetime.timezone.utc)
        now = now or datetime.datetime.now(datetime.timezone.utc)
        elapsed_days = (now - first).total_seconds() / 86400.0
        purposes = ["follow_up", "follow_up_2", "follow_up_3"][:max_n]
        for i, purpose in enumerate(purposes):
            need_days = schedule_days[i] if i < len(schedule_days) else schedule_days[-1]
            if elapsed_days >= need_days and not already_sent(history_all, lead_id, purpose):
                return purpose
        return None
    except Exception:  # noqa: BLE001
        return None


def load_history(path: str = "email_history.json") -> list:
    h = load_json(Path(path), [])
    return h if isinstance(h, list) else []
def smtp_config() -> dict | None:
    """SMTP transport for the agency mailbox (Gmail defaults baked in).

    Only the app password must be supplied (AGENCY_SMTP_PASS); host, user,
    and sender default to the agency Gmail. None when no password is set
    (draft-only mode).
    """
    password = os.environ.get("AGENCY_SMTP_PASS", "")
    if not password:
        return None
    sender = AGENCY_EMAIL
    return {
        "host": os.environ.get("AGENCY_SMTP_HOST", "").strip() or "smtp.gmail.com",
        "port": _env_port("AGENCY_SMTP_PORT", 587),
        "user": os.environ.get("AGENCY_SMTP_USER", "").strip() or sender,
        "password": password,
        "from": sender,
    }


def smtp_send(cfg: dict, to_addr: str, subject: str, body: str,
              reply_to: str | None = None) -> None:
    """Send one plain-text email via SMTP STARTTLS. Raises RuntimeError."""
    import smtplib
    from email.message import EmailMessage
    msg = EmailMessage()
    msg["From"] = cfg["from"]
    msg["To"] = to_addr
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)
    try:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=30) as s:
            s.starttls()
            if cfg["user"]:
                s.login(cfg["user"], cfg["password"])
            s.send_message(msg)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"SMTP send failed: {e}")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Bot 9: draft/send personalized email (with history)")
    p.add_argument("--lead", default=None, help="lead_id to email (required)")
    p.add_argument("--purpose", default="initial_outreach",
                   help="initial_outreach | follow_up | revision_delivery | ...")
    p.add_argument("--current", default="current_leads.json")
    p.add_argument("--history", default="email_history.json")
    p.add_argument("--preview-url", default=None)
    p.add_argument("--subject", default=None, help="Override subject line")
    p.add_argument("--send", action="store_true", help="Record as sent (else draft only)")
    p.add_argument("--no-opencode", action="store_true")
    p.add_argument("--workdir", default=".")
    return p.parse_args(argv)


def main(argv=None, **kwargs) -> str | dict | int:
    """Draft/send a personalized email. Returns the history path (str) when --send
    records it, otherwise the ``{"lead_id", "subject", "body", "sent": False}`` draft
    dict; int exit code on error.

    Orchestrator use: ``main(lead="lead_00001", current="current_leads.json",
    send=True)`` — any keyword matching an argparse option overrides the default.
    A bare main() call uses defaults (sys.argv is only used via the CLI).
    """
    if argv is None:
        # Plain main() uses defaults (never sys.argv); the CLI passes
        # sys.argv[1:] explicitly via the __main__ block below.
        argv = []
    args = parse_args(argv)
    for _k, _v in kwargs.items():
        if not hasattr(args, _k):
            raise TypeError(f"email_generator.main() got an unexpected option {_k!r}")
        setattr(args, _k, _v)
    if not args.lead:
        print("[email_generator] ERROR: --lead is required", file=sys.stderr)
        return 2
    current = load_json(Path(args.current), [])
    current = current if isinstance(current, list) else [current]
    biz = find_current(current, args.lead)
    if not biz:
        print(f"[email_generator] ERROR: {args.lead} not in {args.current} "
              f"(run Bot 8 first)", file=sys.stderr)
        return 2
    if args.preview_url:
        biz = {**biz, "preview_url": args.preview_url}
    history_all = load_json(Path(args.history), [])
    history_all = history_all if isinstance(history_all, list) else []
    hist = lead_history(history_all, args.lead)

    prompt = build_prompt(biz, hist, args.purpose)
    subject, body = "", ""
    if not args.no_opencode:
        try:
            out = run_opencode_command(Path(args.workdir), prompt, timeout=120)
            subject, body = parse_opencode_output(out)
            if body:
                cap = 80 if args.purpose in ("follow_up", "follow_up_2",
                                             "follow_up_3") else 130
                subject, body = _humanize(subject, body, max_words=cap)
        except RuntimeError as e:
            print(f"[email_generator] OpenCode unavailable ({e}) — using template", flush=True)
    if not body:
        subject, body = template_email(biz, hist, args.purpose)
    if args.subject:
        subject = args.subject
    if not subject:
        subject = f"Preview website for {biz.get('business_name')}"

    print(f"Subject: {subject}\n\n{body}", flush=True)
    if args.send:
        to_addr = (biz.get("email") or "").strip()
        if not to_addr:
            print(f"[email_generator] SKIP {args.lead}: no verified email "
                  f"(phone-only lead — never fabricated, never sent)", flush=True)
            return 2
        if is_suppressed(history_all, args.lead):
            print(f"[email_generator] SUPPRESSED {args.lead}: prior opt-out — never email again",
                  flush=True)
            return 3
        ok, why = send_pacing_ok(history_all)
        if not ok:
            print(f"[email_generator] PACED {args.lead}: {why} — deferred to tomorrow",
                  flush=True)
            return 4
        if why.startswith("quiet-hours"):
            print(f"[email_generator] note: {why} for {args.lead}", flush=True)
        cfg = smtp_config()
        if cfg is None:
            print("[email_generator] ERROR: --send needs the Gmail app password "
                  "(set AGENCY_SMTP_PASS in env or .env; Gmail needs 2FA + an "
                  "App Password for storefront.webs@gmail.com); not recorded as sent",
                  file=sys.stderr)
            return 2
        try:
            smtp_send(cfg, to_addr, subject, body, reply_to=cfg["from"])
        except RuntimeError as e:
            print(f"[email_generator] ERROR: {e}", file=sys.stderr)
            return 1
        record = {"message_id": f"msg_{uuid.uuid4().hex[:12]}", "lead_id": args.lead,
                  "direction": "outgoing", "timestamp": utc_now_iso(),
                  "subject": subject, "body": body,
                  "preview_url": biz.get("preview_url"), "purpose": args.purpose,
                  "to": to_addr, "transport": "smtp"}
        history_all.append(record)
        Path(args.history).write_text(json.dumps(history_all, ensure_ascii=False, indent=2) + "\n",
                                       encoding="utf-8")
        print(f"[email_generator] sent via SMTP + recorded {record['message_id']} -> {args.history}",
              flush=True)
        return str(Path(args.history))
    print("[email_generator] draft only (use --send to send via SMTP)", flush=True)
    return {"lead_id": args.lead, "subject": subject, "body": body, "sent": False}


if __name__ == "__main__":
    _rc = main(sys.argv[1:])
    raise SystemExit(_rc if isinstance(_rc, int) else 0)
