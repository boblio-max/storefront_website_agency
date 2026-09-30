"""
lead_prioritizer.py (Bot 4) — Rank the best potential agency customers.

Inputs:
    without_websites.json   (Bot 2: no site at all)
    bad_websites.json       (Bot 3: has site, but scored < threshold)

Process:
    Merge both lead pools -> score each business on:
      • Business / category value
      • Rating + review count (established, visible businesses)
      • No-website vs bad-website (need severity)
      • Contact info availability (can we reach them?)
    Rank by opportunity (highest first).

Output:
    target_leads.json — each lead carries opportunity_score + context
    needed by the website generator (Bot 5) and later sales bots.

Usage:
    python lead_prioritizer.py
    python lead_prioritizer.py --without without_websites.json --bad bad_websites.json --output target_leads.json
    python lead_prioritizer.py --max 50 --min-score 40
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import re
import sys
from pathlib import Path

try:  # Windows consoles default to cp1252; keep unicode output from crashing
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

HIGH_VALUE_PAT = re.compile(
    r"auto|repair|dentist|dental|orthodont|law|attorney|hvac|plumb|roof|electric|"
    r"contractor|remodel|real estate|insurance|clinic|vet|restaurant|salon|spa|"
    r"hotel|fitness|gym|landscap|pest|garage|towing|funeral|daycare|storage",
    re.I)
MEDIUM_VALUE_PAT = re.compile(
    r"shop|store|cafe|coffee|bar|bakery|barber|nail|massage|photo|clean|laundry|"
    r"pet|florist|jewel|optical|travel|tax|account|consult|market|grocery|pizza|"
    r"sushi|tattoo|museum|church|school", re.I)


def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def load_list(path: Path) -> list[dict]:
    if not path.exists():
        print(f"[lead_prioritizer] note: {path} not found, skipping", flush=True)
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        for key in ("businesses", "results", "data", "items", "leads"):
            if isinstance(raw.get(key), list):
                raw = raw[key]
                break
        else:
            raw = [raw]
    if not isinstance(raw, list):
        raise ValueError(f"{path} must contain a JSON list")
    return [r for r in raw if isinstance(r, dict)]


def stable_key(b: dict) -> str:
    seed = "|".join(str(b.get(k) or "") for k in ("name", "phone", "website", "address"))
    return hashlib.md5(seed.encode("utf-8")).hexdigest()[:10]


def score_business(b: dict, lead_type: str) -> tuple[float, list[str]]:
    """Return (opportunity_score 0-100, reasons)."""
    reasons: list[str] = []
    score = 40.0

    # -- Rating (0..+12) ------------------------------------------------
    try:
        rating = float(b.get("rating")) if b.get("rating") is not None else None
    except (TypeError, ValueError):
        rating = None
    if rating is not None:
        if rating >= 4.5:
            score += 12; reasons.append(f"High rating ({rating})")
        elif rating >= 4.0:
            score += 9; reasons.append(f"Good rating ({rating})")
        elif rating >= 3.5:
            score += 5; reasons.append(f"Decent rating ({rating})")
        elif rating < 3.0:
            score -= 5; reasons.append(f"Low rating ({rating}) — risk flag")

    # -- Review count: log-scaled visibility (0..+12) --------------------
    try:
        rc = int(b.get("review_count") or 0)
    except (TypeError, ValueError):
        rc = 0
    if rc >= 500:
        score += 12; reasons.append(f"Very visible ({rc} reviews)")
    elif rc >= 200:
        score += 10; reasons.append(f"Established ({rc} reviews)")
    elif rc >= 100:
        score += 8; reasons.append(f"Active ({rc} reviews)")
    elif rc >= 50:
        score += 5; reasons.append(f"Growing ({rc} reviews)")
    elif rc >= 20:
        score += 3
    elif rc >= 5:
        score += 1

    # -- Need severity ----------------------------------------------------
    if lead_type == "no_website":
        score += 15; reasons.append("No website at all — highest need")
    else:
        wa = b.get("website_analysis") or {}
        try:
            site_score = float(wa.get("score", 50))
        except (TypeError, ValueError):
            site_score = 50
        severity_bonus = round((100 - max(0, min(100, site_score))) / 100 * 12, 1)
        score += severity_bonus
        n_prob = len(wa.get("problems") or [])
        reasons.append(f"Bad website (site score {site_score:.0f}, {n_prob} problems) — strong replacement case")

    # -- Contact availability ----------------------------------------------
    # Email is the only outreach channel that scales (Bot 9 sends, Bot 10
    # reads replies), so a verified address outweighs everything else short
    # of need severity. Phone-only leads still build fine but can't be emailed.
    if (b.get("phone") or "").strip():
        score += 6; reasons.append("Phone available for outreach")
    if (b.get("email") or "").strip():
        score += 25; reasons.append("Verified email — outreach-ready (highest contact value)")
    elif not (b.get("phone") or "").strip():
        score -= 6; reasons.append("No phone/email — hard to reach")
    if (b.get("address") or "").strip():
        score += 2
    if (b.get("maps_url") or b.get("website") or "").strip():
        score += 1

    # -- Category value ----------------------------------------------------
    cat = f"{b.get('category') or ''} {b.get('name') or ''}"
    if HIGH_VALUE_PAT.search(cat):
        score += 8; reasons.append("High-value local-service category")
    elif MEDIUM_VALUE_PAT.search(cat):
        score += 4; reasons.append("Consumer-facing category benefits from a site")
    else:
        score += 2

    return round(max(0, min(100, score)), 1), reasons


def prioritize(without: list[dict], bad: list[dict]) -> list[dict]:
    leads: list[dict] = []
    seen: set[str] = set()
    for b in without:
        key = stable_key(b)
        if key in seen:
            continue
        seen.add(key)
        s, reasons = score_business(b, "no_website")
        leads.append({**b, "lead_type": "no_website", "opportunity_score": s,
                      "opportunity_reasons": reasons, "stable_key": key})
    for b in bad:
        key = stable_key(b)
        if key in seen:
            continue
        seen.add(key)
        s, reasons = score_business(b, "bad_website")
        leads.append({**b, "lead_type": "bad_website", "opportunity_score": s,
                      "opportunity_reasons": reasons, "stable_key": key})
    leads.sort(key=lambda l: (-l["opportunity_score"],
                              -(l.get("review_count") or 0),
                              (l.get("name") or "")))
    now = utc_now_iso()
    for i, lead in enumerate(leads, 1):
        lead["lead_id"] = f"lead_{i:05d}"
        lead["prioritized_at"] = now
    return leads


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Bot 4: merge + score leads -> target_leads.json")
    p.add_argument("--without", default="without_websites.json")
    p.add_argument("--bad", default="bad_websites.json")
    p.add_argument("--output", "-o", default="target_leads.json")
    p.add_argument("--max", type=int, default=0, help="Keep top N (0 = all)")
    p.add_argument("--min-score", type=float, default=0.0, help="Drop leads below this score")
    p.add_argument("--require-email", action="store_true",
                   help="Keep only leads with a verified email (outreach-ready)")
    return p.parse_args(argv)


def main(argv=None, **kwargs) -> str | int:
    """Merge + score leads. Returns the output path (str) or int exit code.

    Orchestrator use: ``main(without="without_websites.json", bad="bad_websites.json",
    output="target_leads.json")`` — any keyword matching an argparse option
    overrides the default. A bare main() call uses defaults (sys.argv is only
    used via the CLI).
    """
    if argv is None:
        # Plain main() uses defaults (never sys.argv); the CLI passes
        # sys.argv[1:] explicitly via the __main__ block below.
        argv = []
    args = parse_args(argv)
    for _k, _v in kwargs.items():
        if not hasattr(args, _k):
            raise TypeError(f"lead_prioritizer.main() got an unexpected option {_k!r}")
        setattr(args, _k, _v)
    try:
        without = load_list(Path(args.without))
        bad = load_list(Path(args.bad))
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[lead_prioritizer] ERROR: {e}", file=sys.stderr)
        return 2
    if not without and not bad:
        print("[lead_prioritizer] ERROR: both inputs empty/missing", file=sys.stderr)
        return 2
    leads = prioritize(without, bad)
    total = len(leads)
    leads = [l for l in leads if l["opportunity_score"] >= args.min_score]
    if args.require_email:
        before = len(leads)
        leads = [l for l in leads if (l.get("email") or "").strip()]
        print(f"[lead_prioritizer] require-email: {before} -> {len(leads)} outreach-ready",
              flush=True)
    if args.max and args.max > 0:
        leads = leads[:args.max]
    out_path = Path(args.output)
    out_path.write_text(json.dumps(leads, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[lead_prioritizer] pools: {len(without)} without + {len(bad)} bad = {total}", flush=True)
    print(f"[lead_prioritizer] ranked -> {out_path} ({len(leads)} leads)", flush=True)
    for l in leads[:5]:
        print(f"  {l['lead_id']} score={l['opportunity_score']} [{l['lead_type']}] {l.get('name')}", flush=True)
    return str(out_path)


if __name__ == "__main__":
    _rc = main(sys.argv[1:])
    raise SystemExit(_rc if isinstance(_rc, int) else 0)
