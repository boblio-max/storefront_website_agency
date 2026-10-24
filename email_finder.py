"""email_finder.py (Bot 2.5) — Turn phone-only leads into outreach-ready ones.

Google Maps never lists emails, so without this bot ~90% of leads can't
be emailed. This bot scrapes each business's own website (homepage +
/contact) for real published addresses. Only genuinely published
addresses are kept — nothing is guessed or fabricated.

Verification (stdlib only, never raises):
  syntax check -> junk filter -> domain resolves (socket.getaddrinfo).

Usage:
    python email_finder.py --input target_leads.json --output target_leads.json
    python email_finder.py --input businesses.json --output businesses.json --max 30

Orchestrator use: main(input=..., output=...) -> output path or int code.
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import sys
import urllib.parse
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) StorefrontBot/1.0"}
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
OBFUSCATED_RE = re.compile(
    r"([A-Za-z0-9._%+-]+)\s*(?:@|\[at\]|\(at\))\s*([A-Za-z0-9.-]+)\s*(?:\.|dot|\[dot\]|\(dot\))\s*([A-Za-z]{2,})",
    re.I)
JUNK_SUBSTR = ("example.", "placeholder", "sentry", "wixpress", "godaddy",
               "schema.", ".png", ".jpg", ".svg", "noreply", "no-reply",
               "test@test", "youremail", "your-email", "email@example")
JUNK_LOCAL = {"info@example", "test@", "webmaster@", "postmaster@"}


def _candidate_pages(website: str) -> list[str]:
    base = website.strip()
    if not base.startswith(("http://", "https://")):
        base = "https://" + base
    pages = [base]
    for slug in ("/contact", "/contact-us", "/about", "/about-us"):
        pages.append(base.rstrip("/") + slug)
    # Facebook-only leads have no scrapeable site — skip fast.
    if "facebook.com" in base.lower():
        return []
    return pages


def _extract(html: str) -> list[str]:
    found = EMAIL_RE.findall(html or "")
    found += [f"{a}@{b}.{c}" for a, b, c in OBFUSCATED_RE.findall(html or "")]
    seen, out = set(), []
    for e in found:
        e = e.strip().lower().rstrip(".,;)")
        low = e
        if "@" not in e or len(e) > 80:
            continue
        if any(j in low for j in JUNK_SUBSTR):
            continue
        if any(low.startswith(j) for j in JUNK_LOCAL):
            continue
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out


def _domain_ok(email: str) -> bool:
    try:
        domain = email.split("@", 1)[1]
        socket.getaddrinfo(domain, 80, type=socket.SOCK_STREAM)
        return True
    except (socket.gaierror, OSError, IndexError):
        return False


def find_emails(website: str, timeout: int = 12) -> list[str]:
    """Scrape a business site for published emails. Never raises."""
    emails: list[str] = []
    try:
        for page in _candidate_pages(website)[:5]:
            try:
                r = requests.get(page, headers=UA, timeout=timeout)
                if r.status_code != 200 or "text/html" not in (r.headers.get("Content-Type") or ""):
                    continue
                for e in _extract(r.text[:400_000]):
                    if e not in emails:
                        emails.append(e)
                    if len(emails) >= 5:
                        break
            except Exception:  # noqa: BLE001
                continue
            if emails:
                break
    except Exception:  # noqa: BLE001
        return []
    return [e for e in emails if _domain_ok(e)][:3]


def enrich_leads(leads: list[dict], max_n: int = 0) -> tuple[list[dict], int]:
    """Add verified emails in place. Returns (leads, newly_found)."""
    found = 0
    scanned = 0
    for b in leads:
        if not isinstance(b, dict):
            continue
        if (b.get("email") or "").strip():
            continue
        site = (b.get("website") or "").strip()
        if not site:
            continue
        if max_n and scanned >= max_n:
            break
        scanned += 1
        emails = find_emails(site)
        if emails:
            b["email"] = emails[0]
            b["email_verified"] = True
            b["email_source"] = "website"
            b["email_alternates"] = emails[1:]
            found += 1
    return leads, found


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Bot 2.5: website email discovery (verified only)")
    p.add_argument("--input", default="target_leads.json")
    p.add_argument("--output", default=None)
    p.add_argument("--max", type=int, default=0, help="Max sites to scan (0 = all)")
    return p.parse_args(argv)


def main(argv=None, **kwargs) -> str | int:
    if argv is None:
        argv = []
    args = parse_args(argv)
    for k, v in kwargs.items():
        if not hasattr(args, k):
            raise TypeError(f"email_finder.main() got an unexpected option {k!r}")
        setattr(args, k, v)
    src = Path(args.input)
    if not src.exists():
        print(f"[email_finder] ERROR: {src} not found", file=sys.stderr)
        return 2
    try:
        raw = json.loads(src.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"[email_finder] ERROR: {e}", file=sys.stderr)
        return 2
    leads = raw if isinstance(raw, list) else raw.get("leads", raw.get("businesses", []))
    if not isinstance(leads, list):
        print("[email_finder] ERROR: input must be a JSON list", file=sys.stderr)
        return 2
    _, found = enrich_leads(leads, max_n=args.max)
    out = Path(args.output) if args.output else src
    try:
        from agency_config import atomic_write_json
        atomic_write_json(out, leads)
    except ImportError:
        out.write_text(json.dumps(leads, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[email_finder] verified emails found for {found} lead(s) -> {out}", flush=True)
    return str(out)


if __name__ == "__main__":
    _rc = main(sys.argv[1:])
    raise SystemExit(_rc if isinstance(_rc, int) else 0)
