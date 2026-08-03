import logging
from datetime import datetime, timedelta

from aiogram import Router, F
from aiogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import WHITELIST_BOT_IDS
from jobs import delete_message_job
from storage import get_delay, is_chat_whitelisted

logger = logging.getLogger(__name__)


def register_handlers(scheduler: AsyncIOScheduler) -> Router:
    router = Router()

    @router.message(F.from_user.is_bot)
    async def handle_bot_message(message: Message) -> None:
        user_id = message.from_user.id

        # Не трогаем ботов из глобального вайтлиста (.env)
        if user_id in WHITELIST_BOT_IDS:
            return

        # Не трогаем ботов из вайтлиста, настроенного прямо в этом чате
        if is_chat_whitelisted(message.chat.id, user_id):
            return

        # Не трогаем самого себя (Cleanny)
        if message.bot and user_id == message.bot.id:
            return

        delay_seconds = get_delay(message.chat.id)
        run_date = datetime.now() + timedelta(seconds=delay_seconds)
        # id задачи привязан к чату и сообщению — защищает от дублей
        job_id = f"del_{message.chat.id}_{message.message_id}"

        scheduler.add_job(
            delete_message_job,
            "date",
            run_date=run_date,
            args=[message.chat.id, message.message_id],
            id=job_id,
            replace_existing=True,
            # если бот был offline дольше запланированного времени,
            # всё равно выполнить удаление (в пределах часа от плана)
            misfire_grace_time=3600,
        )

        logger.info(
            "Запланировано удаление сообщения %s от бота %s (@%s) на %s",
            message.message_id,
            user_id,
            message.from_user.username,
            run_date,
        )

    return router
