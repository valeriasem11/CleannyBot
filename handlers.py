"""Слежение за группами: ставит в очередь удаление сообщений ботов.

Порядок хендлеров важен: aiogram берёт первый подошедший, поэтому
универсальный «команда к боту» стоит последним.
"""
import logging
import re
from datetime import timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message, User

import storage as st
from config import NIGHT_FAST_DELAY_SECONDS, WHITELIST_BOT_IDS
from utils import GROUP_TYPES, IGNORED_BOT_IDS, check_admin, is_night, reply

logger = logging.getLogger(__name__)

router = Router()
router.message.filter(F.chat.type.in_(GROUP_TYPES))

_seen_cache: dict = {}
_CMD_RE = re.compile(r"^/\w+@(\w+)", re.ASCII)


def _remember(user: User) -> None:
    """Запоминает бота (для поиска по @username), пишет в БД только при изменениях."""
    if _seen_cache.get(user.id, "\0") == user.username:
        return
    st.remember_bot(user.id, user.username)
    _seen_cache[user.id] = user.username


def compute_delay(chat_id: int, bot_id: int | None, username: str | None, opts: dict) -> int | None:
    """Через сколько секунд удалять сообщение этого бота, либо None — не удалять."""
    if bot_id is not None and (bot_id in WHITELIST_BOT_IDS or st.list_has("whitelist", chat_id, bot_id)):
        return None
    if username and st.list_has_username("whitelist", chat_id, username):
        return None

    if opts["mode"] == "only":
        listed = (bot_id is not None and st.list_has("targets", chat_id, bot_id)) or bool(
            username and st.list_has_username("targets", chat_id, username)
        )
        if not listed:
            return None

    if st.get_pause_until(chat_id):
        return None

    night = is_night(opts)
    if night and opts["night_action"] == "skip":
        return None

    delay = st.get_bot_delay(chat_id, bot_id) if bot_id is not None else None
    if delay is None:
        delay = st.get_delay(chat_id)
    if night and opts["night_action"] == "fast":
        delay = min(delay, NIGHT_FAST_DELAY_SECONDS)
    return delay


def _schedule(message: Message, bot_id: int | None, username: str | None, kind: str, opts: dict) -> None:
    chat_id = message.chat.id
    delay = compute_delay(chat_id, bot_id, username, opts)
    if delay is None:
        return
    st.enqueue_deletion(
        chat_id, message.message_id, st.utcnow() + timedelta(seconds=delay), kind, bot_id, username
    )
    logger.info(
        "Запланировано удаление (%s) сообщения %s в чате %s от @%s через %s сек",
        kind, message.message_id, chat_id, username, delay,
    )


async def _maybe_clean_command(message: Message) -> None:
    """Команды людей вида /work@SomeBot: удаляем вместе с ответом бота (если включено)."""
    text = message.text or ""
    m = _CMD_RE.match(text)
    if not m:
        return
    username = m.group(1)
    if not username.lower().endswith("bot"):
        return
    me = await message.bot.me()
    if username.lower() == (me.username or "").lower():
        return
    opts = st.get_options(message.chat.id)
    if not st.option_on(opts, "clean_commands"):
        return
    found = st.find_bot(username)
    _schedule(message, found[0] if found else None, username, "cmd", opts)


@router.message(F.from_user.is_bot)
async def on_bot_message(message: Message) -> None:
    user = message.from_user
    # Анонимные админы и каналы приходят как «боты» — это не боты
    if user.id in IGNORED_BOT_IDS or message.sender_chat is not None:
        await _maybe_clean_command(message)
        return
    if user.id == message.bot.id:
        return
    _remember(user)
    _schedule(message, user.id, user.username, "bot", st.get_options(message.chat.id))


@router.message(F.via_bot)
async def on_via_bot_message(message: Message) -> None:
    via = message.via_bot
    if via.id == message.bot.id:
        return
    _remember(via)
    opts = st.get_options(message.chat.id)
    if not st.option_on(opts, "clean_via"):
        return
    _schedule(message, via.id, via.username, "via", opts)


@router.message(Command("keep"))
async def cmd_keep(message: Message) -> None:
    """Ответом на сообщение бота: отменить его удаление."""
    if not await check_admin(message):
        return
    target = message.reply_to_message
    if target is None:
        await reply(message, "Ответьте командой /keep на сообщение, которое нужно оставить.")
        return
    if st.cancel_deletion(message.chat.id, target.message_id):
        await reply(message, "✅ Это сообщение останется в чате — удаление отменено.")
    else:
        await reply(
            message,
            "Для этого сообщения нет запланированного удаления "
            "(оно уже удалено, не от бота или бот в вайтлисте).",
        )


@router.message(F.text.startswith("/"))
async def on_possible_bot_command(message: Message) -> None:
    await _maybe_clean_command(message)
