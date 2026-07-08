"""Config + shared constants for the short-form video pipeline."""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

# --- paths ---
DATA_DIR = Path(os.getenv("DATA_DIR", str(ROOT)))
RAW_DIR = DATA_DIR / "raw"
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"
PROCESSED_DIR = DATA_DIR / "processed"
OUTPUT_DIR = DATA_DIR / "output"
FONTS_DIR = DATA_DIR / "fonts"
DB_PATH = DATA_DIR / "state.db"

for d in (RAW_DIR, TRANSCRIPTS_DIR, PROCESSED_DIR, OUTPUT_DIR, FONTS_DIR):
    d.mkdir(parents=True, exist_ok=True)

# --- auth ---
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
ALLOWED_USERS = {
    int(x) for x in os.getenv("ALLOWED_USERS", str(OWNER_ID)).split(",") if x.strip()
}
ALLOWED_USERS.add(OWNER_ID)
READ_ONLY_USERS = {
    int(x) for x in os.getenv("READ_ONLY_USERS", "").split(",") if x.strip()
}

# --- llm / whisper ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-20250514")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "ru")

# --- rendering ---
# {day} is substituted at render time, e.g. "DAY 1/100".
WATERMARK_TEMPLATE = os.getenv("WATERMARK_TEMPLATE", "DAY {day}/100")
TOTAL_DAYS = int(os.getenv("TOTAL_DAYS", "100"))

# --- telegram user (Telethon for publishing) ---
TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "0") or 0)
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
TG_SESSION_STRING = os.getenv("TG_SESSION_STRING", "")
TG_CHANNEL_ID = os.getenv("TG_CHANNEL_ID", "")

# --- sheets ---
GOOGLE_SA_KEY = os.getenv("GOOGLE_SA_KEY", "service-account.json")
SHEETS_ID = os.getenv("SHEETS_ID", "")

# --- bot ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "")


# ---------------- state db ----------------
def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(_db()) as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS days (
                day INTEGER PRIMARY KEY,
                raw_path TEXT,
                transcript_path TEXT,
                processed_path TEXT,
                output_dir TEXT,
                caption_tt TEXT,
                caption_reels_shorts TEXT,
                caption_tg TEXT,
                status TEXT NOT NULL DEFAULT 'new',
                error TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        c.commit()


def meta_get(key: str, default: str | None = None) -> str | None:
    with closing(_db()) as c:
        row = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def meta_set(key: str, value: str) -> None:
    with closing(_db()) as c:
        c.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        c.commit()


def next_day() -> int:
    """Auto-increment day counter (stored in meta)."""
    cur = int(meta_get("current_day", "0") or 0)
    nxt = cur + 1
    meta_set("current_day", str(nxt))
    return nxt


def set_day(n: int) -> None:
    meta_set("current_day", str(int(n)))


def current_day() -> int:
    return int(meta_get("current_day", "0") or 0)


def upsert_day(day: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values())
    with closing(_db()) as c:
        c.execute(
            "INSERT INTO days(day) VALUES(?) ON CONFLICT(day) DO NOTHING",
            (day,),
        )
        c.execute(
            f"UPDATE days SET {cols}, updated_at=datetime('now') WHERE day=?",
            [*vals, day],
        )
        c.commit()


def get_day(day: int) -> dict | None:
    with closing(_db()) as c:
        row = c.execute("SELECT * FROM days WHERE day=?", (day,)).fetchone()
        return dict(row) if row else None


def list_queue() -> list[dict]:
    with closing(_db()) as c:
        rows = c.execute(
            "SELECT day, status, error, updated_at FROM days "
            "WHERE status NOT IN ('done','cancelled') ORDER BY day DESC"
        ).fetchall()
        return [dict(r) for r in rows]


init_db()
