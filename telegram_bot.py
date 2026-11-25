"""Telegram bot for remote product management.

Run this on your PC (keep it running in a terminal window) and you can add,
list, and remove tracked H&M products from your phone via Telegram.

Commands (send to your bot):
  /add <url>                          Add a product (no price targets)
  /add <url> <target_price>           Add with a steal/target price (₹)
  /add <url> <bought_price> <target_price>  Add with both prices
  /list                               List all tracked products
  /remove <url-or-index>              Remove a product by URL or list index
  /status                             Show current price of every tracked product
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
HM_URL_RE = re.compile(r"https?://(?:www2\.)?hm\.com/en_in/productpage\.\d+\.html")

POLL_INTERVAL = 2.0  # seconds between getUpdates polls


class TelegramBot:
    """A simple polling Telegram bot for managing H&M price tracking."""

    def __init__(self, config_path: Path = CONFIG_PATH) -> None:
        self.config_path = config_path
        self.config = self._load_config()
        self._load_credentials()
        self.offset: Optional[int] = None
        self._running = True

        # Wire up signal handlers for graceful shutdown.
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
        """Find product index by URL match or list position (1-based)."""
        products = self._products()
        # Try exact URL match first.
        for i, p in enumerate(products):
            if p.get("url") == ref:
                return i
        # Try index (1-based).
        try:
            idx = int(ref) - 1
            if 0 <= idx < len(products):
                return idx
        except ValueError:
            pass
        return None

    def _is_hm_url(self, url: str) -> bool:
        return bool(HM_URL_RE.search(url.strip()))

    # ------------------------------------------------------------------ #
    # Command handlers
    # ------------------------------------------------------------------ #
    def _cmd_help(self) -> str:
        return (
            "<b>H&amp;M Price Tracker — Bot Commands</b>\n\n"
            "/add &lt;url&gt;\n"
            "  Add a product to track.\n\n"
            "/add &lt;url&gt; &lt;target_price&gt;\n"
            "  Add and alert when price drops to/below this (₹).\n\n"
            "/add &lt;url&gt; &lt;bought_price&gt; &lt;target_price&gt;\n"
            "  Add with both what you paid and your steal price.\n\n"
            "/list\n"
            "  Show all tracked products.\n\n"
            "/remove &lt;url or #&gt;\n"
            "  Remove a product (use the number from /list).\n\n"
            "/status\n"
            "  Check current price of every tracked product.\n\n"
            "/help\n"
            "  Show this message.\n\n"
            "Tip: copy a product link from the H&amp;M India app/site and paste it "
            "here with /add."
        )

    def _cmd_add(self, args: list) -> str:
        if not args:
            return "Usage: /add &lt;url&gt; [bought_price] [target_price]"

        url = args[0].strip()
        if not self._is_hm_url(url):
            return (
                "That doesn't look like an H&amp;M India product URL.\n"
                "Expected: https://www2.hm.com/en_in/productpage.1234567.html"
            )

        # Check for duplicates.
        for p in self._products():
            if p.get("url") == url:
                return f"That product is already being tracked:\n{url}"

        bought_price: Optional[float] = None
        target_price: Optional[float] = None

        if len(args) >= 2:
            try:
                target_price = float(args[1].replace(",", "").replace("₹", ""))
            except ValueError:
                return f"Invalid price: {args[1]}"
        if len(args) >= 3:
            try:
                bought_price = float(args[2].replace(",", "").replace("₹", ""))
            except ValueError:
                return f"Invalid price: {args[2]}"

        product: dict = {"name": url, "url": url, "currency": "INR"}
        if bought_price is not None:
            product["bought_price"] = bought_price
        if target_price is not None:
            product["target_price"] = target_price

        self._products().append(product)
        self._save_config()

        lines = [f"✅ Added: <code>{url}</code>"]
        if bought_price is not None:
            lines.append(f"Bought price: ₹{bought_price:,.2f}")
        if target_price is not None:
            lines.append(f"Target price: ₹{target_price:,.2f}")
        return "\n".join(lines)

    def _cmd_list(self) -> str:
        products = self._products()
        if not products:
            return "No products being tracked yet. Use /add &lt;url&gt; to add one."
        lines = ["<b>Tracked products:</b>"]
        for i, p in enumerate(products, 1):
            name = p.get("name") or p.get("url")
            url = p.get("url", "")
            parts = [f"{i}. <b>{name}</b>"]
            if p.get("bought_price") is not None:
                parts.append(f"paid ₹{p['bought_price']:,.2f}")
            if p.get("target_price") is not None:
                parts.append(f"target ₹{p['target_price']:,.2f}")
            parts.append(f"<code>{url}</code>")
            lines.append(" · ".join(parts))
        return "\n".join(lines)

    def _cmd_remove(self, args: list) -> str:
        if not args:
            return "Usage: /remove &lt;url or #&gt;"
        idx = self._find_product_index(args[0])
        if idx is None:
            return f"Couldn't find a tracked product matching: {args[0]}"
        removed = self._products().pop(idx)
        self._save_config()
        return f"🗑️ Removed: {removed.get('name') or removed.get('url')}"

    def _cmd_status(self) -> str:
        from scraper import fetch_price

        products = self._products()
        if not products:
            return "No products being tracked yet."
        lines = ["<b>Current prices:</b>"]
        for p in products:
            url = p.get("url", "")
            name = p.get("name") or url
            try:
                price = fetch_price(url, force_browser=True)
            except Exception as exc:  # noqa: BLE001
                log.warning("Price check failed for %s: %s", url, exc)
                price = None
            if price is not None:
                lines.append(f"• {name}: ₹{price:,.2f}")
            else:
                lines.append(f"• {name}: <i>could not read price</i>")
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
        self.send_message("🤖 H&amp;M Price Tracker bot is online. Send /help for commands.")

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
