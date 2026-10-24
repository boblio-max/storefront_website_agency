"""
current_lead_generator.py (Bot 8) — Bridge website production → email outreach.

(Alias: current_lead_manager. This file is the canonical implementation.)

Inputs:
    target_leads.json       (Bot 4 business info + scores)
    deployment record       (Bot 7: deployments/<lead_id>.json)
    preview URL             (from the deployment record, or --preview-url)

Output: current_leads.json — e.g.
    {"lead_id": "lead_00421", "business_name": "Joe's Auto Repair",
     "category": "Auto Repair", "email": "verified@example.com",
     "phone": "...", "website": null,
     "preview_url": "https://...", "status": "ready_for_outreach", ...}

Also returns the current business's contact info (stdout / --contact-only)
for Bot 9 (email_generator.py).

CRITICAL: only uses contact info that actually exists in collected data.
Never fabricates an email address — missing email stays null.

Usage:
    python current_lead_generator.py --lead lead_00001
    python current_lead_generator.py --lead lead_00001 --deployment deployments/lead_00001.json
    python current_lead_generator.py --lead lead_00001 --preview-url https://xyz.vercel.app
    python current_lead_generator.py --lead lead_00001 --contact-only
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

try:  # Windows consoles default to cp1252; keep unicode output from crashing
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass


def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def find_lead(leads: list, lead_id: str) -> dict | None:
    for l in leads:
        if isinstance(l, dict) and l.get("lead_id") == lead_id:
            return l
    return None


def real_email(value) -> str | None:
    """Return the email only if it genuinely exists in collected data."""
    if not isinstance(value, str):
        return None
    e = value.strip()
    if not e or e.lower() in {"null", "none", "n/a", "na", "-", "nan", "unknown"}:
        return None
    if "@" not in e or "." not in e.split("@")[-1] or "example" in e.lower() and False:
        # NOTE: even placeholder-looking domains are passed through if they
        # were actually collected; only structural validation applies.
        pass
    if "@" not in e or len(e) < 5:
        return None
    return e


def build_current_lead(lead: dict, deployment: dict, preview_override: str | None) -> dict:
    preview = (preview_override or "").strip() or (deployment or {}).get("preview_url") or ""
    # One-line site problem for outreach personalization (Bot 9 paraphrases it).
    site_note = ""
    try:
        problems = (lead.get("website_analysis") or {}).get("problems") or []
        if isinstance(problems, list) and problems:
            site_note = str(problems[0])[:160]
    except (AttributeError, TypeError):
        site_note = ""
    return {
        "lead_id": lead.get("lead_id"),
        "business_name": lead.get("name"),
        "category": lead.get("category"),
        "email": real_email(lead.get("email")),
        "phone": (lead.get("phone") or "").strip() or None,
        "website": (lead.get("website") or None),
        "address": lead.get("address"),
        "rating": lead.get("rating"),
        "review_count": lead.get("review_count"),
        "lead_type": lead.get("lead_type"),
        "site_note": site_note or None,
        "opportunity_score": lead.get("opportunity_score"),
        "preview_url": preview or None,
        "deployment_id": (deployment or {}).get("deployment_id"),
        "deployment_status": (deployment or {}).get("status"),
        "status": "ready_for_outreach" if preview else "missing_preview",
        "updated_at": utc_now_iso(),
    }


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Bot 8: prepare outreach-ready lead (never invent emails)")
    p.add_argument("--leads", default="target_leads.json")
    p.add_argument("--lead", default=None, help="lead_id to prepare")
    p.add_argument("--deployment", default=None, help="Bot 7 record path (default: deployments/<lead>.json)")
    p.add_argument("--preview-url", default=None, help="Override preview URL")
    p.add_argument("--output", "-o", default="current_leads.json")
    p.add_argument("--contact-only", action="store_true", help="Print only contact JSON for Bot 9")
    return p.parse_args(argv)


def main(argv=None, **kwargs) -> str | dict | int:
    """Prepare the outreach-ready lead. Returns the output path (str), or the
    contact dict in --contact-only mode, or an int exit code on error.

    Orchestrator use: ``main(leads="target_leads.json", lead="lead_00001")`` —
    any keyword matching an argparse option overrides the default. A bare main()
    call uses defaults (sys.argv is only used via the CLI).
    """
    if argv is None:
        # Plain main() uses defaults (never sys.argv); the CLI passes
        # sys.argv[1:] explicitly via the __main__ block below.
        argv = []
    args = parse_args(argv)
    for _k, _v in kwargs.items():
        if not hasattr(args, _k):
            raise TypeError(f"current_lead_generator.main() got an unexpected option {_k!r}")
        setattr(args, _k, _v)
    leads_path = Path(args.leads)
    if not leads_path.exists():
        print(f"[current_lead_generator] ERROR: {leads_path} not found", file=sys.stderr)
        return 2
    if not args.lead:
        print("[current_lead_generator] ERROR: --lead is required", file=sys.stderr)
        return 2
    try:
        raw = load_json(leads_path)
    except json.JSONDecodeError as e:
        print(f"[current_lead_generator] ERROR: {e}", file=sys.stderr)
        return 2
    leads = raw if isinstance(raw, list) else raw.get("leads", [])
    lead = find_lead(leads, args.lead)
    if not lead:
        print(f"[current_lead_generator] ERROR: {args.lead} not in {leads_path}", file=sys.stderr)
        return 2

    dep_path = Path(args.deployment) if args.deployment else (Path("deployments") / f"{args.lead}.json")
    deployment: dict = {}
    if dep_path.exists():
        try:
            deployment = load_json(dep_path)
        except json.JSONDecodeError as e:
            print(f"[current_lead_generator] WARN bad deployment file: {e}", file=sys.stderr)
    elif not args.preview_url:
        print(f"[current_lead_generator] WARN no deployment record ({dep_path}) and no --preview-url",
              flush=True)

    current = build_current_lead(lead, deployment, args.preview_url)

    if args.contact_only:
        contact = {"lead_id": current["lead_id"], "business_name": current["business_name"],
                   "email": current["email"], "phone": current["phone"]}
        print(json.dumps(contact, ensure_ascii=False, indent=2))
        return contact

    out_path = Path(args.output)
    existing: list = []
    if out_path.exists():
        try:
            r = load_json(out_path)
            existing = r if isinstance(r, list) else [r]
        except json.JSONDecodeError:
            existing = []
    existing = [e for e in existing if isinstance(e, dict) and e.get("lead_id") != current["lead_id"]]
    existing.append(current)
    out_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if current["email"] is None:
        print("[current_lead_generator] note: no verified email in collected data — left null "
              "(never fabricated)", flush=True)
    print(f"[current_lead_generator] {current['lead_id']} -> {out_path} "
          f"(status={current['status']})", flush=True)
    print(json.dumps({"lead_id": current["lead_id"], "business_name": current["business_name"],
                      "email": current["email"], "phone": current["phone"],
                      "preview_url": current["preview_url"]}, ensure_ascii=False, indent=2))
    return str(out_path)


if __name__ == "__main__":
    _rc = main(sys.argv[1:])
    raise SystemExit(_rc if isinstance(_rc, int) else 0)
