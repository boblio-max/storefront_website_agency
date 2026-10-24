"""agency_config.py — One morphable center for the whole agency.

Edit agency_config.json (not code) to morph into whatever a website
agency needs to be: niches, pricing, payment methods, email voice,
follow-up cadence, QA bar, deploy behavior.

Every helper degrades gracefully: missing file -> built-in defaults,
bad values -> safe fallbacks, never raises on load.
"""

from __future__ import annotations

import functools
import json
import os
import tempfile
import time
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / "agency_config.json"


def _load_env_file() -> None:
    """Load .env next to the config so every bot sees the same credentials.

    Optional python-dotenv only; env-only mode still works. Called on import
    so deployment_manager / orchestrator (which never imported email bots
    before) also pick up AGENCY_GH_USER / SMTP / wallet keys.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    try:
        load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env",
                     override=False)
    except Exception:  # noqa: BLE001
        pass


_load_env_file()

DEFAULTS: dict = {
    "agency_name": "Storefront",
    "niches": ["auto repair", "coffee shop", "restaurant"],
    "max_per_query": 20,
    "qa_threshold": 80,
    "max_qa_attempts": 5,
    "default_price_cents": 29900,
    "currency": "USD",
    "payment_methods": ["card", "bank", "crypto", "cash"],
    "followup_schedule_days": [3, 7, 14],
    "max_followups": 3,
    "require_email_for_outreach": True,
    "deploy": {"allow_local_fallback": True, "require_gh_match": True},
    "outreach": {"max_words": 180, "include_pay_block": True},
}

_CACHE: dict | None = None


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | Path = CONFIG_PATH) -> dict:
    """Load JSON config merged over DEFAULTS. Never raises."""
    global _CACHE
    if _CACHE is not None and str(path) == str(CONFIG_PATH):
        return _CACHE
    cfg = dict(DEFAULTS)
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            cfg = _deep_merge(DEFAULTS, raw)
    except (OSError, json.JSONDecodeError):
        pass
    # Env overrides (deploy-safe, no secrets here — secrets stay in env).
    if os.environ.get("AGENCY_NAME", "").strip():
        cfg["agency_name"] = os.environ["AGENCY_NAME"].strip()
    if os.environ.get("AGENCY_DEFAULT_PRICE_CENTS", "").strip():
        try:
            cfg["default_price_cents"] = int(os.environ["AGENCY_DEFAULT_PRICE_CENTS"])
        except ValueError:
            pass
    if os.environ.get("AGENCY_WALLET_CURRENCY", "").strip():
        cfg["currency"] = os.environ["AGENCY_WALLET_CURRENCY"].strip().upper()
    if str(path) == str(CONFIG_PATH):
        _CACHE = cfg
    return cfg


def reset_cache() -> None:
    global _CACHE
    _CACHE = None


# ---------------------------------------------------------------------------
# Resilience primitives shared by all bots.
# ---------------------------------------------------------------------------

def load_json_safe(path: str | Path, default):
    """Read JSON, returning default on missing/corrupt (never raises)."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def atomic_write_json(path: str | Path, data) -> str:
    """Crash-safe JSON write (tmp + fsync + rename). Returns str(path)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return str(p)


def retry(fn, *, tries: int = 3, delay: float = 1.0, backoff: float = 2.0,
          exceptions: tuple = (Exception,)):
    """Run fn(); retry on exceptions with exponential backoff. Raises last."""
    last: BaseException | None = None
    wait = delay
    for _ in range(max(1, tries)):
        try:
            return fn()
        except exceptions as e:  # noqa: BLE001
            last = e
            time.sleep(wait)
            wait *= backoff
    assert last is not None
    raise last


# ---------------------------------------------------------------------------
# Pipeline state: per-lead stage tracking so runs resume, never redo.
# ---------------------------------------------------------------------------

STATE_PATH = Path(__file__).resolve().parent / "pipeline_state.json"
STAGES = ("scraped", "site_built", "qa_passed", "deployed",
          "outreach_sent", "replied", "paid", "final_delivered")


def load_state(path: str | Path = STATE_PATH) -> dict:
    raw = load_json_safe(path, {})
    return raw if isinstance(raw, dict) else {}


def mark_stage(lead_id: str, stage: str, extra: dict | None = None,
               path: str | Path = STATE_PATH) -> dict:
    """Idempotently record a lead's stage. Never raises."""
    try:
        state = load_state(path)
        entry = state.get(lead_id) if isinstance(state.get(lead_id), dict) else {}
        entry["stage"] = stage
        entry["updated_at"] = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat()
        if extra:
            entry.update(extra)
        state[lead_id] = entry
        atomic_write_json(path, state)
        return entry
    except Exception:  # noqa: BLE001
        return {}


def stage_of(lead_id: str, path: str | Path = STATE_PATH) -> str | None:
    entry = load_state(path).get(lead_id)
    return entry.get("stage") if isinstance(entry, dict) else None


# ---------------------------------------------------------------------------
# Money helpers: price + payment instructions block for emails.
# ---------------------------------------------------------------------------

def price_cents(cfg: dict | None = None) -> int:
    cfg = cfg or load_config()
    try:
        return int(cfg.get("default_price_cents", 29900))
    except (TypeError, ValueError):
        return 29900


def price_label(cfg: dict | None = None) -> str:
    cfg = cfg or load_config()
    cents = price_cents(cfg)
    cur = str(cfg.get("currency", "USD")).upper()
    symbol = {"USD": "$", "EUR": "\u20ac", "GBP": "\u00a3"}.get(cur, cur + " ")
    return f"{symbol}{cents / 100:.0f}"


def pay_block(cfg: dict | None = None) -> str:
    """Short payment paragraph for outreach emails. Public refs only."""
    cfg = cfg or load_config()
    if not cfg.get("outreach", {}).get("include_pay_block", True):
        return ""
    label = price_label(cfg)
    methods = [m for m in cfg.get("payment_methods", []) if m in ("card", "bank", "crypto", "cash")]
    lines = [f"Launch price is a flat {label} one-time ({', '.join(methods) or 'card'} accepted)."]
    link = os.environ.get("AGENCY_STRIPE_LINK_BASE", "").strip()
    if "card" in methods and link:
        lines.append(f"Pay by card here: {link}")
    bank = os.environ.get("AGENCY_BANK_REF", "").strip()
    if "bank" in methods and bank:
        lines.append(f"Bank transfer reference: {bank} (reply for details).")
    crypto_raw = os.environ.get("AGENCY_CRYPTO_ADDRESSES", "").strip()
    if "crypto" in methods and crypto_raw:
        try:
            addrs = json.loads(crypto_raw)
            coins = ", ".join(f"{k}: {v}" for k, v in addrs.items()) if isinstance(addrs, dict) else ""
        except json.JSONDecodeError:
            coins = ""
        lines.append(f"Crypto accepted{f' ({coins})' if coins else ''} — reply for the address.")
    if "card" in methods and not link:
        lines.append("Reply 'invoice' and I'll send a secure card link.")
    return " ".join(lines)


def reset_for_tests() -> None:
    reset_cache()
