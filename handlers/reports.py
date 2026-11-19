import os
from datetime import datetime
from aiogram import Router, F, types
from aiogram.filters import Command, CommandObject
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
import database as db
import report as rp
from .utils import ensure_user

router = Router()

@router.message(Command("sessions"))
@router.message(F.text == "📂 Сохранённые мониторинги")
async def cmd_sessions(message: types.Message):
    user = await ensure_user(message)
    if not user: return
    finished = db.get_finished_sessions_for_user(message.from_user.id)
    if not finished:
        await message.answer("Пока нет завершённых мониторингов.")
        return

    lines = ["📂 Завершённые мониторинги:"]
    buttons = []
    for s in finished:
        lines.append(f"#{s['id']} «{s['name']}» — {s['started'].strftime('%d.%m %H:%M')} ({len(db.get_session_events(s['id']))} соб.)")
        buttons.append([InlineKeyboardButton(text=f"📊 Отчёт #{s['id']}", callback_data=f"report:{s['id']}")])
    await message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@router.message(Command("report"))
async def cmd_report(message: types.Message, command: CommandObject):
    user = await ensure_user(message)
    if not user: return
    
    session_id = int(command.args.strip()) if command.args and command.args.strip().isdigit() else None
    if not session_id:
        finished = db.get_finished_sessions_for_user(message.from_user.id)
        if not finished:
            await message.answer("Нет завершённых мониторингов.")
            return
        session_id = finished[0]["id"]

    target = db.get_session(session_id)
    if not target or target["stopped"] is None:
        await message.answer("Сессия не найдена или ещё активна.")
        return

    await _send_report(message, session_id, target)

@router.callback_query(F.data.startswith("report:"))
async def cb_report(callback: types.CallbackQuery):
    await callback.answer()
    session_id = int(callback.data.split(":")[1])
    target = db.get_session(session_id)
    if target:
        await _send_report(callback.message, session_id, target)

async def _send_report(message: types.Message, session_id: int, target: dict):
    await message.answer(f"⏳ Готовлю отчёт по «{target['name']}»...")
    try:
        os.makedirs("reports", exist_ok=True)
        filename = f"report_{session_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        filepath = os.path.join("reports", filename)
        rp.generate_report(session_id, filepath)
        await message.answer_document(FSInputFile(filepath), caption=f"📊 Отчёт «{target['name']}»")
    except Exception as e:
        await message.answer(f"⚠️ Ошибка отчёта: {e}")