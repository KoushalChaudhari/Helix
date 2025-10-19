# 🧠 JOAT Discord Bot [WIP]
*A multipurpose, database-connected moderation and utility bot built in Python.*

---

## 📋 Overview
JOAT (Jack of All Trades) is a **Discord bot** built using `discord.py` and `async SQLAlchemy` with a **PostgreSQL (Neon)** backend.  
It includes moderation tools (warn, mute, kick, ban, etc.), logging, and a modular cog-based architecture.

---

## 🏗️ Project Structure

```
JOAT/
│
├── bot.py                 # Main bot launcher
├── config.py              # Loads environment variables (.env)
├── .env                   # Secrets (token, DB URL)
│
├── db/
│   ├── __init__.py
│   ├── engine.py          # Async engine and init_db()
│   └── models.py          # SQLAlchemy models (GuildConfig, etc.)
│
├── cogs/
│   ├── __init__.py
│   ├── core.py            # Basic ;ping command
│   ├── fun.py             # ;gif, etc.
│   ├── dbtest.py          # ;pingdb for database testing
│   └── mod.py             # All moderation commands
│
└── README.txt             # This file
```

---

## 🧩 Features

### 🛡 Moderation Commands
| Command | Description |
|----------|-------------|
| `;warn @user <reason>` | Warns a member, DMs them, logs an embed in mod-log channel. |
| `;reason <caseNo> <new reason>` | Updates the logged reason for a case. |
| `;duration <caseNo> <time>` | Updates or adds a duration to a case embed. |
| `;mute @user <duration> <reason>` | Timeouts a member (e.g., `;mute @User 30m spam`). |
| `;unmute @user` | Removes timeout. |
| `;kick @user <reason>` | Kicks a member from the server. |
| `;ban @user <reason>` | Bans a member. |
| `;unban <user_id>` | Unbans a previously banned user. |

### ⚙️ Utility Commands
| Command | Description |
|----------|-------------|
| `;ping` | Checks bot latency. |
| `;pingdb` | Confirms DB connectivity. |
| `;gif <query>` | Fetches a random GIF from Tenor. |

---

## 🧰 Dependencies

Ensure you’re using **Python 3.11+**.

Install required dependencies using pip:

```
pip install -U discord.py sqlalchemy asyncpg python-dotenv aiohttp
```

If you want full development support:

```
pip install -U black isort pylint
```

---

## 🧾 Environment Setup

### 1️⃣ Create a `.env` file in the project root:

```
DISCORD_TOKEN=your_discord_bot_token_here
DATABASE_URL=postgresql+asyncpg://user:password@your-neon-host/dbname?ssl=true
TENOR_KEY=your_tenor_api_key_here
PREFIX=;
```

> 🧠 **Notes:**
> - `DATABASE_URL` must use `postgresql+asyncpg://...`
> - `?ssl=true` ensures Neon (PostgreSQL cloud) uses SSL connection
> - `TENOR_KEY` is optional unless you use `;gif`

---

## 🗄️ Database Setup (Neon)

1. Go to [https://neon.tech](https://neon.tech) and sign up (free tier is fine).  
2. Create a new **PostgreSQL project**.  
3. Copy your connection string (e.g.,  
   `postgresql://username:password@ep-abcd123.us-east-2.aws.neon.tech/neondb`).  
4. Convert it to the asyncpg format:  
   ```
   postgresql+asyncpg://username:password@ep-abcd123.us-east-2.aws.neon.tech/neondb?ssl=true
   ```
5. Paste that into your `.env` file as `DATABASE_URL`.

6. When the bot starts, it automatically initializes tables (`GuildConfig`, etc.) via `init_db()`.

---

## ⚙️ Running the Bot

### Windows PowerShell
```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt  # or use pip install -U discord.py sqlalchemy asyncpg python-dotenv
python bot.py
```

### Linux / Mac
```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 bot.py
```

---

## 🔐 Discord Developer Portal Setup

1. Go to [https://discord.com/developers/applications](https://discord.com/developers/applications).  
2. Create a new Application → **Bot** tab → click **Add Bot**.  
3. Copy the **Bot Token** into your `.env` file as `DISCORD_TOKEN`.  
4. Under **Privileged Gateway Intents**, enable:
   - ✅ Message Content Intent  
   - ✅ Server Members Intent  
5. Go to **OAuth2 → URL Generator**:
   - Scopes: `bot`  
   - Permissions: `Administrator` or minimal set (`Send Messages`, `Embed Links`, `Moderate Members`, etc.)  
6. Copy the generated URL and use it to invite your bot to your server.

---

## 🧠 Example Usage

```
;modlog #moderation-logs
;warn @User Breaking rules
;reason 5 Updated reason to include spam details
;duration 5 30m
;mute @User 1h Flooding chat
;unmute @User
;ban @User Repeated rule violations
;unban 123456789012345678 Apologized and appealed
```

---

## 🧩 Debugging Tips
- If you see `PrivilegedIntentsRequired`, enable **Message Content** + **Members** intents in the Developer Portal.  
- If `;pingdb` doesn’t respond, check your Neon connection string format.  
- Use `;modlog` to confirm your logging channel is configured correctly.

---

## 🧾 License
This project is released for **educational and personal use** only.  
Feel free to modify or extend it for your own Discord servers.

