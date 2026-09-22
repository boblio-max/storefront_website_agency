"""
payment_manager.py (Bot 11) — Custom agency wallet.

Clients pay however they want; money settles into accounts only you can
withdraw from. This module never touches card numbers (PCI scope stays
with Stripe) and never holds crypto private keys (watch-only addresses).

How money flows:
  card   -> Stripe (secret key in env) -> automatic payout to your bank.
            Without a key, invoices still work; card shows "setup pending".
  bank   -> Your bank details (reference string in env, no account numbers
            in the repo). Client pays, you confirm with --mark-paid.
  crypto -> Your watch-only addresses (env JSON). Client sends, you confirm
            with --mark-paid (or a block-explorer tx hash as --txref).

Ledger: payments.json (list of invoice records). Amounts in integer cents.
An open invoice per lead is idempotent (re-invoicing returns the open one).
Orchestrator gating: is_paid(ledger, lead_id) -> rebuild with --final only
when True.

Env (all optional except for live money movement):
  AGENCY_DEFAULT_PRICE_CENTS  default invoice amount (default 29900 = $299)
  AGENCY_WALLET_CURRENCY      default currency (default USD)
  AGENCY_STRIPE_KEY           Stripe secret key (sk_...) — live card charges
  AGENCY_STRIPE_LINK_BASE     e.g. https://buy.stripe.com/xxx — manual link
  AGENCY_BANK_REF             public reference, e.g. "Chase ****1234"
  AGENCY_CRYPTO_ADDRESSES     JSON, e.g. {"BTC":"bc1...","ETH":"0x..."}

Usage:
  python payment_manager.py --invoice --lead lead_00001 --amount 49900
  python payment_manager.py --status --invoice inv_lead_00001_01
  python payment_manager.py --mark-paid --invoice inv_lead_00001_01 --method bank --txref "Zelle CHASE-99"
  python payment_manager.py --balance
  python payment_manager.py --payout-report
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

try:  # Windows consoles default to cp1252; keep unicode output from crashing
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

METHODS = ("card", "bank", "crypto")
LEDGER_DEFAULT = "payments.json"


def _load_env_file() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    try:
        load_dotenv(dotenv_path=Path(".env"), override=False)
    except Exception:  # noqa: BLE001
        pass


_load_env_file()


def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def default_price_cents() -> int:
    try:
        return int(os.environ.get("AGENCY_DEFAULT_PRICE_CENTS", "29900"))
    except (TypeError, ValueError):
        return 29900


def default_currency() -> str:
    return (os.environ.get("AGENCY_WALLET_CURRENCY", "USD").strip() or "USD").upper()


def crypto_addresses() -> dict:
    raw = os.environ.get("AGENCY_CRYPTO_ADDRESSES", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return {str(k).upper(): str(v) for k, v in data.items()
            if isinstance(v, str) and v.strip()}


def mask_secret(value: str) -> str:
    """Show only the last 4 chars of a secret (never log full keys)."""
    v = (value or "").strip()
    if len(v) <= 8:
        return "****"
    return "****" + v[-4:]


# ---------------------------------------------------------------- ledger

def load_ledger(path: str | Path = LEDGER_DEFAULT) -> list:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return raw if isinstance(raw, list) else []


def save_ledger(records: list, path: str | Path = LEDGER_DEFAULT) -> str:
    Path(path).write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    return str(path)


def _next_invoice_id(records: list, lead_id: str) -> str:
    n = sum(1 for r in records
            if isinstance(r, dict) and r.get("lead_id") == lead_id) + 1
    return f"inv_{lead_id}_{n:02d}"


def create_invoice(lead_id: str, amount_cents: int | None = None,
                   currency: str | None = None,
                   methods: list[str] | None = None,
                   ledger: str | Path = LEDGER_DEFAULT) -> dict:
    """Create (or return the existing open) invoice for a lead."""
    if not (lead_id or "").strip():
        raise ValueError("lead_id is required")
    amount = default_price_cents() if amount_cents is None else int(amount_cents)
    if amount <= 0:
        raise ValueError("amount must be positive cents")
    cur = (currency or default_currency()).upper()
    want = [m for m in (methods or list(METHODS)) if m in METHODS] or list(METHODS)
    records = load_ledger(ledger)
    for r in records:
        if (isinstance(r, dict) and r.get("lead_id") == lead_id
                and r.get("status") == "open" and r.get("currency") == cur):
            return r
    record = {
        "invoice_id": _next_invoice_id(records, lead_id),
        "lead_id": lead_id,
        "amount_cents": amount,
        "currency": cur,
        "amount_display": f"{amount / 100:.2f} {cur}",
        "methods": want,
        "status": "open",
        "created_at": utc_now_iso(),
        "paid_at": None,
        "method_used": None,
        "txref": None,
    }
    records.append(record)
    save_ledger(records, ledger)
    return record


def find_invoice(records: list, invoice_id: str) -> dict | None:
    for r in records:
        if isinstance(r, dict) and r.get("invoice_id") == invoice_id:
            return r
    return None


def mark_paid(invoice_id: str, method: str, txref: str = "",
              ledger: str | Path = LEDGER_DEFAULT) -> dict:
    """Record payment (you confirming receipt in your account)."""
    if method not in METHODS:
        raise ValueError(f"method must be one of {METHODS}")
    records = load_ledger(ledger)
    rec = find_invoice(records, invoice_id)
    if rec is None:
        raise LookupError(f"invoice not found: {invoice_id}")
    if rec.get("status") == "paid":
        return rec
    rec["status"] = "paid"
    rec["paid_at"] = utc_now_iso()
    rec["method_used"] = method
    rec["txref"] = (txref or "").strip()
    save_ledger(records, ledger)
    return rec


def void_invoice(invoice_id: str, ledger: str | Path = LEDGER_DEFAULT) -> dict:
    records = load_ledger(ledger)
    rec = find_invoice(records, invoice_id)
    if rec is None:
        raise LookupError(f"invoice not found: {invoice_id}")
    rec["status"] = "void"
    save_ledger(records, ledger)
    return rec


def is_paid(records_or_path: list | str | Path, lead_id: str) -> bool:
    """True when the lead has any paid invoice (orchestrator --final gate)."""
    records = (load_ledger(records_or_path) if isinstance(records_or_path, (str, Path))
               else records_or_path)
    return any(isinstance(r, dict) and r.get("lead_id") == lead_id
               and r.get("status") == "paid" for r in records)


def ledger_balance(ledger: str | Path = LEDGER_DEFAULT) -> dict:
    records = load_ledger(ledger)
    paid = [r for r in records if isinstance(r, dict) and r.get("status") == "paid"]
    by_method: dict[str, int] = {}
    for r in paid:
        by_method[r.get("method_used", "?")] = (
            by_method.get(r.get("method_used", "?"), 0) + int(r.get("amount_cents", 0)))
    return {
        "invoices": len(records),
        "paid_count": len(paid),
        "open_count": sum(1 for r in records
                          if isinstance(r, dict) and r.get("status") == "open"),
        "paid_cents": sum(int(r.get("amount_cents", 0)) for r in paid),
        "paid_by_method_cents": by_method,
        "currency": default_currency(),
    }


# ------------------------------------------------------- pay instructions

def stripe_card_link(invoice: dict) -> str | None:
    """Best-effort live Stripe Payment Link, else manual base link, else None.

    Never raises: invoicing must work even when Stripe is unconfigured.
    """
    base = os.environ.get("AGENCY_STRIPE_LINK_BASE", "").strip()
    key = os.environ.get("AGENCY_STRIPE_KEY", "").strip()
    ref = invoice["invoice_id"]
    if key:
        try:
            import requests
            resp = requests.post(
                "https://api.stripe.com/v1/payment_links",
                auth=(key, ""),
                data={
                    "line_items[0][price_data][currency]":
                        invoice["currency"].lower(),
                    "line_items[0][price_data][unit_amount]":
                        invoice["amount_cents"],
                    "line_items[0][price_data][product_data][name]":
                        f"Storefront website — {invoice['lead_id']}",
                    "line_items[0][quantity]": 1,
                    "metadata[invoice_id]": ref,
                },
                timeout=20)
            if resp.status_code in (200, 201):
                url = resp.json().get("url")
                if url:
                    return url
        except Exception:  # noqa: BLE001
            pass
    if base:
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}client_reference_id={ref}"
    return None


def pay_instructions(invoice: dict) -> str:
    """Client-facing 'pay however you want' block for one invoice."""
    lines = [f"Invoice {invoice['invoice_id']} — "
             f"{invoice['amount_display']} — methods: {', '.join(invoice['methods'])}"]
    if "card" in invoice["methods"]:
        link = stripe_card_link(invoice)
        if link:
            lines.append(f"  Card/bank: pay here: {link}")
        else:
            lines.append("  Card/bank: link pending setup — ask us and we'll send a secure link.")
    if "bank" in invoice["methods"]:
        ref = os.environ.get("AGENCY_BANK_REF", "").strip()
        lines.append(f"  Bank transfer: {ref or 'details on request'} "
                     f"(reference {invoice['invoice_id']})")
    if "crypto" in invoice["methods"]:
        addrs = crypto_addresses()
        if addrs:
            lines.append(f"  Crypto (send {invoice['amount_display']} worth, "
                         f"reference {invoice['invoice_id']}):")
            for coin, addr in sorted(addrs.items()):
                lines.append(f"    {coin}: {addr}")
        else:
            lines.append("  Crypto: address on request.")
    return "\n".join(lines)


def payout_report(ledger: str | Path = LEDGER_DEFAULT) -> str:
    """Where your money sits and how to withdraw it (read-only report)."""
    bal = ledger_balance(ledger)
    cur = bal["currency"]
    lines = [f"Wallet balance: {bal['paid_cents'] / 100:.2f} {cur} "
             f"across {bal['paid_count']} paid invoice(s) "
             f"({bal['open_count']} open)"]
    for method, cents in sorted(bal["paid_by_method_cents"].items()):
        where = {"card": "Stripe balance -> automatic payout to your bank",
                 "bank": "your bank account already",
                 "crypto": "your wallet addresses already"}.get(method, "see ledger")
        lines.append(f"  {method}: {cents / 100:.2f} {cur} — {where}")
    if os.environ.get("AGENCY_STRIPE_KEY", "").strip():
        lines.append(f"Stripe key: {mask_secret(os.environ['AGENCY_STRIPE_KEY'])} (payouts automatic)")
    addrs = crypto_addresses()
    if addrs:
        lines.append("Crypto addresses (watch-only, keys never stored here): "
                     + ", ".join(sorted(addrs)))
    return "\n".join(lines)


# ------------------------------------------------------------------- CLI

def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Bot 11: agency wallet (multi-method intake)")
    p.add_argument("--ledger", default=LEDGER_DEFAULT)
    p.add_argument("--invoice", action="store_true", help="Create (or reuse open) invoice")
    p.add_argument("--lead", default=None)
    p.add_argument("--amount", type=int, default=None, help="Amount in cents")
    p.add_argument("--currency", default=None)
    p.add_argument("--methods", default=",".join(METHODS),
                   help="Comma list: card,bank,crypto")
    p.add_argument("--status", action="store_true")
    p.add_argument("--mark-paid", action="store_true")
    p.add_argument("--void", action="store_true")
    p.add_argument("--balance", action="store_true")
    p.add_argument("--payout-report", action="store_true")
    p.add_argument("--is-paid", default=None, metavar="LEAD_ID")
    p.add_argument("--invoice-id", default=None, metavar="INVOICE_ID")
    p.add_argument("--method", default=None, choices=METHODS)
    p.add_argument("--txref", default="")
    return p.parse_args(argv)


def main(argv=None, **kwargs) -> str | dict | int:
    """Orchestrator-friendly entry: artifact path/dict on success, int exit."""
    if argv is None:
        argv = []
    args = parse_args(argv)
    for _k, _v in kwargs.items():
        if not hasattr(args, _k):
            raise TypeError(f"payment_manager.main() got an unexpected option {_k!r}")
        setattr(args, _k, _v)
    try:
        if args.invoice:
            if not args.lead:
                print("[payment_manager] ERROR: --lead is required", file=sys.stderr)
                return 2
            methods = [m.strip() for m in (args.methods or "").split(",") if m.strip() in METHODS]
            rec = create_invoice(args.lead, args.amount, args.currency,
                                 methods or None, args.ledger)
            print(pay_instructions(rec), flush=True)
            return args.ledger
        if args.status:
            if not args.invoice_id:
                print("[payment_manager] ERROR: --invoice-id is required", file=sys.stderr)
                return 2
            rec = find_invoice(load_ledger(args.ledger), args.invoice_id)
            if rec is None:
                print(f"[payment_manager] ERROR: invoice not found: {args.invoice_id}",
                      file=sys.stderr)
                return 2
            print(json.dumps(rec, ensure_ascii=False, indent=2), flush=True)
            return rec
        if args.mark_paid:
            if not args.invoice_id or not args.method:
                print("[payment_manager] ERROR: --invoice-id and --method are required",
                      file=sys.stderr)
                return 2
            rec = mark_paid(args.invoice_id, args.method, args.txref, args.ledger)
            print(f"[payment_manager] paid {rec['invoice_id']} via {args.method}", flush=True)
            return args.ledger
        if args.void:
            if not args.invoice_id:
                print("[payment_manager] ERROR: --invoice-id is required", file=sys.stderr)
                return 2
            rec = void_invoice(args.invoice_id, args.ledger)
            print(f"[payment_manager] void {rec['invoice_id']}", flush=True)
            return args.ledger
        if args.balance:
            bal = ledger_balance(args.ledger)
            print(f"[payment_manager] {bal['paid_cents'] / 100:.2f} {bal['currency']} paid "
                  f"({bal['paid_count']} paid / {bal['open_count']} open / "
                  f"{bal['invoices']} total)", flush=True)
            return bal
        if args.payout_report:
            print(payout_report(args.ledger), flush=True)
            return args.ledger
        if args.is_paid:
            paid = is_paid(args.ledger, args.is_paid)
            print(f"[payment_manager] {args.is_paid}: {'PAID' if paid else 'UNPAID'}", flush=True)
            return {"lead_id": args.is_paid, "paid": paid}
    except (ValueError, LookupError) as e:
        print(f"[payment_manager] ERROR: {e}", file=sys.stderr)
        return 2
    except RuntimeError as e:
        print(f"[payment_manager] ERROR: {e}", file=sys.stderr)
        return 1
    print("[payment_manager] nothing to do (see --help)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    _rc = main(sys.argv[1:])
    raise SystemExit(_rc if isinstance(_rc, int) else 0)
