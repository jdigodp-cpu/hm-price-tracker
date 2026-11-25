"""Helper script to add products to track in config.yaml.

Run this instead of editing config.yaml by hand:

    python add_product.py

It will prompt you for the product details and append them to config.yaml.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


def _ask(prompt: str, default: str | None = None) -> str:
    if default:
        prompt = f"{prompt} [{default}]: "
    else:
        prompt = f"{prompt}: "
    value = input(prompt).strip()
    if not value and default is not None:
        return default
    return value


def _ask_price(prompt: str) -> float | None:
    while True:
        raw = input(prompt).strip()
        if not raw:
            return None
        try:
            return float(raw.replace(",", "").replace("₹", "").replace("Rs.", "").strip())
        except ValueError:
            print("  Please enter a valid number (e.g. 1499 or 1,499.00).")


def _is_hm_url(url: str) -> bool:
    return bool(re.search(r"hm\.com.*productpage\.\d+\.html", url))


def main() -> None:
    print("=== Add H&M India Product to Tracker ===\n")

    if not CONFIG_PATH.exists():
        print(f"config.yaml not found at {CONFIG_PATH}")
        return

    with CONFIG_PATH.open("r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh) or {}

    products = config.setdefault("products", [])

    url = _ask("H&M product page URL")
    if not _is_hm_url(url):
        print("\nWarning: that doesn't look like an H&M India product page URL.")
        print("Expected format: https://www2.hm.com/en_in/productpage.1234567.html")
        cont = input("Continue anyway? (y/N): ").strip().lower()
        if cont != "y":
            print("Cancelled.")
            return

    name = _ask("Product name (optional, used in alerts)", default="")
    if not name:
        name = url

    bought_price = _ask_price("Price you paid (₹) — press Enter to skip: ")
    target_price = _ask_price("Steal / target price to buy at (₹) — press Enter to skip: ")

    if bought_price is None and target_price is None:
        print("\nYou must set at least one of: bought_price or target_price.")
        print("Cancelled.")
        return

    product = {
        "name": name,
        "url": url,
        "bought_price": bought_price,
        "target_price": target_price,
        "currency": "INR",
    }
    # Drop None values so the YAML stays clean.
    product = {k: v for k, v in product.items() if v is not None}

    products.append(product)

    with CONFIG_PATH.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(config, fh, sort_keys=False, allow_unicode=True)

    print(f"\nAdded '{name}' to {CONFIG_PATH.name}")
    print(f"  URL: {url}")
    if bought_price is not None:
        print(f"  Bought price: ₹{bought_price:,.2f}")
    if target_price is not None:
        print(f"  Target price: ₹{target_price:,.2f}")
    print("\nRun: python main.py --dry-run   (to test)")
    print("Or wait for the scheduled GitHub Actions run.")


if __name__ == "__main__":
    main()
