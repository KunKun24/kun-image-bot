# Kun Image Bot

**English** · [中文](README.md)

A Telegram-based AI image generation bot: dual upstream routes, preset templates, balance and daily check-in, USDT top-up (OkPay / TON / TRON), referral rebates, and an admin panel — plus a built-in **clone platform** that replicates the same bot into multiple independent sub-bots for separate operations.

- Author / Open source: **TG KunKun [@sad1881](https://t.me/sad1881)**
- Version: v2.0.0 (modular refactor)
- License: MIT

> This repository was refactored from a single 5,000-line script into 9 modules. It fixes broken history image review, hardcoded tokens, lost database WAL files, and missing dependency declarations, and fills in comments and documentation.

---

## Features

### User Side
| Feature | Description |
| --- | --- |
| Free Creation | Send text or a reference image → prompt optimization → image generation, with size and clarity selection |
| Official Presets | Multiple preset templates (template image + fields + requirement image), charged at the preset price |
| My Designs | Browse past works, with "draw another" and review of the original image |
| Daily Check-in | `/qd` to claim the check-in reward, credited to your balance |
| Top-up | OkPay online payment, TON-USDT and TRON-USDT on-chain transfers, with automatic order lookup and crediting |
| Referral Rebates | Exclusive invite code and invite history; commission is paid proportionally when an invitee tops up |
| Failure Refund | Automatically refunds the cost of a failed generation, idempotent to prevent duplicate refunds |

### Admin Side (`/admin`)
- **Statistics**: users, orders, generation volume, revenue
- **User Management**: search, ban/unban, adjust balance, view details
- **Copy & Buttons**: welcome message, prompts, and button text are all editable, with custom emoji support
- **Preset Management**: add/edit presets, upload template and detail images, configure input fields
- **Payment Config**: OkPay merchant parameters, TON/TRON receiving addresses and exchange rates
- **Route Config**: two upstream APIs (address, model, token pool, timeout), with a switchable default route
- **Pricing Config**: free draw / preset base fee / protocol add-on fee / check-in reward
- **Channel Publishing**: generation results are auto-synced to a designated channel with buttons
- **Manual Cleanup**: expired orders, stuck-order compensation

### Clone Platform (SaaS Mode)
- Main site `/clone` panel: purchase quota → create sub-bot → process daemon
- Tenant isolation: each sub-bot has its own `c{id}_users / c{id}_orders / c{id}_draw_tasks...` tables
- Shared upstream: sub-bots reuse the main site's API routes, billed per call and recorded in the audit log
- Sub-bot tokens are stored encrypted with Fernet (`clone_secret.key`, permissions 600)
- Content moderation: the main site's model moderates prompts for sensitive words and policy compliance, with a configurable failure strategy

---

## Directory Structure

```
.
├── main.py                  # Entry point (python main.py)
├── requirements.txt
├── .env.example             # Environment variable template
├── LICENSE
├── README.md
├── INSTALL.md               # Deployment guide
├── docs/
│   └── ARCHITECTURE.md      # Architecture and flow notes
└── bot/
    ├── __init__.py
    ├── config.py            # Config, constants, default copy, .env loading
    ├── text.py              # Low-level helpers: time/IDs, truncation, amounts, HTML and entity conversion
    ├── db.py                # SQLite access, config read/write, users/orders/tasks, tenant isolation
    ├── utils.py             # Buttons, session state, safe message editing
    ├── drawing.py           # Drawing core: upstream calls, image normalization, task execution and delivery
    ├── payments.py          # OkPay / TON-USDT / TRON-USDT
    ├── handlers.py          # User and admin commands, callbacks, scheduled jobs
    ├── platform.py          # Clone platform: process daemon, shared billing, moderation
    └── main.py              # Assemble the override chain, register handlers, start polling
```

Module dependency direction (reverse imports are forbidden):

```
config → text → db → utils → drawing → payments → handlers → platform → main
```

---

## Quick Start

```bash
# 1. Install dependencies (be sure to include the [job-queue] extra, otherwise scheduled jobs cannot be enabled)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Configure environment variables
cp .env.example .env
vim .env          # fill in BOT_TOKEN / BOT_USERNAME / ADMIN_IDS

# 3. Start
python main.py
```

On first launch the database is created automatically and default config and presets are written. Open the bot and send `/start`; admins send `/admin` to open the panel.

For detailed deployment (systemd, Nginx reverse proxy, auto-start on boot, common failures), see [INSTALL.md](INSTALL.md).

---

## Configuration

| Variable | Required | Description |
| --- | --- | --- |
| `BOT_TOKEN` | Yes | Token generated by BotFather |
| `BOT_USERNAME` | Yes | Bot username (without @) |
| `ADMIN_IDS` | Yes | Admin TGIDs, comma-separated; admins must have sent `/start` to the bot first |
| `WEB_PORT` | No | OkPay callback port, default 5003 |
| `DRAW_TIMEOUT_SECONDS` | No | Generation timeout, default 600 seconds |

All other business settings (API routes, pricing, payments, copy) are changed in the admin panel and stored in the `config` table.

---

## Data and Backup

Runtime data lives in the project root, **not in the repository**:

| File/Directory | Description |
| --- | --- |
| `image_bot.db` | Main SQLite database (WAL mode, plus `-wal` / `-shm`) |
| `generated_images/` | Local copies of generated images |
| `clone_secret.key` | Encryption key for clone tokens; if lost, sub-bot tokens cannot be decrypted |

Suggested backup: `sqlite3 image_bot.db ".backup backup.db"` plus archiving `generated_images/` and `clone_secret.key`.

---

## Docker

```bash
cp .env.example .env      # set BOT_TOKEN, ADMIN_IDS, etc.
docker compose up -d --build
```

- The SQLite database and generated images live in the `data` volume (`/data` inside the container), so recreating the container keeps them
- OkPay callbacks need port 5003 exposed publicly or proxied through a reverse proxy
- Multi-arch image (`linux/amd64`, `linux/arm64`): `ghcr.io/kunkun24/kun-image-bot`


## Disclaimer

This project is for learning and technical research only. Please comply with the laws and regulations of your jurisdiction and with Telegram platform rules, and do not use it to generate or distribute illegal content. All consequences arising from the use of this project are borne by the user.

---

## Copyright

```
Kun Image Bot / Open-sourced by TG KunKun @sad1881 — KunKun takes on all kinds of projects, inquiries welcome
```

---

## About AIGC

> This project contains AIGC (AI-generated) content. AI shortened development time; every line of code was carefully reviewed.
