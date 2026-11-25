# H&M & Myntra Price Tracker

Track prices on H&M India and Myntra. Get alerted (Telegram and/or email)
whenever a tracked product's price drops below your baseline — but only when
sizes are available. Manage everything from your phone via Telegram.

Built as a standalone project — it does not touch any of your other
repositories.

## How it works

1. You add product URLs from H&M India and/or Myntra (via `add_product.py` or
   the Telegram bot `/add` command).
2. Each product can have multiple sources — e.g. the same item on both H&M and
   Myntra. We track each source independently.
3. On the first check, we record the **baseline price** and available sizes.
4. On every subsequent check (every 3 hours via GitHub Actions), if the price
   drops below baseline **and** at least one size is available, you get an
   alert.
5. The baseline then resets to the new lower price, so you're alerted on
   further drops too.

## Features

| Feature | Description |
|---------|-------------|
| **Multi-site** | Track H&M India and Myntra in one place |
| **Multi-source** | Same product on both sites — alert on either price drop |
| **Size availability** | Only alert when sizes are actually in stock |
| **Baseline tracking** | Alerts on any drop below the price when you started tracking |
| **Telegram bot** | Add/list/remove/check from your phone |
| **Scheduled** | Runs every 3 hours on GitHub Actions |

## Files

| File | Purpose |
|------|---------|
| `config.yaml` | Your products, sources, and notification settings (gitignored) |
| `config.yaml.example` | Template — copy to `config.yaml` |
| `main.py` | Tracker logic: fetch, compare vs baseline, alert |
| `scraper.py` | H&M India price + size extraction |
| `myntra_scraper.py` | Myntra price + size extraction |
| `notifier.py` | Telegram Bot API + SMTP email alerts |
| `telegram_bot.py` | Two-way Telegram bot — manage from your phone |
| `add_product.py` | Interactive helper to add products from your PC |
| `requirements.txt` | Python dependencies |
| `.github/workflows/track.yml` | Scheduled GitHub Actions runs |
| `data/prices.json` | Price history + baseline state (gitignored) |

## One-time setup

### 1. Create the project config

```powershell
cd C:\Users\raaaaaaj\VSCode\hm-price-tracker

# Copy the template
copy config.yaml.example config.yaml

# Edit config.yaml and add your real product URLs
notepad config.yaml
```

### 2. Install dependencies

```powershell
& "C:\Users\raaaaaaj\AppData\Local\Programs\Python\Python311\python.exe" -m pip install -r requirements.txt
```

### 3. Install Chromium browser (required to read prices)

H&M and Myntra block plain HTTP requests, so the tracker uses a real browser
(Playwright + Chromium). Install it once:

```powershell
& "C:\Users\raaaaaaj\AppData\Local\Programs\Python\Python311\python.exe" -m playwright install chromium --with-deps
```

The GitHub Actions workflow installs Chromium automatically on every scheduled
run, so it works in the cloud even if this step fails on your PC.

### 4. Add products to track

**Option A — Interactive helper (PC):**
```powershell
& "C:\Users\raaaaaaj\AppData\Local\Programs\Python\Python311\python.exe" add_product.py
```

**Option B — From your phone (Telegram bot):**
Set up the bot (step 6), then message it `/add <url>`.

**Option C — Edit config.yaml by hand:**
Add entries under `products:` with `sources:` listing `{site, url}` pairs.

### 5. Test locally (dry run — no notifications sent)

```powershell
& "C:\Users\raaaaaaj\AppData\Local\Programs\Python\Python311\python.exe" main.py --dry-run
```

### 6. Set up notifications & Telegram bot

**Telegram (recommended):**
1. Create a bot via [@BotFather](https://t.me/botfather) — get your bot token.
2. Get your chat ID (send `/start` to [@userinfobot](https://t.me/userinfobot)).
3. Add these to `config.yaml` under `notifications.telegram`, or as GitHub
   Actions secrets:
   - `HM_TELEGRAM_BOT_TOKEN`
   - `HM_TELEGRAM_CHAT_ID`

**Run the bot** (keep it running in a terminal on your PC):

```powershell
& "C:\Users\raaaaaaj\AppData\Local\Programs\Python\Python311\python.exe" telegram_bot.py
```

**From your phone, message your bot:**

| Command | What it does |
|---------|-------------|
| `/add <url>` | Add an H&M or Myntra product URL |
| `/list` | Show all tracked products with baselines & sizes |
| `/remove <name or #>` | Remove a tracked product |
| `/status` | Check current price & sizes of all products |
| `/help` | Show all commands |

Only your authorized chat ID can control the bot.

> **Note:** The bot needs to stay running to receive messages. Keep that
> terminal window open on your PC.

### 7. Automate with GitHub Actions

1. Push this folder to a new GitHub repository named `hm-price-tracker`.
2. Go to Settings → Secrets and variables → Actions → New repository secret and
   add:

   | Secret | Value |
   |--------|-------|
   | `HM_TELEGRAM_BOT_TOKEN` | Your Telegram bot token |
   | `HM_TELEGRAM_CHAT_ID` | Your Telegram chat ID |
   | (optional email secrets) | … |

3. The workflow runs every 3 hours and can be triggered manually from the
   **Actions** tab. Logs are uploaded as an artifact.

## Return & rebuy logic

Since we track baseline price (the price when you first added the item), you're
alerted whenever the current price drops below that baseline — your signal to
buy again at a lower price. If you set `bought_price` separately, we also alert
when the price drops below what you originally paid.

## Notes & caveats

- **Bot protection:** H&M and Myntra both block plain HTTP requests from
  datacenter IPs. The tracker uses Playwright + Chromium to bypass this.
  Chromium is installed automatically in GitHub Actions.
- **Size availability:** We only alert when at least one size is in stock, so
  you won't get notified about a price drop on a sold-out item.
- Price scraping depends on site structure. If a price stops being detected,
  re-run with `--dry-run` to see the logs.
- Use responsibly — one check per product every few hours is polite; don't
  hammer the sites.
- Your `config.yaml` and `data/` folder are gitignored so prices and secrets
  never leave your machine / repo secrets.

## License

Personal use. Built with 💙 for raaaaaaj.
