# report.py
import os
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.drawing.image import Image as XLImage
import database as db


DURATION_FORMAT = "[h]:mm:ss"
PHOTO_THUMB_WIDTH = 160      # px
PHOTO_THUMB_HEIGHT = 120     # px
PHOTO_ROW_HEIGHT = 95        # высота строки в pt (≈127 px), чтобы миниатюра влезла


# ---------- Вспомогательные ----------

def to_excel_duration(seconds: int | None) -> float | None:
    if seconds is None:
        return None
    return seconds / 86400.0


def fmt_duration(seconds: int | None) -> str:
    if seconds is None or seconds < 0:
        return "—"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def _style_header(ws, row: int, cols: int):
    fill = PatternFill("solid", fgColor="4472C4")
    font = Font(bold=True, color="FFFFFF")
    align = Alignment(horizontal="center", vertical="center")
    border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin"),
    )
    for c in range(1, cols + 1):
        cell = ws.cell(row=row, column=c)
        cell.fill = fill
        cell.font = font
        cell.alignment = align
        cell.border = border


def _style_cells(ws, first_row: int, last_row: int, cols: int):
    border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin"),
    )
    for r in range(first_row, last_row + 1):
        for c in range(1, cols + 1):
            cell = ws.cell(row=r, column=c)
            cell.border = border
            cell.alignment = Alignment(vertical="center")


def _resolve_photo_path(file_path: str | None) -> str | None:
    """Возвращает абсолютный путь, если файл существует, иначе None."""
    if not file_path:
        return None
    # Делаем путь абсолютным относительно папки проекта
    base_dir = os.path.dirname(os.path.abspath(__file__))
    abs_path = os.path.join(base_dir, file_path)
    return abs_path if os.path.exists(abs_path) else None


def _build_event_rows(intervals: list[dict], session_end: datetime) -> list[dict]:
    """
    Строки для листа «События»:
    - work_seconds — для стартов;
    - downtime_seconds — для стопов;
    - комментарии и фото приклеиваются к предыдущему старту/стопу
      (или к первому, если комментарии/фото до него).
    """
    for i, ev in enumerate(intervals):
        if ev["event_type"] == "start":
            next_stop_time = None
            for j in range(i + 1, len(intervals)):
                if intervals[j]["event_type"] == "stop":
                    next_stop_time = intervals[j]["time"]
                    break
            end = next_stop_time if next_stop_time is not None else session_end
            ev["work_seconds"] = int((end - ev["time"]).total_seconds())
            ev["downtime_seconds"] = None
        elif ev["event_type"] == "stop":
            next_time = None
            for j in range(i + 1, len(intervals)):
                if intervals[j]["event_type"] in ("start", "stop"):
                    next_time = intervals[j]["time"]
                    break
            end = next_time if next_time is not None else session_end
            ev["downtime_seconds"] = int((end - ev["time"]).total_seconds())
            ev["work_seconds"] = None
        else:
            ev["work_seconds"] = None
            ev["downtime_seconds"] = None

    result = []
    pending_comments = []   # строки вида "09:32 — текст"
    pending_photos = []     # строки вида "📸 фото №1 в 09:32"

    photo_counter = 0

    for ev in intervals:
        if ev["event_type"] == "note":
            t = ev["time"].strftime("%H:%M:%S")
            text = (ev["text"] or "").strip()
            pending_comments.append(f"{t} — {text}")
            continue
        if ev["event_type"] == "photo":
            photo_counter += 1
            t = ev["time"].strftime("%H:%M:%S")
            pending_photos.append(f"📸 фото №{photo_counter} в {t}")
            continue

        row = {
            "kind": ev["event_type"],
            "number": ev.get("number"),
            "time": ev["time"],
            "reason": ev.get("reason"),
            "work_seconds": ev.get("work_seconds"),
            "downtime_seconds": ev.get("downtime_seconds"),
            "comments": [],
            "photos": [],
        }

        if pending_comments or pending_photos:
            if not result:
                row["comments"] = pending_comments
                row["photos"] = pending_photos
            else:
                result[-1]["comments"].extend(pending_comments)
                result[-1]["photos"].extend(pending_photos)
            pending_comments = []
            pending_photos = []

        result.append(row)

    if (pending_comments or pending_photos) and result:
        result[-1]["comments"].extend(pending_comments)
        result[-1]["photos"].extend(pending_photos)
    elif (pending_comments or pending_photos) and not result:
        result.append({
            "kind": "note_only",
            "number": None, "time": None, "reason": None,
            "work_seconds": None, "downtime_seconds": None,
            "comments": pending_comments,
            "photos": pending_photos,
        })

    return result


def _group_reasons(intervals: list[dict]) -> list[tuple[str, int]]:
    groups: dict[str, dict] = {}
    for ev in intervals:
        if ev["event_type"] != "stop":
            continue
        raw = (ev.get("reason") or "").strip()
        key = raw.lower() if raw else "(без причины)"
        if key not in groups:
            groups[key] = {"label": raw if raw else "(без причины)", "seconds": 0}
        dur = ev.get("stop_duration") or 0
        groups[key]["seconds"] += dur
    return sorted(
        [(g["label"], g["seconds"]) for g in groups.values()],
        key=lambda x: -x[1],
    )


# ---------- Генерация ----------

def generate_report(session_id: int, filepath: str) -> str:
    with db.get_connection() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            raise ValueError(f"Сессия #{session_id} не найдена")
        session = dict(row)

    events = db.get_session_events(session_id)
    session_end = session["stopped"] or db.now_seconds()
    intervals = db.compute_intervals(events, session_end=session_end)

    stops = [e for e in intervals if e["event_type"] == "stop"]
    starts = [e for e in intervals if e["event_type"] == "start"]
    notes = [e for e in intervals if e["event_type"] == "note"]
    photos = [e for e in intervals if e["event_type"] == "photo"]

    total_time = int((session_end - session["started"]).total_seconds())
    work_time = db.total_work_time(events, session["started"], session_end=session_end)
    total_downtime = sum(e["stop_duration"] for e in stops if e["stop_duration"] is not None)

    wb = Workbook()

    # ===== Лист «Сводка» =====
    ws = wb.active
    ws.title = "Сводка"
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 40

    ws["A1"] = "Отчёт по мониторингу оборудования"
    ws["A1"].font = Font(bold=True, size=14)
    ws.merge_cells("A1:B1")
    ws["A1"].alignment = Alignment(horizontal="center")

    summary_rows = [
        ("Название мониторинга", session["name"], None),
        ("Начало", session["started"].strftime("%d.%m.%Y %H:%M:%S"), None),
        ("Конец", session_end.strftime("%d.%m.%Y %H:%M:%S"), None),
        ("Время мониторинга", to_excel_duration(total_time), DURATION_FORMAT),
        ("Время работы", to_excel_duration(work_time), DURATION_FORMAT),
        ("Время простоя", to_excel_duration(total_downtime), DURATION_FORMAT),
        ("Остановок", len(stops), None),
        ("Запусков", len(starts), None),
        ("Комментариев", len(notes), None),
        ("Фотографий", len(photos), None),
    ]
    start_row = 3
    for i, (label, value, fmt) in enumerate(summary_rows):
        r = start_row + i
        c1 = ws.cell(row=r, column=1, value=label)
        c1.font = Font(bold=True)
        c2 = ws.cell(row=r, column=2, value=value)
        if fmt:
            c2.number_format = fmt

    _style_cells(ws, start_row, start_row + len(summary_rows) - 1, 2)

    # ===== Лист «События» =====
    ws2 = wb.create_sheet("События")
    headers = ["#", "Тип", "Время", "Причина / текст", "Время работы", "Простой", "Комментарий / фото"]
    widths = [6, 16, 12, 40, 16, 16, 45]
    for i, (h, w) in enumerate(zip(headers, widths), start=1):
        ws2.cell(row=1, column=i, value=h)
        ws2.column_dimensions[get_column_letter(i)].width = w
    _style_header(ws2, 1, len(headers))

    rows = _build_event_rows(intervals, session_end)

    r = 2
    for row in rows:
        if row["kind"] == "stop":
            ws2.cell(row=r, column=1, value=row["number"])
            ws2.cell(row=r, column=2, value="⏸ Стоп")
            ws2.cell(row=r, column=3, value=row["time"].strftime("%H:%M:%S") if row["time"] else "")
            ws2.cell(row=r, column=4, value=row["reason"] or "—")
            if row["downtime_seconds"] is not None:
                c = ws2.cell(row=r, column=6, value=to_excel_duration(row["downtime_seconds"]))
                c.number_format = DURATION_FORMAT
        elif row["kind"] == "start":
            ws2.cell(row=r, column=1, value="—")
            ws2.cell(row=r, column=2, value="▶️ Старт")
            ws2.cell(row=r, column=3, value=row["time"].strftime("%H:%M:%S") if row["time"] else "")
            if row["work_seconds"] is not None:
                c = ws2.cell(row=r, column=5, value=to_excel_duration(row["work_seconds"]))
                c.number_format = DURATION_FORMAT
        else:
            ws2.cell(row=r, column=1, value="—")
            ws2.cell(row=r, column=2, value="📝 Комментарий")
            ws2.cell(row=r, column=3, value="")

        parts = []
        if row["comments"]:
            parts.extend(row["comments"])
        if row["photos"]:
            parts.extend(row["photos"])
        if parts:
            ws2.cell(row=r, column=7, value="; ".join(parts))

        r += 1

    last_data_row = r - 1
    _style_cells(ws2, 2, last_data_row, len(headers))
    if last_data_row >= 2:
        ws2.auto_filter.ref = f"A1:G{last_data_row}"

    # ===== Итоги =====
    r = last_data_row + 2

    def write_total(label: str, value, fmt=None, bold=False):
        nonlocal r
        c1 = ws2.cell(row=r, column=1, value=label)
        if bold:
            c1.font = Font(bold=True)
        if value is not None:
            c2 = ws2.cell(row=r, column=2, value=value)
            if fmt:
                c2.number_format = fmt
            if bold:
                c2.font = Font(bold=True)
        r += 1

    write_total("Итого", None, bold=True)
    write_total("Время мониторинга", to_excel_duration(total_time), DURATION_FORMAT)
    write_total("Время работы", to_excel_duration(work_time), DURATION_FORMAT)
    write_total("Время простоя", to_excel_duration(total_downtime), DURATION_FORMAT)
    write_total("в том числе:", None, bold=True)

    for label, seconds in _group_reasons(intervals):
        write_total(f"  {label}", to_excel_duration(seconds), DURATION_FORMAT)

    # ===== Лист «Фотографии» =====
    ws3 = wb.create_sheet("Фотографии")
    headers3 = ["№", "Время", "Описание", "Фото"]
    widths3 = [6, 12, 50, 24]
    for i, (h, w) in enumerate(zip(headers3, widths3), start=1):
        ws3.cell(row=1, column=i, value=h)
        ws3.column_dimensions[get_column_letter(i)].width = w
    _style_header(ws3, 1, len(headers3))

    if not photos:
        ws3.cell(row=2, column=1, value="Фотографий в этой сессии нет.")
        ws3.merge_cells("A2:D2")
        ws3["A2"].alignment = Alignment(horizontal="center")
    else:
        r3 = 2
        for idx, ph in enumerate(photos, start=1):
            ws3.cell(row=r3, column=1, value=idx)
            ws3.cell(row=r3, column=2, value=ph["time"].strftime("%H:%M:%S"))
            ws3.cell(row=r3, column=3, value=ph["text"] or "(без описания)")

            resolved = _resolve_photo_path(ph.get("file_path"))
            if resolved is None:
                ws3.cell(row=r3, column=4, value="фото удалено (нет файла)")
            else:
                try:
                    img = XLImage(resolved)
                    img.width = PHOTO_THUMB_WIDTH
                    img.height = PHOTO_THUMB_HEIGHT
                    ws3.add_image(img, f"D{r3}")
                    ws3.row_dimensions[r3].height = PHOTO_ROW_HEIGHT
                except Exception as e:
                    # Это выведет ошибку в терминал бота
                    print(f"[ОШИБКА ФОТО] Файл: {resolved}")
                    print(f"[ОШИБКА ФОТО] Причина: {type(e).__name__}: {e}")
                    ws3.cell(row=r3, column=4, value="фото не читается")

            r3 += 1

        _style_cells(ws3, 2, r3 - 1, len(headers3))

    wb.save(filepath)
    return filepath