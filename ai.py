"""LLM caption generation (Groq primary, Anthropic fallback).

The primary provider is Groq (OpenAI-compatible chat completions); Anthropic
Messages API is used as a fallback. If both are unavailable the pipeline still
produces basic captions so it never hard-fails.

Prompt copy is configurable via environment variables so the same code can drive
any creator/brand voice:
    CAPTION_SYSTEM_PROMPT   — system prompt (persona / tone of voice)
    CAPTION_HASHTAGS        — space-separated hashtags appended to short captions
    CAPTION_LANGUAGE        — output language name (default: Russian)
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import requests

import config

log = logging.getLogger("ai")

LANGUAGE = os.getenv("CAPTION_LANGUAGE", "Russian")

# Base hashtags appended to short-form captions. {day} is substituted per video.
HASHTAGS = os.getenv("CAPTION_HASHTAGS", "#AI #startup #buildinpublic #day{day}")

SYSTEM_PROMPT = os.getenv(
    "CAPTION_SYSTEM_PROMPT",
    (
        "You are an SMM copywriter for a short-form video creator running a "
        "public 'build in public' challenge. Videos are 30-60s selfie clips. "
        f"Write captions in {LANGUAGE}: hook-first, punchy, no fluff, natural "
        "spoken tone, no corporate jargon. "
        "Return STRICTLY valid JSON with no comments and no markdown fences."
    ),
)

USER_TEMPLATE = """This is the transcript of a video (day {day}/{total}).

Transcript:
\"\"\"
{transcript}
\"\"\"

Generate JSON:
{{
  "version_tt": "TikTok — 1-2 short sentences, hook in the first 10 characters, hashtags at the end: {hashtags}",
  "version_reels_shorts": "Reels / Shorts — up to 150 characters, same hashtags",
  "version_tg": "Telegram — a full post of 300-600 characters, with a concrete number/insight and a cliffhanger for the next day, no hashtags, minimal emoji (1-2 max)"
}}

RULES:
- Use numbers and facts ONLY from the transcript (do not invent them).
- Address the audience informally, lively, without marketing pathos.
- Refer to the day as "Day {day}/{total}".
- If the transcript is empty/low quality, still return JSON with short captions on the day's topic.
- Write all caption text in {language}.
"""


def _strip_code_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n", "", s)
        s = re.sub(r"\n?```$", "", s)
    return s.strip()


def _parse_json(text: str) -> dict[str, Any]:
    text = _strip_code_fence(text)
    # Find first { ... last } if the model added extra text
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


def _call_groq(messages: list[dict]) -> str:
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {config.GROQ_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.GROQ_MODEL,
            "messages": messages,
            "temperature": 0.8,
            "max_tokens": 1200,
            "response_format": {"type": "json_object"},
        },
        timeout=90,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _call_anthropic(messages: list[dict]) -> str:
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": config.ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={
            "model": config.ANTHROPIC_MODEL,
            "max_tokens": 1500,
            "system": messages[0]["content"],
            "messages": [
                {"role": "user", "content": messages[1]["content"]}
            ],
        },
        timeout=120,
    )
    r.raise_for_status()
    data = r.json()
    return "".join(b["text"] for b in data["content"] if b.get("type") == "text")


def generate_captions(transcript: str, day: int) -> dict[str, str]:
    """Return {"version_tt": ..., "version_reels_shorts": ..., "version_tg": ...}."""
    transcript = (transcript or "").strip()
    if len(transcript) > 8000:
        transcript = transcript[:8000] + "…"
    hashtags = HASHTAGS.format(day=day)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": USER_TEMPLATE.format(
                day=day,
                total=config.TOTAL_DAYS,
                transcript=transcript,
                hashtags=hashtags,
                language=LANGUAGE,
            ),
        },
    ]

    last_err: Exception | None = None
    if config.GROQ_API_KEY:
        try:
            raw = _call_groq(messages)
            return _normalize(_parse_json(raw), day)
        except Exception as e:
            last_err = e
            log.warning("groq failed: %s", e)

    if config.ANTHROPIC_API_KEY:
        try:
            raw = _call_anthropic(messages)
            return _normalize(_parse_json(raw), day)
        except Exception as e:
            last_err = e
            log.warning("anthropic failed: %s", e)

    # Fallback: no LLM — return a minimal result, the pipeline must keep working.
    log.error("all LLMs failed: %s; using fallback captions", last_err)
    base = transcript[:140].replace("\n", " ") or f"Day {day}/{config.TOTAL_DAYS} — challenge continues."
    tags = HASHTAGS.format(day=day)
    return {
        "version_tt": f"{base[:110]} {tags}",
        "version_reels_shorts": f"{base[:120]} {tags}",
        "version_tg": (
            f"Day {day}/{config.TOTAL_DAYS}.\n\n{base}\n\n"
            f"More tomorrow."
        ),
    }


def _normalize(obj: dict, day: int) -> dict[str, str]:
    out = {
        "version_tt": str(obj.get("version_tt", "")).strip(),
        "version_reels_shorts": str(obj.get("version_reels_shorts", "")).strip(),
        "version_tg": str(obj.get("version_tg", "")).strip(),
    }
    # Ensure hashtags are present in the short versions.
    tags = HASHTAGS.format(day=day)
    day_tag = f"#day{day}"
    for k in ("version_tt", "version_reels_shorts"):
        if day_tag not in out[k]:
            out[k] = out[k].rstrip() + " " + tags
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import sys
    day = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    text = sys.stdin.read() or "Set up the video processing pipeline today. Day one is rolling."
    print(json.dumps(generate_captions(text, day), ensure_ascii=False, indent=2))
