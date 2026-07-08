"""Google Sheets — "100-day tracker".

Колонки:
  Day | Date | Video Link | Views_TT | Views_Reels | Views_Shorts | Views_TG
  Subs_Total | Revenue_Day | Revenue_Cumulative | Notes

Использование:
  python3 tracker.py init                          # создать header если листа пустого
  python3 tracker.py update 1 --views-tt 1200 --subs 80 --revenue 0 --notes "первый день"
  python3 tracker.py get 1
  python3 tracker.py digest                        # сводка за вчера

Требует .env: SHEETS_ID + GOOGLE_SA_KEY.

Note: create the Google Sheet manually and share it with the service account
(the client_email from your service-account JSON), then set SHEETS_ID in .env.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402

log = logging.getLogger("tracker")

HEADER = [
    "Day", "Date", "Video Link",
    "Views_TT", "Views_Reels", "Views_Shorts", "Views_TG",
    "Subs_Total", "Revenue_Day", "Revenue_Cumulative", "Notes",
]


def _client():
    import gspread
    from google.oauth2.service_account import Credentials
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(config.GOOGLE_SA_KEY, scopes=scopes)
    return gspread.authorize(creds)


def _sheet():
    if not config.SHEETS_ID:
        raise SystemExit("SHEETS_ID пусто — впиши ID созданной Google Sheet в .env")
    gc = _client()
    sh = gc.open_by_key(config.SHEETS_ID)
    return sh.sheet1


def init_sheet() -> None:
    ws = _sheet()
    first = ws.row_values(1)
    if first == HEADER:
        log.info("header уже на месте")
        return
    if not first:
        ws.append_row(HEADER)
        log.info("header записан")
    else:
        ws.delete_rows(1)
        ws.insert_row(HEADER, 1)
        log.info("header заменён")


def _find_row(ws, day: int) -> int | None:
    col = ws.col_values(1)[1:]  # без header
    for i, v in enumerate(col, start=2):
        try:
            if int(v) == day:
                return i
        except Exception:
            continue
    return None


def _cum_revenue(ws, up_to_day: int) -> float:
    total = 0.0
    all_rows = ws.get_all_records()
    for r in all_rows:
        try:
            d = int(r.get("Day") or 0)
            if d <= up_to_day:
                val = str(r.get("Revenue_Day") or "0").replace(" ", "").replace(",", ".")
                total += float(val or 0)
        except Exception:
            continue
    return total


def update(day: int, **fields) -> None:
    ws = _sheet()
    # Выровняем row
    row_idx = _find_row(ws, day)
    today = date.today().isoformat()

    row: list = [day, today, "", "", "", "", "", "", 0, 0, ""]
    if row_idx:
        existing = ws.row_values(row_idx)
        existing += [""] * (len(HEADER) - len(existing))
        row = existing[:len(HEADER)]
        row[0] = day  # убедиться что Day на месте

    mapping = {
        "video_link": 2,
        "views_tt": 3,
        "views_reels": 4,
        "views_shorts": 5,
        "views_tg": 6,
        "subs": 7,
        "revenue": 8,
        "notes": 10,
    }
    for k, v in fields.items():
        if v is None:
            continue
        idx = mapping.get(k)
        if idx is not None:
            row[idx] = v

    # Revenue_Cumulative пересчитаем
    try:
        rev_day = float(str(row[8] or 0).replace(",", "."))
    except Exception:
        rev_day = 0.0
    # Чтобы не дважды считать текущий день при пересумме — переустановим
    row[8] = rev_day
    # Считаем cumulative: всё кроме текущего дня + текущий
    ws_for_cum = ws
    cum_prev = _cum_revenue(ws_for_cum, day - 1)
    row[9] = cum_prev + rev_day
    row[1] = row[1] or today

    if row_idx:
        ws.update(f"A{row_idx}:K{row_idx}", [row])
        log.info("день %s обновлён (row %s)", day, row_idx)
    else:
        ws.append_row(row)
        log.info("день %s добавлен", day)


def get(day: int) -> dict:
    ws = _sheet()
    row_idx = _find_row(ws, day)
    if not row_idx:
        return {}
    vals = ws.row_values(row_idx)
    vals += [""] * (len(HEADER) - len(vals))
    return dict(zip(HEADER, vals))


def digest_yesterday() -> dict:
    ws = _sheet()
    rows = ws.get_all_records()
    if not rows:
        return {"empty": True}
    # берём последнюю запись (вчера по смыслу)
    rows.sort(key=lambda r: int(r.get("Day") or 0))
    last = rows[-1]
    prev = rows[-2] if len(rows) >= 2 else None
    def _n(v):
        try: return int(str(v or 0).replace(" ", ""))
        except Exception: return 0
    subs_now = _n(last.get("Subs_Total"))
    subs_prev = _n(prev.get("Subs_Total")) if prev else 0
    return {
        "day": last.get("Day"),
        "date": last.get("Date"),
        "views_tt": _n(last.get("Views_TT")),
        "views_reels": _n(last.get("Views_Reels")),
        "views_shorts": _n(last.get("Views_Shorts")),
        "views_tg": _n(last.get("Views_TG")),
        "subs_delta": subs_now - subs_prev,
        "subs_total": subs_now,
        "revenue_cumulative": float(str(last.get("Revenue_Cumulative") or 0).replace(",", ".")),
    }


# ---------- CLI ----------
def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init")

    u = sub.add_parser("update")
    u.add_argument("day", type=int)
    u.add_argument("--video-link")
    u.add_argument("--views-tt", type=int, dest="views_tt")
    u.add_argument("--views-reels", type=int, dest="views_reels")
    u.add_argument("--views-shorts", type=int, dest="views_shorts")
    u.add_argument("--views-tg", type=int, dest="views_tg")
    u.add_argument("--subs", type=int, dest="subs")
    u.add_argument("--revenue", type=float, dest="revenue")
    u.add_argument("--notes")

    g = sub.add_parser("get")
    g.add_argument("day", type=int)

    sub.add_parser("digest")

    args = ap.parse_args()

    if args.cmd == "init":
        init_sheet()
    elif args.cmd == "update":
        fields = {k: v for k, v in vars(args).items()
                  if k not in ("cmd", "day") and v is not None}
        update(args.day, **fields)
    elif args.cmd == "get":
        print(json.dumps(get(args.day), ensure_ascii=False, indent=2))
    elif args.cmd == "digest":
        print(json.dumps(digest_yesterday(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
