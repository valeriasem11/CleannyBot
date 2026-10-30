"""Меню /settings на inline-кнопках."""
import html
import logging
from datetime import timedelta

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import storage as st
import utils
from utils import (
    CMD_MODE_LABELS,
    CMD_MODE_ORDER,
    check_admin,
    cmd_mode,
    fmt_local,
    format_duration,
    offset_label,
)

logger = logging.getLogger(__name__)
router = Router()

DELAY_PRESETS = ((60, "1 мин"), (300, "5 мин"), (900, "15 мин"), (3600, "1 ч"))
PAUSE_PRESETS = ((10, "10 мин"), (60, "1 ч"), (480, "8 ч"))
TOGGLES = {
    "clean_via": "📎 Сообщения через inline",
    "tidy": "🧽 Убирать мои ответы",
}


class St(CallbackData, prefix="st"):
    action: str
    value: str = ""


def _btn(text: str, action: str, value: str = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=St(action=action, value=value).pack())


def _names(entries: list, limit: int = 8) -> str:
    shown = [f"@{u}" if u else f"ID {b}" for b, u in entries[:limit]]
    extra = f" и ещё {len(entries) - limit}" if len(entries) > limit else ""
    return html.escape(", ".join(shown)) + extra


def render(chat_id: int):
    opts = st.get_options(chat_id)
    delay = st.get_delay(chat_id)
    offset = int(opts["utc_offset"])
    pause = st.get_pause_until(chat_id)
    onoff = lambda key: "вкл" if st.option_on(opts, key) else "выкл"  # noqa: E731

    lines = [
        "<b>⚙️ Настройки Cleanny</b>",
        "",
        f"⏱ Задержка удаления: <b>{format_duration(delay)}</b>",
        "🎯 Режим: " + ("чистить всех, кроме вайтлиста" if opts["mode"] == "all" else "только боты из /targets"),
        f"⏸ Пауза: до {fmt_local(pause, offset)}" if pause else "⏸ Пауза: нет",
    ]
    if opts["night_start"] and opts["night_end"]:
        lines.append(f"🌙 Ночью {opts['night_start']}–{opts['night_end']}: {'не удаляю' if opts['night_action'] == 'skip' else 'удаляю быстро'}")
    cmd_label = CMD_MODE_LABELS[cmd_mode(opts)]
    lines.append(f"🧹 Удаление команд: {cmd_label}")
    for key, title in TOGGLES.items():
        lines.append(f"{title}: {onoff(key)}")
    lines.append(f"🕒 Часовой пояс: {offset_label(offset)}")
    phrases = st.phrase_list(chat_id)
    if phrases:
        lines.append(f"🔤 Фразы для удаления: {len(phrases)} (/phrases list)")

    whitelist = st.list_get("whitelist", chat_id)
    targets = st.list_get("targets", chat_id)
    delays = st.list_bot_delays(chat_id)
    if whitelist:
        lines.append(f"🚫 Вайтлист: {_names(whitelist)}")
    if targets:
        lines.append(f"🎯 Цели: {_names(targets)}")
    if delays:
        lines.append("⏱ Отдельные задержки: " + html.escape(", ".join(f"@{u or b} — {format_duration(s)}" for b, u, s in delays[:8])))
    lines += ["", "<i>Точные значения: /setdelay, /botdelay, /night, /timezone. Вайтлист: /whitelist.</i>"]

    rows = [
        [_btn(("✅ " if delay == sec else "") + label, "delay", str(sec)) for sec, label in DELAY_PRESETS],
        [_btn(f"⏸ {label}", "pause", str(minutes)) for minutes, label in PAUSE_PRESETS] + [_btn("▶️ Снять", "pause", "off")],
    ]
    rows.append([_btn(f"🧹 Команды: {cmd_label}", "cmdmode")])
    for key, title in TOGGLES.items():
        rows.append([_btn(f"{title}: {onoff(key)}", "toggle", key)])
    rows.append([_btn("🎯 Режим: " + ("все" if opts["mode"] == "all" else "только цели"), "toggle", "mode")])
    rows.append([_btn("✖ Закрыть", "close")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    if not await check_admin(message):
        return
    text, kb = render(message.chat.id)
    await message.answer(text, reply_markup=kb)


@router.callback_query(St.filter())
async def on_settings_button(cb: CallbackQuery, callback_data: St) -> None:
    msg = cb.message
    if not isinstance(msg, Message):
        await cb.answer()
        return
    chat_id = msg.chat.id
    if not await utils.is_admin_user(cb.bot, chat_id, cb.from_user.id):
        await cb.answer("Только для администраторов группы", show_alert=True)
        return

    action, value = callback_data.action, callback_data.value
    if action == "close":
        try:
            await msg.delete()
        except TelegramBadRequest:
            pass
        await cb.answer()
        return

    if action == "delay" and value.isdigit():
        seconds = int(value)
        if st.MIN_DELAY_SECONDS <= seconds <= st.MAX_DELAY_SECONDS:
            st.set_delay(chat_id, seconds)
    elif action == "pause":
        if value == "off":
            st.clear_pause(chat_id)
        elif value.isdigit():
            st.set_pause(chat_id, st.utcnow() + timedelta(minutes=int(value)))
    elif action == "cmdmode":
        current = cmd_mode(st.get_options(chat_id))
        nxt = CMD_MODE_ORDER[(CMD_MODE_ORDER.index(current) + 1) % len(CMD_MODE_ORDER)]
        st.set_option(chat_id, "clean_commands", nxt)
    elif action == "toggle":
        if value in TOGGLES:
            opts = st.get_options(chat_id)
            st.set_option(chat_id, value, "0" if st.option_on(opts, value) else "1")
        elif value == "mode":
            opts = st.get_options(chat_id)
            st.set_option(chat_id, "mode", "only" if opts["mode"] == "all" else "all")

    text, kb = render(chat_id)
    try:
        await msg.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        pass  # «message is not modified»
    await cb.answer()
