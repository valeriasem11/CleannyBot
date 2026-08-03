import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from config import BOT_TOKEN

logger = logging.getLogger(__name__)


async def delete_message_job(chat_id: int, message_id: int) -> None:
    """
    Вызывается планировщиком (APScheduler) в собственном контексте,
    поэтому создаёт свой экземпляр Bot, а не переиспользует объект
    из основного процесса polling'а.
    """
    bot = Bot(token=BOT_TOKEN)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.info("Удалено сообщение %s в чате %s", message_id, chat_id)
    except TelegramBadRequest as e:
        # Сообщение уже удалено вручную, слишком старое (>48ч) и т.п.
        logger.warning(
            "Не удалось удалить сообщение %s в чате %s: %s", message_id, chat_id, e
        )
    except TelegramForbiddenError as e:
        # Бот больше не админ / его кикнули из чата
        logger.warning(
            "Нет прав на удаление сообщения %s в чате %s: %s", message_id, chat_id, e
        )
    finally:
        await bot.session.close()
