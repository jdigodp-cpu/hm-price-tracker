"""Myntra product price scraper.

Extracts the current selling price (INR) and available sizes from a Myntra
product page. Myntra renders prices client-side, so a real browser
(Playwright) is required.

Usage:
    from myntra_scraper import fetch_price_and_sizes
    price, sizes = fetch_price_and_sizes("https://www.myntra.com/...")
    # -> (1499.0, ["S", "M", "L"]) or (None, [])
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional, Tuple

log = logging.getLogger("hm-tracker")

# Myntra product URL pattern.
_MYNTRA_URL_RE = re.compile(r"myntra\.com/[^?]*/\d+/buy")


def _to_float(raw: Optional[str]) -> Optional[float]:
    if not raw:
        return None
    cleaned = raw.replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _extract_sizes_from_json(data) -> list:
    """Recursively search JSON for size-related data."""
    sizes = []

    def walk(node):
        if isinstance(node, dict):
            # Look for size objects with availability flags.
            if "size" in node and ("available" in node or "soldOut" in node or "stock" in node):
                size_val = node.get("size")
                available = node.get("available", node.get("stock", True))
                sold_out = node.get("soldOut", False)
                if size_val and available and not sold_out:
                    sizes.append(str(size_val))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    # Deduplicate while preserving order.
    seen = set()
    result = []
    for s in sizes:
        if s not in seen:
            seen.add(s)
            result.append(s)
    return result


def extract_from_page_html(html: str) -> Tuple[Optional[float], list]:
    """Extract price and available sizes from Myntra page HTML.

    Myntra embeds product data in __NEXT_DATA__ or similar JSON blobs.
    """
    price: Optional[float] = None
    sizes: list = []

    # Try to find JSON data embedded in the page.
    for pattern in [
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        r'<script type="application/json"[^>]*>(.*?)</script>',
    ]:
        match = re.search(pattern, html, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1))
                # Look for price.
                price = _find_price_in_json(data)
                # Look for sizes.
                sizes = _extract_sizes_from_json(data)
                if price or sizes:
                    return price, sizes
            except (json.JSONDecodeError, ValueError):
                continue

    # Fallback: regex for price in raw HTML.
    price_patterns = [
        re.compile(r'"price"\s*:\s*"?([\d,]+\.?\d*)"?'),
        re.compile(r'₹\s*([\d,]+\.?\d*)'),
        re.compile(r'"discountedPrice"\s*:\s*"?([\d,]+\.?\d*)"?'),
    ]
    for pat in price_patterns:
        m = pat.search(html)
        if m:
            price = _to_float(m.group(1))
            if price and 50 < price < 200000:
                break

    # Fallback regex for sizes.
    size_matches = re.findall(r'"size"\s*:\s*"([^"]+)"', html)
    if size_matches:
        sizes = list(dict.fromkeys(size_matches))

    return price, sizes


def _find_price_in_json(node) -> Optional[float]:
    """Recursively search JSON for a price field."""
    if isinstance(node, dict):
        for key, val in node.items():
            if key.lower() in ("price", "discountedprice", "sellingprice", "mrp"):
                result = _to_float(str(val))
                if result and 50 < result < 200000:
                    return result
            result = _find_price_in_json(val)
            if result:
                return result
    elif isinstance(node, list):
        for item in node:
            result = _find_price_in_json(item)
            if result:
                return result
    return None


def fetch_price_and_sizes(url: str, timeout: int = 30) -> Tuple[Optional[float], list]:
    """Fetch current price and available sizes from a Myntra product URL.

    Returns (price, available_sizes). price is None if not found.
    sizes is a list of available size strings (may be empty).
    """
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
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            )
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            page = context.new_page()
            page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
            page.wait_for_timeout(4000)  # Let Myntra's JS render
            html = page.content()
            browser.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("Myntra browser fetch failed for %s: %s", url, exc)
        return None, []

    if not html:
        return None, []

    return extract_from_page_html(html)


if __name__ == "__main__":  # pragma: no cover
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    url = sys.argv[1] if len(sys.argv) > 1 else ""
    if not url:
        print("Usage: python myntra_scraper.py <myntra-product-url>")
        sys.exit(2)
    price, sizes = fetch_price_and_sizes(url)
    print(f"Price: {price if price is not None else 'NOT FOUND'}")
    print(f"Available sizes: {sizes if sizes else 'NONE FOUND'}")
