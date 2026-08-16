import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import BOT_TOKEN, DATABASE_URL
from chat_tracker import ChatTrackerMiddleware
from chat_tracker import router as chat_tracker_router
from commands import router as commands_router
from debug_log import router as debug_router
from handlers import register_handlers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    # Задачи хранятся в БД (Postgres на bothost или локальный SQLite) —
    # переживают перезапуск и передеплой
    jobstores = {"default": SQLAlchemyJobStore(url=DATABASE_URL)}
    scheduler = AsyncIOScheduler(jobstores=jobstores)
    scheduler.start()

    dp.update.outer_middleware(ChatTrackerMiddleware())

    dp.include_router(chat_tracker_router)
    dp.include_router(commands_router)
    dp.include_router(register_handlers(scheduler))
    dp.include_router(debug_router)

    me = await bot.get_me()
    logger.info("Cleanny запущен как @%s", me.username)

    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
