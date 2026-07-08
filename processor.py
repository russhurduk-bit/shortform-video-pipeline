"""Pipeline: raw mp4 → transcript → 9:16 video + subtitles + watermark → captions.

CLI:
    python3 processor.py /path/to/raw.mp4 --day 1
    python3 processor.py --day 1          # возьмёт raw/day-1.mp4
"""
from __future__ import annotations

import argparse
import json
import logging
import shlex
import shutil
import subprocess
from pathlib import Path

import config
import ai

log = logging.getLogger("processor")

# Lazy import — whisper грузится медленно, только когда нужно
_whisper_model = None


def _load_whisper():
    global _whisper_model
    if _whisper_model is None:
        import whisper  # type: ignore
        log.info("loading whisper model '%s'...", config.WHISPER_MODEL)
        _whisper_model = whisper.load_model(config.WHISPER_MODEL)
    return _whisper_model


# ---------- ffmpeg helpers ----------
def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    log.info("$ %s", " ".join(shlex.quote(x) for x in cmd))
    return subprocess.run(cmd, check=True, capture_output=True)


def probe(path: Path) -> dict:
    """Return {width, height, duration}."""
    r = subprocess.run(
        ["ffprobe", "-v", "error",
         "-select_streams", "v:0",
         "-show_entries", "stream=width,height:format=duration",
         "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    )
    data = json.loads(r.stdout)
    stream = (data.get("streams") or [{}])[0]
    fmt = data.get("format") or {}
    return {
        "width": int(stream.get("width", 0)),
        "height": int(stream.get("height", 0)),
        "duration": float(fmt.get("duration", 0) or 0),
    }


def _format_ts(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds - h * 3600 - m * 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def transcribe(video: Path, out_txt: Path, out_srt: Path) -> str:
    model = _load_whisper()
    log.info("whisper transcribe %s", video)
    result = model.transcribe(str(video), language=config.WHISPER_LANGUAGE or None, verbose=False)
    text = (result.get("text") or "").strip()
    out_txt.write_text(text, encoding="utf-8")

    # SRT
    lines = []
    for i, seg in enumerate(result.get("segments") or [], 1):
        lines.append(str(i))
        lines.append(f"{_format_ts(seg['start'])} --> {_format_ts(seg['end'])}")
        lines.append(seg["text"].strip())
        lines.append("")
    out_srt.write_text("\n".join(lines), encoding="utf-8")
    return text


def _vf_9x16_and_subs(srt_path: Path, day: int, src_w: int, src_h: int) -> str:
    """ffmpeg -vf строка: crop/pad до 9:16, subtitles, watermark."""
    # 1) привести к 1080x1920 (9:16)
    target_w, target_h = 1080, 1920
    if src_w == 0 or src_h == 0:
        scale_stage = (
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black"
        )
    else:
        aspect = src_w / src_h
        if abs(aspect - 9 / 16) < 0.02:
            # уже вертикаль, только scale
            scale_stage = f"scale={target_w}:{target_h}"
        elif aspect > 9 / 16:
            # горизонтальное — crop по центру под 9:16 (ширину считаем в Python,
            # чтобы не использовать min() внутри ffmpeg и не конфликтовать с
            # запятыми в -vf), потом scale до 1080x1920
            crop_w = min(src_w, int(round(src_h * 9 / 16)))
            # crop_w должна быть чётной для libx264
            if crop_w % 2:
                crop_w -= 1
            scale_stage = (
                f"crop={crop_w}:{src_h}:{(src_w - crop_w) // 2}:0,"
                f"scale={target_w}:{target_h}"
            )
        else:
            # слишком узкое — pad боками
            scale_stage = (
                f"scale={target_w}:-2:force_original_aspect_ratio=decrease,"
                f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black"
            )

    # 2) subtitles (burn-in)
    srt_escaped = str(srt_path).replace(":", "\\:").replace("'", "\\'")
    sub_stage = (
        f"subtitles='{srt_escaped}':force_style='"
        f"FontName=DejaVu Sans,FontSize=18,PrimaryColour=&H00FFFFFF,"
        f"OutlineColour=&H00000000,BorderStyle=3,Outline=2,Shadow=0,"
        f"Alignment=2,MarginV=140'"
    )

    # 3) watermark "ДЕНЬ N/100"
    wm_text = config.WATERMARK_TEMPLATE.format(day=day)
    wm_stage = (
        f"drawtext=text='{wm_text}':fontcolor=white:fontsize=56:"
        f"box=1:boxcolor=black@0.55:boxborderw=18:"
        f"fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
        f"x=(w-text_w)/2:y=80"
    )

    return ",".join([scale_stage, sub_stage, wm_stage])


def render_video(src: Path, srt: Path, day: int, dst: Path) -> None:
    info = probe(src)
    vf = _vf_9x16_and_subs(srt, day, info["width"], info["height"])
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(dst),
    ]
    _run(cmd)


def make_thumbnail(src: Path, dst: Path) -> None:
    cmd = [
        "ffmpeg", "-y", "-ss", "0.5", "-i", str(src),
        "-vframes", "1", "-q:v", "2", str(dst),
    ]
    _run(cmd)


# ---------- pipeline entry ----------
def process(raw_path: Path, day: int) -> dict:
    raw_path = Path(raw_path)
    assert raw_path.exists(), f"raw not found: {raw_path}"

    # запомним путь в raw/ — если не там, скопируем
    target_raw = config.RAW_DIR / f"day-{day}.mp4"
    if raw_path.resolve() != target_raw.resolve():
        shutil.copy2(raw_path, target_raw)
    raw = target_raw

    transcript_txt = config.TRANSCRIPTS_DIR / f"day-{day}.txt"
    transcript_srt = config.TRANSCRIPTS_DIR / f"day-{day}.srt"
    processed = config.PROCESSED_DIR / f"day-{day}.mp4"
    out_dir = config.OUTPUT_DIR / f"day-{day}"
    out_dir.mkdir(parents=True, exist_ok=True)

    config.upsert_day(
        day,
        raw_path=str(raw),
        status="transcribing",
    )

    text = transcribe(raw, transcript_txt, transcript_srt)
    config.upsert_day(
        day,
        transcript_path=str(transcript_txt),
        status="rendering",
    )

    render_video(raw, transcript_srt, day, processed)
    shutil.copy2(processed, out_dir / "video.mp4")
    make_thumbnail(processed, out_dir / "thumbnail.jpg")

    config.upsert_day(
        day,
        processed_path=str(processed),
        output_dir=str(out_dir),
        status="captioning",
    )

    captions = ai.generate_captions(text, day)
    (out_dir / "captions.json").write_text(
        json.dumps(captions, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "captions.txt").write_text(
        f"== TikTok ==\n{captions['version_tt']}\n\n"
        f"== Reels / Shorts ==\n{captions['version_reels_shorts']}\n\n"
        f"== Telegram ==\n{captions['version_tg']}\n",
        encoding="utf-8",
    )
    (out_dir / "transcript.txt").write_text(text, encoding="utf-8")

    config.upsert_day(
        day,
        caption_tt=captions["version_tt"],
        caption_reels_shorts=captions["version_reels_shorts"],
        caption_tg=captions["version_tg"],
        status="done",
        error=None,
    )
    log.info("day %s DONE → %s", day, out_dir)
    return {
        "day": day,
        "video": str(out_dir / "video.mp4"),
        "captions": captions,
        "out_dir": str(out_dir),
    }


def regenerate_captions(day: int) -> dict[str, str]:
    d = config.get_day(day)
    if not d:
        raise FileNotFoundError(f"day {day} not in db")
    text = ""
    if d.get("transcript_path"):
        p = Path(d["transcript_path"])
        if p.exists():
            text = p.read_text(encoding="utf-8")
    captions = ai.generate_captions(text, day)
    out_dir = Path(d.get("output_dir") or (config.OUTPUT_DIR / f"day-{day}"))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "captions.json").write_text(
        json.dumps(captions, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "captions.txt").write_text(
        f"== TikTok ==\n{captions['version_tt']}\n\n"
        f"== Reels / Shorts ==\n{captions['version_reels_shorts']}\n\n"
        f"== Telegram ==\n{captions['version_tg']}\n",
        encoding="utf-8",
    )
    config.upsert_day(
        day,
        caption_tt=captions["version_tt"],
        caption_reels_shorts=captions["version_reels_shorts"],
        caption_tg=captions["version_tg"],
    )
    return captions


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s — %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("src", nargs="?", help="raw video path (default: raw/day-N.mp4)")
    ap.add_argument("--day", type=int, required=True)
    ap.add_argument("--regen-captions", action="store_true",
                    help="только перегенерировать подписи")
    args = ap.parse_args()

    if args.regen_captions:
        print(json.dumps(regenerate_captions(args.day), ensure_ascii=False, indent=2))
        return

    src = Path(args.src) if args.src else (config.RAW_DIR / f"day-{args.day}.mp4")
    result = process(src, args.day)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
