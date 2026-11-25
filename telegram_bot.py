"""Telegram bot for remote product management.

Run this on your PC (keep it running in a terminal window) and you can add,
list, and remove tracked products from your phone via Telegram.

Tracks products across H&M India and Myntra. Each product can have multiple
sources (one URL per site). Baseline price is set automatically on first check.

Commands (send to your bot):
  /add <url>                          Add a product URL (H&M or Myntra)
  /list                               List all tracked products with baselines
  /remove <name-or-#>                 Remove a tracked product
  /status                             Check current price & sizes of all products
  /help                               Show this help

Setup:
  1. Create a bot via @BotFather on Telegram and get your bot token.
  2. Get your chat ID (send /start to @userinfobot).
  3. Set HM_TELEGRAM_BOT_TOKEN and HM_TELEGRAM_CHAT_ID as environment variables,
     or put them in config.yaml under notifications.telegram.
  4. Run: python telegram_bot.py

Only your authorized chat ID can control the bot.
"""

from __future__ import annotations

import logging
import os
import re
import signal
import sys
import time
from pathlib import Path
from typing import Optional

import requests
import yaml

log = logging.getLogger("hm-bot")

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.yaml"
STATE_PATH = BASE_DIR / "data" / "prices.json"

HM_URL_RE = re.compile(r"hm\.com/en_in/productpage\.\d+\.html")
MYNTRA_URL_RE = re.compile(r"myntra\.com")

POLL_INTERVAL = 2.0  # seconds between getUpdates polls


def _detect_site(url: str) -> str:
    if MYNTRA_URL_RE.search(url):
        return "myntra"
    if HM_URL_RE.search(url):
        return "hm"
    return "hm"  # default fallback


class TelegramBot:
    """A simple polling Telegram bot for managing price tracking."""

    def __init__(self, config_path: Path = CONFIG_PATH) -> None:
        self.config_path = config_path
        self.config = self._load_config()
        self._load_credentials()
        self.offset: Optional[int] = None
        self._running = True

        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

    # ------------------------------------------------------------------ #
    # Config / credentials
    # ------------------------------------------------------------------ #
    def _load_config(self) -> dict:
        if self.config_path.exists():
            with self.config_path.open("r", encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {}
        return {}

    def _save_config(self) -> None:
        with self.config_path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(self.config, fh, sort_keys=False, allow_unicode=True)

    def _load_state(self) -> dict:
        if STATE_PATH.exists():
            try:
                with STATE_PATH.open("r", encoding="utf-8") as fh:
                    return yaml.safe_load(fh) or {}
            except Exception:
                return {}
        return {}

    def _load_credentials(self) -> None:
        tg = self.config.get("notifications", {}).get("telegram", {}) or {}
        self.bot_token = tg.get("bot_token") or os.environ.get("HM_TELEGRAM_BOT_TOKEN")
        self.chat_id = tg.get("chat_id") or os.environ.get("HM_TELEGRAM_CHAT_ID")
        if not self.bot_token or not self.chat_id:
            log.warning(
                "Telegram not fully configured. Set HM_TELEGRAM_BOT_TOKEN and "
                "HM_TELEGRAM_CHAT_ID, or fill them in config.yaml."
            )

    def _api_url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.bot_token}/{method}"

    # ------------------------------------------------------------------ #
    # Telegram API
    # ------------------------------------------------------------------ #
    def _tg_request(self, method: str, params: dict) -> Optional[dict]:
        if not self.bot_token:
            return None
        try:
            resp = requests.post(
                self._api_url(method), json=params, timeout=15
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                log.warning("Telegram API error: %s", data.get("description"))
                return None
            return data.get("result")
        except requests.RequestException as exc:
            log.warning("Telegram request failed: %s", exc)
            return None

    def send_message(self, text: str, parse_mode: str = "HTML") -> None:
        if not self.bot_token or not self.chat_id:
            return
        self._tg_request(
            "sendMessage",
            {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            },
        )

    def get_updates(self) -> list:
        if not self.bot_token:
            return []
        params = {"timeout": 30, "allowed_updates": ["message"]}
        if self.offset is not None:
            params["offset"] = self.offset
        try:
            resp = requests.post(
                self._api_url("getUpdates"), json=params, timeout=35
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                return []
            return data.get("result", [])
        except requests.RequestException:
            return []

    # ------------------------------------------------------------------ #
    # Product helpers
    # ------------------------------------------------------------------ #
    def _products(self) -> list:
        return self.config.setdefault("products", [])

    def _find_product_index(self, ref: str) -> Optional[int]:
        """Find product index by name match or list position (1-based)."""
        products = self._products()
        # Try exact name match (case-insensitive).
        for i, p in enumerate(products):
            if p.get("name", "").lower() == ref.lower():
                return i
        # Try index (1-based).
        try:
            idx = int(ref) - 1
            if 0 <= idx < len(products):
                return idx
        except ValueError:
            pass
        return None

    def _source_urls(self, product: dict) -> list:
        """Return list of (site, url) tuples for a product."""
        sources = product.get("sources", [])
        if sources:
            return [(s.get("site", "hm"), s.get("url", "")) for s in sources]
        # Backward compat: single url field.
        url = product.get("url", "")
        site = product.get("site", _detect_site(url))
        return [(site, url)] if url else []

    # ------------------------------------------------------------------ #
    # Command handlers
    # ------------------------------------------------------------------ #
    def _cmd_help(self) -> str:
        return (
            "<b>Price Tracker — Bot Commands</b>\n\n"
            "/add &lt;url&gt;\n"
            "  Add an H&M or Myntra product URL to track.\n\n"
            "/list\n"
            "  Show all tracked products with baseline prices & sizes.\n\n"
            "/remove &lt;name or #&gt;\n"
            "  Remove a tracked product (use # from /list).\n\n"
            "/status\n"
            "  Check current price & available sizes of all products.\n\n"
            "/help\n"
            "  Show this message.\n\n"
            "Tip: paste a product link from H&M India or Myntra. You'll be alerted\n"
            "when the price drops below your baseline AND sizes are available.\n"
        )

    def _cmd_add(self, args: list) -> str:
        if not args:
            return "Usage: /add &lt;url&gt;"

        url = args[0].strip()
        site = _detect_site(url)

        if site == "hm" and not HM_URL_RE.search(url):
            return (
                "That doesn't look like an H&amp;M India product URL.\n"
                "Expected: https://www2.hm.com/en_in/productpage.1234567.html"
            )

        # Check if product already exists (by name derived from URL, or URL already in sources).
        products = self._products()
        for p in products:
            for s_site, s_url in self._source_urls(p):
                if s_url == url:
                    return f"That URL is already being tracked:\n<code>{url}</code>"

        # Use URL as name (user can rename later).
        name = url.split("/")[-1]
        product: dict = {
            "name": name,
            "currency": "INR",
            "sources": [{"site": site, "url": url}],
        }
        products.append(product)
        self._save_config()

        site_label = site.upper()
        return (
            f"✅ Added ({site_label}): <code>{url}</code>\n"
            f"Baseline price & sizes will be set on the first check."
        )

    def _cmd_list(self) -> str:
        products = self._products()
        if not products:
            return "No products being tracked yet. Use /add &lt;url&gt; to add one."

        state = self._load_state()
        lines = ["<b>Tracked products:</b>"]
        for i, p in enumerate(products, 1):
            name = p.get("name") or "Untitled"
            product_state = state.get(name, {})
            src_states = product_state.get("sources", {})
            lines.append(f"{i}. <b>{name}</b>")
            for site, url in self._source_urls(p):
                src = src_states.get(url, {})
                baseline = src.get("baseline_price")
                sizes = src.get("sizes", [])
                size_str = f" | sizes: {', '.join(sizes)}" if sizes else " | sizes: —"
                baseline_str = f"baseline ₹{baseline:,.2f}" if baseline else "baseline: —"
                lines.append(f"   [{site.upper()}] {baseline_str}{size_str}")
                lines.append(f"      <code>{url}</code>")
        return "\n".join(lines)

    def _cmd_remove(self, args: list) -> str:
        if not args:
            return "Usage: /remove &lt;name or #&gt;"
        idx = self._find_product_index(args[0])
        if idx is None:
            return f"Couldn't find a tracked product matching: {args[0]}"
        removed = self._products().pop(idx)
        self._save_config()
        return f"🗑️ Removed: {removed.get('name') or removed.get('url')}"

    def _cmd_status(self) -> str:
        from scraper import fetch_price_and_sizes as fetch_hm
        from myntra_scraper import fetch_price_and_sizes as fetch_myntra

        products = self._products()
        if not products:
            return "No products being tracked yet."
        lines = ["<b>Current prices & sizes:</b>"]
        for p in products:
            name = p.get("name") or "Untitled"
            lines.append(f"• <b>{name}</b>")
            for site, url in self._source_urls(p):
                try:
                    if site == "myntra":
                        price, sizes = fetch_myntra(url, force_browser=True)
                    else:
                        price, sizes = fetch_hm(url, force_browser=True)
                except Exception as exc:  # noqa: BLE001
                    log.warning("Status check failed for %s: %s", url, exc)
                    price, sizes = None, []
                if price is not None:
                    size_str = f" | sizes: {', '.join(sizes)}" if sizes else " | sizes: —"
                    lines.append(f"   [{site.upper()}] ₹{price:,.2f}{size_str}")
                else:
                    lines.append(f"   [{site.upper()}] <i>could not read price</i>")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Message dispatch
    # ------------------------------------------------------------------ #
    def handle_message(self, message: dict) -> None:
        chat = message.get("chat", {})
        sender_id = str(chat.get("id", ""))
        if sender_id != str(self.chat_id):
            log.debug("Ignoring message from unauthorized chat %s", sender_id)
            return

        text = message.get("text", "").strip()
        if not text:
            return

        log.info("Message from %s: %s", sender_id, text)

        if text.startswith("/"):
            parts = text.split()
            command = parts[0].lower()
            args = parts[1:]

            if command in ("/start", "/help"):
                reply = self._cmd_help()
            elif command == "/add":
                reply = self._cmd_add(args)
            elif command == "/list":
                reply = self._cmd_list()
            elif command == "/remove":
                reply = self._cmd_remove(args)
            elif command == "/status":
                reply = self._cmd_status()
            else:
                reply = f"Unknown command: {command}\nSend /help for available commands."
        else:
            # Treat plain text as a URL to add.
            reply = self._cmd_add([text])

        self.send_message(reply)

    # ------------------------------------------------------------------ #
    # Main loop
    # ------------------------------------------------------------------ #
    def _shutdown(self, signum, frame) -> None:  # noqa: ARG002
        log.info("Shutting down...")
        self._running = False

    def run(self) -> None:
        if not self.bot_token or not self.chat_id:
            log.error(
                "Telegram credentials not configured. Set HM_TELEGRAM_BOT_TOKEN "
                "and HM_TELEGRAM_CHAT_ID."
            )
            sys.exit(1)

        log.info("Bot started. Send /help to your bot from Telegram.")
        self.send_message("🤖 Price Tracker bot is online. Send /help for commands.")

        while self._running:
            try:
                updates = self.get_updates()
                for update in updates:
                    self.offset = update["update_id"] + 1
                    message = update.get("message")
                    if message:
                        self.handle_message(message)
            except KeyboardInterrupt:
                break
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                log.warning("Loop error: %s", exc)
            if self._running:
                time.sleep(POLL_INTERVAL)

        log.info("Bot stopped.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    bot = TelegramBot()
    bot.run()
