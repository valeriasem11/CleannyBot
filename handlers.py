"""Слежение за группами: сообщения ботов, команды людей, фразы.

Две части:
* CleanupMiddleware видит КАЖДОЕ сообщение до хендлеров и ставит в очередь
  удаление по фразам и по правилам /cleancmds (не мешая остальной обработке);
* хендлеры ниже обрабатывают сообщения ботов, via-bot и команду /keep.
"""
import logging
from datetime import datetime, timedelta

from aiogram import BaseMiddleware, F, Router
from aiogram.filters import Command
from aiogram.types import Message, User

import pairing
import storage as st
from config import NIGHT_FAST_DELAY_SECONDS, WHITELIST_BOT_IDS
from utils import (
    GROUP_TYPES,
    IGNORED_BOT_IDS,
    OWN_COMMANDS,
    check_admin,
    cmd_mode,
    is_night,
    parse_command,
    reply,
)

logger = logging.getLogger(__name__)

router = Router()
router.message.filter(F.chat.type.in_(GROUP_TYPES))

_seen_cache: dict = {}


def _remember(user: User) -> None:
    """Запоминает бота (для поиска по @username), в БД пишет только при изменениях."""
    if _seen_cache.get(user.id, "\0") == user.username:
        return
    st.remember_bot(user.id, user.username)
    _seen_cache[user.id] = user.username


# ---------- Сколько ждать до удаления ----------

def base_delay(chat_id: int, opts: dict, bot_id: int | None = None) -> int | None:
    """Задержка с учётом паузы, ночного режима и своей задержки бота. None — не удалять."""
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


def compute_delay(chat_id: int, bot_id: int | None, username: str | None, opts: dict) -> int | None:
    """Для сообщений ботов: то же, что base_delay, плюс вайтлист и режим only."""
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

    return base_delay(chat_id, opts, bot_id)


def _enqueue(message: Message, delay: int, kind: str, bot_id: int | None, username: str | None) -> datetime:
    delete_at = st.utcnow() + timedelta(seconds=delay)
    st.enqueue_deletion(message.chat.id, message.message_id, delete_at, kind, bot_id, username)
    return delete_at


def _schedule(message: Message, bot_id: int | None, username: str | None, kind: str, opts: dict) -> datetime | None:
    delay = compute_delay(message.chat.id, bot_id, username, opts)
    if delay is None:
        return None
    delete_at = _enqueue(message, delay, kind, bot_id, username)
    logger.info(
        "Запланировано удаление (%s) сообщения %s в чате %s от @%s через %s сек",
        kind, message.message_id, message.chat.id, username, delay,
    )
    return delete_at


# ---------- Middleware: фразы и команды людей ----------

def _is_human_sender(message: Message) -> bool:
    user = message.from_user
    return user is None or not user.is_bot or user.id in IGNORED_BOT_IDS or message.sender_chat is not None


class CleanupMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: Message, data: dict):
        try:
            await self._process(event)
        except Exception:
            logger.exception("Ошибка при проверке фраз/команд")
        return await handler(event, data)

    async def _process(self, message: Message) -> None:
        if message.chat.type not in GROUP_TYPES:
            return
        me = await message.bot.me()
        if message.from_user and message.from_user.id == me.id:
            return

        chat_id = message.chat.id
        text = message.text or message.caption or ""
        opts = st.get_options(chat_id)

        # 1. Фразы: любые сообщения, содержащие текст из /phrases
        phrase = st.phrase_match(chat_id, text)
        if phrase is not None:
            delay = base_delay(chat_id, opts)
            if delay is not None:
                _enqueue(message, delay, "phrase", None, None)
                logger.info("Сообщение %s в чате %s содержит фразу %r — удаление через %s сек",
                            message.message_id, chat_id, phrase, delay)
            return

        # 2. Команды людей
        mode = cmd_mode(opts)
        if mode == "0" or not _is_human_sender(message):
            return
        parsed = parse_command(text)
        if parsed is None:
            return
        name, target = parsed

        if target:  # /команда@бот — адресат известен
            if target.lower() == (me.username or "").lower() or not target.lower().endswith("bot"):
                return
            found = st.find_bot(target)
            _schedule(message, found[0] if found else None, target, "cmd", opts)
            return

        if name in OWN_COMMANDS:
            return
        if mode == "all":
            delay = base_delay(chat_id, opts)
            if delay is not None:
                _enqueue(message, delay, "cmd", None, None)
        elif mode == "smart":
            pairing.record(chat_id, message.message_id, name)


# ---------- Хендлеры ----------

def _real_reply(message: Message):
    """Сообщение, на которое ответили. В топиках корень темы — не настоящий ответ."""
    rep = message.reply_to_message
    if rep is not None and getattr(rep, "forum_topic_created", None) is not None:
        return None
    return rep


@router.message(F.from_user.is_bot)
async def on_bot_message(message: Message) -> None:
    user = message.from_user
    # Анонимные админы и каналы приходят как «боты» — это не боты
    if user.id in IGNORED_BOT_IDS or message.sender_chat is not None:
        return
    if user.id == message.bot.id:
        return
    _remember(user)
    opts = st.get_options(message.chat.id)
    delete_at = _schedule(message, user.id, user.username, "bot", opts)

    # Умный режим: команда, на которую ответил этот бот, удаляется вместе с ответом.
    # Забираем её в любом случае: если бот в вайтлисте или идёт пауза, команда
    # просто остаётся, но не достаётся другому боту.
    if cmd_mode(opts) == "smart":
        command_id = pairing.claim(message.chat.id, _real_reply(message))
        if command_id is not None and delete_at is not None:
            st.enqueue_deletion(message.chat.id, command_id, delete_at, "cmd", user.id, user.username)
            logger.info("Команда %s в чате %s связана с ответом @%s и будет удалена вместе с ним",
                        command_id, message.chat.id, user.username)


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
