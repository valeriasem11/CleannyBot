import logging
import time
from datetime import timedelta
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Router
from aiogram.types import Chat, ChatMemberUpdated, TelegramObject, Update

import storage as st
from alerts import notify_owner
from config import DEFAULT_UTC_OFFSET_HOURS
from utils import GROUP_TYPES

import html

logger = logging.getLogger(__name__)
router = Router()

TIME_FORMAT = "%d.%m.%Y %H:%M"
_PASSIVE_WRITE_EVERY = 300  # не чаще раза в 5 минут на чат
_last_passive: dict = {}


def _stamp() -> str:
    return (st.utcnow() + timedelta(hours=DEFAULT_UTC_OFFSET_HOURS)).strftime(TIME_FORMAT)


def _chat_title(chat: Chat) -> str:
    return chat.title or chat.full_name or (f"@{chat.username}" if chat.username else str(chat.id))


def _can_delete(member) -> bool:
    if member.status == "creator":
        return True
    return member.status == "administrator" and bool(getattr(member, "can_delete_messages", False))


@router.my_chat_member()
async def on_my_chat_member(event: ChatMemberUpdated) -> None:
    """Статус бота в чате изменился: добавили, повысили, лишили прав, удалили."""
    if event.chat.type not in GROUP_TYPES:
        return

    new, old = event.new_chat_member, event.old_chat_member
    title = _chat_title(event.chat)
    st.upsert_known_chat_status(event.chat.id, title, new.status, _stamp())
    logger.info("Статус бота в чате %s: %s -> %s", event.chat.id, old.status, new.status)

    safe_title = html.escape(title)
    bot = event.bot

    if new.status in ("left", "kicked"):
        dropped = st.drop_chat_pending(event.chat.id)
        await notify_owner(
            bot,
            f"🚪 Cleanny больше не в чате «{safe_title}» (<code>{event.chat.id}</code>): {new.status}."
            + (f" Очередь удалений этого чата очищена ({dropped})." if dropped else ""),
        )
        return

    now_ok, was_ok = _can_delete(new), _can_delete(old)

    if not now_ok:
        just_added = old.status in ("left", "kicked")
        if just_added:
            chat_text = (
                "👋 Привет! Чтобы я начал убирать сообщения ботов, сделайте меня администратором "
                "с правом «Удаление сообщений» (настройки группы → Администраторы)."
            )
            owner_text = f"➕ Cleanny добавлен в «{safe_title}» без права удалять сообщения."
        else:
            chat_text = (
                "⚠️ У меня больше нет права «Удаление сообщений», поэтому сообщения ботов "
                "не удаляются. Верните его: настройки группы → Администраторы → Cleanny."
            )
            owner_text = f"⚠️ В «{safe_title}» (<code>{event.chat.id}</code>) Cleanny потерял право удалять сообщения."
        try:
            await bot.send_message(event.chat.id, chat_text)
        except Exception as exc:
            logger.warning("Не смог написать в чат %s: %s", event.chat.id, exc)
        await notify_owner(bot, owner_text)
    elif not was_ok:
        try:
            await bot.send_message(event.chat.id, "✅ Теперь я могу удалять сообщения. Настройки — /settings.")
        except Exception as exc:
            logger.warning("Не смог написать в чат %s: %s", event.chat.id, exc)


class ChatTrackerMiddleware(BaseMiddleware):
    """Пассивно запоминает чаты по любому входящему апдейту (не блокирует хендлеры)."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict], Awaitable[Any]],
        event: TelegramObject,
        data: dict,
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
            return  # точный статус запишет on_my_chat_member

        chat = None
        if update.message:
            chat = update.message.chat
        elif update.edited_message:
            chat = update.edited_message.chat
        elif update.callback_query and update.callback_query.message:
            chat = update.callback_query.message.chat

        if chat is None or chat.type not in GROUP_TYPES:
            return

        now = time.monotonic()
        if now - _last_passive.get(chat.id, float("-inf")) < _PASSIVE_WRITE_EVERY:
            return
        _last_passive[chat.id] = now
        st.upsert_known_chat_passive(chat.id, _chat_title(chat), _stamp())
