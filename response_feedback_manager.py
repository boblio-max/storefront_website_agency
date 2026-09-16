"""
response_feedback_manager.py (Bot 10) — Read the reply, decide what happens next.

Input: incoming business email + the business's complete email history.

Flow:
    Incoming email → identify business (lead_id) → load conversation
    history → analyze response →
      ┌───────────────┬───────────────┬───────────────┐
      │ Wants changes │ Interested    │ Not interested │
      └───────┬───────┴───────┬───────┴───────────────┘
              ↓               ↓
           BOT 5             sales
              ↓
           BOT 6 → BOT 7 → updated preview → BOT 9 (contextual email)

A revision request becomes structured feedback, e.g.
    {"lead_id": "lead_00421", "action": "website_revision",
     "requested_changes": ["Change the homepage color scheme to blue",
       "Add Saturday business hours",
       "Make the booking button more prominent"]}

Bot 5 then builds the next version from that file (--feedback).

Usage:
    python response_feedback_manager.py --incoming "Change the color to blue, add Saturday hours" --lead lead_00001
    python response_feedback_manager.py --incoming reply.json --lead lead_00001 --output feedback.json
    python response_feedback_manager.py --incoming reply.json --from-email joe@example.com
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

try:  # Windows consoles default to cp1252; keep unicode output from crashing
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

CHANGE_HINTS = re.compile(
    r"chang|updat|revis|edit|fix|adjust|tweak|instead|rather|prefer|add|remove|"
    r"replac|color|colour|blue|red|green|font|logo|photo|image|hour|schedule|"
    r"button|bigger|smaller|darker|lighter|move|section| headline|title", re.I)
INTERESTED_HINTS = re.compile(
    r"\b(interested|love it|looks great|let'?s do it|sign me up|how much|pricing|"
    r"call me|meet|demo|next step|when can|ready to|go ahead|approve)\b", re.I)
NOT_INTERESTED_HINTS = re.compile(
    r"\b(not interested|no thanks|no thank|pass|don'?t (call|email|contact)|"
    r"too expensive|already have|not now|stop|leave me alone)\b", re.I)
UNSUB_HINTS = re.compile(r"unsubscribe|remove me|stop emailing|opt.?out", re.I)


def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def read_incoming(raw: str) -> dict:
    """Accept a JSON file path, a JSON string, or plain text."""
    p = Path(raw)
    if p.exists() and p.is_file():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                d.setdefault("body", d.get("body") or d.get("text") or "")
                return d
        except json.JSONDecodeError:
            return {"body": p.read_text(encoding="utf-8")}
    try:
        d = json.loads(raw)
        if isinstance(d, dict):
            d.setdefault("body", d.get("body") or d.get("text") or "")
            return d
    except (json.JSONDecodeError, TypeError):
        pass
    return {"body": raw}


def load_list(path: Path) -> list:
    if not path.exists():
        return []
    try:
        r = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return r if isinstance(r, list) else [r]


def identify_lead(args, incoming: dict) -> str | None:
    if args.lead:
        return args.lead
    if incoming.get("lead_id"):
        return incoming["lead_id"]
    sender = (args.from_email or incoming.get("from") or incoming.get("email") or "").lower()
    if sender:
        for src in (load_list(Path(args.current)), load_list(Path(args.history))):
            for r in src:
                if isinstance(r, dict) and sender in str(r.get("email") or "").lower():
                    return r.get("lead_id")
    return None


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    return [p.strip(" -•\t\"'") for p in parts if p.strip()]


def extract_changes(body: str) -> list[str]:
    changes = []
    for s in split_sentences(body):
        if CHANGE_HINTS.search(s) and 8 < len(s) < 300:
            polished = s[0].upper() + s[1:] if s else s
            changes.append(polished.rstrip("."))
        if len(changes) >= 10:
            break
    return changes


def classify(body: str) -> tuple[str, str, str]:
    """Return (response_type, action, route)."""
    t = body or ""
    if UNSUB_HINTS.search(t):
        return ("unsubscribe", "opt_out", "suppress_future_outreach")
    if NOT_INTERESTED_HINTS.search(t):
        return ("not_interested", "opt_out", "sales_no_further_action")
    if CHANGE_HINTS.search(t):
        return ("wants_changes", "website_revision", "BOT5 → BOT6 → BOT7 → BOT9")
    if INTERESTED_HINTS.search(t):
        return ("interested", "sales_followup", "sales → BOT9 contextual email")
    if "?" in t or re.search(r"\b(how|what|when|price|cost|long)\b", t, re.I):
        return ("question", "answer_question", "BOT9 contextual email")
    return ("unclear", "needs_review", "human_review → BOT9")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Bot 10: classify reply → route + structured feedback")
    p.add_argument("--incoming", default=None, help="Reply text, JSON string, or path to JSON/text file (required)")
    p.add_argument("--lead", default=None)
    p.add_argument("--from-email", default=None)
    p.add_argument("--history", default="email_history.json")
    p.add_argument("--current", default="current_leads.json")
    p.add_argument("--output", "-o", default=None, help="Write feedback JSON here")
    p.add_argument("--record", action="store_true", help="Append incoming msg to history as direction=incoming")
    p.add_argument("--poll", action="store_true", help="Poll IMAP inbox for unseen replies instead of --incoming")
    p.add_argument("--limit", type=int, default=20, help="Max inbox messages per poll")
    return p.parse_args(argv)


def imap_config() -> dict | None:
    """IMAP inbox from env; None when not configured."""
    import os as _os
    host = _os.environ.get("AGENCY_IMAP_HOST", "").strip()
    if not host:
        return None
    return {
        "host": host,
        "port": int(_os.environ.get("AGENCY_IMAP_PORT", "993")),
        "user": _os.environ.get("AGENCY_IMAP_USER", "").strip(),
        "password": _os.environ.get("AGENCY_IMAP_PASS", ""),
        "mailbox": _os.environ.get("AGENCY_IMAP_MAILBOX", "INBOX"),
    }


def _decode_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if ctype == "text/plain" and "attachment" not in disp:
                try:
                    return part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    continue
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    html = part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace")
                    return re.sub(r"<[^>]+>", " ", html)
                except Exception:  # noqa: BLE001
                    continue
        return ""
    try:
        payload = msg.get_payload(decode=True)
        if isinstance(payload, bytes):
            return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
        return str(payload or "")
    except Exception:  # noqa: BLE001
        return ""


def poll_inbox(current: str = "current_leads.json",
               history: str = "email_history.json",
               limit: int = 20) -> list[dict]:
    """Fetch unseen inbox replies via IMAP (BODY.PEEK — nothing marked seen).

    Returns [{uid, lead_id (or None), from, subject, body, message_id}].
    Raises RuntimeError when IMAP is unconfigured or unreachable.
    """
    import imaplib
    import email as _email
    import email.utils as _eu
    cfg = imap_config()
    if cfg is None:
        raise RuntimeError("IMAP not configured "
                           "(AGENCY_IMAP_HOST/PORT/USER/PASS)")
    known: dict[str, str] = {}
    for src in (load_list(Path(current)), load_list(Path(history))):
        for r in src:
            if isinstance(r, dict) and r.get("email") and r.get("lead_id"):
                known[str(r["email"]).lower()] = str(r["lead_id"])
    try:
        box = imaplib.IMAP4_SSL(cfg["host"], cfg["port"])
        box.login(cfg["user"], cfg["password"])
        box.select(cfg["mailbox"], readonly=True)
        _, data = box.search(None, "UNSEEN")
        uids = (data[0] or b"").split()[:max(1, limit)]
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"IMAP poll failed: {e}")
    out: list[dict] = []
    try:
        for uid in uids:
            try:
                _, fetched = box.fetch(uid, "(BODY.PEEK[])")
            except Exception:  # noqa: BLE001
                continue
            raw = fetched[0][1] if fetched and fetched[0] else b""
            try:
                msg = _email.message_from_bytes(raw)
            except Exception:  # noqa: BLE001
                continue
            sender = _eu.parseaddr(str(msg.get("From") or ""))[1].lower()
            subject = str(msg.get("Subject") or "")
            body = _decode_body(msg).strip()
            mid = str(msg.get("Message-ID") or f"uid_{uid.decode(errors='replace')}")
            out.append({"uid": uid.decode(errors="replace"),
                        "lead_id": known.get(sender),
                        "from": sender, "subject": subject,
                        "body": body or subject, "message_id": mid})
    finally:
        try:
            box.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            box.logout()
        except Exception:  # noqa: BLE001
            pass
    return out


def mark_seen(uids: list[str]) -> None:
    """Flag inbox messages as \\Seen after handling. Raises RuntimeError."""
    import imaplib
    cfg = imap_config()
    if cfg is None:
        raise RuntimeError("IMAP not configured")
    try:
        box = imaplib.IMAP4_SSL(cfg["host"], cfg["port"])
        box.login(cfg["user"], cfg["password"])
        box.select(cfg["mailbox"])
        for uid in uids:
            box.store(uid, "+FLAGS", "\\Seen")
        box.close()
        box.logout()
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"IMAP mark-seen failed: {e}")


def main(argv=None, **kwargs) -> str | dict | int:
    """Classify a client reply. Returns the feedback output path (str) when --output
    is given, otherwise the feedback dict; int exit code on error.

    Orchestrator use: ``main(incoming="...", lead="lead_00001", output="feedback.json")``
    — any keyword matching an argparse option overrides the default. A bare main()
    call uses defaults (sys.argv is only used via the CLI).
    """
    if argv is None:
        # Plain main() uses defaults (never sys.argv); the CLI passes
        # sys.argv[1:] explicitly via the __main__ block below.
        argv = []
    args = parse_args(argv)
    for _k, _v in kwargs.items():
        if not hasattr(args, _k):
            raise TypeError(f"response_feedback_manager.main() got an unexpected option {_k!r}")
        setattr(args, _k, _v)
    if not args.incoming and not args.poll:
        print("[response_feedback_manager] ERROR: --incoming is required (or --poll)",
              file=sys.stderr)
        return 2
    if args.poll and not args.incoming:
        try:
            found = poll_inbox(current=args.current, history=args.history,
                               limit=args.limit)
        except RuntimeError as e:
            print(f"[response_feedback_manager] ERROR: {e}", file=sys.stderr)
            return 1
        print(f"[response_feedback_manager] {len(found)} unseen message(s)", flush=True)
        for m in found:
            print(f"  uid={m['uid']} lead={m['lead_id'] or '?'} "
                  f"from={m['from']} subj={m['subject'][:60]}", flush=True)
        return found
    incoming = read_incoming(args.incoming)
    body = str(incoming.get("body") or "")
    if not body.strip():
        print("[response_feedback_manager] ERROR: empty incoming email", file=sys.stderr)
        return 2
    lead_id = identify_lead(args, incoming)
    if not lead_id:
        print("[response_feedback_manager] ERROR: could not identify lead "
              "(pass --lead or --from-email)", file=sys.stderr)
        return 2

    history = [m for m in load_list(Path(args.history))
               if isinstance(m, dict) and m.get("lead_id") == lead_id]
    rtype, action, route = classify(body)
    changes = extract_changes(body) if action == "website_revision" else []

    feedback = {"lead_id": lead_id, "action": action, "response_type": rtype,
                "requested_changes": changes,
                "summary": f"Client reply classified as {rtype} "
                           f"({len(history)} prior messages).",
                "route": route, "analyzed_at": utc_now_iso(),
                "incoming_excerpt": body[:500]}

    if args.record:
        hp = Path(args.history)
        all_hist = load_list(hp)
        all_hist.append({"message_id": incoming.get("message_id") or f"in_{lead_id}",
                         "lead_id": lead_id, "direction": "incoming",
                         "timestamp": incoming.get("timestamp") or utc_now_iso(),
                         "subject": incoming.get("subject") or "(reply)",
                         "body": body,
                         "preview_url": incoming.get("preview_url")})
        hp.write_text(json.dumps(all_hist, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[response_feedback_manager] recorded incoming → {hp}", flush=True)

    if args.output:
        Path(args.output).write_text(json.dumps(feedback, ensure_ascii=False, indent=2) + "\n",
                                      encoding="utf-8")
        print(f"[response_feedback_manager] feedback -> {args.output}", flush=True)
        return str(Path(args.output))
    print(json.dumps(feedback, ensure_ascii=False, indent=2))
    print(f"[response_feedback_manager] {lead_id}: {rtype} -> {action} | route: {route}", flush=True)
    return feedback


if __name__ == "__main__":
    _rc = main(sys.argv[1:])
    raise SystemExit(_rc if isinstance(_rc, int) else 0)
