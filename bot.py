import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

import storage as st
from alerts import notify_owner, on_error, run_watchdog
from chat_tracker import ChatTrackerMiddleware
from chat_tracker import router as chat_tracker_router
from commands import router as commands_router
from config import BOT_TOKEN
from handlers import CleanupMiddleware
from handlers import router as handlers_router
from queue_worker import run_worker
from settings_ui import router as settings_router

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

    dp.update.outer_middleware(ChatTrackerMiddleware())
    dp.message.outer_middleware(CleanupMiddleware())
    dp.errors.register(on_error)

    # Порядок важен: команды и настройки раньше общего слежения за группами
    dp.include_router(chat_tracker_router)
    dp.include_router(commands_router)
    dp.include_router(settings_router)
    dp.include_router(handlers_router)

    me = await bot.get_me()
    logger.info("Cleanny запущен как @%s", me.username)
    await notify_owner(bot, f"✅ Cleanny запущен (@{me.username}). В очереди на удаление: {st.pending_count()}.")

    tasks = [
        asyncio.create_task(run_worker(bot), name="deletion-worker"),
        asyncio.create_task(run_watchdog(bot), name="watchdog"),
    ]
    try:
        await dp.start_polling(bot)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
