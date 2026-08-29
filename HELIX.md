# HELIX Discord Bot

## 1. Overview

Helix is a modular Discord bot built in Python using `discord.py` and a PostgreSQL-backed configuration layer powered by SQLAlchemy and `asyncpg`. It is designed as a server utility bot with:

- moderation and logging tools
- per-guild prefix configuration
- user profile and server utility commands
- fun commands (GIF, meme, trivia, polls)
- media conversion tools
- global allowlist-based access control
- owner-only cloning/cross-server tools
- optional feedback and bug-report channels

The bot entry point is `bot.py`, and it loads feature modules from the `cogs/` folder. A per-guild database model stores settings like prefix and moderation metadata.

---

## 2. High-level architecture

### Runtime flow

1. `bot.py` starts the bot.
2. It loads environment secrets from `config.py` and `.env`.
3. It initializes the SQLAlchemy async database engine.
4. It warms the in-memory prefix cache from `guild_config`.
5. It loads all bot cogs from `cogs/`.
6. It syncs Discord application commands and starts the bot.

### Main files

- `bot.py` — main bot bootstrapping and startup logic
- `config.py` — environment-variable loader
- `db/engine.py` — async DB engine setup
- `db/models.py` — database schema definitions
- `cogs/core.py` — core bot features, help, prefix, feedback, bug, broadcast
- `cogs/mod.py` — moderation actions and case logging
- `cogs/fun.py` — GIF, meme, trivia, reaction-based games
- `cogs/utility.py` — role and server utilities
- `cogs/userinfo.py` — user/profile commands
- `cogs/tools.py` — GIF conversion from attachments and context-menu tool
- `cogs/access.py` — allowlist-based access control
- `cogs/secret.py` — additional utility functions, not clearly public-facing
- `cogs/help_descriptions.json` — command metadata for help system
- `allowlist.json` — per-guild allowlisted role IDs
- `Helix.bat` — Windows launcher for the bot

---

## 3. Bot startup and runtime behavior

### Discord intent configuration

In `bot.py`, the bot enables:

- `message_content` for prefix commands and message inspection
- `members` for member-related moderation and user lookups
- `guilds` for guild-based operations

It uses a dynamic prefix system where each guild can have its own configured prefix stored in the database. The default default is `;`.

### Database initialization

On startup, the bot calls:

```python
await init_db(Base.metadata)
```

This creates database tables if they do not already exist based on SQLAlchemy models.

### Prefix behavior

- default prefix: `;`
- per-guild prefix is stored in `GuildConfig.prefix`
- a cache is warmed from the DB at startup with `load_prefixes()`
- `get_prefix()` supports either guild prefix or mention-based command invocation

---

## 4. Database model and schema

The bot uses SQLAlchemy models in `db/models.py`:

### `GuildConfig`

Stores per-guild settings:

- `id` — primary key
- `guild_id` — unique guild ID
- `prefix` — custom command prefix
- `modules` — JSON field for feature flags/configuration
- `timezone` — optional timezone metadata
- `created_at`, `updated_at`

### `Case`

Stores moderation case metadata:

- `guild_id`
- `user_id`
- `moderator_id`
- `action`
- `reason`
- `duration_ms`
- `active`
- `created_at`, `updated_at`

This is used for moderation case tracking and later lookup/editing via commands like `warn`, `reason`, `duration`, etc.

### `Economy`

A basic money/balance table is defined:

- `guild_id`
- `user_id`
- `balance`
- `updated_at`

This is present in schema but not the primary focus of the current bot features.

---

## 5. Dependency stack

### Python runtime

This project expects a Python environment, typically:

- Python 3.11+
- virtual environment recommended

### Core Python dependencies

The bot code directly imports:

```python
discord.py
sqlalchemy
asyncpg
python-dotenv
aiohttp
Pillow
requests
moviepy
imageio-ffmpeg
psutil
```

The repo also contains a `package.json` with Node/Prisma tooling:

```json
{
  "dependencies": {
    "@prisma/client": "^6.17.1",
    "prisma": "^6.17.1"
  }
}
```

This indicates the project includes optional Prisma tooling, but the actual runtime bot logic in `bot.py` is Python-based and uses SQLAlchemy/PostgreSQL directly rather than Prisma at runtime.

### Recommended installation commands

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install "discord.py" "sqlalchemy" "asyncpg" "python-dotenv" "aiohttp" "Pillow" "requests" "moviepy" "imageio-ffmpeg" "psutil"
```

Optional Node tools:

```powershell
npm install
```

The repo does not currently include a `requirements.txt`, so you need to install the Python packages explicitly or create one for reproducibility.

---

## 6. Required environment variables

`config.py` expects the following environment variables:

```python
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
DATABASE_URL = os.environ["DATABASE_URL"]
PREFIX = os.getenv("PREFIX", ";")
OWNER_IDS = {int(x) for x in os.getenv("OWNER_IDS", "").split(",") if x.strip()}
TENOR_KEY = os.getenv("TENOR_KEY") [Tenor API deprecated]
```

### `.env` template

Create a root-level `.env` file:

```env
DISCORD_TOKEN=your_discord_bot_token_here
DATABASE_URL=postgresql+asyncpg://username:password@host:port/dbname?ssl=true
TENOR_KEY=your_tenor_api_key
PREFIX=;
OWNER_IDS=123456789012345678,987654321098765432
FEEDBACK_CHANNEL_ID=123456789012345678
BUG_CHANNEL_ID=123456789012345678
```

### Notes

- `DISCORD_TOKEN` is mandatory.
- `DATABASE_URL` is mandatory and must use the async SQLAlchemy/asyncpg format.
- `TENOR_KEY` is optional but required for `;gif`.
- `OWNER_IDS` is used for owner-only commands.
- `FEEDBACK_CHANNEL_ID` and `BUG_CHANNEL_ID` are optional delivery targets for feedback/bug reports.

---

## 7. PostgreSQL / Neon setup

The code is designed for PostgreSQL and explicitly references Neon-compatible connection strings.

Example:

```env
DATABASE_URL=postgresql+asyncpg://user:password@ep-abc123.us-east-2.aws.neon.tech/neondb?ssl=true
```

### Why this matters

`db/engine.py` creates the async SQLAlchemy engine like this:

```python
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    connect_args={"ssl": ssl_ctx},
)
```

This configuration expects a PostgreSQL backend and uses `ssl` for secure connections, which matches Neon usage.

### Database check

The included `check_url.py` validates whether the database URL is in the expected format:

```python
u = make_url(os.getenv("DATABASE_URL", ""))
print("Driver =", u.drivername)
print("Query  =", u.query)
```

It is useful to confirm that the URL resolves to `postgresql+asyncpg` and includes `ssl=true` if needed.

---

## 8. Bot command modules and functionality

### Core features (`cogs/core.py`)

The bot supports commands such as:

- `;ping` — check bot latency
- `;uptime` — bot online duration
- `;invite` — invite the bot with admin perms
- `;about` — bot information
- `;prefix` — view or change guild prefix
- `;feedback` — send feedback to maintainer or configured channel
- `;bug` — report bugs
- `;help` / `;helpui` — category-based and interactive help
- `;broadcast` — owner-only announcement to all guilds

The help system reads `cogs/help_descriptions.json` to build command/category metadata.

### Moderation features (`cogs/mod.py`)

The moderation cog includes:

- `;modlog` — configure a moderation logging channel
- `;warn` — warn a user and log a case
- `;warns` — view warnings for a user
- `;clearwarns` — clear warnings
- `;kick` — remove a member and record a moderation case
- `;ban` / `;unban` — ban/unban users
- `;mute` / `;unmute` / `;timeout` — manage member timeout
- `;reason` — update a case reason
- `;duration` — adjust case duration
- `;purge` / `;clean` — bulk message cleanup
- `;lock` / `;unlock` — channel lock controls
- `;slowmode` — set slowmode per channel
- `;modstats` — moderation stats

The moderation system stores case metadata and logs actions into a configured mod-log channel in the guild.

### Fun and entertainment features (`cogs/fun.py`)

- `;gif <query>` — search Tenor for a GIF
- `;meme` — fetch a random meme from the Meme API
- `;trivia` — interactive multiple-choice trivia with reaction-based answers
- reaction listeners handle answer checking
- optional poll-like functionality exists in the project’s help metadata but depends on more code pathways not fully visible in the initial file reads

### User info features (`cogs/userinfo.py`)

- `;userinfo` / `;whois` / `;ui`
- resolves users by mention, ID, profile URL, or guild member lookup
- shows:
  - user ID
  - bot status
  - account creation date
  - join date
  - badges/flags
  - roles
  - inferred role significance
  - permission summary

### Utility features (`cogs/utility.py`)

Includes server/admin role management commands such as:

- `;addrole`
- `;delrole`
- `;role` — assign or remove a role from a user
- role search and role info helpers
- server info and channel metadata-related utilities

### Access control (`cogs/access.py`)

The bot includes a role-based allowlist system:

- admins always bypass restrictions
- owner always bypasses restrictions
- non-admins can use commands only if their role is in `allowlist.json`
- `;allowrole @Role` toggles role access in a guild

The file is persisted in the project root as `allowlist.json`.

### Media conversion tools (`cogs/tools.py`)

This cog converts uploaded media into GIFs:

- `;mkgif` / `;makegif` / `;togif`
- works with images or short videos
- uses `moviepy` + `imageio-ffmpeg`
- can be invoked via Discord app context menu: `Make GIF (Helix)`

### Owner-only features

The repo contains code for owner-only cloning tools (`cogs/clone.py`) with commands like:

- `;clone`
- `;clonemsg`

These allow a bot owner to copy messages from one channel to another, with permission checks and rate limiting. They are intended as high-privilege admin tools and should only be enabled if explicitly needed.

---

## 9. Working details of the permission model

The bot uses a layered authorization approach:

1. Discord permission checks via `@commands.has_permissions(...)` and `@commands.bot_has_permissions(...)`
2. custom global allowlist via `AccessControl.global_allow_check()`
3. owner checks via `is_owner()` and `OWNER_IDS`

This means an admin can usually operate the bot, but additional role-based access restrictions can be applied for certain guilds.

---

## 10. Startup command and Windows launcher

The included `Helix.bat` file starts the bot using the project virtual environment:

```bat
@echo off
title Helix Discord Bot
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo ERROR: Virtual environment not found at "%~dp0.venv"
    pause
    exit /b 1
)
"%~dp0.venv\Scripts\python.exe" "%~dp0bot.py"
```

This is the simplest Windows reproduction path if `.venv` exists and the required dependencies are installed.

---

## 11. Reproduction checklist

### Step-by-step reproduction

1. Clone the project to a local folder.
2. Create and activate a Python virtual environment.
3. Install the Python dependencies:
   ```powershell
   pip install "discord.py" "sqlalchemy" "asyncpg" "python-dotenv" "aiohttp" "Pillow" "requests" "moviepy" "imageio-ffmpeg" "psutil"
   ```
4. Create a `.env` file with the required values.
5. Create or connect a PostgreSQL database (Neon/Postgres recommended).
6. Configure `DATABASE_URL` in the `.env` file.
7. Ensure `DISCORD_TOKEN` matches a Discord application bot token.
8. Optionally add `TENOR_KEY` if GIF commands will be used.
9. Start the bot:
   ```powershell
   python bot.py
   ```
   or use:
   ```powershell
   .\Helix.bat
   ```
10. Invite the bot to a server with the required permissions.
11. Confirm guild settings, prefix configuration, and moderation channel setup.

---

## 12. Discord Developer Portal setup

To make the bot work in Discord:

1. Go to the Discord Developer Portal.
2. Create an application.
3. Create a bot user.
4. Copy the bot token into `DISCORD_TOKEN`.
5. Enable the required gateway intents:
   - Message Content Intent
   - Server Members Intent
6. Generate an OAuth2 invite URL with:
   - scope: `bot`
   - `applications.commands`
7. Add the bot to a server.

Some commands such as moderation, member lookups, and prefix parsing rely on guild member and message content access.

---

## 13. Operational notes and caveats

### Potential issues during reproduction

- missing `DISCORD_TOKEN` causes startup failure
- invalid `DATABASE_URL` causes the bot to fail during DB initialization
- missing `TENOR_KEY` disables `;gif`
- `moviepy`/`imageio-ffmpeg` may require FFmpeg binaries on the system
- if `psutil` is missing, some stats-related logic may degrade gracefully but still function
- the project does not currently include a full `requirements.txt`, so reproducibility depends on your pip install list

### Missing or incomplete implementation details

Some of the code indicates active development and partial features:

- there is a Prisma `package.json`, but the bot does not appear to use Prisma in runtime code paths
- some modules or commands appear to be in development or partially commented out
- `allowlist.json` stores allowlisted role IDs and is not automatically generated from the DB

---

## 14. Security considerations

This bot includes high-privilege operations and should be treated as a production/admin tool:

- changes to prefix and moderation settings are guild-scoped
- owner-only broadcast and clone features can have broad effects
- access control via allowlist should be reviewed before exposing the bot to public guilds
- do not commit or expose `.env` files with live Discord tokens or database credentials

---

## 15. Summary

Helix is a Python-based Discord bot that combines:

- modular cog architecture
- guild-aware command prefixing
- PostgreSQL-backed settings and moderation records
- moderation tools, user utilities, and fun commands
- optional media conversion and GIF generation
- access control and owner-level administrative automation

It is reproducible with:

- Python 3.11+
- `discord.py`
- SQLAlchemy + asyncpg + PostgreSQL
- `.env` secrets
- Discord app setup with gateway intents enabled
- proper bot invite and server permissions

This repository is ready for local reproduction when its environment and database connection are configured correctly.
