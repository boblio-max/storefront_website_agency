"""dashboard.py — Sleek modern pygame mission control for the agency.

Shows everything at a glance: KPIs, top leads with pipeline stages,
money (Bot 11 ledger), inbox watcher status, and recent activity.
Read-only: it never sends emails, deploys, or touches leads — it just
re-reads the JSON files every few seconds.

Run:
    py dashboard.py                 # 1280x800 window, R refresh, Up/Down select, Q quit
    py dashboard.py --frames 60     # headless smoke test (no window needed)

Controls:
    Up / Down  select lead          R  refresh now
    Q / Esc    quit
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# -- palette (modern dark) ---------------------------------------------------
BG = (11, 14, 20)
CARD = (21, 27, 38)
CARD_EDGE = (38, 48, 66)
TEXT = (235, 240, 248)
DIM = (148, 163, 184)
ACCENT = (94, 234, 212)      # teal
VIOLET = (167, 139, 250)
GOLD = (251, 191, 36)
GREEN = (52, 211, 153)
RED = (248, 113, 113)
BLUE = (96, 165, 250)

STAGE_COLORS = {
    "scraped": DIM, "site_built": BLUE, "qa_passed": VIOLET,
    "deployed": GREEN, "outreach_sent": ACCENT, "replied": GOLD,
    "paid": GOLD, "final_delivered": GREEN,
}


def _load_json(name, default):
    try:
        raw = json.loads((ROOT / name).read_text(encoding="utf-8"))
        return raw
    except (OSError, json.JSONDecodeError):
        return default


def _as_list(raw):
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    if isinstance(raw, dict) and isinstance(raw.get("leads"), list):
        return [r for r in raw["leads"] if isinstance(r, dict)]
    return []


def collect_stats():
    """Read every state file. Never raises — missing files become zeros."""
    try:
        targets = _as_list(_load_json("target_leads.json", []))
        state = _load_json("pipeline_state.json", {})
        if not isinstance(state, dict):
            state = {}
        current = _as_list(_load_json("current_leads.json", []))
        deployments = _load_json("deployments/deployments.json", [])
        if not isinstance(deployments, list):
            deployments = [deployments] if deployments else []
        live = [d for d in deployments
                if isinstance(d, dict) and d.get("status") == "deployed"
                and str(d.get("preview_url") or "").startswith("http")]
        history = _load_json("email_history.json", [])
        if not isinstance(history, list):
            history = []
        sent = [m for m in history
                if isinstance(m, dict) and m.get("direction") == "outgoing"]
        inbox = _load_json("inbox_state.json", {})
        if not isinstance(inbox, dict):
            inbox = {}
        ledger = _load_json("payments.json", [])
        if not isinstance(ledger, list):
            ledger = []
        paid = [r for r in ledger
                if isinstance(r, dict) and r.get("status") == "paid"]
        paid_cents = sum(int(r.get("amount_cents", 0) or 0) for r in paid)
        sites = [p for p in (ROOT / "generated_sites").iterdir()] \
            if (ROOT / "generated_sites").exists() else []

        scored = sorted(targets,
                        key=lambda e: float(e.get("opportunity_score") or 0),
                        reverse=True)
        feed = []
        for m in history[-8:]:
            if isinstance(m, dict):
                feed.append(("mail", str(m.get("lead_id", "?")),
                             str(m.get("subject", ""))[:44]))
        for lid, ent in sorted(
                state.items(),
                key=lambda kv: str(kv[1].get("updated_at", "")),
                reverse=True)[:6]:
            if isinstance(ent, dict):
                feed.append(("stage", str(lid),
                             f"{ent.get('stage', '?')} · "
                             f"{str(ent.get('updated_at', ''))[:16]}"))
        return {
            "targets": targets, "scored": scored, "state": state,
            "current": current, "deployments": deployments, "live": live,
            "history": history, "sent": sent, "inbox": inbox,
            "ledger": ledger, "paid": paid, "paid_cents": paid_cents,
            "sites": len(sites),
            "kpis": [
                ("Leads", len(targets), ACCENT),
                ("Sites built", len(sites), VIOLET),
                ("Live", len(live), GREEN),
                ("Paid", len(paid), GOLD),
                ("Emails sent", len(sent), BLUE),
                ("Inbox handled", inbox.get("last_handled", "—"), DIM),
            ],
        }
    except Exception:  # noqa: BLE001 — dashboard never dies on bad data
        return {"targets": [], "scored": [], "state": {}, "current": [],
                "deployments": [], "live": [], "history": [], "sent": [],
                "inbox": {}, "ledger": [], "paid": [], "paid_cents": 0,
                "sites": 0, "kpis": [("Leads", 0, ACCENT)]}


def run(frames: int = 0):
    import pygame
    pygame.init()
    W, H = 1280, 800
    screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption("Storefront — Agency Command")
    clock = pygame.time.Clock()
    f_title = pygame.font.SysFont("segoeui", 26, bold=True)
    f_big = pygame.font.SysFont("segoeui", 30, bold=True)
    f_med = pygame.font.SysFont("segoeui", 17)
    f_small = pygame.font.SysFont("segoeui", 14)
    f_tiny = pygame.font.SysFont("segoeui", 12)

    def card(rect, radius=14):
        pygame.draw.rect(screen, CARD, rect, border_radius=radius)
        pygame.draw.rect(screen, CARD_EDGE, rect, 1, border_radius=radius)

    def text(surf, s, font, color, pos):
        surf.blit(font.render(str(s), True, color), pos)

    stats = collect_stats()
    sel = 0
    last_refresh = 0.0
    frame = 0
    running = True
    while running:
        now = pygame.time.get_ticks() / 1000.0
        if now - last_refresh > 5.0:  # auto re-read JSON every 5s
            stats = collect_stats()
            last_refresh = now
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key in (pygame.K_q, pygame.K_ESCAPE):
                    running = False
                elif ev.key == pygame.K_r:
                    stats = collect_stats()
                    last_refresh = now
                elif ev.key == pygame.K_DOWN:
                    sel = min(sel + 1, max(0, len(stats["scored"][:8]) - 1))
                elif ev.key == pygame.K_UP:
                    sel = max(sel - 1, 0)

        screen.fill(BG)
        # header
        pygame.draw.circle(screen, ACCENT, (36, 34), 9)
        pygame.draw.circle(screen, BG, (36, 34), 4)
        text(screen, "STOREFRONT  ·  Agency Command", f_title, TEXT, (56, 16))
        stamp = datetime.now(timezone.utc).strftime("%H:%M UTC")
        text(screen, f"{stamp}   ·   R refresh   ·   ↑↓ select   ·   Q quit",
             f_small, DIM, (W - 380, 28))
        pygame.draw.circle(screen, GREEN, (W - 24, 34), 6)  # live dot

        # KPI row
        kpis = stats["kpis"]
        kw = (W - 48 - 5 * 12) // 6
        for i, (label, val, color) in enumerate(kpis):
            x = 24 + i * (kw + 12)
            card(pygame.Rect(x, 64, kw, 86))
            text(screen, str(val), f_big, color, (x + 16, 72))
            text(screen, label, f_small, DIM, (x + 16, 112))

        # leads panel
        card(pygame.Rect(24, 162, 620, 470))
        text(screen, "TOP LEADS  ·  by opportunity score", f_med, TEXT, (44, 176))
        y = 206
        for idx, e in enumerate(stats["scored"][:8]):
            lid = e.get("lead_id", "?")
            stage = stats["state"].get(lid, {}).get("stage", "scraped") \
                if isinstance(stats["state"].get(lid), dict) else "scraped"
            color = STAGE_COLORS.get(stage, DIM)
            if idx == sel:
                pygame.draw.rect(screen, (30, 41, 59),
                                 pygame.Rect(36, y - 6, 596, 52),
                                 border_radius=10)
                pygame.draw.rect(screen, color,
                                 pygame.Rect(36, y - 6, 8, 52),
                                 border_radius=4)
            name = str(e.get("name", "?"))[:34]
            score = e.get("opportunity_score", "—")
            mail = "✉" if (e.get("email") or "").strip() else "☎"
            text(screen, f"{mail}  {name}", f_med, TEXT, (56, y))
            text(screen, f"{score}", f_med, ACCENT, (480, y))
            text(screen, stage, f_small, color, (540, y + 3))
            y += 56

        # money + inbox panel
        card(pygame.Rect(656, 162, 600, 210))
        text(screen, "MONEY  ·  Bot 11 ledger", f_med, TEXT, (676, 176))
        dollars = stats["paid_cents"] / 100
        text(screen, f"${dollars:,.0f}  collected", f_big, GOLD, (676, 204))
        text(screen,
             f"{len(stats['paid'])} paid  ·  "
             f"{sum(1 for r in stats['ledger'] if isinstance(r, dict) and r.get('status') == 'open')} open invoices",
             f_small, DIM, (676, 244))
        ib = stats["inbox"]
        last = str(ib.get("last_poll", "never"))[:19] if ib else "never (run --watch or watcher)"
        text(screen, f"Inbox last poll: {last}", f_small, DIM, (676, 270))
        text(screen, "Gmail: storefront.webs@gmail.com  ·  install_inbox_watcher.bat for 24/7",
             f_tiny, DIM, (676, 296))
        # progress bar: leads → live
        total = max(1, len(stats["targets"]))
        frac = min(1.0, len(stats["live"]) / total)
        pygame.draw.rect(screen, (30, 41, 59), pygame.Rect(676, 322, 560, 12),
                         border_radius=6)
        pygame.draw.rect(screen, GREEN,
                         pygame.Rect(676, 322, int(560 * frac), 12),
                         border_radius=6)
        text(screen, f"{len(stats['live'])}/{len(stats['targets'])} live",
             f_tiny, DIM, (676, 340))

        # activity panel
        card(pygame.Rect(656, 384, 600, 248))
        text(screen, "ACTIVITY  ·  latest first", f_med, TEXT, (676, 398))
        ay = 428
        feed = stats.get("feed", []) if "feed" in stats else []
        # rebuild feed here (collect_stats returns it inline as mixed list)
        items = []
        for m in stats["history"][-6:]:
            if isinstance(m, dict):
                items.append((str(m.get("lead_id", "?")),
                              str(m.get("subject", ""))[:48]))
        items = list(reversed(items))
        for lid, subj in items[:7]:
            pygame.draw.circle(screen, BLUE, (690, ay + 7), 4)
            text(screen, lid, f_small, ACCENT, (704, ay))
            text(screen, subj, f_small, DIM, (800, ay))
            ay += 28
        if not items:
            text(screen, "No emails yet — run the pipeline with --send-emails.",
                 f_small, DIM, (676, ay))

        # selected lead detail footer
        card(pygame.Rect(24, 644, 1232, 132))
        leads = stats["scored"][:8]
        if leads:
            e = leads[min(sel, len(leads) - 1)]
            lid = e.get("lead_id", "?")
            st = stats["state"].get(lid, {}) if isinstance(
                stats["state"].get(lid), dict) else {}
            text(screen, f"{e.get('name', '?')}  ·  {lid}", f_med, TEXT, (44, 656))
            text(screen,
                 f"{e.get('category', '—')}   ·   ★ {e.get('rating', '—')} "
                 f"({e.get('review_count', '—')} reviews)   ·   "
                 f"{e.get('phone', 'no phone')}   ·   "
                 f"stage: {st.get('stage', 'scraped')}",
                 f_small, DIM, (44, 684))
            url = st.get("preview_url") or e.get("preview_url") or "no preview yet"
            text(screen, str(url)[:90], f_small, ACCENT, (44, 710))
            pct = min(100, float(e.get("opportunity_score") or 0))
            pygame.draw.rect(screen, (30, 41, 59),
                             pygame.Rect(44, 740, 400, 10), border_radius=5)
            pygame.draw.rect(screen, ACCENT,
                             pygame.Rect(44, 740, int(400 * pct / 100), 10),
                             border_radius=5)
            text(screen, f"score {pct:.0f}", f_tiny, DIM, (452, 736))
        else:
            text(screen, "No leads yet — run:  py orchestrator.py --reuse --limit 1",
                 f_med, DIM, (44, 680))

        pygame.display.flip()
        clock.tick(30)
        frame += 1
        if frames and frame >= frames:
            running = False
    pygame.quit()
    return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Storefront agency dashboard")
    p.add_argument("--frames", type=int, default=0,
                   help="Run N frames then exit (headless smoke test)")
    return p.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(run(parse_args(sys.argv[1:]).frames))
