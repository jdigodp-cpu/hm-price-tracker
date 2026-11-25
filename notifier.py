"""Notification backends for the H&M price tracker.

Supports:
  * Telegram Bot API (simple HTTPS POST, no bot account management)
  * Email via SMTP (TLS)

Each backend is independent — enable the ones you want in config.yaml.
All secrets can come from environment variables so you never commit them.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Optional

import requests

log = logging.getLogger("hm-tracker")


def _env(key: str, default: Optional[str] = None) -> Optional[str]:
    return os.environ.get(key, default)


class Notifier:
    """Send price-drop alerts through one or more channels."""

    def __init__(self, config: dict) -> None:
        self.cfg = config or {}
        self.enabled = bool(self.cfg.get("enabled", True))

    # ------------------------------------------------------------------ #
    # Telegram
    # ------------------------------------------------------------------ #
    def _telegram(self, message: str) -> bool:
        tg = self.cfg.get("telegram", {}) or {}
        if not tg.get("enabled"):
            return False
        bot_token = tg.get("bot_token") or _env("HM_TELEGRAM_BOT_TOKEN")
        chat_id = tg.get("chat_id") or _env("HM_TELEGRAM_CHAT_ID")
        if not bot_token or not chat_id:
            log.warning("Telegram not configured — missing bot_token or chat_id")
            return False
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            resp = requests.post(url, json=payload, timeout=15)
            resp.raise_for_status()
            log.info("Telegram alert sent")
            return True
        except requests.RequestException as exc:
            log.warning("Telegram send failed: %s", exc)
            return False

    # ------------------------------------------------------------------ #
    # Email
    # ------------------------------------------------------------------ #
    def _email(self, subject: str, body: str) -> bool:
        em = self.cfg.get("email", {}) or {}
        if not em.get("enabled"):
            return False
        host = em.get("smtp_host")
        port = int(em.get("smtp_port", 587))
        username = em.get("username") or _env("HM_EMAIL_USERNAME")
        password = em.get("password") or _env("HM_EMAIL_PASSWORD")
        to = em.get("to") or _env("HM_EMAIL_TO")
        if not all([host, username, password, to]):
            log.warning("Email not configured — missing SMTP settings")
            return False
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = username
        msg["To"] = to
        msg.set_content(body)
        context = ssl.create_default_context()
        try:
            with smtplib.SMTP(host, port, timeout=20) as server:
                server.starttls(context=context)
                server.login(username, password)
                server.send_message(msg)
            log.info("Email alert sent to %s", to)
            return True
        except Exception as exc:  # noqa: BLE001 - network issues are expected
            log.warning("Email send failed: %s", exc)
            return False

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def send_alert(self, product_name: str, url: str, current_price: float,
                   bought_price: Optional[float], target_price: Optional[float],
                   reason: str, site: str = "hm") -> None:
        """Fire an alert across every enabled channel."""
        if not self.enabled:
            return

        site_label = site.upper() if site else "H&M"
        lines = [
            f"🛍️ <b>{site_label} price alert</b>",
            f"<b>{product_name}</b>",
            f"Current price: <b>₹{current_price:,.2f}</b>",
        ]
        if bought_price is not None:
            lines.append(f"You paid: ₹{bought_price:,.2f}")
        if target_price is not None:
            lines.append(f"Target price: ₹{target_price:,.2f}")
        lines.append(f"Reason: {reason}")
        lines.append(f"<a href=\"{url}\">View product</a>")
        message = "\n".join(lines)

        subject = f"{site_label} price drop: {product_name} — ₹{current_price:,.2f}"
        email_body = "\n".join(
            f"{k}: {v}" for k, v in [
                ("Product", product_name),
                ("Site", site_label),
                ("Current price", f"₹{current_price:,.2f}"),
                ("You paid", f"₹{bought_price:,.2f}" if bought_price is not None else "—"),
                ("Target price", f"₹{target_price:,.2f}" if target_price is not None else "—"),
                ("Reason", reason),
                ("Link", url),
            ]
        )

        self._telegram(message)
        self._email(subject, email_body)


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    n = Notifier({
        "enabled": True,
        "telegram": {
            "enabled": True,
            "bot_token": _env("HM_TELEGRAM_BOT_TOKEN"),
            "chat_id": _env("HM_TELEGRAM_CHAT_ID"),
        },
        "email": {"enabled": False},
    })
    n.send_alert("Test Cotton Shirt", "https://www2.hm.com/en_in/productpage.000000.html",
                 799.0, 1499.0, 999.0, "Price is below your target price")
