"""Morning digest (9:00) — sends a summary to the owner via the ingest bot.

Cron (not installed automatically — enable it yourself when ready):
    0 9 * * * /usr/bin/python3 /path/to/short-form-video-pipeline/morning_digest.py >> /var/log/digest.log 2>&1
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import tracker  # noqa: E402

log = logging.getLogger("digest")


GOAL = 10_000  # $10K/мес MVP цель


def _usd(x: float) -> str:
    return f"${x:,.0f}".replace(",", " ")


def build_text() -> str:
    try:
        d = tracker.digest_yesterday()
    except Exception as e:
        return f"⚠️ Дайджест: не смог прочитать Sheet — {e}"

    if d.get("empty"):
        return "📊 В трекере пока пусто. Заполни первый день через /tracker.py update 1 …"

    day = d.get("day", "?")
    total_views = (d.get("views_tt", 0) + d.get("views_reels", 0) +
                   d.get("views_shorts", 0) + d.get("views_tg", 0))
    rev = float(d.get("revenue_cumulative") or 0)
    left = max(GOAL - rev, 0)
    return (
        f"<b>☀️ День {day}/100 — утренний дайджест</b>\n\n"
        f"<b>Вчера:</b>\n"
        f"  • TT:    {d.get('views_tt', 0):,} views\n"
        f"  • Reels: {d.get('views_reels', 0):,} views\n"
        f"  • Shorts:{d.get('views_shorts', 0):,} views\n"
        f"  • TG:    {d.get('views_tg', 0):,} views\n"
        f"  • <b>Итого: {total_views:,} views</b>\n"
        f"  • Подписчики: <b>+{d.get('subs_delta', 0)}</b> "
        f"(всего {d.get('subs_total', 0)})\n\n"
        f"<b>💰 Выручка:</b> {_usd(rev)} / цель {_usd(GOAL)}\n"
        f"<b>До цели осталось:</b> {_usd(left)}\n"
    ).replace(",", " ")


async def send() -> None:
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode

    if not config.BOT_TOKEN or config.BOT_TOKEN == "PUT_BOTFATHER_TOKEN_HERE":
        print("BOT_TOKEN не задан — не шлю дайджест")
        return

    text = build_text()
    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        await bot.send_message(config.OWNER_ID, text)
        print("sent")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(send())
