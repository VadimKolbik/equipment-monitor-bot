import asyncio
import os
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
import database as db

# Импортируем наши роутеры
from handlers import common, users, sessions, monitoring, reports

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Подключаем роутеры (порядок имеет значение: common ловит общее, monitoring — текст в конце)
dp.include_router(common.router)
dp.include_router(users.router)
dp.include_router(sessions.router)
dp.include_router(reports.router)
dp.include_router(monitoring.router) 

async def main():
    db.init_db()
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())