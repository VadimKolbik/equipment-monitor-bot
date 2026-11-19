from aiogram import Router, F, types
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
import database as db
from .utils import ensure_user, fmt_duration
from .common import get_idle_keyboard, COMMANDS_HELP
from .monitoring import main_keyboard

router = Router()

class SessionStates(StatesGroup):
    waiting_session_name = State()
    waiting_join_code = State()

def session_menu_keyboard(session: dict, user_id: int, is_admin: bool) -> InlineKeyboardMarkup:
    rows = []
    if session["stopped"] is None:
        rows.append([InlineKeyboardButton(text="⏹ Завершить мониторинг", callback_data="menu_stop")])
    rows.append([InlineKeyboardButton(text="ℹ️ Информация", callback_data="menu_info")])
    rows.append([InlineKeyboardButton(text="❓ Стопы без причины", callback_data="menu_no_reason")])
    rows.append([InlineKeyboardButton(text="👥 Участники и запросы на вступление", callback_data="menu_participants")])
    rows.append([InlineKeyboardButton(text="🔑 Показать код", callback_data="menu_code")])
    rows.append([InlineKeyboardButton(text="📊 Отчёт по этой сессии", callback_data=f"report:{session['id']}")])
    rows.append([InlineKeyboardButton(text="📖 Команды", callback_data="menu_help")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

@router.message(Command("start_monitoring"))
@router.message(F.text == "▶️ Начать мониторинг")
async def cmd_start_monitoring(message: types.Message, state: FSMContext):
    user = await ensure_user(message)
    if not user: return

    active = db.get_active_session_for_user(message.from_user.id)
    if active is not None:
        await message.answer(f"Уже идёт мониторинг «{active['name']}».\nСначала завершите его: /stop_monitoring.", reply_markup=main_keyboard)
        return
    await state.set_state(SessionStates.waiting_session_name)
    await message.answer("Введите название мониторинга (например, «Смена 1»).", reply_markup=ReplyKeyboardRemove())

@router.message(SessionStates.waiting_session_name, F.text)
async def handle_session_name(message: types.Message, state: FSMContext):
    user = await ensure_user(message)
    if not user: return

    name = message.text.strip()
    session = db.create_session(name, user["telegram_id"])
    db.add_event(session["id"], "start")
    await state.clear()
    await message.answer(
        f"✅ Мониторинг «{name}» начат в {session['started'].strftime('%d.%m.%Y %H:%M:%S')}.\n"
        f"▶️ Оборудование считается работающим.\n"
        f"Если оно стоит — нажмите «⏸ Стоп».\n\n"
        f"🔑 Код доступа: {session['code']}\n"
        f"Передайте его коллеге, чтобы подключиться к этому мониторингу.",
        reply_markup=main_keyboard,
    )

@router.message(Command("join"))
async def cmd_join(message: types.Message, command: CommandObject):
    user = await ensure_user(message)
    if not user: return
    if not command.args:
        await message.answer("Формат: /join <код>\nНапример: /join A3F9-K2M7")
        return
    await _handle_join_code(message, command.args.strip())

@router.message(F.text == "🔑 Подключиться по коду")
async def btn_join(message: types.Message, state: FSMContext):
    user = await ensure_user(message)
    if not user: return
    active = db.get_active_session_for_user(message.from_user.id)
    if active is not None:
        await message.answer("У вас есть активная сессия. Чтобы подключиться к другой, сначала завершите текущую.", reply_markup=main_keyboard)
        return
    await state.set_state(SessionStates.waiting_join_code)
    await message.answer("Введите код доступа (вида XXXX-XXXX).\nЕго можно получить у владельца мониторинга.", reply_markup=ReplyKeyboardRemove())

@router.message(SessionStates.waiting_join_code, F.text)
async def handle_join_code(message: types.Message, state: FSMContext):
    user = await ensure_user(message)
    if not user: return
    await state.clear()
    await _handle_join_code(message, message.text.strip())

async def _handle_join_code(message: types.Message, code: str):
    user = db.get_user(message.from_user.id)
    session = db.get_session_by_code(code)

    if session is None:
        await message.answer("Код не найден. Проверьте, правильно ли ввели (формат XXXX-XXXX).", reply_markup=get_idle_keyboard(has_active=False, is_admin=(user["role"] == "admin")))
        return

    if db.has_access(session["id"], user["telegram_id"]):
        await message.answer("Вы уже участник этой сессии.")
        active = db.get_active_session_for_user(message.from_user.id)
        await message.answer(f"Текущая сессия: «{active['name']}»." if active else "Активной сессии нет.", reply_markup=main_keyboard if active else get_idle_keyboard(has_active=False, is_admin=(user["role"] == "admin")))
        return

    if session["stopped"] is not None:
        db.add_participant(session["id"], user["telegram_id"])
        await message.answer(f"✅ Сессия «{session['name']}» завершена. Добавлена в сохранённые.\nМожно запросить отчёт командой /report {session['id']}.", reply_markup=get_idle_keyboard(has_active=False, is_admin=(user["role"] == "admin")))
        return

    req_id = db.create_join_request(session["id"], user["telegram_id"])
    if req_id is None:
        await message.answer("Запрос уже отправлен. Ожидайте одобрения.")
        return

    await message.answer(f"📨 Запрос на вступление в «{session['name']}» отправлен.\nОжидайте одобрения участников.", reply_markup=get_idle_keyboard(has_active=False, is_admin=(user["role"] == "admin")))

    participants = db.get_session_participants(session["id"])
    for p in participants:
        try:
            await message.bot.send_message(p["telegram_id"], f"📨 Пользователь {user['name']} хочет присоединиться к «{session['name']}».\n⚙️ Меню → Участники.", )
        except Exception:
            pass

@router.message(F.text == "⚙️ Меню")
async def btn_menu(message: types.Message):
    user = await ensure_user(message)
    if not user: return
    active = db.get_active_session_for_user(message.from_user.id)
    if not active:
        await message.answer("Нет активного мониторинга.")
        return
    await message.answer("Меню мониторинга:", reply_markup=session_menu_keyboard(active, user["telegram_id"], user["role"] == "admin"))

@router.message(Command("stop_monitoring"))
async def cmd_stop_monitoring(message: types.Message, state: FSMContext):
    user = await ensure_user(message)
    if not user: return
    active = db.get_active_session_for_user(message.from_user.id)
    if not active:
        await message.answer("Сейчас нет активного мониторинга.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, завершить", callback_data=f"finish_confirm:{active['id']}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="finish_cancel")],
    ])
    await message.answer(f"Завершить мониторинг «{active['name']}»?", reply_markup=kb)

@router.callback_query(F.data.startswith("finish_confirm:"))
async def cb_finish_confirm(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    session_id = int(callback.data.split(":", 1)[1])
    user = db.get_user(callback.from_user.id)
    is_adm = user and user["role"] == "admin"

    if not is_adm and not db.has_access(session_id, callback.from_user.id):
        await callback.message.edit_text("Нет доступа к этой сессии.")
        return

    session = db.get_session(session_id)
    if session is None or session["stopped"] is not None:
        await callback.message.edit_text("Сессия уже завершена.")
        return

    stopped_at = db.finish_session(session_id)
    db.cancel_pending_requests_for_session(session_id)

    events = db.get_session_events(session_id)
    intervals = db.compute_intervals(events, session_end=stopped_at)
    stops = [e for e in intervals if e["event_type"] == "stop"]
    starts = [e for e in intervals if e["event_type"] == "start"]
    notes = [e for e in intervals if e["event_type"] == "note"]
    photos = [e for e in intervals if e["event_type"] == "photo"]
    
    total_time = int((stopped_at - session["started"]).total_seconds())
    work_time = db.total_work_time(events, session["started"], session_end=stopped_at)
    total_downtime = sum(e["stop_duration"] for e in stops if e["stop_duration"] is not None)

    summary = (
        f"⏹ Мониторинг «{session['name']}» завершён в {stopped_at.strftime('%d.%m.%Y %H:%M:%S')}.\n\n"
        f"Всего: {fmt_duration(total_time)}\nРабота: {fmt_duration(work_time)}\nПростои: {fmt_duration(total_downtime)}\n\n"
        f"Остановок: {len(stops)}\nЗапусков: {len(starts)}\nКомментариев: {len(notes)}\nФотографий: {len(photos)}"
    )
    summary += "\n\n📊 Сформировать отчёт: /report"

    await callback.message.edit_text(summary, reply_markup=None)
    final_user = db.get_user(callback.from_user.id)
    await callback.message.answer("Кнопки мониторинга убраны.", reply_markup=get_idle_keyboard(has_active=False, is_admin=(final_user["role"] == "admin" if final_user else False)))

@router.callback_query(F.data == "finish_cancel")
async def cb_finish_cancel(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.edit_text("Завершение отменено.")


# ---------- Обработчики inline-меню сессии ----------

@router.callback_query(F.data == "menu_stop")
async def cb_menu_stop(callback: types.CallbackQuery):
    await callback.answer()
    active = db.get_active_session_for_user(callback.from_user.id)
    if active is None:
        await callback.message.edit_text("Мониторинг уже завершён.", reply_markup=None)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, завершить", callback_data=f"finish_confirm:{active['id']}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="finish_cancel")],
    ])
    await callback.message.edit_text(
        f"Завершить мониторинг «{active['name']}»?\n"
        f"После подтверждения события больше не будут приниматься.",
        reply_markup=kb,
    )


@router.callback_query(F.data == "menu_info")
async def cb_menu_info(callback: types.CallbackQuery):
    await callback.answer()
    active = db.get_active_session_for_user(callback.from_user.id)
    if active is None:
        await callback.message.edit_text("Активного мониторинга нет.", reply_markup=None)
        return
    
    events = db.get_session_events(active["id"])
    intervals = db.compute_intervals(events)
    stops = [e for e in intervals if e["event_type"] == "stop"]
    starts = [e for e in intervals if e["event_type"] == "start"]
    notes = [e for e in intervals if e["event_type"] == "note"]
    photos = [e for e in intervals if e["event_type"] == "photo"]
    no_reason = [e for e in stops if e["reason"] is None]

    now = db.now_seconds()
    total_time = int((now - active["started"]).total_seconds())
    work_time = db.total_work_time(events, active["started"], session_end=now)
    total_downtime = sum(e["stop_duration"] for e in stops if e["stop_duration"] is not None)

    text = (
        f"📊 Мониторинг: «{active['name']}»\n"
        f"Начат: {active['started'].strftime('%d.%m.%Y %H:%M:%S')}\n"
        f"🔑 Код: {active['code']}\n"
        f"Всего идёт: {fmt_duration(total_time)}\n"
        f"Работа: {fmt_duration(work_time)}\n"
        f"Простои (закрытые): {fmt_duration(total_downtime)}\n"
        f"Стопов: {len(stops)}, стартов: {len(starts)}, "
        f"комментариев: {len(notes)}, фото: {len(photos)}"
    )
    if no_reason:
        text += f"\nБез причины: {', '.join(f'#{e['number']}' for e in no_reason)}"
    await callback.message.edit_text(text)


@router.callback_query(F.data == "menu_help")
async def cb_menu_help(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(COMMANDS_HELP)


@router.callback_query(F.data == "menu_no_reason")
async def cb_menu_no_reason(callback: types.CallbackQuery):
    await callback.answer()
    active = db.get_active_session_for_user(callback.from_user.id)
    if active is None:
        await callback.message.edit_text("Активного мониторинга нет.", reply_markup=None)
        return
        
    events = db.get_session_events(active["id"])
    no_reason = [e for e in events if e["event_type"] == "stop" and e["reason"] is None]
    if not no_reason:
        await callback.message.edit_text("✅ Все стопы имеют причину.")
        return
        
    lines = ["❓ Стопы без причины:"]
    for ev in no_reason:
        t = ev["time"].strftime("%H:%M:%S")
        lines.append(f"#{ev['number']} — {t}")
    lines.append("\nЗаполнить: /edit <номер> <причина>")
    await callback.message.edit_text("\n".join(lines))


@router.callback_query(F.data == "menu_code")
async def cb_menu_code(callback: types.CallbackQuery):
    await callback.answer()
    active = db.get_active_session_for_user(callback.from_user.id)
    if active is None:
        await callback.message.edit_text("Активного мониторинга нет.", reply_markup=None)
        return
    await callback.message.answer(
        f"🔑 Код доступа к «{active['name']}»:\n`{active['code']}`\n\n"
        f"Передайте его коллеге, чтобы подключиться.",
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "menu_participants")
async def cb_menu_participants(callback: types.CallbackQuery):
    await callback.answer()
    active = db.get_active_session_for_user(callback.from_user.id)
    if active is None:
        await callback.message.edit_text("Активного мониторинга нет.", reply_markup=None)
        return

    participants = db.get_session_participants(active["id"])
    requests = db.get_pending_requests(active["id"])

    lines = [f"👥 Участники «{active['name']}»:"]
    for p in participants:
        role = "👑 владелец" if p["role"] == "owner" else "— участник"
        lines.append(f"• {p['name']} (id {p['telegram_id']}) — {role}")

    if not requests:
        lines.append("\n📨 Запросов на вступление нет.")
        await callback.message.edit_text("\n".join(lines))
        return

    lines.append("\n📨 Запросы на вступление:")
    buttons = []
    for req in requests:
        lines.append(f"• {req['name']} (id {req['user_id']})")
        buttons.append([
            InlineKeyboardButton(text=f"✅ Одобрить {req['name']}", callback_data=f"req_approve:{req['id']}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"req_reject:{req['id']}"),
        ])
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


# ---------- Обработка заявок на вступление ----------

@router.callback_query(F.data.startswith("req_approve:"))
async def cb_req_approve(callback: types.CallbackQuery):
    await callback.answer()
    req_id = int(callback.data.split(":", 1)[1])
    req = db.get_request(req_id)
    if req is None or req["status"] != "pending":
        await callback.message.edit_text("Запрос уже обработан.")
        return

    session = db.get_session(req["session_id"])
    if session is None or session["stopped"] is not None:
        db.reject_request(req_id)
        await callback.message.edit_text("Сессия завершена — запрос отклонён.")
        try:
            await callback.bot.send_message(
                req["user_id"],
                f"❌ Сессия «{session['name'] if session else '—'}» завершена. "
                f"Вступление невозможно."
            )
        except Exception:
            pass
        return

    db.approve_request(req_id)
    await callback.message.edit_text(f"✅ Запрос от {req['user_id']} одобрен.")
    try:
        await callback.bot.send_message(
            req["user_id"],
            f"✅ Ваш запрос на вступление в «{session['name']}» одобрен. "
            f"Теперь вы можете участвовать в мониторинге."
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("req_reject:"))
async def cb_req_reject(callback: types.CallbackQuery):
    await callback.answer()
    req_id = int(callback.data.split(":", 1)[1])
    req = db.get_request(req_id)
    if req is None or req["status"] != "pending":
        await callback.message.edit_text("Запрос уже обработан.")
        return
    db.reject_request(req_id)
    session = db.get_session(req["session_id"])
    await callback.message.edit_text(f"❌ Запрос от {req['user_id']} отклонён.")
    try:
        await callback.bot.send_message(
            req["user_id"],
            f"❌ Ваш запрос на вступление в «{session['name'] if session else '—'}» отклонён."
        )
    except Exception:
        pass