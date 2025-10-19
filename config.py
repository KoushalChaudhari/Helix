# config.py
from dotenv import load_dotenv
import os
from typing import Set

load_dotenv()

DISCORD_TOKEN: str = os.environ["DISCORD_TOKEN"]       
PREFIX: str = os.getenv("PREFIX", ";")
OWNER_IDS: Set[int] = {
    int(x) for x in os.getenv("OWNER_IDS", "").split(",") if x.strip()
}
DATABASE_URL: str = os.environ["DATABASE_URL"]         
TENOR_KEY: str | None = os.getenv("TENOR_KEY")
