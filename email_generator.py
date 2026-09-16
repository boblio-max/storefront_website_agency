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
AGENCY_NAME = os.environ.get("AGENCY_NAME", "Storefront Web")


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
                     "website", "preview_url", "opportunity_score")}, ensure_ascii=False, indent=2),
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
        f"Sign off as {AGENCY_NAME} (the agency that built the preview).",
        "Keep it under 180 words. End with a clear next step (review the preview link).",
        "Reply in this exact format:\nSubject: <subject>\n\n<body>",
    ])


def template_email(biz: dict, history: list[dict], purpose: str) -> tuple[str, str]:
    name = biz.get("business_name") or "there"
    category = biz.get("category") or "your business"
    preview = biz.get("preview_url") or ""
    n = len(history)
    purpose_line = {
        "initial_outreach": (f"I built a free preview website for {name} "
                             f"({category}) — modern, mobile-friendly, with click-to-call "
                             f"and a quote form."),
        "follow_up": ("Following up on the free preview website I prepared — "
                      "wanted to make sure you saw it."),
        "revision_delivery": ("I've updated your preview website based on your feedback — "
                              "the new version is ready for review."),
    }.get(purpose, f"Quick note about the preview website I prepared for {name}.")
    subject = {"initial_outreach": f"Free preview website for {name}",
               "follow_up": f"Re: preview website for {name}",
               "revision_delivery": f"Updated preview for {name} — ready to review"}.get(
        purpose, f"Preview website for {name}")
    body = (f"Hi {name} team,\n\n{purpose_line}\n\n"
            + (f"You can view it here: {preview}\n\n" if preview else "")
            + ("Since we haven't connected yet, I'll keep this short: " if n == 0 and purpose == "initial_outreach" else "")
            + "if you like the direction, I can connect it to your domain and launch it this week.\n\n"
              "Worth a 2-minute look? Just reply and tell me what you'd change.\n\n"
              f"Best regards,\n{AGENCY_NAME}")
    return subject, body


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


def load_history(path: str = "email_history.json") -> list:
    h = load_json(Path(path), [])
    return h if isinstance(h, list) else []
def smtp_config() -> dict | None:
    """SMTP transport from env; None when not configured (draft-only mode)."""
    host = os.environ.get("AGENCY_SMTP_HOST", "").strip()
    if not host:
        return None
    return {
        "host": host,
        "port": int(os.environ.get("AGENCY_SMTP_PORT", "587")),
        "user": os.environ.get("AGENCY_SMTP_USER", "").strip(),
        "password": os.environ.get("AGENCY_SMTP_PASS", ""),
        "from": os.environ.get("AGENCY_FROM", "").strip()
                or os.environ.get("AGENCY_SMTP_USER", "").strip(),
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
            out = run_opencode_command(Path(args.workdir), prompt)
            subject, body = parse_opencode_output(out)
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
        cfg = smtp_config()
        if cfg is None:
            print("[email_generator] ERROR: --send needs SMTP config "
                  "(AGENCY_SMTP_HOST/PORT/USER/PASS + AGENCY_FROM); "
                  "not recorded as sent", file=sys.stderr)
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
