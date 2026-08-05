import logging
from datetime import datetime
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Router
from aiogram.types import Chat, ChatMemberUpdated, TelegramObject, Update

from storage import upsert_known_chat_passive, upsert_known_chat_status

logger = logging.getLogger(__name__)
router = Router()

TIME_FORMAT = "%d.%m.%Y %H:%M"


def _chat_title(chat: Chat) -> str:
    return chat.title or chat.full_name or (f"@{chat.username}" if chat.username else str(chat.id))


@router.my_chat_member()
async def on_my_chat_member(event: ChatMemberUpdated) -> None:
    """
    Достоверное событие: статус бота в чате изменился
    (добавили, повысили до админа, удалили и т.п.)
    """
    status = event.new_chat_member.status
    upsert_known_chat_status(
        chat_id=event.chat.id,
        title=_chat_title(event.chat),
        status=status,
        updated_at=datetime.now().strftime(TIME_FORMAT),
    )
    logger.info("Статус бота в чате %s изменён на: %s", event.chat.id, status)


class ChatTrackerMiddleware(BaseMiddleware):
    """
    Пассивно фиксирует чат при любом входящем апдейте (сообщение,
    редактирование, callback и т.п.), не мешая обработке хендлерами —
    это middleware уровня Update, а не отдельный хендлер, поэтому
    не "съедает" апдейт и не блокирует остальные роутеры.
    Так в базу попадают и те группы, где бот уже был до появления
    этой функции — не только новые события my_chat_member.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Update):
            try:
                self._track(event)
            except Exception:
                logger.exception("Не удалось обновить список известных чатов")
        return await handler(event, data)

    @staticmethod
    def _track(update: Update) -> None:
        if update.my_chat_member:
            # Это событие уже обработает on_my_chat_member — пропускаем,
            # чтобы не дублировать запись с менее точным статусом.
            return

        chat: Chat | None = None
        if update.message:
            chat = update.message.chat
        elif update.edited_message:
            chat = update.edited_message.chat
        elif update.callback_query and update.callback_query.message:
            chat = update.callback_query.message.chat

        if chat is None or chat.type == "private":
            return

        upsert_known_chat_passive(
            chat_id=chat.id,
            title=_chat_title(chat),
            updated_at=datetime.now().strftime(TIME_FORMAT),
        )
