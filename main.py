"""H&M India Price Tracker — main entry point.

Tracks products across multiple sites (H&M India, Myntra). Each product can
have several sources (one URL per site). For each source we store a baseline
price (the price when first tracked). If the current price drops below the
baseline AND at least one size is available, we alert.

Run once:
    python main.py

Dry run (no notifications):
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

from scraper import fetch_price_and_sizes
from myntra_scraper import fetch_price_and_sizes as fetch_myntra_price_and_sizes
from notifier import Notifier

log = logging.getLogger("hm-tracker")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
STATE_FILE = DATA_DIR / "prices.json"

DEFAULT_CONFIG = BASE_DIR / "config.yaml"
EXAMPLE_CONFIG = BASE_DIR / "config.yaml.example"

# Site name -> fetch function.
FETCHERS = {
    "hm": fetch_price_and_sizes,
    "myntra": fetch_myntra_price_and_sizes,
}


def now_ist() -> str:
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


def source_key(source: dict) -> str:
    """Stable key for a source — its URL."""
    return source.get("url", "")


def fetch_for_source(source: dict, force_browser: bool = False):
    """Fetch price and sizes for a single source.

    Returns (price, available_sizes) or (None, []) on failure.
    """
    site = source.get("site", "hm").lower()
    url = source.get("url", "")
    fetcher = FETCHERS.get(site, fetch_price_and_sizes)
    try:
        return fetcher(url, force_browser=force_browser)
    except Exception as exc:  # noqa: BLE001
        log.warning("Fetch failed for %s (%s): %s", url, site, exc)
        return None, []


def check_product(product: dict, state: dict, dry_run: bool,
                  notifier: Notifier, force_browser: bool = False) -> dict:
    """Check all sources of a product. Alert if any source's price drops
    below its baseline AND sizes are available."""
    name = product.get("name") or "Untitled product"
    sources = product.get("sources", [])
    currency = product.get("currency", "INR")

    log.info("Checking: %s (%d source(s))", name, len(sources))

    product_state = state.get(name, {"sources": {}})
    product_state.setdefault("sources", {})

    any_alert = False

    for source in sources:
        url = source.get("url")
        site = source.get("site", "hm").lower()
        if not url:
            log.warning("Skipping source with no url in %s", name)
            continue

        src_state = product_state["sources"].get(url, {
            "baseline_price": None,
            "last_alerted_price": None,
            "sizes": [],
        })

        log.info("  [%s] %s", site.upper(), url)

        current_price, available_sizes = fetch_for_source(source, force_browser)

        if current_price is None:
            log.warning("    Could not read price for %s", url)
            src_state["last_checked"] = now_ist()
            src_state["last_error"] = "price not found"
            product_state["sources"][url] = src_state
            continue

        # Record size availability.
        src_state["sizes"] = available_sizes
        src_state["last_price"] = current_price
        src_state["last_checked"] = now_ist()
        src_state.pop("last_error", None)

        # Set baseline on first sighting.
        baseline = src_state.get("baseline_price")
        if baseline is None:
            src_state["baseline_price"] = current_price
            baseline = current_price
            log.info("    Baseline set: ₹%.2f (sizes: %s)", baseline,
                     ", ".join(available_sizes) if available_sizes else "none")

        # Check for price drop below baseline + size availability.
        has_sizes = len(available_sizes) > 0
        if current_price < baseline and has_sizes:
            last_alerted = src_state.get("last_alerted_price")
            if last_alerted is None or current_price < last_alerted:
                if not dry_run:
                    notifier.send_alert(
                        product_name=name,
                        url=url,
                        current_price=current_price,
                        bought_price=None,
                        target_price=None,
                        reason=(
                            f"Price dropped to ₹{current_price:,.2f} on {site.upper()} "
                            f"(baseline was ₹{baseline:,.2f}). "
                            f"Available sizes: {', '.join(available_sizes)}"
                        ),
                        site=site,
                    )
                src_state["baseline_price"] = current_price
                src_state["last_alerted_price"] = current_price
                src_state["last_alerted_at"] = now_ist()
                any_alert = True
                log.info("    ALERT: ₹%.2f < baseline ₹%.2f, sizes: %s",
                         current_price, baseline, ", ".join(available_sizes))
            else:
                log.info("    Already alerted at ₹%.2f — no repeat", last_alerted)
        elif current_price < baseline and not has_sizes:
            log.info("    Price dropped to ₹%.2f but no sizes available — skipping alert",
                     current_price)
        else:
            log.info("    No alert — ₹%.2f is at/above baseline ₹%.2f",
                     current_price, baseline)

        product_state["sources"][url] = src_state

    state[name] = product_state
    return product_state


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

    for product in products:
        check_product(product, state, dry_run, notifier, force_browser)

    save_state(state)

    log.info("Finished checking %d product(s)", len(products))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Track H&M and Myntra prices and alert on drops.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG),
                        help="Path to config.yaml")
    parser.add_argument("--dry-run", action="store_true",
                        help="Check prices but do not send notifications")
    parser.add_argument("--browser", action="store_true",
                        help="Force the Playwright browser fetcher")
    args = parser.parse_args()

    force_browser = args.browser or os.environ.get("HM_USE_BROWSER", "").lower() in ("1", "true", "yes")

    return run(Path(args.config), dry_run=args.dry_run, force_browser=force_browser)


if __name__ == "__main__":
    sys.exit(main())
