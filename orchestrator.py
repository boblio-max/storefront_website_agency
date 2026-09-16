"""orchestrator.py — Chain the bots via their orchestrator-friendly main() calls.

Convention every bot follows:
    result = bot.main(argv=None, **kwargs)   # kwargs override argparse options
    # success -> output path (str), tuple, or dict artifact
    # failure -> int exit code

Each ``main`` can still be run from the CLI (``python <bot>.py --help``);
the ``**kwargs`` form is what the orchestrator uses below.

Run:
    python orchestrator.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import maps_scraper as ms
import website_classifier as wc
import website_qualifier as wq
import lead_prioritizer as lp
import website_generator as wg
import qa_bot as qb
import deployment_manager as dmgr
import current_lead_generator as clg
import email_generator as eg
import response_feedback_manager as rfm
def _require(result, stage: str):
    """Unwrap a bot return: artifact passes through, int exit code raises."""
    if isinstance(result, int):
        raise RuntimeError(f"{stage} failed with exit code {result}")
    return result


def notify_owner(subject: str, body: str, notify_to: str | None = None) -> bool:
    """Email the owner an update via the agency mailbox. Never raises.

    Returns True if sent. Skips quietly (log line only) when SMTP is
    unconfigured or notify_to is empty — pipeline must never die because
    a notification failed.
    """
    to = (notify_to if notify_to is not None
          else os.environ.get("AGENCY_NOTIFY_TO", "nikhilmahankali56@gmail.com")).strip()
    if not to:
        return False
    cfg = eg.smtp_config()
    if cfg is None:
        print("[orchestrator] owner notify skipped (no SMTP transport)", flush=True)
        return False
    try:
        eg.smtp_send(cfg, to, f"[Storefront] {subject}", body,
                     reply_to=cfg["from"])
    except RuntimeError as e:
        print(f"[orchestrator] owner notify failed: {e}", flush=True)
        return False
    print(f"[orchestrator] owner notified: {subject}", flush=True)
    return True


def deployed_lids(record_file: str | Path = "deployments/deployments.json") -> set[str]:
    """Lead IDs (and site slugs) with a recorded deployment."""
    try:
        raw = json.loads(Path(record_file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    records = raw if isinstance(raw, list) else [raw]
    return {str(r.get(k)) for r in records if isinstance(r, dict)
            for k in ("lead_id", "site") if r.get(k)}


def _latest_preview(record_file: str | Path, lid: str) -> str:
    """Return the newest preview_url Bot 7 recorded for lid (or "")."""
    # Fast path: per-lead record Bot 7 writes (deployments/<lead_id>.json).
    per_lead = Path("deployments") / f"{lid}.json"
    if per_lead.exists():
        try:
            d = json.loads(per_lead.read_text(encoding="utf-8"))
            if isinstance(d, dict) and d.get("preview_url"):
                return str(d["preview_url"])
        except (json.JSONDecodeError, OSError):
            pass
    p = Path(record_file)
    if not p.exists():
        return ""
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    records = raw if isinstance(raw, list) else [raw]
    for r in reversed(records):
        if isinstance(r, dict) and r.get("preview_url") and \
                lid in (r.get("site"), r.get("lead_id")):
            return str(r["preview_url"])
    return ""


def run(queries: list[str] | None = None, max_per_query: int = 20,
        threshold: int = 60, output_dir: str = "generated_sites",
        force: bool = False, no_opencode: bool = False,
        limit: int = 1, qa_threshold: int = 80,
        max_qa_attempts: int = 5, prod: bool = True,
        current_output: str = "current_leads.json",
        send_emails: bool = False,
        skip_scrape: bool = False,
        businesses: str = "businesses.json",
        history: str = "email_history.json",
        skip_deployed: bool = False,
        notify: bool = True,
        notify_to: str | None = None,
        preview: bool = True) -> str:
    """Run scrape -> classify -> qualify -> merge -> prioritize -> per-lead loop.

    Per entry: Bot 5 generate -> Bot 6 QA loop ("good") -> Bot 7 deploy
    (GitHub + Vercel production) -> Bot 8 outreach-ready lead -> Bot 9
    outreach email (draft unless send_emails=True). Bot 10 (replies) is
    reactive via handle_reply(); Bot 11 is a stub.
    Returns target_leads path.

    Pass skip_scrape=True (or --reuse) to reuse the existing businesses.json
    instead of hitting Google Maps again. limit caps processed leads
    (0 = all; default 1 for a safe first run).
    """
    if skip_scrape:
        businesses_file = businesses
        if not Path(businesses_file).exists():
            raise RuntimeError(f"skip_scrape: {businesses_file} not found")
        print(f"[orchestrator] reusing {businesses_file} (skip_scrape)", flush=True)
    else:
        # Bot 1: scrape nearby businesses -> businesses.json
        scrape_kwargs: dict = {"max": max_per_query, "output": businesses}
        if queries:
            scrape_kwargs["query"] = queries
        businesses_file = _require(ms.main(**scrape_kwargs), "maps_scraper")

    # Bot 2: split by website presence -> with/without files
    classified = _require(
        wc.main(input=businesses_file,
                with_path="with_websites.json",
                without_path="without_websites.json"),
        "website_classifier",
    )
    with_path, without_path = classified

    # Bot 3: keep the BAD websites -> bad_websites.json
    bad_path = _require(
        wq.main(input=with_path, output="bad_websites.json", threshold=threshold),
        "website_qualifier",
    )

    # Bot 4: merge + rank -> target_leads.json
    leads_path = _require(
        lp.main(without=without_path, bad=bad_path, output="target_leads.json"),
        "lead_prioritizer",
    )
    
    # Bot 5: loop through target_leads.json -> generate one site per entry.
    # website_generator.main takes a single entry; looping lives here.
    leads = wg.load_leads(Path(leads_path))
    if skip_deployed:
        done = deployed_lids("deployments/deployments.json")
        before = len(leads)
        leads = [e for e in leads
                 if not (isinstance(e, dict) and e.get("lead_id") in done)]
        print(f"[orchestrator] skipping {before - len(leads)} already-deployed lead(s)",
              flush=True)
    if limit and limit > 0:
        leads = leads[:limit]
    print(f"[orchestrator] Bot 5+6: generating {len(leads)} site(s)...", flush=True)
    failures = 0
    for entry in leads:
        lid = entry.get("lead_id", "?")
        try:
            _process_lead(entry, lid, leads_path, output_dir, force, no_opencode,
                          max_qa_attempts, qa_threshold, prod, current_output,
                          send_emails, history, notify, notify_to, preview)
        except RuntimeError as e:
            failures += 1
            print(f"[orchestrator] lead {lid} failed ({e}) — continuing "
                  f"with next lead", flush=True)
            if notify:
                notify_owner(f"lead {lid} failed: {e}",
                             f"Lead: {entry.get('name')} ({lid})\n"
                             f"Error: {e}\nLead skipped; pipeline continued.",
                             notify_to)
    if failures:
        print(f"[orchestrator] {failures}/{len(leads)} lead(s) failed", flush=True)
    return leads_path


def _process_lead(entry: dict, lid: str, leads_path: str, output_dir: str,
                  force: bool, no_opencode: bool, max_qa_attempts: int,
                  qa_threshold: int, prod: bool, current_output: str,
                  send_emails: bool, history: str,
                  notify: bool, notify_to: str | None,
                  preview: bool = True) -> None:
    """Generate → QA → deploy → outreach for one lead. Raises RuntimeError."""
    website_path = _require(
        wg.main(lead_data=entry, output_dir=output_dir,
                force=force, no_opencode=no_opencode, preview=preview),
        "website_generator",
    )

    # Bot 6: loop until QA passes, then print good.
    # Retries force a regenerate so QA re-checks fresh output.
    for attempt in range(1, max_qa_attempts + 1):
        if attempt > 1:
            website_path = _require(
                wg.main(lead_data=entry, output_dir=output_dir,
                        force=True, no_opencode=no_opencode, preview=preview),
                "website_generator",
            )
        qa_res = qb.main(website_path, threshold=qa_threshold)
        if isinstance(qa_res, str):
            print(f"[orchestrator] good — {lid} passed QA", flush=True)
            break
        if qa_res == 1:
            print(f"[orchestrator] QA failed for {lid} "
                  f"(attempt {attempt}/{max_qa_attempts}), regenerating...",
                  flush=True)
            continue
        _require(qa_res, "qa_bot")
    else:
        raise RuntimeError(
            f"qa_bot: {lid} still failing QA after {max_qa_attempts} attempts")

    rc = dmgr.main(path_to_file=website_path, prod=prod)
    if rc != 0:
        raise RuntimeError(f"deployment_manager failed for {lid} (exit {rc})")

    # Lead secured: site is live. Tell the owner.
    name = entry.get("name", lid)
    preview_url = _latest_preview("deployments/deployments.json", lid)
    if notify:
        notify_owner(f"lead secured: {name} is live",
                     f"Business: {name} ({lid})\n"
                     f"Preview: {preview_url or website_path}\n"
                     f"Outreach: {'sending' if send_emails else 'draft-only'}.",
                     notify_to)

    # Bot 8: outreach-ready lead (preview URL from Bot 7's record).
    _require(
        clg.main(leads=leads_path, lead=lid, preview_url=preview_url or None,
                 output=current_output),
        "current_lead_generator",
    )

    # Bot 9: outreach email (draft unless send_emails=True).
    if send_emails and eg.already_sent(eg.load_history(history), lid, "initial_outreach"):
        print(f"[orchestrator] email already sent for {lid} — skipping resend",
              flush=True)
        email_res = 0
    else:
        email_res = eg.main(lead=lid, current=current_output, send=send_emails,
                            preview_url=preview_url or None, history=history)
    if email_res == 1:
        raise RuntimeError(f"email_generator failed for {lid} (exit 1)")
    if isinstance(email_res, int):
        print(f"[orchestrator] email skipped for {lid} (exit {email_res}: "
              f"no address, suppressed, or no SMTP transport)", flush=True)
    else:
        print(f"[orchestrator] email {'sent' if send_emails else 'drafted'} for {lid}",
              flush=True)
        if send_emails and notify:
            notify_owner(f"outreach sent to {name}",
                         f"Initial outreach email sent for {name} ({lid}).\n"
                         f"Preview: {preview_url}.",
                         notify_to)
     
 
def handle_reply(incoming: str, lead: str | None = None,
                 leads: str = "target_leads.json",
                 output_dir: str = "generated_sites",
                 no_opencode: bool = False,
                 qa_threshold: int = 80,
                 max_qa_attempts: int = 5,
                 prod: bool = False,
                 current_output: str = "current_leads.json",
                 send_emails: bool = False,
                 history: str = "email_history.json",
                 notify: bool = True,
                 notify_to: str | None = None) -> dict:
    """Handle one inbound client reply (Bot 10) and route it.

    - revision request → Bot 5 rebuild with feedback → Bot 6 QA loop →
      Bot 7 redeploy → Bot 8 refresh → Bot 9 revision-delivery email.
    - anything else → classified feedback dict (sales/human handles it).
    Returns the feedback dict.
    """
    fb_path = f"feedback_{lead}.json" if lead else "feedback.json"
    fb_res = rfm.main(incoming=incoming, lead=lead, output=fb_path, record=True)
    feedback = _require(fb_res, "response_feedback_manager")
    if not isinstance(feedback, dict):
        feedback = json.loads(Path(fb_path).read_text(encoding="utf-8"))
    print(f"[orchestrator] reply: {feedback['lead_id']}: "
          f"{feedback['response_type']} -> {feedback['action']}", flush=True)
    if notify:
        notify_owner(f"reply from {feedback['lead_id']}: {feedback['response_type']}",
                     f"Lead: {feedback['lead_id']}\n"
                     f"Type: {feedback['response_type']} -> {feedback['action']}\n"
                     f"Route: {feedback.get('route')}\n"
                     f"Excerpt: {feedback.get('incoming_excerpt', '')[:400]}",
                     notify_to)
    if feedback.get("action") != "website_revision":
        return feedback  # sales / human review route — nothing to rebuild

    lid = feedback["lead_id"]
    raw = json.loads(Path(leads).read_text(encoding="utf-8"))
    items = raw if isinstance(raw, list) else raw.get("leads", [])
    entry = next((e for e in items
                  if isinstance(e, dict) and e.get("lead_id") == lid), None)
    if entry is None:
        raise RuntimeError(f"handle_reply: {lid} not in {leads}")

    site = _require(
        wg.main(lead_data=entry, output_dir=output_dir, force=True,
                no_opencode=no_opencode, feedback=feedback, preview=True),
        "website_generator",
    )
    for attempt in range(1, max_qa_attempts + 1):
        qa_res = qb.main(site, threshold=qa_threshold)
        if isinstance(qa_res, str):
            print(f"[orchestrator] good — {lid} revision passed QA", flush=True)
            break
        if qa_res == 1:
            print(f"[orchestrator] revision QA failed for {lid} "
                  f"(attempt {attempt}/{max_qa_attempts}), regenerating...", flush=True)
            site = _require(
                wg.main(lead_data=entry, output_dir=output_dir, force=True,
                        no_opencode=no_opencode, feedback=feedback, preview=True),
                "website_generator",
            )
            continue
        _require(qa_res, "qa_bot")
    else:
        raise RuntimeError(
            f"qa_bot: {lid} revision still failing after {max_qa_attempts} attempts")

    rc = dmgr.main(path_to_file=site, prod=prod)
    if rc != 0:
        raise RuntimeError(f"deployment_manager failed for {lid} (exit {rc})")
    preview_url = _latest_preview("deployments/deployments.json", lid)
    _require(
        clg.main(leads=leads, lead=lid, preview_url=preview_url or None,
                 output=current_output),
        "current_lead_generator",
    )
    email_res = eg.main(lead=lid, current=current_output, send=send_emails,
                        preview_url=preview_url or None, history=history,
                        purpose="revision_delivery")
    if email_res == 1:
        raise RuntimeError(f"email_generator failed for {lid} (exit 1)")
    return feedback


def watch(interval: int = 3600, poll_inbox: bool = True, **run_kwargs) -> None:
    """Run the agency continuously: new leads -> sites -> outreach, plus replies.

    Each cycle:
      1. Pipeline for leads without a deployment yet (never rebuilds,
         redeploys, or resends what's done).
      2. IMAP poll for unseen replies -> handle_reply each (revision
         requests rebuild + redeploy + redeliver; everything else routes
         to sales/human and the sender is marked seen).
    Sleeps `interval` seconds between cycles; Ctrl-C stops cleanly.
    """
    import time as _time
    history = run_kwargs.get("history", "email_history.json")
    print(f"[orchestrator] watch mode: cycle every {interval}s "
          f"(Ctrl-C to stop)", flush=True)
    cycle = 0
    while True:
        cycle += 1
        print(f"[orchestrator] --- cycle {cycle} ---", flush=True)
        try:
            run(skip_deployed=True, **run_kwargs)
        except RuntimeError as e:
            print(f"[orchestrator] cycle {cycle} pipeline: {e}", flush=True)
        if poll_inbox:
            try:
                incoming = rfm.poll_inbox(history=history)
            except RuntimeError as e:
                print(f"[orchestrator] inbox poll skipped: {e}", flush=True)
                incoming = []
            print(f"[orchestrator] {len(incoming)} unseen replie(s)", flush=True)
            for msg in incoming:
                if not msg.get("lead_id"):
                    print(f"[orchestrator] no lead match for {msg['from']} "
                          f"({msg['subject'][:50]}) — marking seen", flush=True)
                    try:
                        rfm.mark_seen([msg["uid"]])
                    except RuntimeError as e:
                        print(f"[orchestrator] mark-seen: {e}", flush=True)
                    continue
                try:
                    fb = handle_reply(
                        msg["body"], lead=msg["lead_id"],
                        leads=run_kwargs.get("leads", "target_leads.json"),
                        output_dir=run_kwargs.get("output_dir", "generated_sites"),
                        no_opencode=run_kwargs.get("no_opencode", False),
                        qa_threshold=run_kwargs.get("qa_threshold", 80),
                        max_qa_attempts=run_kwargs.get("max_qa_attempts", 5),
                        prod=run_kwargs.get("prod", True),
                        current_output=run_kwargs.get("current_output",
                                                      "current_leads.json"),
                        send_emails=run_kwargs.get("send_emails", False),
                        history=history,
                        notify=run_kwargs.get("notify", True),
                        notify_to=run_kwargs.get("notify_to"))
                    print(f"[orchestrator] reply {msg['lead_id']}: "
                          f"{fb.get('action')} — marking seen", flush=True)
                    try:
                        rfm.mark_seen([msg["uid"]])
                    except RuntimeError as e:
                        print(f"[orchestrator] mark-seen: {e}", flush=True)
                except RuntimeError as e:
                    print(f"[orchestrator] reply {msg['lead_id']} failed: {e} "
                          f"(left unseen)", flush=True)
        print(f"[orchestrator] cycle {cycle} done — sleeping {interval}s", flush=True)
        try:
            _time.sleep(interval)
        except KeyboardInterrupt:
            print("[orchestrator] watch stopped", flush=True)
            return


def parse_args(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="Agency pipeline: scrape -> sites -> QA -> deploy -> outreach")
    p.add_argument("--query", action="append", default=[], dest="queries",
                   help="Business type to search (repeatable)")
    p.add_argument("--max-per-query", type=int, default=20)
    p.add_argument("--threshold", type=int, default=60)
    p.add_argument("--output-dir", default="generated_sites")
    p.add_argument("--force", action="store_true")
    p.add_argument("--no-opencode", action="store_true")
    p.add_argument("--final", dest="preview", action="store_false", default=True,
                   help="Paid final builds: unlock preview blockers (default: locked previews)")
    p.add_argument("--limit", type=int, default=1, help="Leads to process (0 = all)")
    p.add_argument("--qa-threshold", type=int, default=80)
    p.add_argument("--max-qa-attempts", type=int, default=5)
    p.add_argument("--no-prod", dest="prod", action="store_false", default=True)
    p.add_argument("--current-output", default="current_leads.json")
    p.add_argument("--send-emails", action="store_true")
    p.add_argument("--reuse", dest="skip_scrape", action="store_true",
                   help="Reuse existing businesses.json instead of scraping")
    p.add_argument("--businesses", default="businesses.json")
    p.add_argument("--reply", default=None,
                   help="Inbound client reply text (triggers Bot 10 feedback loop)")
    p.add_argument("--reply-file", default=None,
                   help="Path to file containing the inbound reply (alt. to --reply)")
    p.add_argument("--reply-lead", default=None,
                   help="lead_id the reply belongs to (required with --reply/--reply-file)")
    p.add_argument("--watch", action="store_true",
                   help="Run continuously: new leads + inbox replies, every --interval")
    p.add_argument("--interval", type=int, default=3600,
                   help="Seconds between watch cycles (default: 3600)")
    p.add_argument("--no-inbox", dest="poll_inbox", action="store_false", default=True,
                   help="Watch without polling the inbox")
    p.add_argument("--history", default="email_history.json")
    p.add_argument("--no-notify", dest="notify", action="store_false", default=True,
                   help="Don't email owner updates (default: notify)")
    p.add_argument("--notify-to", default=None,
                   help="Owner update recipient (default: AGENCY_NOTIFY_TO or nikhilmahankali56@gmail.com)")
    return p.parse_args(argv)


def _run_kwargs(_a) -> dict:
    return {"queries": _a.queries or None, "max_per_query": _a.max_per_query,
            "threshold": _a.threshold, "output_dir": _a.output_dir,
            "force": _a.force, "no_opencode": _a.no_opencode, "limit": _a.limit,
            "qa_threshold": _a.qa_threshold, "max_qa_attempts": _a.max_qa_attempts,
            "prod": _a.prod, "current_output": _a.current_output,
            "send_emails": _a.send_emails, "skip_scrape": _a.skip_scrape,
            "businesses": _a.businesses, "history": _a.history,
            "notify": _a.notify, "notify_to": _a.notify_to,
            "preview": _a.preview}


if __name__ == "__main__":
    _a = parse_args(sys.argv[1:])
    if _a.watch:
        watch(interval=_a.interval, poll_inbox=_a.poll_inbox, **_run_kwargs(_a))
        raise SystemExit(0)
    if _a.reply is not None or _a.reply_file is not None:
        _incoming = _a.reply if _a.reply is not None else _a.reply_file
        try:
            _fb = handle_reply(_incoming, lead=_a.reply_lead, leads="target_leads.json",
                               output_dir=_a.output_dir, no_opencode=_a.no_opencode,
                               qa_threshold=_a.qa_threshold,
                               max_qa_attempts=_a.max_qa_attempts, prod=_a.prod,
                               current_output=_a.current_output,
                               send_emails=_a.send_emails, history=_a.history,
                               notify=_a.notify, notify_to=_a.notify_to)
        except RuntimeError as e:
            print(f"[orchestrator] ERROR: {e}", file=sys.stderr)
            raise SystemExit(1)
        print(f"[orchestrator] reply handled -> {_fb['action']}", flush=True)
        raise SystemExit(0)
    try:
        final = run(**_run_kwargs(_a))
    except RuntimeError as e:
        print(f"[orchestrator] ERROR: {e}", file=sys.stderr)
        raise SystemExit(1)
    print(f"[orchestrator] done -> {final}", flush=True)
