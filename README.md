# Short-Form Video Pipeline

An automated pipeline that turns a raw selfie clip into a publish-ready
vertical (9:16) short with burned-in subtitles, a watermark, and
platform-specific captions — driven end to end from a single Telegram message.

Built for a daily "build in public" content cadence (TikTok / Reels / Shorts /
Telegram), where the bottleneck is editing and captioning rather than filming.

## What it does

1. You send a video to the Telegram ingest bot.
2. The bot saves it to `raw/day-N.mp4` and kicks off the processor in the
   background (so the bot stays responsive).
3. The processor:
   - **Transcribes** the audio with OpenAI Whisper (produces `.txt` + `.srt`).
   - **Renders** with ffmpeg: crop/pad to 1080x1920 (9:16), burn in the
     subtitles, and overlay a `DAY N/100` watermark.
   - **Generates 3 captions** with an LLM (Groq / Llama primary, Anthropic
     fallback): one for TikTok, one for Reels/Shorts, one long-form for Telegram.
4. The result lands in `output/day-N/`: `video.mp4`, `thumbnail.jpg`,
   `captions.json`, `captions.txt`, `transcript.txt`.

Optional add-ons: a Google Sheets performance tracker, a one-command Telegram
channel publisher, and a morning digest.

## Architecture

Clean module split — each piece is independently runnable and testable:

| Module              | Responsibility |
|---------------------|----------------|
| `bot.py`            | aiogram Telegram ingest bot (commands + video handling) |
| `processor.py`      | **The core.** Whisper + ffmpeg + caption orchestration. Runs standalone via CLI. |
| `ai.py`             | LLM caption generation (Groq primary, Anthropic fallback, graceful degradation) |
| `tracker.py`        | Google Sheets "day tracker" (views / subs / revenue, cumulative) |
| `publish_tg.py`     | Publish a finished day to a Telegram channel via Telethon |
| `morning_digest.py` | Daily summary sent to the owner |
| `config.py`         | Env config + a small SQLite state store (`state.db`) |

The design keeps the ingestion transport (Telegram) decoupled from the media
work: `processor.py` has no Telegram dependency and can be run against any file.

## The core: `processor.py`

`processor.py` is the strongest, self-contained piece and is fully CLI-runnable
without a bot token or any Telegram setup:

```bash
# Full pipeline on any file (Whisper + ffmpeg + captions)
python3 processor.py /path/to/clip.mp4 --day 1
ls output/day-1/

# Re-generate captions only (no re-render)
python3 processor.py --day 1 --regen-captions
```

Highlights:
- Aspect-ratio-aware framing: detects the source geometry with `ffprobe` and
  chooses crop vs. pad to reach a clean 1080x1920 (with even dimensions for
  libx264).
- Subtitle burn-in via ffmpeg `subtitles` filter with a readable outlined style.
- Lazy Whisper model loading (only loaded when transcription is actually needed).

## Setup

Requirements: Python 3.10+, `ffmpeg`/`ffprobe` installed system-wide.

```bash
pip install -r requirements.txt
cp .env.example .env      # fill in what you need
```

Minimal run needs only `GROQ_API_KEY` (for captions) and ffmpeg. The Telegram
bot, Sheets tracker, and channel publisher are all optional and only activate
when their env vars are set.

## Running the bot

```bash
# After setting BOT_TOKEN and OWNER_ID in .env
python3 bot.py
```

Bot commands:

| Command            | Description |
|--------------------|-------------|
| `/start`           | Greeting + current day |
| `/day N`           | Set the current day counter (owner only) |
| `/status`          | Show the processing queue |
| `/regenerate N`    | Rebuild captions for day N (no re-render) |
| `/id`              | Show your Telegram ID |
| *(send a video)*   | Ingest -> process -> return the result |

Access control is built in: an owner, an allow-list, and a read-only tier.

## Optional features

**Performance tracker (Google Sheets):**
```bash
python3 tracker.py init
python3 tracker.py update 1 --views-tt 1200 --subs 80 --revenue 0 --notes "start"
python3 tracker.py get 1
python3 tracker.py digest
```

**Publish to a Telegram channel:**
```bash
python3 publish_tg.py --day 1 --channel @your_channel
```

## Configuration

All secrets and tunables live in `.env` (see `.env.example`). Nothing sensitive
is hardcoded — API keys, tokens, user IDs, the watermark text, caption language,
and the persona prompt are all environment-driven.

## Notes

This is a sanitized portfolio version of a private project. Personal
identifiers, tokens, and account details have been removed and replaced with
environment variables.
