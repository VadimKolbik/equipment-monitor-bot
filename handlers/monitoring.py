import os
import uuid
from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
import database as db
from .utils import ensure_user, fmt_duration, calc_work_before

router = Router()

# Глобальные словари для состояний (лучше вынести в отдельный файл, если проект будет расти)
pending_decisions: dict[str, dict] = {}
pending_photo_descr: dict[int, dict] = {}

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="⏸ Стоп"), KeyboardButton(text="▶️ Старт")],
        [KeyboardButton(text="📝 Комментарий"), KeyboardButton(text="⚙️ Меню")],
    ],
    resize_keyboard=True,
)

def parse_message(text: str) -> dict | None:
    low = text.lower().strip()
    stop_words = ["остановка", "стоп", "простой", "останов"]
    start_words = ["запуск", "старт", "запуст"]
    if any(w in low for w in stop_words):
        return {"event": "stop", "reason": extract_reason(text, stop_words)}
    if any(w in low for w in start_words):
        return {"event": "start", "reason": None}
    return None

def extract_reason(original: str, keywords: list[str]) -> str | None:
    if "," in original:
        part = original.split(",", 1)[1].strip()
        if part: return part
    low = original.lower()
    for kw in keywords:
        idx = low.find(kw)
        if idx != -1:
            part = original[idx + len(kw):].strip(" .,:;-—")
            if part: return part
    return None

@router.message(F.text == "⏸ Стоп")
async def btn_stop(message: types.Message):
    user = await ensure_user(message)
    if not user: return
    active = db.get_active_session_for_user(message.from_user.id)
    if not active:
        await message.answer("Сначала начните мониторинг.")
        return

    now = db.now_seconds()
    events = db.get_session_events(active["id"])
    intervals = db.compute_intervals(events)
    work_before = calc_work_before(intervals, now, active["started"])

    prev_open_stop = None
    for ev in reversed(intervals):
        if ev["event_type"] in ("start", "stop"):
            if ev["event_type"] == "stop": prev_open_stop = ev
            break

    number = db.next_stop_number(active["id"])
    db.add_event(active["id"], "stop", number=number)

    text = f"⏸ Стоп #{number} зафиксирован в {now.strftime('%H:%M:%S')}.\n⌛ Оборудование работало: {fmt_duration(work_before)}\n"
    if prev_open_stop:
        prev_dur = int((now - prev_open_stop["time"]).total_seconds())
        text += f"⏱ Стоп #{prev_open_stop['number']} длился: {fmt_duration(prev_dur)}\n"
    text += "\nНапишите причину."
    await message.answer(text)

@router.message(F.text == "▶️ Старт")
async def btn_start(message: types.Message):
    user = await ensure_user(message)
    if not user: return
    active = db.get_active_session_for_user(message.from_user.id)
    if not active:
        await message.answer("Сначала начните мониторинг.")
        return

    now = db.now_seconds()
    events_before = db.get_session_events(active["id"])
    intervals_before = db.compute_intervals(events_before)
    prev_stop = None
    for ev in reversed(intervals_before):
        if ev["event_type"] in ("start", "stop"):
            if ev["event_type"] == "stop": prev_stop = ev
            break

    event_id = db.add_event(active["id"], "start")
    if event_id is None:
        last = db.last_event(active["id"])
        await message.answer(f"⚠️ Оборудование уже работает (старт был в {last['time'].strftime('%H:%M:%S')}).")
        return

    if prev_stop:
        dur = int((now - prev_stop["time"]).total_seconds())
        await message.answer(f"▶️ Старт зафиксирован в {now.strftime('%H:%M:%S')}.\n⏱ Стоп #{prev_stop['number']} длился: {fmt_duration(dur)}")
    else:
        await message.answer(f"▶️ Старт зафиксирован в {now.strftime('%H:%M:%S')}.\n(Предыдущей остановки не было)")

@router.message(F.text == "📝 Комментарий")
async def btn_note(message: types.Message, state: FSMContext):
    user = await ensure_user(message)
    if not user: return
    active = db.get_active_session_for_user(message.from_user.id)
    if not active:
        await message.answer("Сначала начните мониторинг.")
        return
    await state.set_state("waiting_note") # Используем строковое состояние для простоты
    awaiting = db.find_awaiting_stop(active["id"])
    hint = f"\n\n⚠️ После комментария останется причина для #{awaiting['number']}." if awaiting else ""
    await message.answer("Напишите текст комментария." + hint)

@router.message(F.photo)
async def handle_photo(message: types.Message):
    user = await ensure_user(message)
    if not user: return
    active = db.get_active_session_for_user(message.from_user.id)
    if not active:
        await message.answer("Фото можно отправлять только во время активного мониторинга.")
        return

    if db.find_awaiting_stop(active["id"]):
        await message.answer("⚠️ Сначала напишите причину для предыдущего стопа.")
        return
    if message.from_user.id in pending_photo_descr:
        await message.answer("⚠️ Сначала напишите описание (или /skip) для предыдущего фото.")
        return
    if db.count_photos(active["id"]) >= 10:
        await message.answer("⚠️ Достигнут лимит фотографий (10 на сессию).")
        return

    photo = message.photo[-1]
    photo_dir = os.path.join(db.PHOTOS_DIR, str(active["id"]))
    os.makedirs(photo_dir, exist_ok=True)
    file_path = os.path.join(photo_dir, f"{uuid.uuid4().hex}.jpg")

    try:
        file = await message.bot.get_file(photo.file_id)
        tmp_path = file_path + ".tmp"
        await message.bot.download_file(file.file_path, destination=tmp_path)
        from PIL import Image as PILImage
        with PILImage.open(tmp_path) as im:
            im.convert("RGB").save(file_path, "JPEG", quality=85)
        os.remove(tmp_path)
    except Exception as e:
        await message.answer(f"⚠️ Не удалось скачать фото: {e}")
        return

    event_time = message.date.astimezone().replace(tzinfo=None, microsecond=0)
    event_id = db.add_event(active["id"], "photo", text=None, file_path=file_path.replace(os.sep, "/"), time=event_time)

    pending_photo_descr[message.from_user.id] = {"event_id": event_id, "session_id": active["id"]}
    await message.answer(f"📸 Фото №{db.count_photos(active['id'])} сохранено.\nНапишите описание (или /skip).")

@router.message(F.text)
async def handle_text(message: types.Message, state: FSMContext):
    user = await ensure_user(message)
    if not user: return
    
    current_state = await state.get_state()

    if message.from_user.id in pending_photo_descr:
        data = pending_photo_descr.pop(message.from_user.id)
        db.set_event_text(data["event_id"], message.text.strip())
        await state.clear()
        await message.answer(f"📸 Описание сохранено: {message.text.strip()}")
        return

    if current_state == "waiting_note":
        active = db.get_active_session_for_user(message.from_user.id)
        if active:
            db.add_event(active["id"], "note", text=message.text.strip())
            await message.answer("📝 Комментарий записан.")
        await state.clear()
        return

    active = db.get_active_session_for_user(message.from_user.id)
    if not active:
        await message.answer("Начните мониторинг.")
        return

    awaiting = db.find_awaiting_stop(active["id"])
    if awaiting:
        db.set_reason(awaiting["id"], message.text.strip())
        await message.answer(f"Причина для #{awaiting['number']} записана.")
        return

    parsed = parse_message(message.text)
    if parsed:
        now = db.now_seconds()
        if parsed["event"] == "stop":
            number = db.next_stop_number(active["id"])
            db.add_event(active["id"], "stop", number=number, reason=parsed["reason"])
            await message.answer(f"⏸ Стоп #{number} записан. Причина: {parsed['reason'] or '—'}")
        else:
            if db.add_event(active["id"], "start"):
                await message.answer(f"▶️ Старт записан.")
        return

    # Если ничего не распознали, предлагаем выбор
    key = uuid.uuid4().hex[:12]
    pending_decisions[key] = {"text": message.text.strip(), "session_id": active["id"]}
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏸ В стоп", callback_data=f"decide:stop:{key}")],
        [InlineKeyboardButton(text="📝 В примечание", callback_data=f"decide:note:{key}")],
        [InlineKeyboardButton(text="🚫 Игнорировать", callback_data=f"decide:ignore:{key}")]
    ])
    await message.answer(f"Вы написали: «{message.text.strip()}». Что сделать?", reply_markup=kb)

# Обработчик для решений из текста
@router.callback_query(F.data.startswith("decide:"))
async def cb_decide(callback: types.CallbackQuery):
    await callback.answer()
    _, action, key = callback.data.split(":", 2)
    data = pending_decisions.pop(key, None)
    if not data:
        await callback.message.edit_text("Решение устарело.")
        return
    
    active = db.get_active_session_for_user(callback.from_user.id)
    if not active or active["id"] != data["session_id"]:
        await callback.message.edit_text("Мониторинг завершён.")
        return

    if action == "stop":
        number = db.next_stop_number(active["id"])
        db.add_event(active["id"], "stop", number=number, reason=data["text"])
        await callback.message.edit_text(f"⏸ Стоп #{number} с причиной: {data['text']}")
    elif action == "note":
        db.add_event(active["id"], "note", text=data["text"])
        await callback.message.edit_text(f"📝 Записано в примечания.")
    else:
        await callback.message.edit_text("🚫 Пропущено.")