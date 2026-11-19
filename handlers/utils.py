import os
import database as db
from aiogram import types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]

async def ensure_user(message: types.Message) -> dict | None:
    """Регистрирует пользователя и проверяет его доступ."""
    user = db.get_user(message.from_user.id)
    
    if user is None:
        is_admin = message.from_user.id in ADMIN_IDS
        user = db.register_user(
            message.from_user.id,
            message.from_user.full_name or str(message.from_user.id),
            is_admin
        )
        if is_admin:
            db.set_user_status(user["telegram_id"], "approved")
            user["status"] = "approved"

    db.update_last_seen(user["telegram_id"])

    if user["status"] != "approved":
        await _show_access_denied(message, user)
        return None
    return user

async def _show_access_denied(message: types.Message, user: dict):
    if user["status"] == "blocked":
        await message.answer(
            "⛔ Ваш доступ к боту приостановлен администратором.\n"
            "Обратитесь к руководителю для восстановления доступа."
        )
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📨 Подать заявку на доступ", callback_data="req_access")]
        ])
        await message.answer(
            "⛔ Доступ к боту ограничен.\n\n"
            "Этот бот предназначен для конкретных сотрудников. "
            "Пожалуйста, подайте заявку, чтобы администратор мог ее одобрить.",
            reply_markup=kb
        )

def fmt_duration(seconds: int | None) -> str:
    if seconds is None or seconds < 0:
        return "—"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h: return f"{h}ч {m}м {s}с"
    if m: return f"{m}м {s}с"
    return f"{s}с"

def calc_work_before(intervals: list[dict], moment, session_started) -> int:
    last_state_ev = None
    for ev in reversed(intervals):
        if ev["event_type"] in ("start", "stop"):
            last_state_ev = ev
            break
    if last_state_ev is None:
        return int((moment - session_started).total_seconds())
    if last_state_ev["event_type"] == "start":
        return int((moment - last_state_ev["time"]).total_seconds())
    return 0