"""Telegram ingest bot for the short-form video pipeline.

Flow:
  User sends a video -> download to raw/day-N.mp4 -> run the processor in the
  готовое в output/day-N/.

Команды:
  /start             — приветствие и текущий день
  /day N             — установить текущий день вручную
  /status            — очередь / статусы дней
  /regenerate N      — перегенерировать подписи для дня N (без перерендера видео)
  /id                — свой TG id
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

import config
import processor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
log = logging.getLogger("100days-bot")

router = Router()


def _is_allowed(user_id: int) -> bool:
    return user_id in config.ALLOWED_USERS


def _is_owner(user_id: int) -> bool:
    return user_id == config.OWNER_ID


def _is_read_only(user_id: int) -> bool:
    return user_id in config.READ_ONLY_USERS


async def guard(m: Message, write: bool = True) -> bool:
    """write=True — операция меняющая данные. Read-only юзеры не могут."""
    uid = m.from_user.id
    if not _is_allowed(uid):
        await m.answer(f"Нет доступа. Твой ID: <code>{uid}</code>")
        return False
    if write and _is_read_only(uid):
        await m.answer("У тебя read-only доступ — смотреть можно, менять нельзя.")
        return False
    return True


@router.message(Command("start"))
async def cmd_start(m: Message):
    if not await guard(m, write=False):
        return
    cur = config.current_day()
    await m.answer(
        "<b>🎬 100 дней — контент-пайплайн</b>\n\n"
        f"Текущий день: <b>{cur or '—'}</b>\n\n"
        "Кидай сюда <b>селфи-видео</b> — я:\n"
        "  1. Сохраню в <code>raw/</code>\n"
        "  2. Расшифрую Whisper'ом\n"
        "  3. Подрежу до 9:16, добавлю субтитры и ватермарк <i>ДЕНЬ N/100</i>\n"
        "  4. Сгенерирую 3 подписи (TT / Reels/Shorts / TG)\n"
        "  5. Положу готовое в <code>output/day-N/</code>\n\n"
        "Команды:\n"
        "  /day N — установить день вручную\n"
        "  /status — что в очереди\n"
        "  /regenerate N — пересобрать подписи для дня N",
    )


@router.message(Command("id"))
async def cmd_id(m: Message):
    await m.answer(f"<code>{m.from_user.id}</code>")


@router.message(Command("day"))
async def cmd_day(m: Message, command: CommandObject):
    if not await guard(m):
        return
    if not _is_owner(m.from_user.id):
        await m.answer("Только owner может менять счётчик.")
        return
    arg = (command.args or "").strip()
    if not arg.isdigit():
        await m.answer("Использование: <code>/day 7</code>")
        return
    n = int(arg)
    config.set_day(n)
    await m.answer(f"Текущий день установлен: <b>{n}</b>")


@router.message(Command("status"))
async def cmd_status(m: Message):
    if not await guard(m, write=False):
        return
    queue = config.list_queue()
    cur = config.current_day()
    if not queue:
        await m.answer(f"Текущий день: <b>{cur or '—'}</b>\nОчередь пуста.")
        return
    lines = [f"<b>Текущий день:</b> {cur or '—'}", "", "<b>В работе / ждут:</b>"]
    for r in queue[:20]:
        err = f" · ⚠️ {r['error']}" if r.get("error") else ""
        lines.append(f"  • День {r['day']}: <code>{r['status']}</code>{err}")
    await m.answer("\n".join(lines))


@router.message(Command("regenerate"))
async def cmd_regen(m: Message, command: CommandObject):
    if not await guard(m):
        return
    arg = (command.args or "").strip()
    if not arg.isdigit():
        await m.answer("Использование: <code>/regenerate 3</code>")
        return
    day = int(arg)
    await m.answer(f"Перегенерирую подписи для дня {day}…")
    try:
        caps = await asyncio.get_running_loop().run_in_executor(
            None, processor.regenerate_captions, day
        )
    except Exception as e:
        log.exception("regen")
        await m.answer(f"⚠️ <code>{e}</code>")
        return
    await m.answer(
        f"✅ Подписи дня {day} обновлены.\n\n"
        f"<b>TT:</b> {caps['version_tt']}\n\n"
        f"<b>Reels/Shorts:</b> {caps['version_reels_shorts']}\n\n"
        f"<b>TG:</b>\n{caps['version_tg']}"
    )


# ---------- video ingest ----------
@router.message(F.video | F.video_note | F.document)
async def ingest_video(m: Message, bot: Bot):
    if not await guard(m):
        return

    # Понимаем что это видео-файл
    file_obj = None
    mime = ""
    fn = ""
    if m.video:
        file_obj = m.video
        mime = m.video.mime_type or "video/mp4"
    elif m.video_note:
        file_obj = m.video_note
        mime = "video/mp4"
    elif m.document:
        file_obj = m.document
        mime = (m.document.mime_type or "").lower()
        fn = (m.document.file_name or "").lower()
        if not (mime.startswith("video/") or fn.endswith((".mp4", ".mov", ".m4v", ".webm"))):
            await m.answer("Файл не похож на видео. Пришли видео-сообщение или .mp4.")
            return

    if file_obj is None:
        return

    # Определяем день
    day = config.next_day()
    raw_path = config.RAW_DIR / f"day-{day}.mp4"

    status = await m.answer(f"📥 Принимаю день {day}, качаю…")

    try:
        tg_file = await bot.get_file(file_obj.file_id)
        await bot.download_file(tg_file.file_path, destination=str(raw_path))
    except Exception as e:
        log.exception("download")
        config.upsert_day(day, status="error", error=f"download: {e}")
        await status.edit_text(f"⚠️ Не смог скачать: <code>{e}</code>")
        return

    size_mb = raw_path.stat().st_size / 1024 / 1024
    config.upsert_day(day, raw_path=str(raw_path), status="queued")
    await status.edit_text(
        f"✅ День {day} принят ({size_mb:.1f} MB)\n"
        f"🧠 Транскрибирую + рендерю 9:16 + генерирую подписи…\n"
        f"Напишу когда готово."
    )

    # Фоново — чтобы бот продолжал принимать сообщения
    asyncio.create_task(_run_pipeline(bot, m.chat.id, raw_path, day))


async def _run_pipeline(bot: Bot, chat_id: int, raw: Path, day: int):
    try:
        result = await asyncio.get_running_loop().run_in_executor(
            None, processor.process, raw, day
        )
    except Exception as e:
        log.exception("pipeline day=%s", day)
        config.upsert_day(day, status="error", error=str(e)[:500])
        try:
            await bot.send_message(chat_id, f"⚠️ День {day} упал: <code>{e}</code>")
        except Exception:
            pass
        return

    caps = result["captions"]
    out_dir = result["out_dir"]
    try:
        video_fs = Path(result["video"])
        # Отправим превью-видео прямо в чат
        from aiogram.types import FSInputFile
        await bot.send_video(
            chat_id,
            FSInputFile(video_fs),
            caption=(
                f"🎬 <b>День {day}/100 готов</b>\n"
                f"📂 <code>{out_dir}</code>\n\n"
                f"<b>TT:</b> {caps['version_tt']}\n\n"
                f"<b>Reels/Shorts:</b> {caps['version_reels_shorts']}"
            ),
        )
        await bot.send_message(
            chat_id,
            f"<b>TG-пост:</b>\n\n{caps['version_tg']}",
        )
    except Exception as e:
        log.warning("post-process notify failed: %s", e)
        await bot.send_message(chat_id, f"✅ День {day} готов. Папка: <code>{out_dir}</code>")


@router.message()
async def fallback(m: Message):
    if not await guard(m, write=False):
        return
    await m.answer(
        "Пришли видео-сообщение / видеофайл — обработаю.\n"
        "Или используй /status / /day N / /regenerate N."
    )


async def main():
    if not config.BOT_TOKEN or config.BOT_TOKEN == "PUT_BOTFATHER_TOKEN_HERE":
        log.error("BOT_TOKEN не задан в .env — запусти после получения токена от @BotFather")
        return
    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(router)
    log.info("100days bot starting… allowed=%s read_only=%s",
             config.ALLOWED_USERS, config.READ_ONLY_USERS)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
