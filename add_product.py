"""Helper script to add products to track in config.yaml.

Run this instead of editing config.yaml by hand:

    python add_product.py

It will prompt you for product details and append them to config.yaml.
Supports H&M India and Myntra URLs.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"

HM_URL_RE = re.compile(r"hm\.com/en_in/productpage\.\d+\.html")
MYNTRA_URL_RE = re.compile(r"myntra\.com")


def _ask(prompt: str, default: str | None = None) -> str:
    if default:
        prompt = f"{prompt} [{default}]: "
    else:
        prompt = f"{prompt}: "
    value = input(prompt).strip()
    if not value and default is not None:
        return default
    return value


def _detect_site(url: str) -> str:
    if MYNTRA_URL_RE.search(url):
        return "myntra"
    if HM_URL_RE.search(url):
        return "hm"
    return "hm"


def _is_valid_url(url: str) -> bool:
    return bool(HM_URL_RE.search(url) or MYNTRA_URL_RE.search(url))


def main() -> None:
    print("=== Add Product to Price Tracker ===\n")

    if not CONFIG_PATH.exists():
        print(f"config.yaml not found at {CONFIG_PATH}")
        return

    with CONFIG_PATH.open("r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh) or {}

    products = config.setdefault("products", [])

    while True:
        url = _ask("Product page URL (or press Enter to finish)")
        if not url:
            break

        if not _is_valid_url(url):
            print("\nThat doesn't look like an H&M India or Myntra product URL.")
            print("Expected H&M: https://www2.hm.com/en_in/productpage.1234567.html")
            print("Expected Myntra: https://www.myntra.com/...")
            cont = input("Continue anyway? (y/N): ").strip().lower()
            if cont != "y":
                continue

        site = _detect_site(url)

        # Check for duplicate URL across all products.
        dup = False
        for p in products:
            for s in p.get("sources", []):
                if s.get("url") == url:
                    dup = True
                    break
            if dup:
                break
        if dup:
            print(f"\nThat URL is already being tracked.\n")
            continue

        name = _ask("Product name (optional)", default="")
        if not name:
            name = url.split("/")[-1]

        product: dict = {
            "name": name,
            "currency": "INR",
            "sources": [{"site": site, "url": url}],
        }

        products.append(product)

        with CONFIG_PATH.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(config, fh, sort_keys=False, allow_unicode=True)

        site_label = site.upper()
        print(f"\nAdded ({site_label}): '{name}'")
        print(f"  URL: {url}")
        print("  Baseline price & sizes will be set on the first check.\n")

    print("Done. Run: python main.py --dry-run   (to test)")
    print("Or wait for the scheduled GitHub Actions run.")


if __name__ == "__main__":
    main()
