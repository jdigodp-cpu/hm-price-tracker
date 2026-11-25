"""H&M India product price scraper.

Extracts the current selling price (INR) and available sizes from an H&M
India product page.

Two fetch strategies are available:

1. **`requests`** (fast, lightweight) — tries to read the price from the raw
   HTML. H&M India is protected by Akamai bot protection, so this often returns
   403 from datacenter IPs (e.g. GitHub Actions). It works best from a machine
   that already has an H&M session/cookies.

2. **Playwright** (real browser) — launches a headless Chromium with stealth
   options, which passes Akamai's browser-fingerprint checks. This is the
   recommended method for scheduled/GitHub Actions runs.

Price extraction tries, in order:
  a. JSON-LD Product schema (`offers.price`)
  b. Open Graph / meta tags (`product:price:amount`, `itemprop=price`)
  c. Fallback regex for common INR price patterns

Size extraction looks for size options in JSON-LD offers and in page HTML
(size selector elements with availability classes).

Usage:
    from scraper import fetch_price_and_sizes
    price, sizes = fetch_price_and_sizes("https://www2.hm.com/en_in/productpage.0941366.html")
    # -> (1499.0, ["S", "M", "L"]) or (None, [])

Force the browser path:
    price, sizes = fetch_price_and_sizes(url, force_browser=True)
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional, Tuple

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

# Size option patterns found on H&M product pages.
_SIZE_PATTERNS = [
    # <span class="...">S</span> inside size selector
    re.compile(r'<option[^>]*value="([^"]+)"[^>]*>(?:<span[^>]*>)?([^<]{1,3})(?:</span>)?</option>', re.IGNORECASE),
    # data-size attributes
    re.compile(r'data-size=["\']([^"\']+)["\']', re.IGNORECASE),
    # size buttons with availability
    re.compile(r'(?:size|Size)[:\s]*["\']?([SMLX]{1,3}(?:\.\d+)?(?:/[SMLX]{1,3})?)["\']?', re.IGNORECASE),
]

# Common clothing sizes to validate against.
_VALID_SIZES = {
    "XS", "S", "M", "L", "XL", "XXL", "XXXL",
    "28", "30", "32", "34", "36", "38", "40", "42", "44", "46", "48",
    "28/30", "30/32", "32/34", "34/36",
}


def _to_float(raw: Optional[str]) -> Optional[float]:
    """Convert '1,499.00' -> 1499.0."""
    if not raw:
        return None
    cleaned = raw.replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _clean_size(raw: str) -> Optional[str]:
    """Clean and validate a size string."""
    s = raw.strip().upper()
    if not s or len(s) > 6:
        return None
    # Skip non-size noise.
    noise = {"TRUE", "FALSE", "NULL", "NONE", "SELECT", "CHOOSE", "SIZE"}
    if s in noise:
        return None
    return s


def _extract_sizes_from_json_ld(soup: BeautifulSoup) -> list:
    """Extract available sizes from JSON-LD Product offers."""
    sizes = []
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
                offer_list = []
                if isinstance(offers, dict):
                    offer_list = [offers]
                elif isinstance(offers, list):
                    offer_list = offers
                for offer in offer_list:
                    if not isinstance(offer, dict):
                        continue
                    # H&M may encode size info in offer properties.
                    for key in ("size", "sizes", "availableSize", "sku"):
                        val = offer.get(key)
                        if isinstance(val, str):
                            clean = _clean_size(val)
                            if clean and clean not in sizes:
                                sizes.append(clean)
                        elif isinstance(val, list):
                            for v in val:
                                clean = _clean_size(str(v))
                                if clean and clean not in sizes:
                                    sizes.append(clean)
    return sizes


def _extract_sizes_from_html(html: str) -> list:
    """Extract available sizes from raw page HTML using regex patterns."""
    sizes = []
    for pattern in _SIZE_PATTERNS:
        for match in pattern.finditer(html):
            # Group 1 is usually the value, group 2 may be the display text.
            val = match.group(1) or match.group(2) or ""
            clean = _clean_size(val)
            if clean and clean not in sizes:
                sizes.append(clean)
    return sizes


def extract_price_and_sizes_from_html(html: str) -> Tuple[Optional[float], list]:
    """Extract price and available sizes from raw H&M product-page HTML.

    Returns (price, available_sizes).
    """
    soup = BeautifulSoup(html, "html.parser")

    # --- Price extraction ------------------------------------------------- #
    price: Optional[float] = None

    # a. JSON-LD Product schema.
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
                        break
                if isinstance(offers, list):
                    for offer in offers:
                        if isinstance(offer, dict):
                            price = _to_float(offer.get("price"))
                            if price:
                                break
            if price:
                break
        if price:
            break

    # b. Open Graph / schema meta tags.
    if not price:
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
                    break

    # c. Fallback regex.
    if not price:
        for pattern in _PRICE_PATTERNS:
            match = pattern.search(html)
            if match:
                price = _to_float(match.group(1))
                if price and 50 < price < 200000:
                    break

    # --- Size extraction -------------------------------------------------- #
    sizes = _extract_sizes_from_json_ld(soup)
    if not sizes:
        sizes = _extract_sizes_from_html(html)

    # Filter to valid sizes only.
    sizes = [s for s in sizes if s in _VALID_SIZES or re.match(r"^\d{2}$", s)]

    return price, sizes


def fetch_price_and_sizes_requests(url: str, timeout: int = 20) -> Tuple[Optional[float], list]:
    """Fetch price and sizes using raw HTTP requests."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("Request failed for %s: %s", url, exc)
        return None, []

    price, sizes = extract_price_and_sizes_from_html(resp.text)
    if price:
        log.info("Price & sizes extracted via requests: ₹%.2f, sizes=%s", price, sizes)
    return price, sizes


def fetch_price_and_sizes_browser(url: str, timeout: int = 30) -> Tuple[Optional[float], list]:
    """Fetch price and sizes using a real Chromium browser via Playwright."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.warning("Playwright not installed — install with: pip install playwright")
        return None, []

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
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            page = context.new_page()
            page.set_extra_http_headers({"Accept-Language": "en-IN,en;q=0.9"})
            page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
            page.wait_for_timeout(3000)
            html = page.content()
            browser.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("Browser fetch failed for %s: %s", url, exc)
        return None, []

    if not html:
        return None, []

    price, sizes = extract_price_and_sizes_from_html(html)
    if price:
        log.info("Price & sizes extracted via browser: ₹%.2f, sizes=%s", price, sizes)
    return price, sizes


def fetch_price_and_sizes(url: str, timeout: int = 20,
                          force_browser: Optional[bool] = None
                          ) -> Tuple[Optional[float], list]:
    """Fetch the current price (INR) and available sizes for an H&M product.

    Strategy:
      1. Try `requests` (fast).
      2. If that fails and a browser is available/forced, try Playwright.

    `force_browser`:
      - None (default): use env `HM_USE_BROWSER` ("1"/"true" -> browser only).
      - True: browser only.
      - False: requests only.

    Returns (price, available_sizes).
    """
    if force_browser is None:
        force_browser = os.environ.get("HM_USE_BROWSER", "").lower() in ("1", "true", "yes")

    if force_browser:
        return fetch_price_and_sizes_browser(url, timeout=timeout)

    price, sizes = fetch_price_and_sizes_requests(url, timeout=timeout)
    if price:
        return price, sizes

    log.info("Requests yielded no price — falling back to browser for %s", url)
    return fetch_price_and_sizes_browser(url, timeout=timeout)


# Backward-compatible alias: fetch_price returns just the price.
def fetch_price(url: str, timeout: int = 20,
                force_browser: Optional[bool] = None) -> Optional[float]:
    """Backward-compatible: return only the price (ignores sizes)."""
    price, _ = fetch_price_and_sizes(url, timeout=timeout, force_browser=force_browser)
    return price


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    url = sys.argv[1] if len(sys.argv) > 1 else ""
    browser = "--browser" in sys.argv
    if not url:
        print("Usage: python scraper.py <hm-product-url> [--browser]")
        sys.exit(2)
    price, sizes = fetch_price_and_sizes(url, force_browser=browser)
    print(f"Price: {price if price is not None else 'NOT FOUND'}")
    print(f"Available sizes: {sizes if sizes else 'NONE FOUND'}")
