import logging

from aiogram import Router
from aiogram.types import Message

logger = logging.getLogger(__name__)
router = Router()


@router.message()
async def log_unhandled(message: Message) -> None:
    """
    Ловит любые сообщения, которые не подошли ни под один из
    предыдущих обработчиков (команды, сообщения от ботов).
    Нужен только для диагностики — помогает понять, почему
    Cleanny не отреагировал на конкретное сообщение.
    """
    user = message.from_user
    logger.info(
        "DEBUG необработанное сообщение: chat_id=%s message_id=%s "
        "from_id=%s username=%s is_bot=%s text=%r",
        message.chat.id,
        message.message_id,
        user.id if user else None,
        user.username if user else None,
        user.is_bot if user else None,
        message.text,
    )
