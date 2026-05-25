# GTL Swiggy Bot 🎮

Telegram bot for The Good Times League Swiggy game — with **Force Subscribe** and **Admin Channel Management**.

---

## Features

| Feature | Description |
|---|---|
| 🎮 Play game | OTP login → score submit → coupon |
| 📱 SMS coupon | Sends coupon via Fast2SMS |
| 📊 Leaderboard | Top players |
| 📢 Force Subscribe | Users must join channels before using bot |
| ✅ Verify membership | Button + /verify command |
| 🔧 Admin: Add channel | `/addchannel` — add required channels |
| 🗑 Admin: Remove channel | `/removechannel` — remove with buttons |
| 📋 Admin: List channels | `/listchannels` |
| 📊 Admin: Stats | `/stats` — total users, verified, channels |
| 👥 Admin: Users list | `/users` |
| 📣 Admin: Broadcast | `/broadcast <message>` — message all users |

---

## Setup & Deploy on Railway

### Step 1 — Prepare the Bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Create a new bot → copy the **bot token**
3. Get your **Telegram user ID** from [@userinfobot](https://t.me/userinfobot)

### Step 2 — Push to GitHub

```bash
git init
git add .
git commit -m "initial commit"
git remote add origin https://github.com/YOUR_USERNAME/gtl-bot.git
git push -u origin main
```

### Step 3 — Deploy on Railway

1. Go to [railway.app](https://railway.app) → **New Project**
2. Select **Deploy from GitHub repo** → choose your repo
3. Click on the service → **Variables** tab → add:

```
TELEGRAM_BOT_TOKEN    =  your_bot_token
ADMIN_IDS             =  your_telegram_user_id
FAST2SMS_API_KEY      =  your_fast2sms_key   (optional)
DB_PATH               =  gtl_bot.db
```

4. Go to **Settings** → make sure **Start Command** is empty (uses Procfile)
5. Railway will auto-deploy! ✅

### Step 4 — Add Bot to Channels

For the force-subscribe feature:
1. Add the bot as **Admin** to each of your channels
2. In Telegram, message the bot: `/addchannel`
3. Send the channel username (e.g. `@mychannel`)
4. Done — users will be forced to join before using `/play`

---

## Commands Reference

### User Commands
| Command | Description |
|---|---|
| `/start` | Welcome message |
| `/play` | Start game (enter mobile → OTP → coupon) |
| `/verify` | Check & verify channel membership |
| `/leaderboard` | Top players |
| `/cancel` | Cancel current operation |

### Admin Commands
| Command | Description |
|---|---|
| `/addchannel` | Add a required channel |
| `/removechannel` | Remove a channel (inline buttons) |
| `/listchannels` | List all required channels |
| `/stats` | Bot statistics |
| `/users` | List last 20 users |
| `/broadcast <msg>` | Send message to all users |

---

## Force Subscribe Flow

```
User sends /play
     ↓
Bot checks all required channels
     ↓
Not joined? → Show JOIN buttons + "✅ I Joined — Verify Me" button
     ↓
User joins channels → clicks verify button
     ↓
Bot re-checks membership via Telegram API
     ↓
All joined? → Proceed to /play
```

---

## Local Development

```bash
# Install deps
pip install -r requirements.txt

# Create .env from example
cp .env.example .env
# Edit .env with your values

# Run
python bot.py
```

---

## Architecture

```
gtl-bot/
├── bot.py          # Main bot — all handlers, force-subscribe logic
├── database.py     # SQLite helpers (channels + users tables)
├── requirements.txt
├── Procfile        # Railway/Heroku start command
├── railway.json    # Railway config
├── .env.example    # Environment variable template
└── .gitignore
```
