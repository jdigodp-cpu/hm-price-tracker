"""H&M India product price scraper.

Extracts the current selling price (INR) from an H&M India product page.

Two fetch strategies are available:

1. **`requests`** (fast, lightweight) — tries to read the price from the raw
   HTML. H&M India is protected by Akamai bot protection, so this often returns
   403 from datacenter IPs (e.g. GitHub Actions). It works best from a machine
   that already has an H&M session/cookies.

2. **Playwright** (real browser) — launches a headless Chromium with stealth
   options, which passes Akamai's browser-fingerprint checks. This is the
   recommended method for scheduled/GitHub Actions runs.

Price extraction (shared by both strategies) tries, in order:
  a. JSON-LD Product schema (`offers.price`)
  b. Open Graph / meta tags (`product:price:amount`, `itemprop=price`)
  c. Fallback regex for common INR price patterns

Usage:
    from scraper import fetch_price
    price = fetch_price("https://www2.hm.com/en_in/productpage.0941366.html")
    # -> 1499.0 or None

Force the browser path:
    price = fetch_price(url, force_browser=True)
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("hm-tracker")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

_PRICE_PATTERNS = [
    re.compile(r"₹\s*([\d,]+\.?\d*)"),
    re.compile(r"(?:INR|Rs\.?)\s*([\d,]+\.?\d*)", re.IGNORECASE),
    re.compile(r'"price"\s*:\s*"?([\d,]+\.?\d*)"?'),
]


def _to_float(raw: Optional[str]) -> Optional[float]:
    """Convert '1,499.00' -> 1499.0."""
    if not raw:
        return None
    cleaned = raw.replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def extract_price_from_html(html: str) -> Optional[float]:
    """Extract a price (INR) from raw H&M product-page HTML.

    Tries JSON-LD, then meta tags, then regex. Returns None if nothing found.
    """
    soup = BeautifulSoup(html, "html.parser")

    # --- a. JSON-LD Product schema ------------------------------------- #
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (ValueError, TypeError):
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            candidates = [node]
            if isinstance(node.get("@graph"), list):
                candidates.extend(node["@graph"])
            for cand in candidates:
                if not isinstance(cand, dict):
                    continue
                offers = cand.get("offers")
                if isinstance(offers, dict):
                    price = _to_float(offers.get("price"))
                    if price:
                        return price
                if isinstance(offers, list):
                    for offer in offers:
                        if isinstance(offer, dict):
                            price = _to_float(offer.get("price"))
                            if price:
                                return price

    # --- b. Open Graph / schema meta tags ------------------------------ #
    for tag_name, attrs in [
        ("meta", {"property": "product:price:amount"}),
        ("meta", {"itemprop": "price"}),
        ("meta", {"name": "twitter:price:amount"}),
    ]:
        tag = soup.find(tag_name, attrs)
        if tag:
            value = tag.get("content") or tag.get("value")
            price = _to_float(value)
            if price:
                return price

    # --- c. Fallback regex --------------------------------------------- #
    for pattern in _PRICE_PATTERNS:
        match = pattern.search(html)
        if match:
            price = _to_float(match.group(1))
            if price and 50 < price < 200000:  # sane INR clothing range
                return price

    return None


def fetch_price_requests(url: str, timeout: int = 20) -> Optional[float]:
    """Fetch price using raw HTTP requests (fast, may be blocked by Akamai)."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("Request failed for %s: %s", url, exc)
        return None

    price = extract_price_from_html(resp.text)
    if price:
        log.info("Price extracted via requests: ₹%.2f", price)
    return price


def fetch_price_browser(url: str, timeout: int = 30) -> Optional[float]:
    """Fetch price using a real Chromium browser via Playwright.

    Bypasses Akamai bot protection by presenting a genuine browser fingerprint.
    Requires the `playwright` package and a Chromium browser install:

        pip install playwright
        playwright install chromium
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.warning("Playwright not installed — install with: pip install playwright")
        return None

    html = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                ],
            )
            context = browser.new_context(
                viewport={"width": 1366, "height": 768},
                locale="en-IN",
                timezone_id="Asia/Kolkata",
                user_agent=HEADERS["User-Agent"],
            )
            # Strip the `navigator.webdriver` flag that Akamai checks.
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            page = context.new_page()
            page.set_extra_http_headers({
                "Accept-Language": "en-IN,en;q=0.9",
            })
            page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
            # Give Akamai's JS challenge a moment to resolve.
            page.wait_for_timeout(3000)
            html = page.content()
            browser.close()
    except Exception as exc:  # noqa: BLE001 - browser issues are expected
        log.warning("Browser fetch failed for %s: %s", url, exc)
        return None

    if not html:
        return None

    price = extract_price_from_html(html)
    if price:
        log.info("Price extracted via browser: ₹%.2f", price)
    return price


def fetch_price(url: str, timeout: int = 20,
                force_browser: Optional[bool] = None) -> Optional[float]:
    """Fetch the current price (INR) for an H&M India product URL.

    Strategy:
      1. Try `requests` (fast).
      2. If that fails and a browser is available/forced, try Playwright.

    `force_browser`:
      - None (default): use env `HM_USE_BROWSER` ("1"/"true" -> browser only).
      - True: browser only.
      - False: requests only.
    """
    if force_browser is None:
        force_browser = os.environ.get("HM_USE_BROWSER", "").lower() in ("1", "true", "yes")

    if force_browser:
        return fetch_price_browser(url, timeout=timeout)

    price = fetch_price_requests(url, timeout=timeout)
    if price:
        return price

    # Fall back to browser when requests didn't yield a price.
    log.info("Requests yielded no price — falling back to browser for %s", url)
    return fetch_price_browser(url, timeout=timeout)


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    url = sys.argv[1] if len(sys.argv) > 1 else ""
    browser = "--browser" in sys.argv
    if not url:
        print("Usage: python scraper.py <hm-product-url> [--browser]")
        sys.exit(2)
    price = fetch_price(url, force_browser=browser)
    print(f"Price: {price if price is not None else 'NOT FOUND'}")
