from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
import database as db
from .utils import ensure_user

router = Router()

COMMANDS_HELP = (
    "📖 Доступные команды:\n\n"
    "Управление мониторингом:\n"
    "/start_monitoring — начать новый мониторинг\n"
    "/stop_monitoring — завершить текущий мониторинг\n"
    "/status — статус активного мониторинга\n"
    "/sessions — мои сохранённые мониторинги\n"
    "/join <код> — подключиться к мониторингу по коду\n"
    "/code — показать код текущего мониторинга\n\n"
    "Работа с событиями:\n"
    "/list — все события текущей сессии\n"
    "/edit <номер> <причина> — изменить/добавить причину стопа\n\n"
    "Отчёты:\n"
    "/report [id] — сформировать Excel-отчёт (без указания id бот сформирует отчет по последнему мониторингу)\n\n"
    "Прочее:\n"
    "/start — перезапустить бота\n"
    "/help — показать это сообщение"
)

BOT_DOCS = (
    "ℹ️ Что умеет этот бот\n\n"
    "Бот помогает вести мониторинг работы оборудования: фиксирует остановки "
    "и запуски, считает длительности, хранит историю и формирует Excel-отчёты.\n\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "▶️ Как начать\n\n"
    "1. Нажмите «▶️ Начать мониторинг» (или /start_monitoring).\n"
    "2. Введите название — например, «Смена 1».\n"
    "3. Бот зафиксирует старт и выдаст код доступа.\n"
    "4. Если оборудование стоит — сразу нажмите «⏸ Стоп».\n\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "🎛 Кнопки во время мониторинга\n\n"
    "⏸ Стоп — зафиксировать остановку. Время фиксируется в момент нажатия, "
    "причина — следующим сообщением.\n\n"
    "▶️ Старт — зафиксировать возобновление работы. Бот покажет, "
    "сколько длился предыдущий простой.\n\n"
    "📝 Комментарий — заметка по времени. В отчёт как простой не идёт, "
    "но выводится рядом с ближайшим событием.\n\n"
    "📸 Фото — можно отправить фотографию в любой момент. Бот зафиксирует время "
    "и попросит описание. Фото появятся на отдельном листе в отчёте.\n\n"
    "⚙️ Меню — дополнительное меню: завершить мониторинг, информация, "
    "стопы без причины, участники и запросы, показать код, отчёт, команды.\n\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "🔑 Код доступа\n\n"
    "При создании сессии бот выдаёт код вида XXXX-XXXX. Его можно передать "
    "коллеге — он введёт код и подключится. Пока сессия активна, подключение "
    "требует одобрения любого участника. Если сессия завершена — доступ "
    "добавится сразу (только для просмотра и отчёта).\n\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "✍️ Причины и несколько стопов подряд\n\n"
    "После «⏸ Стоп» напишите причину — она привяжется к последнему стопу. "
    "Если нажать «⏸ Стоп» несколько раз подряд, каждый стоп закрывает предыдущий.\n\n"
    "Причины можно изменить: /edit <номер> <новая причина>.\n\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "💬 Произвольный текст\n\n"
    "Если написать боту во время мониторинга, он предложит: зафиксировать стоп "
    "с этим текстом как причиной, записать в примечания или проигнорировать.\n\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "📊 Отчёт\n\n"
    "/report — Excel-отчёт по последней завершённой сессии.\n"
    "/report <id> — по конкретной.\n\n"
    "В отчёте три листа: «Сводка», «События», «Фотографии».\n\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "📖 Команды\n\n"
    "/start_monitoring, /stop_monitoring, /status, /sessions,\n"
    "/join <код>, /code, /list, /edit, /report, /help, /start"
)

def get_idle_keyboard(has_active: bool, is_admin: bool = False):
    rows = [[KeyboardButton(text="▶️ Начать мониторинг")]]
    if not has_active:
        rows.append([KeyboardButton(text="🔑 Подключиться по коду")])
    rows.append([KeyboardButton(text="📂 Сохранённые мониторинги")])
    if is_admin:
        rows.append([KeyboardButton(text="👥 Пользователи")])
        rows.append([KeyboardButton(text="👥 Все мониторинги")])
    rows.append([KeyboardButton(text="ℹ️ Что умеет этот бот")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)

@router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    user = await ensure_user(message)
    if not user: return

    active = db.get_active_session_for_user(message.from_user.id)
    if active:
        from .monitoring import main_keyboard
        await message.answer(f"Идёт мониторинг «{active['name']}».", reply_markup=main_keyboard)
    else:
        await message.answer(
            "Привет! Я бот для мониторинга оборудования.\nСписок команд: /help",
            reply_markup=get_idle_keyboard(has_active=False, is_admin=(user["role"] == "admin")),
        )

@router.message(Command("help"))
async def cmd_help(message: types.Message):
    user = await ensure_user(message)
    if not user: return
    await message.answer(COMMANDS_HELP)

@router.message(F.text == "ℹ️ Что умеет этот бот")
async def btn_about(message: types.Message):
    user = await ensure_user(message)
    if not user: return
    await message.answer(BOT_DOCS)