"""H&M India Price Tracker — main entry point.

Reads tracked products from config.yaml, checks each product's current price
on the H&M India website, and alerts you when a price hits your target (or
drops below what you paid, so you can return & rebuy).

Run once:
    python main.py

Run a dry run (no notifications):
    python main.py --dry-run

Schedule via .github/workflows/track.yml (GitHub Actions).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from scraper import fetch_price
from notifier import Notifier

log = logging.getLogger("hm-tracker")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
STATE_FILE = DATA_DIR / "prices.json"

DEFAULT_CONFIG = BASE_DIR / "config.yaml"
EXAMPLE_CONFIG = BASE_DIR / "config.yaml.example"


def now_ist() -> str:
    """Current time as an ISO string (IST for logging)."""
    return datetime.now(timezone.utc).astimezone().isoformat()


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            with STATE_FILE.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not read price state (%s) — starting fresh", exc)
    return {}


def save_state(state: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with STATE_FILE.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


def product_key(product: dict) -> str:
    """Stable key for a product — its URL."""
    return product.get("url", "")


def check_product(product: dict, state: dict, dry_run: bool,
                  notifier: Notifier, force_browser: bool = False) -> dict:
    """Check a single product and return an updated state entry."""
    url = product.get("url")
    name = product.get("name") or url
    bought_price = product.get("bought_price")
    target_price = product.get("target_price")
    currency = product.get("currency", "INR")

    log.info("Checking: %s (%s)", name, url)

    current_price = fetch_price(url, force_browser=force_browser)
    entry = state.get(url, {"history": [], "last_alerted_price": None})

    if current_price is None:
        log.warning("Skipping %s — could not read price", name)
        entry["last_checked"] = now_ist()
        entry["last_error"] = "price not found"
        return entry

    # Record price history (keep last 90 points).
    entry.setdefault("history", [])
    entry["history"].append({
        "price": current_price,
        "checked_at": now_ist(),
    })
    entry["history"] = entry["history"][-90:]
    entry["last_price"] = current_price
    entry["last_checked"] = now_ist()
    entry.pop("last_error", None)

    # Decide whether to alert.
    alert_reason = None
    if target_price is not None and current_price <= target_price:
        alert_reason = (
            f"Price ₹{current_price:,.2f} is at/below your target "
            f"price ₹{target_price:,.2f}"
        )
    elif bought_price is not None and current_price < bought_price:
        alert_reason = (
            f"Price ₹{current_price:,.2f} is below what you paid "
            f"(₹{bought_price:,.2f}) — return & rebuy opportunity"
        )

    if alert_reason:
        last_alerted = entry.get("last_alerted_price")
        # Re-alert only if the price dropped further since we last alerted.
        if last_alerted is None or current_price < last_alerted:
            if not dry_run:
                notifier.send_alert(
                    product_name=name,
                    url=url,
                    current_price=current_price,
                    bought_price=bought_price,
                    target_price=target_price,
                    reason=alert_reason,
                )
            entry["last_alerted_price"] = current_price
            entry["last_alerted_at"] = now_ist()
            log.info("ALERT: %s — %s", name, alert_reason)
        else:
            log.info("Already alerted at ₹%.2f — no repeat alert for %s",
                     last_alerted, name)
    else:
        log.info("No alert — ₹%.2f is above your targets", current_price)

    return entry


def run(config_path: Path = DEFAULT_CONFIG, dry_run: bool = False,
        force_browser: bool = False) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    config = load_config(config_path)
    notifier_cfg = config.get("notifications", {})
    notifier = Notifier(notifier_cfg)
    if dry_run:
        notifier.enabled = False
        log.info("DRY RUN — notifications disabled")

    products = config.get("products", [])
    if not products:
        log.warning("No products found in config. Add some under 'products:'")
        return 0

    state = load_state()
    changed = False

    for product in products:
        url = product_key(product)
        if not url:
            log.warning("Skipping product with no url: %s", product.get("name"))
            continue
        entry = check_product(product, state, dry_run, notifier, force_browser)
        state[url] = entry
        changed = True

    if changed:
        save_state(state)

    log.info("Finished checking %d product(s)", len(products))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Track H&M India prices and alert on drops.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG),
                        help="Path to config.yaml")
    parser.add_argument("--dry-run", action="store_true",
                        help="Check prices but do not send notifications")
    parser.add_argument("--browser", action="store_true",
                        help="Force the Playwright browser fetcher (bypasses bot protection)")
    args = parser.parse_args()

    # HM_USE_BROWSER env var (e.g. set in GitHub Actions) forces browser mode.
    force_browser = args.browser or os.environ.get("HM_USE_BROWSER", "").lower() in ("1", "true", "yes")

    return run(Path(args.config), dry_run=args.dry_run, force_browser=force_browser)


if __name__ == "__main__":
    sys.exit(main())
