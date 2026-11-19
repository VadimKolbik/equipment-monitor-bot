from aiogram import Router, F, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import database as db
from .utils import ensure_user

router = Router()

@router.callback_query(F.data == "req_access")
async def cb_request_access(callback: types.CallbackQuery):
    await callback.answer()
    user = db.get_user(callback.from_user.id)
    if not user:
        return

    if user["status"] == "approved":
        await callback.message.edit_text("✅ У вас уже есть доступ. Напишите /start.")
        return

    await callback.message.edit_text("✅ Заявка отправлена. Ожидайте одобрения администратора.")

    admins = db.get_admins()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"user_approve:{user['telegram_id']}"),
            InlineKeyboardButton(text="❌ Отклонить (блок)", callback_data=f"user_block:{user['telegram_id']}")
        ]
    ])
    text = (
        f"🔔 **Новая заявка на доступ**\n\n"
        f"Имя: {user['name']}\n"
        f"ID: `{user['telegram_id']}`"
    )

    for admin_id in admins:
        try:
            await callback.bot.send_message(admin_id, text, reply_markup=kb, parse_mode="Markdown")
        except Exception:
            pass

@router.message(F.text == "👥 Пользователи")
async def cmd_users_list(message: types.Message):
    user = await ensure_user(message)
    if not user or user["role"] != "admin":
        return

    users = db.get_all_users_sorted()
    
    text = "👥 **Пользователи бота:**\n\n"
    buttons = []

    for u in users:
        last_seen = "Никогда"
        if u["last_seen"]:
            last_seen = u["last_seen"].strftime("%d.%m.%Y %H:%M")

        if u["status"] == "approved":
            icon = "🟢"
        elif u["status"] == "pending":
            icon = "🟡"
        else:
            icon = "🔴"

        role_mark = " 👑" if u["role"] == "admin" else ""
        
        text += f"{icon} {u['name']}{role_mark} (`{u['telegram_id']}`)\n"
        text += f"    └ Последний раз: {last_seen}\n"

        if u["status"] == "pending":
            buttons.append([InlineKeyboardButton(text=f"✅ Одобрить {u['name']}", callback_data=f"user_approve:{u['telegram_id']}")])
        elif u["status"] == "approved" and u["role"] != "admin":
            buttons.append([InlineKeyboardButton(text=f"🚫 Заблокировать {u['name']}", callback_data=f"user_block:{u['telegram_id']}")])
        elif u["status"] == "blocked":
            buttons.append([InlineKeyboardButton(text=f"🔄 Разблокировать {u['name']}", callback_data=f"user_approve:{u['telegram_id']}")])

    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="Markdown")

@router.callback_query(F.data.startswith("user_approve:"))
async def cb_user_approve(callback: types.CallbackQuery):
    uid = int(callback.data.split(":")[1])
    db.set_user_status(uid, "approved")
    await callback.message.edit_text("✅ Пользователь одобрен.")
    try:
        await callback.bot.send_message(uid, "🎉 Ваш доступ к боту одобрен! Напишите /start для начала работы.")
    except Exception:
        pass

@router.callback_query(F.data.startswith("user_block:"))
async def cb_user_block(callback: types.CallbackQuery):
    uid = int(callback.data.split(":")[1])
    db.set_user_status(uid, "blocked")
    await callback.message.edit_text("🚫 Пользователь заблокирован.")
    try:
        await callback.bot.send_message(uid, "⛔ Ваш доступ к боту приостановлен администратором.")
    except Exception:
        pass