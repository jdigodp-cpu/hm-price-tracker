# H&M India Price Tracker

Track prices on H&M India and get alerted (Telegram and/or email) whenever a
product hits your target price — or drops below what you paid, so you can
return it and buy it again at the lower price.

Built as a standalone project — it does not touch any of your other
repositories.

## How it works

1. You list H&M India product URLs in `config.yaml`, along with the price you
   paid (`bought_price`) and/or a "steal price" you'd buy at (`target_price`).
2. `main.py` fetches each product page, extracts the current price, and compares
   it against your targets.
3. If the price qualifies, you get an alert (Telegram message and/or email).
4. Price history is saved in `data/prices.json` so you aren't re-alerted
   every run — only when the price drops further.
5. GitHub Actions runs the check on a schedule (every 3 hours by default) and
   can be triggered manually anytime.

## Files

| File | Purpose |
|------|---------|
| `config.yaml` | Your products, prices, and notification settings (gitignored) |
| `config.yaml.example` | Template — copy to `config.yaml` and fill in |
| `main.py` | Tracker logic: fetch, compare, alert |
| `scraper.py` | Pulls the current price from an H&M India product page |
| `notifier.py` | Telegram Bot API + SMTP email alerts |
| `telegram_bot.py` | Two-way Telegram bot — add/remove/list products from your phone |
| `add_product.py` | Interactive helper to add products from your PC |
| `requirements.txt` | Python dependencies |
| `.github/workflows/track.yml` | Scheduled GitHub Actions runs |
| `data/prices.json` | Price history + last-alerted state (gitignored) |

## One-time setup

### 1. Create the project config

```powershell
cd C:\Users\raaaaaaj\VSCode\hm-price-tracker

# Copy the template
copy config.yaml.example config.yaml

# Open config.yaml and add your real H&M India product URLs,
# bought prices, and target prices.
notepad config.yaml
```

### 2. Install dependencies (local testing)

```powershell
# Using the Python 3.11 you already have
& "C:\Users\raaaaaaj\AppData\Local\Programs\Python\Python311\python.exe" -m pip install -r requirements.txt
```

### 3. Install Chromium browser (required to read H&M prices)

H&M India blocks plain HTTP requests, so the tracker uses a real browser
(Playwright + Chromium) to fetch prices. Install it once:

```powershell
& "C:\Users\raaaaaaj\AppData\Local\Programs\Python\Python311\python.exe" -m playwright install chromium --with-deps
```

If that command hangs or produces no output (some networks block browser
downloads), run it from a different network or skip this step — the GitHub
Actions workflow installs Chromium automatically on every scheduled run.

### 4. Add products to track (the easy way)

Don't edit `config.yaml` by hand — run the helper script instead:

```powershell
& "C:\Users\raaaaaaj\AppData\Local\Programs\Python\Python311\python.exe" add_product.py
```

It will prompt you for:
1. **H&M product page URL** — copy it from your browser when viewing a product
   on `www2.hm.com/en_in` (format: `https://www2.hm.com/en_in/productpage.1234567.html`)
2. **Product name** — a friendly label for alerts (press Enter to use the URL)
3. **Price you paid** (₹) — for "return & rebuy" alerts when it drops below this
4. **Steal / target price** (₹) — alert when the price hits this or lower

You can run `add_product.py` as many times as you like — each product gets
appended to `config.yaml`. At least one of "price you paid" or "target price"
must be set.

Or, if you prefer editing by hand, just add entries under `products:` in
`config.yaml` (see `config.yaml.example` for the format).

### 6. Control from your phone (Telegram bot)

Instead of editing files, you can manage your tracked products by messaging
your Telegram bot from your phone. This turns the bot into a remote control
for the tracker.

**One-time setup:**
1. Create a bot via [@BotFather](https://t.me/botfather) on Telegram and get
   your bot token.
2. Get your chat ID (send `/start` to [@userinfobot](https://t.me/userinfobot)).
3. Add these to `config.yaml` under `notifications.telegram`, or set them as
   environment variables:
   - `HM_TELEGRAM_BOT_TOKEN`
   - `HM_TELEGRAM_CHAT_ID`

**Run the bot** (keep it running in a terminal on your PC):

```powershell
& "C:\Users\raaaaaaj\AppData\Local\Programs\Python\Python311\python.exe" telegram_bot.py
```

**Then from your phone, message your bot:**

| Command | What it does |
|---------|-------------|
| `/add <url>` | Add a product to track |
| `/add <url> <target_price>` | Add + alert when price hits this (₹) |
| `/add <url> <bought_price> <target_price>` | Add with both prices |
| `/list` | Show all tracked products |
| `/remove <# or url>` | Remove a product (use # from `/list`) |
| `/status` | Check current price of every product |
| `/help` | Show all commands |

Example: paste an H&M product link from your phone:

```
/add https://www2.hm.com/en_in/productpage.1359341002.html 1499
```

The bot replies confirming the product was added. Only your authorized chat ID
can control the bot — strangers can't mess with your list.

> **Note:** The bot needs to stay running to receive messages. Run it in a
> terminal window on your PC and keep that window open. For `/status` to read
> live prices, you'll also need Chromium installed (step 3 above) — otherwise
> it'll report "could not read price" for each item.

### 7. Test it locally (dry run — no notifications sent)

```powershell
& "C:\Users\raaaaaaj\AppData\Local\Programs\Python\Python311\python.exe" main.py --dry-run
```

### 8. Set up notifications

**Telegram (recommended — you already use Telegram):**
- Create a bot via [@BotFather](https://t.me/botfather) on Telegram and get a
  bot token.
- Get your chat ID (send `/start` to [@userinfobot](https://t.me/userinfobot)).
- Add these as GitHub Actions secrets (below) or set them in `config.yaml`:
  - `HM_TELEGRAM_BOT_TOKEN`
  - `HM_TELEGRAM_CHAT_ID`

**Email (optional):**
- Use an app password (not your regular password) if using Gmail.
- Secrets: `HM_EMAIL_USERNAME`, `HM_EMAIL_PASSWORD`, `HM_EMAIL_TO`

### 9. Automate with GitHub Actions

1. Push this folder to a new GitHub repository.
2. Go to Settings → Secrets and variables → Actions → New repository secret and
   add:
   | Secret | Value |
   |--------|-------|
   | `HM_TELEGRAM_BOT_TOKEN` | Your Telegram bot token |
   | `HM_TELEGRAM_CHAT_ID` | Your Telegram chat ID |
   | (optional email secrets) | … |
3. The workflow runs every 3 hours and can be triggered manually from the
   **Actions** tab (`workflow_dispatch`). Results and logs are uploaded as an
   artifact.

## Return & rebuy logic

If you set `bought_price` on a product, the tracker alerts you whenever the
current price drops below it — that's your signal that you could return the
item (within H&M's return window) and buy it again at the lower price.

## Notes & caveats

- **Bot protection:** H&M India is protected by Akamai, so plain HTTP requests
  often return `403` from datacenter IPs (e.g. GitHub Actions). The tracker
  handles this with a two-tier fetcher:
  1. `requests` (fast) — works from a machine that already has an H&M session.
  2. Playwright + real Chromium (robust) — passes Akamai's browser checks.
     The GitHub Actions workflow installs Chromium automatically and runs in
     browser mode via `HM_USE_BROWSER=1`.
- H&M India pages change occasionally. If a price stops being detected, open
  an issue or re-run with `--dry-run` to see the logs.
- Use responsibly — one check per product every few hours is polite; don't
  hammer the site.
- Your `config.yaml` and `data/` folder are gitignored so prices and secrets
  never leave your machine / repo secrets.

## License

Personal use. Built with 💙 for raaaaaaj.
