"""Publish day N to a Telegram channel via Telethon (a Telegram user session).

Использование:
    python3 publish_tg.py --day 1
    python3 publish_tg.py --day 1 --channel @your_channel

Требует в .env:
    TELEGRAM_API_ID, TELEGRAM_API_HASH (from https://my.telegram.org)
    TG_SESSION_STRING (a Telethon StringSession for the publishing account)
    TG_CHANNEL_ID (@username or -100xxx)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from telethon import TelegramClient
from telethon.sessions import StringSession

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402

log = logging.getLogger("publish_tg")


async def publish(day: int, channel: str | None = None) -> None:
    channel = channel or config.TG_CHANNEL_ID
    if not channel:
        raise SystemExit("TG_CHANNEL_ID не задан — впиши @username или -100xxx в .env")
    if not config.TG_SESSION_STRING:
        raise SystemExit("TG_SESSION_STRING is not set — generate a Telethon StringSession and put it in .env")

    out_dir = config.OUTPUT_DIR / f"day-{day}"
    video = out_dir / "video.mp4"
    caps = out_dir / "captions.json"
    if not video.exists():
        raise SystemExit(f"video не найдено: {video}")
    if not caps.exists():
        raise SystemExit(f"captions.json не найдено: {caps}")

    captions = json.loads(caps.read_text(encoding="utf-8"))
    tg_text = captions.get("version_tg", f"День {day}/100.")

    # Нормализация channel
    target: str | int = channel
    if isinstance(channel, str) and channel.lstrip("-").isdigit():
        target = int(channel)

    async with TelegramClient(
        StringSession(config.TG_SESSION_STRING),
        config.TELEGRAM_API_ID,
        config.TELEGRAM_API_HASH,
    ) as cli:
        me = await cli.get_me()
        log.info("acc logged in: %s (id=%s)", me.first_name, me.id)
        log.info("sending day %s video to %s", day, target)
        msg = await cli.send_file(
            target,
            str(video),
            caption=tg_text,
            supports_streaming=True,
            thumb=str(out_dir / "thumbnail.jpg") if (out_dir / "thumbnail.jpg").exists() else None,
        )
        log.info("posted msg_id=%s", msg.id)
        print(json.dumps({"ok": True, "msg_id": msg.id, "channel": str(target)},
                         ensure_ascii=False))


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", type=int, required=True)
    ap.add_argument("--channel", help="@username или -100xxx (по умолчанию TG_CHANNEL_ID)")
    args = ap.parse_args()
    asyncio.run(publish(args.day, args.channel))


if __name__ == "__main__":
    main()
