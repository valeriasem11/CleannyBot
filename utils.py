"""Общие помощники: проверка админов, поиск бота, время, ответы с авто-уборкой."""
import re
from datetime import datetime, timedelta

from aiogram.types import Message

import storage as st
from config import OWNER_USER_ID, TIDY_DELAY_SECONDS

GROUP_TYPES = ("group", "supergroup")

# Служебные «боты» Telegram: сообщения от имени анонимного админа и каналов.
# У них is_bot=True, но это не настоящие боты — трогать их нельзя.
ANON_ADMIN_ID = 1087968824
IGNORED_BOT_IDS = {ANON_ADMIN_ID, 136817688, 777000}


# ---------- Админы ----------

def is_anonymous_admin(message: Message) -> bool:
    if message.sender_chat is not None and message.sender_chat.id == message.chat.id:
        return True
    return bool(message.from_user and message.from_user.id == ANON_ADMIN_ID)


async def is_admin_user(bot, chat_id: int, user_id: int) -> bool:
    if user_id == OWNER_USER_ID:
        return True
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except Exception:
        return False
    return member.status in ("administrator", "creator")


async def check_admin(message: Message) -> bool:
    """True, если команду вызвал админ группы. Иначе отвечает и возвращает False."""
    if message.chat.type not in GROUP_TYPES:
        await message.answer("Эта команда работает только в группах.")
        return False
    if is_anonymous_admin(message):
        return True
    if message.from_user and await is_admin_user(message.bot, message.chat.id, message.from_user.id):
        return True
    await reply(message, "Эта команда доступна только администраторам группы.")
    return False


# ---------- Ответы ----------

async def reply(message: Message, text: str, *, tidy: bool = True, **kwargs) -> Message:
    """Отвечает в чат. В группах при включённом /tidy убирает и ответ, и команду."""
    sent = await message.answer(text, **kwargs)
    if tidy and message.chat.type in GROUP_TYPES:
        opts = st.get_options(message.chat.id)
        if st.option_on(opts, "tidy"):
            due = st.utcnow() + timedelta(seconds=TIDY_DELAY_SECONDS)
            st.enqueue_deletion(message.chat.id, sent.message_id, due, "tidy", None, None)
            st.enqueue_deletion(message.chat.id, message.message_id, due, "tidy", None, None)
    return sent


# ---------- Поиск бота по ответу / @username / ID ----------

async def resolve_bot(message: Message, token: str | None):
    """Возвращает (bot_id, username) или None."""
    me = await message.bot.me()
    rep = message.reply_to_message
    if rep is not None:
        candidates = [rep.from_user, rep.via_bot]
        for user in candidates:
            if user and user.is_bot and user.id not in IGNORED_BOT_IDS and user.id != me.id:
                return user.id, user.username
    if not token:
        return None
    token = token.strip().lstrip("@")
    if token.isdigit():
        return int(token), None
    found = st.find_bot(token)
    if found:
        return found
    if not token.lower().endswith("bot"):
        return None
    try:
        chat = await message.bot.get_chat(f"@{token}")
    except Exception:
        return None
    if chat.type == "private":
        return chat.id, chat.username or token
    return None


UNKNOWN_BOT_TEXT = (
    "Не знаю такого бота: он ещё не писал в чатах, где я есть. "
    "Ответьте командой на любое его сообщение — так я его опознаю."
)


# ---------- Время ----------

_DUR_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*([a-zа-яё]*)\s*$", re.IGNORECASE)


def parse_duration(text: str) -> int | None:
    """'30s', '10m', '2h', '1d', '5' (минуты), '10 минут', '2 часа' -> секунды."""
    m = _DUR_RE.match(text or "")
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    unit = m.group(2).lower()
    if unit == "":
        mult = 60
    elif unit[0] in ("s", "с"):
        mult = 1
    elif unit[0] in ("m", "м"):
        mult = 60
    elif unit[0] in ("h", "ч"):
        mult = 3600
    elif unit[0] in ("d", "д"):
        mult = 86400
    else:
        return None
    return int(value * mult)


def format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} сек"
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days:
        parts.append(f"{days} дн")
    if hours:
        parts.append(f"{hours} ч")
    if minutes or not parts:
        parts.append(f"{minutes} мин")
    return " ".join(parts)


def offset_label(offset_min: int) -> str:
    sign = "+" if offset_min >= 0 else "-"
    h, m = divmod(abs(offset_min), 60)
    return f"UTC{sign}{h}" + (f":{m:02d}" if m else "")


def fmt_local(dt_utc: datetime, offset_min: int) -> str:
    return (dt_utc + timedelta(minutes=offset_min)).strftime("%d.%m %H:%M")


_TZ_RE = re.compile(r"^(?:utc|gmt)?\s*([+-]?)\s*(\d{1,2})(?::(\d{2}))?$", re.IGNORECASE)


def parse_utc_offset(text: str) -> int | None:
    """'+5', '5', '-3', 'UTC+5:30' -> минуты от UTC (или None)."""
    m = _TZ_RE.match((text or "").strip())
    if not m:
        return None
    minutes = int(m.group(2)) * 60 + int(m.group(3) or 0)
    if m.group(1) == "-":
        minutes = -minutes
    if not -12 * 60 <= minutes <= 14 * 60:
        return None
    return minutes


def parse_hm(value: str) -> int | None:
    try:
        h, m = value.split(":")
        h, m = int(h), int(m)
    except ValueError:
        return None
    if 0 <= h <= 23 and 0 <= m <= 59:
        return h * 60 + m
    return None


def is_night(opts: dict, now_utc: datetime | None = None) -> bool:
    start, end = parse_hm(opts.get("night_start", "")), parse_hm(opts.get("night_end", ""))
    if start is None or end is None or start == end:
        return False
    local = (now_utc or st.utcnow()) + timedelta(minutes=int(opts["utc_offset"]))
    cur = local.hour * 60 + local.minute
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end
