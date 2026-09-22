"""
maps_scraper.py — Find businesses nearby via Google Maps, save to JSON.

Location is based around the *current machine location* (IP geolocation)
unless overridden with --lat/--lng or --location.

Each record in the output JSON looks like:
    {
      "name": "Example Auto Repair",
      "category": "Auto repair shop",
      "address": "123 Main St, Bothell, WA",
      "phone": "(425) 555-1234",
      "website": "https://example.com",
      "rating": 4.7,
      "review_count": 183,
      "maps_url": "https://www.google.com/maps/place/...",
      "hours_table": [["Monday", "9:00 AM – 5:00 PM"], ...],
      "hours": "Mon–Fri 9am–5pm",
      "price_level": 2,
      "photo_urls": ["https://lh3.googleusercontent.com/..."],
      "reviews_list": [{"text": "...", "author": "..."}],
      "menu_url": "https://example.com/menu",
      "coords": {"lat": 47.6, "lng": -122.2},
      "attributes": ["Dine-in", "Takeout"],
      "source": "google_maps",
      "discovered_at": "2026-09-07T..."
    }

Deep fields (hours/price/photos/reviews/menu/coords/attributes) are
best-effort: Google changes markup often, so each extractor fails soft to
None/[] and never sinks the record. Email is never on Maps — Bot 9 skips
phone-only leads instead of fabricating addresses.

Usage:
    pip install playwright requests
    playwright install chromium
    python maps_scraper.py --query "auto repair" --max 20 --output businesses.json
    python maps_scraper.py --query "coffee shop" --query "plumber" --max 50
    python maps_scraper.py --query "restaurants" --lat 47.6062 --lng -122.3321
    python maps_scraper.py --query "dentist" --location "Bothell, WA" --no-headless

Notes:
- Google Maps HTML changes often; selectors below use several fallbacks.
- Keep --max modest (20-50) to avoid bot detection / long runs.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
import time
import urllib.parse

import requests

try:  # Windows consoles default to cp1252; keep unicode output from crashing
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

DEFAULT_QUERIES = ["auto repair", "coffee shop", "restaurant"]


# ---------------------------------------------------------------------------
# Geolocation (current machine location)
# ---------------------------------------------------------------------------

def get_current_location(timeout: int = 10) -> dict:
    """Return {'lat', 'lng', 'city', 'region', 'country'} via IP geolocation.

    Tries ipapi.co first (lat/long + city), falls back to ip-api.com.
    Raises RuntimeError if both fail.
    """
    errors = []

    # Provider 1: ipapi.co (no key needed for basic usage)
    try:
        r = requests.get("https://ipapi.co/json/", timeout=timeout)
        r.raise_for_status()
        d = r.json()
        lat, lng = d.get("latitude"), d.get("longitude")
        if lat is not None and lng is not None:
            return {
                "lat": float(lat),
                "lng": float(lng),
                "city": d.get("city") or "",
                "region": d.get("region") or "",
                "country": d.get("country_name") or d.get("country") or "",
            }
        errors.append(f"ipapi.co missing coords: {d}")
    except Exception as e:  # noqa: BLE001
        errors.append(f"ipapi.co failed: {e}")

    # Provider 2: ip-api.com (plain http, generous free tier)
    try:
        r = requests.get("http://ip-api.com/json/", timeout=timeout)
        r.raise_for_status()
        d = r.json()
        if d.get("status") == "success":
            return {
                "lat": float(d["lat"]),
                "lng": float(d["lon"]),
                "city": d.get("city") or "",
                "region": d.get("regionName") or "",
                "country": d.get("country") or "",
            }
        errors.append(f"ip-api.com error: {d}")
    except Exception as e:  # noqa: BLE001
        errors.append(f"ip-api.com failed: {e}")

    raise RuntimeError("Could not determine machine location: " + " | ".join(errors))


def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def parse_rating(text: str | None) -> float | None:
    if not text:
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)", text.replace(",", "."))
    if not m:
        return None
    try:
        v = float(m.group(1))
        return v if 0 <= v <= 5 else None
    except ValueError:
        return None


def parse_review_count(text: str | None) -> int | None:
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def dedupe(records: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for r in records:
        key = (r.get("maps_url") or "").split("?")[0].lower() or (r.get("name") or "").lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# Deep-field parsing helpers (pure functions — unit-tested, no browser)
# ---------------------------------------------------------------------------

_DAY_RE = re.compile(
    r"^(Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|Thu(?:rsday)?|Fri(?:day)?|"
    r"Sat(?:urday)?|Sun(?:day)?)\s*:?\s*(.+)$", re.I)
_BARE_DAY_RE = re.compile(
    r"^(Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|Thu(?:rsday)?|Fri(?:day)?|"
    r"Sat(?:urday)?|Sun(?:day)?)$", re.I)
_HOURS_REST_RE = re.compile(r"\d|closed|open", re.I)


def _looks_like_hours(rest: str) -> bool:
    """Guard against fragments ('Monday s') — real hours name a time/state."""
    rest = (rest or "").strip()
    return len(rest) >= 3 and bool(_HOURS_REST_RE.search(rest))


def parse_hours_lines(lines: list[str]) -> list[list[str]]:
    """Turn raw day-row strings into [[Day, hours], ...] (max 7).

    Accepts 'Monday: 9 AM – 5 PM', 'Tue 9am-5pm', 'Sunday: Closed'.
    """
    table: list[list[str]] = []
    for raw in lines or []:
        t = (raw or "").strip()
        if not t or len(t) > 80:
            continue
        m = _DAY_RE.match(t)
        if m and _looks_like_hours(m.group(2)):
            table.append([_canon_day(m.group(1)), m.group(2).strip()])
        if len(table) >= 7:
            break
    return table


def pair_hours_texts(texts: list[str]) -> list[list[str]]:
    """Pair bare day nodes with the following time-like node.

    Google often renders 'Monday' and '5:00 AM – 11:00 PM' as siblings, so
    no single text contains the full row. Walks DOM-order texts and pairs
    a bare day token with the next short hours-like text.
    """
    table: list[list[str]] = []
    i = 0
    texts = [(t or "").strip() for t in (texts or [])]
    while i < len(texts) and len(table) < 7:
        t = texts[i]
        if _BARE_DAY_RE.match(t) and i + 1 < len(texts):
            nxt = texts[i + 1].strip()
            if len(nxt) <= 40 and _looks_like_hours(nxt):
                table.append([_canon_day(t), nxt])
                i += 2
                continue
        i += 1
    return table


def _canon_day(token: str) -> str:
    short = token.strip()[:3].lower()
    return {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday",
            "thu": "Thursday", "fri": "Friday", "sat": "Saturday",
            "sun": "Sunday"}.get(short, token.strip().title())


def summarize_hours(table: list[list[str]]) -> str | None:
    """One-line hours summary ('Mon–Fri 9am–5pm') or None."""
    if not table:
        return None
    days = [d for d, _ in table]
    hours = [h for _, h in table]
    if len(set(hours)) == 1:
        return f"{days[0][:3]}–{days[-1][:3]} {hours[0]}" if len(days) > 1 \
            else f"{days[0]} {hours[0]}"
    return "; ".join(f"{d[:3]} {h}" for d, h in table)[:120]


def parse_price_level(text: str | None) -> int | None:
    """Google '$'..'$$$$' indicator → 1..4."""
    if not text:
        return None
    m = re.search(r"(\${1,4})", text)
    return len(m.group(1)) if m else None


def parse_coords_from_url(url: str | None) -> dict | None:
    """Lat/lng from a Maps URL (@lat,lng, or !3dLAT!4dLNG)."""
    if not url:
        return None
    m = re.search(r"@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)", url)
    if m:
        return {"lat": float(m.group(1)), "lng": float(m.group(2))}
    m = re.search(r"!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)", url)
    if m:
        return {"lat": float(m.group(1)), "lng": float(m.group(2))}
    return None


# ---------------------------------------------------------------------------
# Google Maps scraping (Playwright)
# ---------------------------------------------------------------------------

def build_search_url(query: str, lat: float, lng: float, zoom: int = 14, city: str = "") -> str:
    """Build a Google Maps search URL biased to the machine location."""
    if city:
        q = f"{query} near {city}"
    else:
        q = query
    encoded = urllib.parse.quote_plus(q)
    # /@lat,lng,zoom biases results without requiring geolocation permission.
    return f"https://www.google.com/maps/search/{encoded}/@{lat},{lng},{zoom}z"


def _safe_text(locator, timeout: int = 3000) -> str | None:
    try:
        el = locator.first
        if el.count() == 0:
            return None
        t = el.inner_text(timeout=timeout).strip()
        return t or None
    except Exception:  # noqa: BLE001
        return None


def _safe_attr(locator, attr: str, timeout: int = 3000) -> str | None:
    try:
        el = locator.first
        if el.count() == 0:
            return None
        v = el.get_attribute(attr, timeout=timeout)
        return v.strip() if v else None
    except Exception:  # noqa: BLE001
        return None


def collect_listing_urls(page, max_results: int, scroll_delay: float = 1.2) -> list[str]:
    """Scroll the results feed and collect unique business URLs."""
    urls: list[str] = []
    seen: set[str] = set()

    try:
        page.wait_for_selector('div[role="feed"], a.hfpxzc', timeout=15000)
    except Exception:  # noqa: BLE001
        pass  # single-result pages may have no feed

    feed = None
    try:
        if page.locator('div[role="feed"]').count() > 0:
            feed = page.locator('div[role="feed"]').first
    except Exception:  # noqa: BLE001
        feed = None

    last_count = -1
    stall_rounds = 0
    # Scroll until we have enough or the feed stops growing.
    for _ in range(40):
        cards = page.locator("a.hfpxzc")
        try:
            n = cards.count()
        except Exception:  # noqa: BLE001
            n = 0
        for i in range(n):
            try:
                href = cards.nth(i).get_attribute("href")
            except Exception:  # noqa: BLE001
                continue
            if href and href.startswith("http") and href not in seen:
                seen.add(href)
                urls.append(href)
                if len(urls) >= max_results:
                    return urls
        if len(urls) == last_count:
            stall_rounds += 1
            if stall_rounds >= 4:
                break
        else:
            stall_rounds = 0
            last_count = len(urls)
        try:
            if feed is not None:
                feed.evaluate("(el) => el.scrollBy(0, el.clientHeight * 0.9)")
            else:
                page.mouse.wheel(0, 2000)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(scroll_delay)
        # Click "next page" style; Google mostly infinite-scrolls the feed.
    return urls


# ---------------------------------------------------------------------------
# Deep-field DOM extractors (best-effort — each fails soft, never raises)
# ---------------------------------------------------------------------------

def _panel_texts(page, selector: str, limit: int = 60) -> list[str]:
    """All visible texts under selector (empty list on any failure)."""
    try:
        loc = page.locator(selector)
        n = min(loc.count(), limit)
        out = []
        for i in range(n):
            try:
                t = loc.nth(i).inner_text(timeout=800)
            except Exception:  # noqa: BLE001
                continue
            t = (t or "").strip()
            if t:
                out.append(t)
        return out
    except Exception:  # noqa: BLE001
        return []


def extract_hours(page) -> tuple[list[list[str]], str | None]:
    """Opening-hours table + one-line summary (both empty when absent)."""
    status: str | None = None
    try:
        aria = (_safe_attr(page.locator("button[data-item-id='oh']").first,
                           "aria-label") or "")
        # e.g. 'Open 24 hours · See more hours' -> today's live status.
        if aria:
            status = aria.split("·")[0].strip() or None
    except Exception:  # noqa: BLE001
        pass
    # The weekly table hides behind the hours expander — open it first.
    try:
        btn = page.locator("button[data-item-id='oh']").first
        if btn.count() > 0:
            btn.click(timeout=3000)
            page.wait_for_timeout(1200)
    except Exception:  # noqa: BLE001
        pass
    texts: list[str] = []
    for sel in ["div[data-item-id*='oh']", "div[aria-label*='Hours']",
                "div.m6QErb", "div[role='main']"]:
        texts = _panel_texts(page, sel, 120)
        rows = parse_hours_lines(texts)
        if len(rows) >= 3:
            return rows, summarize_hours(rows)
    rows = parse_hours_lines(texts)
    if rows:
        return rows, summarize_hours(rows)
    # Sibling-node layout: bare 'Monday' + '5:00 AM – 11 PM' as neighbors.
    rows = pair_hours_texts([t for t in texts if len(t) <= 40])
    if len(rows) >= 3:
        return rows, summarize_hours(rows)
    # Last resort: page-wide scan for day-row text nodes (no clicks — hours
    # sometimes render outside the info containers).
    try:
        found = page.evaluate("""() => {
          const out = [];
          const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
          let node;
          while (node = walker.nextNode()) {
            const t = (node.nodeValue || '').trim();
            if (/^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)(day)?(\\s*:?\\s*\\S.*)?$/i.test(t) && t.length < 60) {
              out.push(t);
              if (out.length >= 30) break;
            }
          }
          return out;
        }""")
        rows = parse_hours_lines(found or []) or pair_hours_texts(found or [])
        if rows:
            return rows, summarize_hours(rows)
    except Exception:  # noqa: BLE001
        pass
    # No weekly rows (headless Google often withholds them) — fall back to
    # today's live status from the expander ('Open 24 hours', 'Closed · …').
    if status and len(status) <= 60:
        return [], status
    return [], None


def extract_price_level(page) -> int | None:
    for sel in ["span[aria-label*='Price']", "button.DkEaL", "div.PYvSYb"]:
        for t in _panel_texts(page, sel, 8):
            v = parse_price_level(t)
            if v:
                return v
    return None


def extract_photo_urls(page, limit: int = 6) -> list[str]:
    """Direct Google-hosted photo URLs (thumbnails usable for download)."""
    out: list[str] = []
    try:
        imgs = page.locator("div[role='main'] img, div.m6QErb img")
        n = min(imgs.count(), 40)
        for i in range(n):
            try:
                src = imgs.nth(i).get_attribute("src", timeout=800)
            except Exception:  # noqa: BLE001
                continue
            if not src or not src.startswith("http"):
                continue
            low = src.lower()
            if ("googleusercontent" not in low and "ggpht" not in low
                    and "gstatic" not in low):
                continue
            if any(bad in low for bad in ("favicon", "logo", "pin", "marker")):
                continue
            if src not in out:
                out.append(src)
            if len(out) >= limit:
                break
    except Exception:  # noqa: BLE001
        pass
    return out


def extract_reviews(page, limit: int = 3) -> list[dict]:
    """Top review snippets [{text, author}] (empty when absent)."""
    out: list[dict] = []
    for sel in ["div[data-review-id]", "div.jftiEf", "div.MyEned"]:
        try:
            cards = page.locator(sel)
            n = min(cards.count(), 8)
        except Exception:  # noqa: BLE001
            continue
        for i in range(n):
            card = cards.nth(i)
            text = None
            for tsel in ["span.wiI7pd", "div.MyEned span"]:
                text = _safe_text(card.locator(tsel))
                if text and len(text) > 20:
                    break
            if not text or len(text) < 20:
                continue
            author = (_safe_text(card.locator("div.d4r55").first)
                      or _safe_text(card.locator("button span").first) or "")
            out.append({"text": text[:400], "author": (author or "")[:60]})
            if len(out) >= limit:
                return out
        if out:
            return out
    return out


def extract_menu_url(page) -> str | None:
    try:
        href = _safe_attr(page.locator("a[data-item-id*='menu']").first, "href")
        if href and href.startswith("http"):
            return href
    except Exception:  # noqa: BLE001
        pass
    try:
        links = page.locator("div[role='main'] a, div.m6QErb a")
        n = min(links.count(), 60)
        for i in range(n):
            try:
                t = (links.nth(i).inner_text(timeout=500) or "").strip().lower()
                href = links.nth(i).get_attribute("href", timeout=500)
            except Exception:  # noqa: BLE001
                continue
            if t == "menu" and href and href.startswith("http"):
                return href
    except Exception:  # noqa: BLE001
        pass
    return None


_AMENITIES = ("dine-in", "dine in", "takeout", "takeaway", "delivery",
              "drive-through", "outdoor seating", "wheelchair accessible",
              "free wi-fi", "free wifi", "reservations", "curbside pickup")


def extract_attributes(page, limit: int = 10) -> list[str]:
    found: list[str] = []
    for t in _panel_texts(page, "div[role='main'], div.m6QErb", 200):
        if len(t) > 60:
            continue  # amenity chips are short; long text is reviews (false +)
        low = t.lower()
        for a in _AMENITIES:
            if a in low and len(found) < limit:
                label = {"Dine In": "Dine-in"}.get(a.title(), a.title())
                if label not in found:
                    found.append(label)
        if len(found) >= limit:
            break
    return found


def scrape_detail(context, url: str, timeout_ms: int = 15000) -> dict:
    """Visit one business page and extract the target record fields."""
    page = context.new_page()
    rec: dict = {
        "name": None,
        "category": None,
        "address": None,
        "phone": None,
        "website": None,
        "rating": None,
        "review_count": None,
        "maps_url": url.split("?")[0] if "?" in url else url,
        "hours_table": [],
        "hours": None,
        "price_level": None,
        "photo_urls": [],
        "reviews_list": [],
        "menu_url": None,
        "coords": None,
        "attributes": [],
        "source": "google_maps",
        "discovered_at": utc_now_iso(),
    }
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(1500)

        # Canonical URL (contains place id / coords)
        try:
            rec["maps_url"] = page.url
        except Exception:  # noqa: BLE001
            pass

        # Name
        name = _safe_text(page.locator("h1.DUwDvf"))
        if name:
            rec["name"] = name

        # Category (e.g. "Auto repair shop")
        cat = _safe_text(page.locator("button.DkEaL"))
        if not cat:
            # fallback: first span in the header area listing categories
            cat = _safe_text(page.locator("button.DkEaL span, div.PYvSYb span"))
        if cat:
            rec["category"] = cat.split("·")[0].strip()

        # Rating, e.g. <span aria-hidden="true">4.7</span>
        rating_txt = _safe_text(page.locator("div.F7nice span[aria-hidden='true']").first)
        if not rating_txt:
            rating_txt = _safe_text(page.locator("div.F7nice"))
        rec["rating"] = parse_rating(rating_txt)

        # Review count, e.g. <span>(183)</span> near the rating
        rc_txt = _safe_text(page.locator("div.F7nice span[aria-label*='review']").first)
        if not rc_txt:
            # common pattern: button containing "(183)"
            for sel in ["button span", "div.F7nice span", "span.UY7F9"]:
                try:
                    for i in range(min(page.locator(sel).count(), 12)):
                        t = page.locator(sel).nth(i).inner_text(timeout=1000).strip()
                        if re.fullmatch(r"\(\s*[\d.,\s]+\s*\)", t):
                            rc_txt = t
                            break
                    if rc_txt:
                        break
                except Exception:  # noqa: BLE001
                    continue
        rec["review_count"] = parse_review_count(rc_txt)

        # Address
        addr = _safe_text(page.locator("button[data-item-id='address'] div.Io6YTe"))
        if not addr:
            addr = _safe_text(page.locator("button[data-item-id*='address']"))
        if addr:
            rec["address"] = addr

        # Phone
        phone = _safe_text(page.locator("button[data-item-id*='phone'] div.Io6YTe"))
        if not phone:
            phone = _safe_text(page.locator("button[data-item-id*='tel:']"))
        if phone:
            rec["phone"] = phone

        # Website
        site = _safe_attr(page.locator("a[data-item-id='authority']").first, "href")
        if not site:
            # fallback: any outbound link labelled with the domain
            try:
                for i in range(min(page.locator("a[data-item-id='authority']").count(), 3)):
                    href = page.locator("a[data-item-id='authority']").nth(i).get_attribute("href")
                    if href and href.startswith("http"):
                        site = href
                        break
            except Exception:  # noqa: BLE001
                pass
        if site:
            rec["website"] = site

        # Deep fields — each fails soft to None/[] (never sinks the record).
        try:
            hours_table, hours = extract_hours(page)
            rec["hours_table"] = hours_table
            rec["hours"] = hours
        except Exception:  # noqa: BLE001
            pass
        try:
            rec["price_level"] = extract_price_level(page)
        except Exception:  # noqa: BLE001
            pass
        try:
            rec["photo_urls"] = extract_photo_urls(page)
        except Exception:  # noqa: BLE001
            pass
        try:
            rec["reviews_list"] = extract_reviews(page)
        except Exception:  # noqa: BLE001
            pass
        try:
            rec["menu_url"] = extract_menu_url(page)
        except Exception:  # noqa: BLE001
            pass
        try:
            rec["coords"] = parse_coords_from_url(rec.get("maps_url"))
        except Exception:  # noqa: BLE001
            pass
        try:
            rec["attributes"] = extract_attributes(page)
        except Exception:  # noqa: BLE001
            pass
    finally:
        try:
            page.close()
        except Exception:  # noqa: BLE001
            pass
    return rec


def scrape_query(query: str, lat: float, lng: float, city: str,
                 max_results: int, headless: bool, zoom: int,
                 scroll_delay: float, timeout_ms: int) -> list[dict]:
    from playwright.sync_api import sync_playwright

    url = build_search_url(query, lat, lng, zoom=zoom, city=city)
    print(f"[maps_scraper] query={query!r} url={url}", flush=True)
    records: list[dict] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless, args=["--disable-blink-features=AutomationControlled"])
        context = browser.new_context(
            locale="en-US",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"),
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(2500)

            # If search resolves directly to a single place (no feed), scrape it.
            is_place = "/place/" in (page.url or "")
            n_cards = 0
            try:
                n_cards = page.locator("a.hfpxzc").count()
            except Exception:  # noqa: BLE001
                n_cards = 0

            if is_place and n_cards == 0:
                print("[maps_scraper] single-place result, scraping directly", flush=True)
                records.append(scrape_detail(context, page.url, timeout_ms))
            else:
                urls = collect_listing_urls(page, max_results, scroll_delay)
                print(f"[maps_scraper] found {len(urls)} listing(s) for {query!r}", flush=True)
                for u in urls[:max_results]:
                    try:
                        records.append(scrape_detail(context, u, timeout_ms))
                        time.sleep(0.5)  # be polite
                    except Exception as e:  # noqa: BLE001
                        print(f"[maps_scraper] WARN detail failed: {e}", flush=True)
        finally:
            try:
                page.close()
            except Exception:  # noqa: BLE001
                pass
            context.close()
            browser.close()
    return records


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Scrape nearby businesses from Google Maps into JSON.")
    p.add_argument("--query", action="append", default=[],
                   help="Business type to search (repeatable). e.g. --query 'auto repair' --query 'coffee shop'")
    p.add_argument("--lat", type=float, default=None, help="Override latitude (default: IP geolocation)")
    p.add_argument("--lng", type=float, default=None, help="Override longitude (default: IP geolocation)")
    p.add_argument("--location", default="", help="City/region hint, e.g. 'Bothell, WA' (used in search text)")
    p.add_argument("--max", type=int, default=20, help="Max businesses PER query (default: 20)")
    p.add_argument("--zoom", type=int, default=14, help="Map zoom bias 1-21 (default: 14)")
    p.add_argument("--output", "-o", default="", help="Output JSON path (default: businesses_<timestamp>.json)")
    p.add_argument("--headless", dest="headless", action="store_true", default=True, help="Run browser headless (default)")
    p.add_argument("--no-headless", dest="headless", action="store_false", help="Show browser window (debug)")
    p.add_argument("--scroll-delay", type=float, default=1.2, help="Seconds between result scrolls (default: 1.2)")
    p.add_argument("--timeout", type=int, default=30000, help="Page timeout ms (default: 30000)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None, **kwargs) -> str | int:
    """Run the scraper. Returns the output JSON path (str), or an int exit code on error.

    Orchestrator-friendly: ``main(output="businesses.json", query=["plumber"], max=20)``.
    Any keyword matching an argparse option overrides the CLI default. A bare
    main() call uses defaults (sys.argv is only used via the CLI).
    """
    if argv is None:
        # Plain main() uses defaults (never sys.argv); the CLI passes
        # sys.argv[1:] explicitly via the __main__ block below.
        argv = []
    args = parse_args(argv)
    for _k, _v in kwargs.items():
        if not hasattr(args, _k):
            raise TypeError(f"maps_scraper.main() got an unexpected option {_k!r}")
        setattr(args, _k, _v)
    if isinstance(args.query, str):
        args.query = [args.query]
    queries = args.query or DEFAULT_QUERIES

    # Resolve location: explicit flags win, else IP geolocation of this machine.
    city = args.location.strip()
    if args.lat is not None and args.lng is not None:
        lat, lng = args.lat, args.lng
        print(f"[maps_scraper] using override location: {lat},{lng} ({city or 'no city hint'})", flush=True)
    else:
        print("[maps_scraper] detecting machine location via IP geolocation…", flush=True)
        try:
            loc = get_current_location()
        except RuntimeError as e:
            print(f"[maps_scraper] ERROR {e}", file=sys.stderr)
            print("[maps_scraper] Hint: pass --lat/--lng manually.", file=sys.stderr)
            return 2
        lat, lng = loc["lat"], loc["lng"]
        if not city and loc.get("city"):
            region = loc.get("region") or ""
            city = f"{loc['city']}, {region}".strip(", ") if region else loc["city"]
        print(f"[maps_scraper] machine location ≈ {lat},{lng} ({city}, {loc.get('country','')})", flush=True)

    all_records: list[dict] = []
    for q in queries:
        # allow comma-separated: --query "plumber, electrician"
        for sub_q in [s.strip() for s in q.split(",") if s.strip()]:
            try:
                recs = scrape_query(sub_q, lat, lng, city, args.max,
                                    args.headless, args.zoom, args.scroll_delay, args.timeout)
                all_records.extend(recs)
            except Exception as e:  # noqa: BLE001
                print(f"[maps_scraper] ERROR query {sub_q!r}: {e}", file=sys.stderr)

    all_records = dedupe(all_records)
    # Drop entries with no name (failed parses)
    all_records = [r for r in all_records if r.get("name")]

    out = args.output.strip() or f"businesses_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(all_records, f, ensure_ascii=False, indent=2)
    print(f"[maps_scraper] wrote {len(all_records)} business(es) → {out}", flush=True)
    return out


if __name__ == "__main__":
    _rc = main(sys.argv[1:])
    raise SystemExit(_rc if isinstance(_rc, int) else 0)
