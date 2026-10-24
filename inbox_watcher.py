"""inbox_watcher.py — Hands-free Gmail inbox daemon for the agency.

Polls the agency Gmail (IMAP UNSEEN) every --interval seconds, routes each
reply through orchestrator.handle_reply (revision → rebuild+redeploy+redeliver,
interested → invoice email, else sales/human), saves client photo attachments,
extracts structured facts back into the lead files, and marks handled mail
\\Seen. Nothing is ever deleted.

Seamless by design:
- state in inbox_state.json (last poll, last counts, error backoff) survives
  restarts, so a reboot just resumes where it left off.
- exponential backoff on IMAP failures (1m → 5m → 15m max) — never hot-loops.
- every handled reply is logged to inbox_watcher.log AND email_history.json.

Usage:
    py inbox_watcher.py --once                  # single poll (cron/task friendly)
    py inbox_watcher.py --daemon                # loop forever (default 300s)
    py inbox_watcher.py --daemon --interval 120 --send-emails
    py install_inbox_watcher.bat                 # Windows: run on logon, hands-free

Gmail setup (once): enable 2FA on storefront.webs@gmail.com, create an App
Password, put it in .env as AGENCY_IMAP_PASS=xxxx (same value as
AGENCY_SMTP_PASS). No OAuth, no browser clicks after that.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import time
from pathlib import Path

STATE_PATH = Path(__file__).resolve().parent / "inbox_state.json"
LOG_PATH = Path(__file__).resolve().parent / "inbox_watcher.log"


def _log(line: str) -> None:
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    entry = f"[{stamp}] {line}"
    print(entry, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except OSError:
        pass


def _load_state() -> dict:
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    try:
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
    except OSError:
        pass


def poll_once(send_emails: bool = False, history: str = "email_history.json",
              current: str = "current_leads.json", leads: str = "target_leads.json",
              output_dir: str = "generated_sites") -> dict:
    """One inbox cycle. Returns {"handled": n, "errors": [...]}. Never raises."""
    import response_feedback_manager as rfm
    stats: dict = {"handled": 0, "skipped": 0, "errors": []}
    try:
        incoming = rfm.poll_inbox(history=history)
    except RuntimeError as e:
        stats["errors"].append(str(e))
        _log(f"poll skipped: {e}")
        return stats
    _log(f"{len(incoming)} unseen message(s)")
    # Lazy import: orchestrator imports rfm, so import here to avoid a cycle.
    import orchestrator as orch
    for msg in incoming:
        lid = msg.get("lead_id")
        if not lid:
            _log(f"no lead match for {msg.get('from')} "
                 f"({(msg.get('subject') or '')[:50]}) — marking seen")
            try:
                rfm.mark_seen([msg["uid"]])
                stats["skipped"] += 1
            except RuntimeError as e:
                stats["errors"].append(str(e))
            continue
        try:
            fb = orch.handle_reply(
                msg["body"], lead=lid, leads=leads, output_dir=output_dir,
                current_output=current, send_emails=send_emails, history=history)
            _log(f"reply {lid}: {fb.get('response_type')} -> {fb.get('action')} "
                 f"(extracted={bool(fb.get('extracted'))}) — marking seen")
            try:
                rfm.mark_seen([msg["uid"]])
            except RuntimeError as e:
                stats["errors"].append(str(e))
            stats["handled"] += 1
        except RuntimeError as e:
            _log(f"reply {lid} failed: {e} (left unseen for next cycle)")
            stats["errors"].append(str(e))
    state = _load_state()
    state.update({
        "last_poll": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "last_handled": stats["handled"],
        "last_errors": stats["errors"][-3:],
    })
    _save_state(state)
    return stats


def daemon(interval: int = 300, send_emails: bool = False, **kwargs) -> None:
    """Loop forever with exponential backoff. Ctrl-C stops cleanly."""
    _log(f"inbox watcher started: every {interval}s "
         f"({'send' if send_emails else 'draft-only'} replies) — Ctrl-C to stop")
    backoff = interval
    while True:
        try:
            stats = poll_once(send_emails=send_emails, **kwargs)
            # Success resets the backoff, even when the inbox was empty.
            backoff = interval if not stats["errors"] else min(backoff * 2, 900)
            if stats["errors"]:
                _log(f"errors: {len(stats['errors'])} — next poll in {backoff}s")
        except KeyboardInterrupt:
            _log("inbox watcher stopped")
            return
        except Exception as e:  # noqa: BLE001 — daemon never dies
            _log(f"unexpected: {e} — next poll in {backoff}s")
            backoff = min(backoff * 2, 900)
        try:
            time.sleep(backoff)
        except KeyboardInterrupt:
            _log("inbox watcher stopped")
            return


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Hands-free Gmail inbox watcher")
    p.add_argument("--interval", type=int, default=300,
                   help="Seconds between polls in --daemon (default: 300)")
    p.add_argument("--once", action="store_true",
                   help="Poll once and exit (cron / Task Scheduler friendly)")
    p.add_argument("--daemon", action="store_true",
                   help="Poll forever until Ctrl-C")
    p.add_argument("--send-emails", action="store_true",
                   help="Actually send reply emails (default: draft + log only)")
    p.add_argument("--history", default="email_history.json")
    p.add_argument("--current", default="current_leads.json")
    p.add_argument("--leads", default="target_leads.json")
    p.add_argument("--output-dir", default="generated_sites")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.once:
        stats = poll_once(send_emails=args.send_emails, history=args.history,
                          current=args.current, leads=args.leads,
                          output_dir=args.output_dir)
        print(json.dumps(stats, ensure_ascii=False, indent=2), flush=True)
        return 0 if not stats["errors"] else 1
    # Default with no flags = daemon (hands-free). Explicit is better than
    # sitting idle because the operator forgot --daemon.
    daemon(interval=max(60, args.interval), send_emails=args.send_emails,
           history=args.history, current=args.current, leads=args.leads,
           output_dir=args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
