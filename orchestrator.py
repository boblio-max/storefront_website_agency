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
import time as _time
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

try:
    import email_finder as ef
except ImportError:
    ef = None

# Every entry point loads .env so gh/SMTP/wallet guards see the same env
# even when the operator never exported variables in the shell.
try:
    from dotenv import load_dotenv as _dotenv
    try:
        _dotenv(dotenv_path=Path(__file__).resolve().parent / ".env",
                override=False)
    except Exception:  # noqa: BLE001
        pass
except ImportError:
    pass
try:
    from agency_config import load_config as _load_cfg, mark_stage as _mark_stage
except ImportError:
    _load_cfg = None

    def _mark_stage(*a, **k):
        return {}
def _require(result, stage: str):
    """Unwrap a bot return: artifact passes through, int exit code raises."""
    if isinstance(result, int):
        raise RuntimeError(f"{stage} failed with exit code {result}")
    return result


def _stage(t0: float, label: str) -> float:
    """Heartbeat: timestamped progress line so long autonomous runs never
    look hung. Returns a fresh timestamp (stages chain)."""
    now = _time.monotonic()
    print(f"[orchestrator] {label} (t+{now - t0:.0f}s)", flush=True)
    return now


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
    """Lead IDs (and site slugs) with a recorded LIVE deployment.

    Local-only previews (status == "local_preview" or no http preview_url)
    are deliberately excluded: watch mode + --skip-deployed must retry them
    until they are actually live, never skip them forever.
    """
    try:
        raw = json.loads(Path(record_file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    records = raw if isinstance(raw, list) else [raw]
    out: set[str] = set()
    for r in records:
        if not isinstance(r, dict):
            continue
        if r.get("status") == "local_preview":
            continue
        if not str(r.get("preview_url") or "").startswith("http"):
            continue
        for k in ("lead_id", "site"):
            if r.get(k):
                out.add(str(r.get(k)))
    return out


def _is_paid(lid: str, ledger: str | Path = "payments.json") -> bool:
    """True when Bot 11 records a paid invoice for lid. Never raises."""
    try:
        import payment_manager as pm
        return bool(pm.is_paid(ledger, lid))
    except Exception:  # noqa: BLE001
        return False


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
        require_opencode: bool = False,
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
        preview: bool = True,
        require_email: bool = False,
        enrich_emails: bool = True,
        allow_local: bool = True,
        send_followups: bool = False,
        no_reviewer: bool = False,
        review_timeout: int = 90) -> str:
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

    # --reuse fast path: prior classification still on disk and no reason
    # to re-fetch 50+ live sites. Use it unless --force forces reclassification.
    if skip_scrape and not force and Path("target_leads.json").exists():
        leads_path = "target_leads.json"
        print("[orchestrator] reusing target_leads.json (skip re-classify; "
              "use --force to re-fetch site scores)", flush=True)
    else:
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

        # Bot 2.5: enrich phone-only leads with verified website emails BEFORE
        # scoring (an email is +25 opportunity points). Never fails the run.
        if enrich_emails and ef is not None:
            for _p in (without_path, bad_path):
                try:
                    ef.main(input=_p, output=_p)
                except Exception as _e:  # noqa: BLE001
                    print(f"[orchestrator] email enrichment skipped for {_p}: {_e}",
                          flush=True)
        elif enrich_emails:
            print("[orchestrator] email_finder unavailable — skipping enrichment",
                  flush=True)

        # Bot 4: merge + rank -> target_leads.json
        leads_path = _require(
            lp.main(without=without_path, bad=bad_path, output="target_leads.json",
                    require_email=require_email),
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
                          require_opencode, max_qa_attempts, qa_threshold, prod,
                          current_output, send_emails, history, notify, notify_to,
                          preview, allow_local, no_reviewer, review_timeout)
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
    if send_followups:
        try:
            send_followup_batch(current=current_output, history=history,
                                send_emails=send_emails)
        except RuntimeError as e:
            print(f"[orchestrator] follow-ups skipped: {e}", flush=True)
    # Seamless closer: paid leads automatically get their unlocked --final
    # rebuild + redeploy + delivery email, every run. No manual step.
    try:
        deliver_finals(leads_path=leads_path, output_dir=output_dir,
                       current_output=current_output, history=history,
                       send_emails=send_emails, notify=notify,
                       notify_to=notify_to, no_opencode=no_opencode,
                       require_opencode=require_opencode,
                       qa_threshold=qa_threshold,
                       max_qa_attempts=max_qa_attempts, prod=prod,
                       no_reviewer=no_reviewer,
                       review_timeout=review_timeout)
    except RuntimeError as e:
        print(f"[orchestrator] final delivery skipped: {e}", flush=True)
    return leads_path


def deliver_finals(leads_path: str = "target_leads.json",
                   output_dir: str = "generated_sites",
                   current_output: str = "current_leads.json",
                   history: str = "email_history.json",
                   send_emails: bool = False,
                   notify: bool = True,
                   notify_to: str | None = None,
                   no_opencode: bool = False,
                   require_opencode: bool = False,
                   qa_threshold: int = 80,
                   max_qa_attempts: int = 5,
                   prod: bool = True,
                   no_reviewer: bool = False,
                   review_timeout: int = 90) -> int:
    """Rebuild + redeploy + email every PAID lead whose site is still locked.

    Runs automatically at the end of run() and inside every watch cycle, so
    money-in → live-site-out needs no operator step. Returns count delivered.
    """
    try:
        raw = json.loads(Path(leads_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    items = raw if isinstance(raw, list) else raw.get("leads", [])
    delivered = 0
    for entry in items:
        if not isinstance(entry, dict) or not entry.get("lead_id"):
            continue
        lid = entry["lead_id"]
        try:
            from agency_config import stage_of
            stage = stage_of(lid)
        except Exception:  # noqa: BLE001
            stage = None
        if stage == "final_delivered":
            continue
        if not _is_paid(lid):
            continue
        # Skip if the live site is already unlocked (no preview banner).
        try:
            _dir, _slug = wg.site_dir_for(entry, Path(output_dir))
            idx = Path(_dir) / "index.html"
            if idx.exists():
                html = idx.read_text(encoding="utf-8", errors="replace")
                if "sw-preview-banner" not in html and "Demo preview" not in html:
                    try:
                        _mark_stage(lid, "final_delivered", {"live": True})
                    except Exception:  # noqa: BLE001
                        pass
                    continue
        except Exception:  # noqa: BLE001
            pass
        print(f"[orchestrator] {lid}: paid — delivering unlocked final site...",
              flush=True)
        try:
            site = _require(
                wg.main(lead_data=entry, output_dir=output_dir, force=True,
                        no_opencode=no_opencode,
                        require_opencode=require_opencode, preview=False),
                "website_generator",
            )
            qa_res = qb.main(site, threshold=qa_threshold,
                             no_reviewer=no_reviewer,
                             review_timeout=review_timeout)
            if qa_res == 1:
                print(f"[orchestrator] {lid}: final QA failed — keeping preview live",
                      flush=True)
                continue
            _require(qa_res, "qa_bot")
            rc = dmgr.main(path_to_file=site, prod=prod, allow_local=False)
            if rc != 0:
                print(f"[orchestrator] {lid}: final redeploy failed (exit {rc})",
                      flush=True)
                continue
            preview_url = _latest_preview("deployments/deployments.json", lid)
            try:
                _mark_stage(lid, "final_delivered",
                             {"preview_url": preview_url or site, "live": True})
            except Exception:  # noqa: BLE001
                pass
            try:
                _require(clg.main(leads=leads_path, lead=lid,
                                  preview_url=preview_url or None,
                                  output=current_output),
                         "current_lead_generator")
            except RuntimeError as e:
                print(f"[orchestrator] {lid}: outreach refresh skipped: {e}",
                      flush=True)
            email_res = eg.main(lead=lid, current=current_output,
                                send=send_emails,
                                preview_url=preview_url or None,
                                history=history,
                                purpose="revision_delivery")
            if not isinstance(email_res, int):
                delivered += 1
                print(f"[orchestrator] {lid}: final delivered "
                      f"({'sent' if send_emails else 'drafted'})", flush=True)
                if notify:
                    notify_owner(f"final delivered: {lid}",
                                 f"Paid site unlocked + redeployed for {lid}.\n"
                                 f"Preview: {preview_url}.", notify_to)
        except RuntimeError as e:
            print(f"[orchestrator] {lid}: final delivery failed ({e})",
                  flush=True)
    if delivered:
        print(f"[orchestrator] finals delivered: {delivered}", flush=True)
    return delivered


def _process_lead(entry: dict, lid: str, leads_path: str, output_dir: str,
                  force: bool, no_opencode: bool, require_opencode: bool,
                  max_qa_attempts: int,
                  qa_threshold: int, prod: bool, current_output: str,
                  send_emails: bool, history: str,
                  notify: bool, notify_to: str | None,
                  preview: bool = True, allow_local: bool = True,
                  no_reviewer: bool = False,
                  review_timeout: int = 90) -> None:
    """Generate → QA → deploy → outreach for one lead. Raises RuntimeError."""
    # Payment gate: --final (preview=False) unlocks only after Bot 11 is paid.
    # Without payment the build stays a locked preview — never give away the
    # unlocked site by accident.
    if not preview and not _is_paid(lid):
        print(f"[orchestrator] {lid}: --final requested but unpaid — "
              f"building locked preview instead (collect payment first)",
              flush=True)
        preview = True
    t0 = _time.monotonic()
    print(f"[orchestrator] {lid}: Bot 5 generate...", flush=True)
    website_path = _require(
        wg.main(lead_data=entry, output_dir=output_dir,
                force=force, no_opencode=no_opencode,
                require_opencode=require_opencode, preview=preview),
        "website_generator",
    )
    t0 = _stage(t0, f"{lid}: Bot 5 done → Bot 6 QA")

    # Bot 6: loop until QA passes, then print good.
    # Retries force a regenerate so QA re-checks fresh output.
    for attempt in range(1, max_qa_attempts + 1):
        if attempt > 1:
            website_path = _require(
                wg.main(lead_data=entry, output_dir=output_dir,
                        force=True, no_opencode=no_opencode,
                        require_opencode=require_opencode, preview=preview),
                "website_generator",
            )
        qa_res = qb.main(website_path, threshold=qa_threshold,
                         no_reviewer=no_reviewer,
                         review_timeout=review_timeout)
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

    rc = dmgr.main(path_to_file=website_path, prod=prod,
                   allow_local=allow_local)
    if rc != 0:
        # No local fallback (operator opted out) — lead fails loudly.
        raise RuntimeError(f"deployment_manager failed for {lid} (exit {rc})")
    t0 = _stage(t0, f"{lid}: Bot 6 QA good → Bot 7 deployed")
    try:
        _mark_stage(lid, "qa_passed", {"site": website_path})
    except Exception:  # noqa: BLE001
        pass

    # Lead secured: site is live. Tell the owner.
    name = entry.get("name", lid)
    preview_url = _latest_preview("deployments/deployments.json", lid)
    live = bool(preview_url.startswith("http")) if preview_url else False
    if not live:
        print(f"[orchestrator] {lid}: no live URL (local preview kept) — "
              f"outreach will draft without a public link; redeploy later "
              f"with --reuse after fixing deploy (gh auth / vercel login)",
              flush=True)
    try:
        _mark_stage(lid, "deployed" if live else "site_built",
                    {"preview_url": preview_url or website_path,
                     "live": live, "site": website_path,
                     # Clear the redeploy flag on live success so the next
                     # watch cycle stops retrying this lead.
                     **({} if not live else {"needs_redeploy": False})})
    except Exception:  # noqa: BLE001
        pass
    if notify:
        notify_owner(f"lead secured: {name} is {'live' if live else 'built (local preview)'}",
                     f"Business: {name} ({lid})\n"
                     f"Preview: {preview_url or website_path}\n"
                     f"Live: {live}\n"
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
                            preview_url=preview_url or None, history=history,
                            no_opencode=no_opencode)
    if email_res == 1:
        raise RuntimeError(f"email_generator failed for {lid} (exit 1)")
    if isinstance(email_res, int):
        print(f"[orchestrator] email skipped for {lid} (exit {email_res}: "
              f"no verified address, opt-out suppression, or no SMTP transport "
              f"— set AGENCY_SMTP_PASS/AGENCY_IMAP_PASS app passwords to enable mail)",
              flush=True)
    else:
        print(f"[orchestrator] email {'sent' if send_emails else 'drafted'} for {lid}",
              flush=True)
        if send_emails and notify:
            notify_owner(f"outreach sent to {name}",
                         f"Initial outreach email sent for {name} ({lid}).\n"
                         f"Preview: {preview_url}.",
                         notify_to)
     
 
def _send_payment_request(lid: str, current_output: str, history: str,
                          send_emails: bool, notify: bool,
                          notify_to: str | None) -> None:
    """Interested reply -> idempotent invoice + payment-details email."""
    try:
        import payment_manager as pm
    except ImportError:
        print("[orchestrator] payment_manager unavailable — invoice skipped",
              flush=True)
        return
    try:
        cfg = _load_cfg() if _load_cfg else {}
    except Exception:  # noqa: BLE001
        cfg = {}
    amount = 29900
    currency = "USD"
    try:
        from agency_config import price_cents
        amount = price_cents(cfg if isinstance(cfg, dict) else None)
        currency = (cfg or {}).get("currency", "USD")
        methods = list((cfg or {}).get("payment_methods",
                                       ["card", "bank", "crypto", "cash"]))
    except Exception:  # noqa: BLE001
        methods = ["card", "bank", "crypto", "cash"]
    try:
        rec = pm.create_invoice(lid, amount, currency, methods)
        print(f"[orchestrator] invoice {rec['invoice_id']} open for {lid} "
              f"({amount} {currency})", flush=True)
    except (ValueError, RuntimeError) as e:
        print(f"[orchestrator] invoice skipped for {lid}: {e}", flush=True)
        return
    try:
        _mark_stage(lid, "replied", {"response_type": "interested",
                                     "invoice_id": rec["invoice_id"]})
    except Exception:  # noqa: BLE001
        pass
    email_res = eg.main(lead=lid, current=current_output, send=send_emails,
                        history=history, purpose="payment_request")
    if isinstance(email_res, int):
        print(f"[orchestrator] payment email skipped for {lid} (exit "
              f"{email_res})", flush=True)
    else:
        print(f"[orchestrator] payment email {'sent' if send_emails else 'drafted'} "
              f"for {lid}", flush=True)
    if notify:
        notify_owner(f"payment requested: {lid}",
                     f"Invoice {rec['invoice_id']} ({amount} {currency}) for {lid}.\n"
                     f"Payment email: {'sent' if send_emails else 'draft-only'}.",
                     notify_to)


def send_followup_batch(current: str = "current_leads.json",
                        history: str = "email_history.json",
                        send_emails: bool = False) -> int:
    """Send due follow-ups (one purpose per lead per run). Returns count."""
    hist = eg.load_history(history)
    try:
        cur = json.loads(Path(current).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print(f"[orchestrator] follow-ups skipped: {current} not found",
              flush=True)
        return 0
    items = cur if isinstance(cur, list) else [cur]
    sent = 0
    for item in items:
        if not isinstance(item, dict) or not item.get("lead_id"):
            continue
        lid = item["lead_id"]
        try:
            purpose = eg.followup_due(hist, lid)
        except AttributeError:
            purpose = None
        if not purpose:
            continue
        res = eg.main(lead=lid, current=current, send=send_emails,
                      history=history, purpose=purpose)
        if isinstance(res, int) and res == 1:
            print(f"[orchestrator] follow-up failed for {lid}", flush=True)
            continue
        if not isinstance(res, int):
            sent += 1
            hist = eg.load_history(history)
            print(f"[orchestrator] follow-up {purpose} "
                  f"{'sent' if send_emails else 'drafted'} for {lid}", flush=True)
    print(f"[orchestrator] follow-ups: {sent} due", flush=True)
    return sent


def handle_reply(incoming: str, lead: str | None = None,
                 leads: str = "target_leads.json",
                  output_dir: str = "generated_sites",
                  no_opencode: bool = False,
                  require_opencode: bool = False,
                  qa_threshold: int = 80,
                 max_qa_attempts: int = 5,
                 prod: bool = False,
                  current_output: str = "current_leads.json",
                  send_emails: bool = False,
                  history: str = "email_history.json",
                  notify: bool = True,
                  notify_to: str | None = None,
                  no_reviewer: bool = False,
                  review_timeout: int = 90) -> dict:
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
        if feedback.get("response_type") == "interested":
            _send_payment_request(feedback["lead_id"], current_output,
                                  history, send_emails, notify, notify_to)
        else:
            try:
                _mark_stage(feedback["lead_id"], "replied",
                            {"response_type": feedback.get("response_type")})
            except Exception:  # noqa: BLE001
                pass
        return feedback  # sales / human review route — nothing to rebuild

    lid = feedback["lead_id"]
    raw = json.loads(Path(leads).read_text(encoding="utf-8"))
    items = raw if isinstance(raw, list) else raw.get("leads", [])
    entry = next((e for e in items
                  if isinstance(e, dict) and e.get("lead_id") == lid), None)
    if entry is None:
        raise RuntimeError(f"handle_reply: {lid} not in {leads}")

    # Client-sent photos (email attachments saved by Bot 10, or files
    # dropped into client_photos/<lead_id>/) join the site before rebuild.
    # collect_business_photos picks up photo-*.* automatically.
    adopted: list[str] = []
    try:
        _dir, _slug = wg.site_dir_for(entry, Path(output_dir))
        adopted = wg.adopt_client_photos(lid, _dir)
    except Exception:  # noqa: BLE001
        adopted = []
    if adopted:
        feedback = {**feedback, "client_photos": adopted}
        (feedback.setdefault("requested_changes", [])
            .append(f"Use the {len(adopted)} real client photo(s) just added "
                    f"({', '.join(adopted)}) in hero/about/gallery spots."))

    site = _require(
        wg.main(lead_data=entry, output_dir=output_dir, force=True,
                no_opencode=no_opencode, require_opencode=require_opencode,
                feedback=feedback, preview=True),
        "website_generator",
    )
    for attempt in range(1, max_qa_attempts + 1):
        qa_res = qb.main(site, threshold=qa_threshold,
                         no_reviewer=no_reviewer,
                         review_timeout=review_timeout)
        if isinstance(qa_res, str):
            print(f"[orchestrator] good — {lid} revision passed QA", flush=True)
            break
        if qa_res == 1:
            print(f"[orchestrator] revision QA failed for {lid} "
                  f"(attempt {attempt}/{max_qa_attempts}), regenerating...", flush=True)
            site = _require(
                wg.main(lead_data=entry, output_dir=output_dir, force=True,
                        no_opencode=no_opencode, require_opencode=require_opencode,
                        feedback=feedback, preview=True),
                "website_generator",
            )
            continue
        _require(qa_res, "qa_bot")
    else:
        raise RuntimeError(
            f"qa_bot: {lid} revision still failing after {max_qa_attempts} attempts")

    rc = dmgr.main(path_to_file=site, prod=prod, allow_local=True)
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


def watch(interval: int = 3600, poll_inbox: bool = True,
          inbox_interval: int = 300, **run_kwargs) -> None:
    """Run the agency continuously: new leads -> sites -> outreach, plus replies.

    Two clocks in one process (hands-free):
    - pipeline every `interval` seconds (slow: scrape/build/deploy, default 1h)
    - inbox every `inbox_interval` seconds (fast: Gmail replies, default 5m)

    Each inbox poll routes through handle_reply (revision requests rebuild +
    redeploy + redeliver; everything else routes to sales/human and the
    sender is marked seen). Standalone alternative: `py inbox_watcher.py
    --daemon` (same logic, no pipeline) + install_inbox_watcher.bat for a
    Windows Scheduled Task that survives reboots.
    Sleeps `interval` seconds between cycles; Ctrl-C stops cleanly.
    """
    import time as _time
    history = run_kwargs.get("history", "email_history.json")
    run_kwargs = {**run_kwargs, "skip_deployed": True}
    inbox_interval = max(60, int(inbox_interval or 300))
    print(f"[orchestrator] watch mode: pipeline every {interval}s, "
          f"inbox every {inbox_interval}s (Ctrl-C to stop)", flush=True)
    cycle = 0
    last_inbox = 0.0
    while True:
        cycle += 1
        print(f"[orchestrator] --- cycle {cycle} ---", flush=True)
        try:
            run(**run_kwargs)
        except RuntimeError as e:
            print(f"[orchestrator] cycle {cycle} pipeline: {e}", flush=True)
        # Fast inbox clock: poll every inbox_interval, not just once per
        # (slow) pipeline cycle. Loop catches up after long builds.
        if poll_inbox and (_time.monotonic() - last_inbox) >= inbox_interval:
            last_inbox = _time.monotonic()
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
                        require_opencode=run_kwargs.get("require_opencode", False),
                        qa_threshold=run_kwargs.get("qa_threshold", 80),
                        max_qa_attempts=run_kwargs.get("max_qa_attempts", 5),
                        prod=run_kwargs.get("prod", True),
                        current_output=run_kwargs.get("current_output",
                                                      "current_leads.json"),
                        send_emails=run_kwargs.get("send_emails", False),
                        history=history,
                        notify=run_kwargs.get("notify", True),
                        notify_to=run_kwargs.get("notify_to"),
                        no_reviewer=run_kwargs.get("no_reviewer", False),
                        review_timeout=run_kwargs.get("review_timeout", 90))
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
    p.add_argument("--require-opencode", action="store_true",
                   help="Fail loudly if OpenCode is unavailable instead of using the template engine")
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
    p.add_argument("--inbox-interval", type=int, default=300,
                   help="Seconds between Gmail inbox polls in --watch (default: 300). "
                        "Standalone alternative: py inbox_watcher.py --daemon")
    p.add_argument("--history", default="email_history.json")
    p.add_argument("--no-notify", dest="notify", action="store_false", default=True,
                   help="Don't email owner updates (default: notify)")
    p.add_argument("--notify-to", default=None,
                   help="Owner update recipient (default: AGENCY_NOTIFY_TO or nikhilmahankali56@gmail.com)")
    p.add_argument("--require-email", action="store_true",
                   help="Only target leads with a verified email (outreach-ready)")
    p.add_argument("--no-enrich-emails", dest="enrich_emails",
                   action="store_false", default=True,
                   help="Skip Bot 2.5 website email discovery")
    p.add_argument("--no-local-fallback", dest="allow_local",
                   action="store_false", default=True,
                   help="Fail hard on blocked deploys instead of keeping a local preview")
    p.add_argument("--followups", dest="send_followups", action="store_true",
                   help="Send due follow-up emails (schedule in agency_config.json)")
    p.add_argument("--skip-deployed", action="store_true", default=False,
                   help="Skip leads that already have a deployment record")
    p.add_argument("--no-reviewer", action="store_true",
                   help="Skip QA Gate 2 taste review (structural checks only; faster)")
    p.add_argument("--review-timeout", type=int, default=90,
                   help="Seconds for the QA reviewer instance (default: 90)")
    return p.parse_args(argv)


def _run_kwargs(_a) -> dict:
    return {"queries": _a.queries or None, "max_per_query": _a.max_per_query,
            "threshold": _a.threshold, "output_dir": _a.output_dir,
            "force": _a.force, "no_opencode": _a.no_opencode,
            "require_opencode": _a.require_opencode, "limit": _a.limit,
            "qa_threshold": _a.qa_threshold, "max_qa_attempts": _a.max_qa_attempts,
            "prod": _a.prod, "current_output": _a.current_output,
            "send_emails": _a.send_emails, "skip_scrape": _a.skip_scrape,
            "businesses": _a.businesses, "history": _a.history,
            "notify": _a.notify, "notify_to": _a.notify_to,
            "preview": _a.preview, "require_email": _a.require_email,
            "enrich_emails": _a.enrich_emails, "allow_local": _a.allow_local,
            "send_followups": _a.send_followups,
            "skip_deployed": _a.skip_deployed,
            "no_reviewer": _a.no_reviewer,
            "review_timeout": _a.review_timeout}


if __name__ == "__main__":
    _a = parse_args(sys.argv[1:])
    if _a.watch:
        watch(interval=_a.interval, poll_inbox=_a.poll_inbox,
              inbox_interval=_a.inbox_interval, **_run_kwargs(_a))
        raise SystemExit(0)
    if _a.reply is not None or _a.reply_file is not None:
        _incoming = _a.reply if _a.reply is not None else _a.reply_file
        try:
            _fb = handle_reply(_incoming, lead=_a.reply_lead, leads="target_leads.json",
                               output_dir=_a.output_dir, no_opencode=_a.no_opencode,
                               require_opencode=_a.require_opencode,
                               qa_threshold=_a.qa_threshold,
                               max_qa_attempts=_a.max_qa_attempts, prod=_a.prod,
                               current_output=_a.current_output,
                               send_emails=_a.send_emails, history=_a.history,
                               notify=_a.notify, notify_to=_a.notify_to,
                               no_reviewer=_a.no_reviewer,
                               review_timeout=_a.review_timeout)
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
