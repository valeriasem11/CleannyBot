import html
import re
from datetime import timedelta

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

import storage as st
from config import DEFAULT_UTC_OFFSET_HOURS, OWNER_USER_ID, TIDY_DELAY_SECONDS, WHITELIST_BOT_IDS
from utils import cmd_mode as clean_cmd_mode  # не путать с обработчиком /mode ниже
from utils import (
    CMD_MODE_LABELS,
    GROUP_TYPES,
    UNKNOWN_BOT_TEXT,
    check_admin,
    fmt_local,
    format_duration,
    offset_label,
    parse_duration,
    parse_hm,
    parse_utc_offset,
    reply,
    resolve_bot,
)

router = Router()

MAX_PAUSE_SECONDS = 30 * 24 * 3600
ON_WORDS = {"on", "1", "yes", "вкл", "включить", "да"}
OFF_WORDS = {"off", "0", "no", "выкл", "выключить", "нет"}
CANCEL_WORDS = {"cancel", "all", "отмена", "сброс"}
ACTIVE_STATUSES = {"member", "administrator", "creator"}

WELCOME_TEXT = (
    "👋 Привет! Я <b>Cleanny</b> — убираю из чата сообщения других ботов.\n\n"
    "<b>Как это работает</b>\n"
    "Если в группе пишет бот, я удаляю его сообщение через заданное время "
    "(по умолчанию 5 минут).\n\n"
    "<b>Чтобы я заработал</b>\n"
    "1. Добавьте меня в группу.\n"
    "2. Назначьте администратором с правом «Удаление сообщений».\n"
    "3. Дальше я работаю сам. Настройки — команда /settings (для админов группы).\n\n"
    "Все команды: /help"
)

HELP_TEXT = (
    "<b>Команды Cleanny</b>\n\n"
    "<b>Для всех</b>\n"
    "/start — приветствие\n"
    "/help — эта справка\n"
    "/status — настройки этого чата\n\n"
    "<b>Для админов группы</b>\n"
    "/settings — меню настроек с кнопками\n"
    "/setdelay &lt;время&gt; — через сколько удалять сообщения ботов\n"
    "/botdelay &lt;@бот&gt; &lt;время|reset&gt; — своя задержка для конкретного бота\n"
    "/pause &lt;время&gt; [cancel] — пауза (cancel — ещё и отменить уже запланированные удаления)\n"
    "/resume — снять паузу\n"
    "/keep — ответом на сообщение бота: оставить его\n"
    "/whitelist add|remove|list — боты, которых не трогаем\n"
    "/mode all|only — чистить всех или только ботов из /targets\n"
    "/targets add|remove|list — список ботов для режима only\n"
    "/cleancmds off|on|smart|all — какие команды людей удалять (подробнее: /cleancmds)\n"
    "/phrases add|remove|list — удалять любые сообщения, содержащие заданный текст\n"
    "/cleanvia on|off — удалять сообщения, отправленные через inline-режим\n"
    f"/tidy on|off — убирать мои ответы и команды админов через ~{TIDY_DELAY_SECONDS} сек\n"
    "/night 23:00-08:00 [skip|fast] | off — ночной режим\n"
    "/timezone +5 — часовой пояс чата\n"
    "/stats — статистика удалений\n\n"
    "<b>Формат времени:</b> 30s, 10m, 2h, 1d; число без буквы — минуты.\n"
    "<b>Выбрать бота</b> можно по @username (если он уже писал там, где я есть) "
    "или ответом на его сообщение."
)


# ---------- Базовые ----------

@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await reply(message, WELCOME_TEXT)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await reply(message, HELP_TEXT)


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    if message.chat.type not in GROUP_TYPES:
        await reply(message, "Настройки задаются отдельно в каждой группе — откройте /status в нужном чате.")
        return

    chat_id = message.chat.id
    opts = st.get_options(chat_id)
    offset = int(opts["utc_offset"])
    onoff = lambda key: "вкл" if st.option_on(opts, key) else "выкл"  # noqa: E731

    lines = [
        "<b>Настройки Cleanny в этом чате</b>",
        "",
        f"⏱ Задержка удаления: {format_duration(st.get_delay(chat_id))}",
        "🎯 Режим: " + ("чистить всех, кроме вайтлиста" if opts["mode"] == "all" else "только боты из /targets"),
    ]
    pause = st.get_pause_until(chat_id)
    if pause:
        lines.append(f"⏸ На паузе до {fmt_local(pause, offset)} ({offset_label(offset)})")
    if opts["night_start"] and opts["night_end"]:
        action = "не удаляю" if opts["night_action"] == "skip" else f"удаляю быстро (~{format_duration(30)})"
        lines.append(f"🌙 Ночной режим {opts['night_start']}–{opts['night_end']}: {action}")
    lines += [
        f"🧹 Удаление команд: {CMD_MODE_LABELS[clean_cmd_mode(opts)]}",
        f"🔤 Фраз для удаления: {len(st.phrase_list(chat_id))}",
        f"📎 Сообщения через inline-режим: {onoff('clean_via')}",
        f"🧽 Убирать мои ответы: {onoff('tidy')}",
        f"🕒 Часовой пояс: {offset_label(offset)}",
        f"🚫 Вайтлист: {len(st.list_get('whitelist', chat_id))} · целей: {len(st.list_get('targets', chat_id))}"
        f" · отдельных задержек: {len(st.list_bot_delays(chat_id))}",
        f"📬 В очереди на удаление: {st.pending_count(chat_id)}",
    ]
    if WHITELIST_BOT_IDS:
        lines.append(f"Общий вайтлист (для всех чатов): {len(WHITELIST_BOT_IDS)} бот(ов)")
    await reply(message, "\n".join(lines))


# ---------- Задержка и пауза ----------

def _validate_delay(seconds: int | None) -> str | None:
    if seconds is None:
        return "Не понял формат. Примеры: 5 (минуты), 30s, 10m, 2h."
    if seconds < st.MIN_DELAY_SECONDS:
        return f"Минимальная задержка — {st.MIN_DELAY_SECONDS} сек."
    if seconds > st.MAX_DELAY_SECONDS:
        return "Telegram не позволяет удалять сообщения старше 48 часов — поставьте поменьше."
    return None


@router.message(Command("setdelay"))
async def cmd_setdelay(message: Message, command: CommandObject) -> None:
    if not await check_admin(message):
        return
    if not command.args:
        await reply(message, "Укажите время, например: /setdelay 2 (минуты), /setdelay 30s, /setdelay 1h")
        return
    seconds = parse_duration(command.args)
    error = _validate_delay(seconds)
    if error:
        await reply(message, error)
        return
    st.set_delay(message.chat.id, seconds)
    await reply(message, f"✅ Готово! Сообщения ботов в этом чате будут удаляться через {format_duration(seconds)}.")


@router.message(Command("pause"))
async def cmd_pause(message: Message, command: CommandObject) -> None:
    if not await check_admin(message):
        return
    tokens = (command.args or "").split()
    cancel = any(t.lower() in CANCEL_WORDS for t in tokens)
    duration_text = " ".join(t for t in tokens if t.lower() not in CANCEL_WORDS)
    if not duration_text:
        await reply(
            message,
            "Укажите время: /pause 10 (минуты), /pause 30s, /pause 1h.\n"
            "Добавьте cancel, чтобы заодно отменить уже запланированные удаления: /pause 1h cancel",
        )
        return
    seconds = parse_duration(duration_text)
    if seconds is None or seconds <= 0 or seconds > MAX_PAUSE_SECONDS:
        await reply(message, "Не понял время (максимум — 30 дней). Примеры: /pause 10, /pause 2h, /pause 1d")
        return

    chat_id = message.chat.id
    until = st.utcnow() + timedelta(seconds=seconds)
    st.set_pause(chat_id, until)
    cancelled = st.cancel_chat_deletions(chat_id) if cancel else 0
    offset = int(st.get_options(chat_id)["utc_offset"])
    text = f"⏸ Пауза до {fmt_local(until, offset)} ({offset_label(offset)}). Новые сообщения ботов удаляться не будут."
    if cancel:
        text += f"\nОтменено уже запланированных удалений: {cancelled}."
    else:
        text += "\nУже запланированные удаления сработают. Чтобы отменить и их — /pause ... cancel."
    text += "\nСнять паузу раньше: /resume."
    await reply(message, text)


@router.message(Command("resume"))
async def cmd_resume(message: Message) -> None:
    if not await check_admin(message):
        return
    if st.get_pause_until(message.chat.id) is None:
        await reply(message, "Бот и так сейчас не на паузе.")
        return
    st.clear_pause(message.chat.id)
    await reply(message, "▶️ Готово! Cleanny снова удаляет сообщения ботов.")


# ---------- Списки ботов: whitelist / targets ----------

async def _manage_list(message: Message, command: CommandObject, kind: str, title: str, cmd: str) -> None:
    if not await check_admin(message):
        return
    chat_id = message.chat.id
    parts = (command.args or "").split(maxsplit=1)
    action = parts[0].lower() if parts else ""
    token = parts[1].strip() if len(parts) > 1 else None

    if action == "list":
        entries = st.list_get(kind, chat_id)
        if not entries:
            await reply(message, f"{title} этого чата пуст.")
            return
        lines = "\n".join(f"• @{u}" if u else f"• ID {b}" for b, u in entries)
        await reply(message, f"<b>{title} этого чата:</b>\n{lines}")
        return

    if action in ("add", "remove"):
        target = await resolve_bot(message, token)
        if target is None:
            await reply(message, UNKNOWN_BOT_TEXT if token else f"Укажите бота: /{cmd} {action} @username — или ответьте командой на его сообщение.")
            return
        bot_id, username = target
        name = f"@{username}" if username else f"ID {bot_id}"
        if action == "add":
            st.list_add(kind, chat_id, bot_id, username)
            text = f"✅ {html.escape(name)} добавлен в список «{title.lower()}» этого чата."
            if kind == "whitelist":
                cancelled = st.cancel_bot_deletions(chat_id, bot_id)
                text += " Его сообщения больше не удаляются."
                if cancelled:
                    text += f" Отменено уже запланированных удалений: {cancelled}."
            await reply(message, text)
        else:
            st.list_remove(kind, chat_id, bot_id)
            await reply(message, f"🗑 {html.escape(name)} убран из списка «{title.lower()}».")
        return

    await reply(
        message,
        f"<b>Использование:</b>\n/{cmd} add @username — добавить\n/{cmd} remove @username — убрать\n"
        f"/{cmd} list — показать список\nМожно ответить командой на сообщение бота вместо @username.",
    )


@router.message(Command("whitelist"))
async def cmd_whitelist(message: Message, command: CommandObject) -> None:
    await _manage_list(message, command, "whitelist", "Вайтлист", "whitelist")


@router.message(Command("targets"))
async def cmd_targets(message: Message, command: CommandObject) -> None:
    await _manage_list(message, command, "targets", "Цели", "targets")


@router.message(Command("mode"))
async def cmd_mode(message: Message, command: CommandObject) -> None:
    if not await check_admin(message):
        return
    arg = (command.args or "").strip().lower()
    if arg not in ("all", "only"):
        current = st.get_options(message.chat.id)["mode"]
        await reply(
            message,
            f"Сейчас: <b>{current}</b>.\n/mode all — чистить всех ботов, кроме вайтлиста\n"
            "/mode only — чистить только ботов из списка /targets",
        )
        return
    st.set_option(message.chat.id, "mode", arg)
    if arg == "only":
        await reply(message, "🎯 Режим «only»: чищу только ботов из /targets. Добавить: /targets add @бот")
    else:
        await reply(message, "🎯 Режим «all»: чищу всех ботов, кроме вайтлиста.")


@router.message(Command("botdelay"))
async def cmd_botdelay(message: Message, command: CommandObject) -> None:
    if not await check_admin(message):
        return
    chat_id = message.chat.id
    tokens = (command.args or "").split()

    if tokens and tokens[0].lower() == "list":
        entries = st.list_bot_delays(chat_id)
        if not entries:
            await reply(message, "Отдельных задержек нет — все боты удаляются с общей задержкой.")
            return
        lines = "\n".join(f"• {'@' + u if u else 'ID ' + str(b)} — {format_duration(s)}" for b, u, s in entries)
        await reply(message, f"<b>Отдельные задержки:</b>\n{lines}")
        return

    rep = message.reply_to_message
    replying_to_bot = bool(rep and ((rep.from_user and rep.from_user.is_bot) or rep.via_bot))
    if replying_to_bot:
        token, time_token = None, " ".join(tokens)
    elif tokens:
        token, time_token = tokens[0], " ".join(tokens[1:])
    else:
        token, time_token = None, ""

    if not time_token:
        await reply(
            message,
            "Использование:\n/botdelay @бот 10 — удалять этого бота через 10 минут\n"
            "/botdelay @бот reset — вернуть общую задержку\n/botdelay list — показать все\n"
            "Можно ответить командой на сообщение бота и не указывать @бот.",
        )
        return

    target = await resolve_bot(message, token)
    if target is None:
        await reply(message, UNKNOWN_BOT_TEXT)
        return
    bot_id, username = target
    name = html.escape(f"@{username}" if username else f"ID {bot_id}")

    if time_token.lower() in ("reset", "сброс", "off"):
        removed = st.remove_bot_delay(chat_id, bot_id)
        await reply(message, f"↩️ Для {name} снова общая задержка." if removed else f"Для {name} не было отдельной задержки.")
        return

    seconds = parse_duration(time_token)
    error = _validate_delay(seconds)
    if error:
        await reply(message, error)
        return
    st.set_bot_delay(chat_id, bot_id, username, seconds)
    await reply(message, f"✅ Сообщения {name} будут удаляться через {format_duration(seconds)}.")


# ---------- Переключатели ----------

async def _toggle(message: Message, command: CommandObject, key: str, title: str, hint: str) -> None:
    if not await check_admin(message):
        return
    chat_id = message.chat.id
    arg = (command.args or "").strip().lower()
    current = st.option_on(st.get_options(chat_id), key)
    if arg in ON_WORDS:
        new = True
    elif arg in OFF_WORDS:
        new = False
    else:
        await reply(message, f"{title}: сейчас <b>{'включено' if current else 'выключено'}</b>.\nВключить: on · выключить: off.\n{hint}")
        return
    st.set_option(chat_id, key, "1" if new else "0")
    await reply(message, f"{title}: {'включено ✅' if new else 'выключено ⛔️'}")


CMD_MODE_WORDS = {
    "off": "0", "0": "0", "выкл": "0",
    "on": "1", "1": "1", "вкл": "1",
    "smart": "smart", "умный": "smart",
    "all": "all", "все": "all", "всё": "all",
}

CMD_MODE_HELP = (
    "/cleancmds off — не удалять команды\n"
    "/cleancmds on — только команды с @, например /work@бот\n"
    "/cleancmds smart — ещё и команды без @, после которых сразу ответил бот "
    "(удаляются вместе с его ответом)\n"
    "/cleancmds all — все команды людей, кроме моих. Удалятся даже /ban и подобные, "
    "бот-адресат неизвестен"
)


@router.message(Command("cleancmds"))
async def cmd_cleancmds(message: Message, command: CommandObject) -> None:
    if not await check_admin(message):
        return
    chat_id = message.chat.id
    mode = CMD_MODE_WORDS.get((command.args or "").strip().lower())
    if mode is None:
        current = CMD_MODE_LABELS[clean_cmd_mode(st.get_options(chat_id))]
        await reply(message, f"🧹 Сейчас: <b>{current}</b>.\n\n{CMD_MODE_HELP}")
        return
    st.set_option(chat_id, "clean_commands", mode)
    await reply(message, f"🧹 Удаление команд: <b>{CMD_MODE_LABELS[mode]}</b> ✅")


# ---------- Фразы ----------

PHRASES_USAGE = (
    "<b>Фразы для удаления</b>\n"
    "Любое сообщение, в котором встречается фраза (регистр не важен), будет удалено "
    "через общую задержку чата — от кого бы оно ни было, даже от админа.\n\n"
    "/phrases add /casino — добавить фразу\n"
    "/phrases remove /casino — убрать (или по номеру: /phrases remove 2)\n"
    "/phrases list — показать список\n\n"
    f"Длина фразы — от {st.MIN_PHRASE_LEN} до {st.MAX_PHRASE_LEN} символов, не больше {st.MAX_PHRASES} фраз на чат. "
    "Сообщение «/casino 10» удалится по фразе «/casino»."
)


@router.message(Command("phrases"))
async def cmd_phrases(message: Message, command: CommandObject) -> None:
    if not await check_admin(message):
        return
    chat_id = message.chat.id
    action, _, rest = (command.args or "").strip().partition(" ")
    action, rest = action.lower(), rest.strip()

    if action == "list":
        phrases = st.phrase_list(chat_id)
        if not phrases:
            await reply(message, "Список фраз пуст. Добавить: /phrases add текст")
            return
        lines = "\n".join(f"{i}. <code>{html.escape(p)}</code>" for i, p in enumerate(phrases, 1))
        await reply(message, f"<b>Фразы для удаления ({len(phrases)}):</b>\n{lines}")
        return

    if action == "add":
        if len(rest) < st.MIN_PHRASE_LEN:
            await reply(message, f"Фраза слишком короткая — минимум {st.MIN_PHRASE_LEN} символа, иначе удалится слишком много.")
            return
        if len(rest) > st.MAX_PHRASE_LEN:
            await reply(message, f"Фраза слишком длинная — максимум {st.MAX_PHRASE_LEN} символов.")
            return
        result = st.phrase_add(chat_id, rest)
        shown = html.escape(rest)
        if result == "exists":
            await reply(message, f"Фраза <code>{shown}</code> уже в списке.")
        elif result == "limit":
            await reply(message, f"Достигнут лимит — {st.MAX_PHRASES} фраз на чат. Уберите ненужные: /phrases remove номер")
        else:
            await reply(message, f"✅ Теперь сообщения со словами <code>{shown}</code> будут удаляться.")
        return

    if action in ("remove", "del", "delete"):
        if not rest:
            await reply(message, "Укажите фразу или её номер из /phrases list.")
            return
        target = rest
        if rest.isdigit():
            phrases = st.phrase_list(chat_id)
            if 1 <= int(rest) <= len(phrases):
                target = phrases[int(rest) - 1]
        if st.phrase_remove(chat_id, target):
            await reply(message, f"🗑 Фраза <code>{html.escape(target)}</code> убрана.")
        else:
            await reply(message, "Такой фразы нет в списке. Посмотреть: /phrases list")
        return

    await reply(message, PHRASES_USAGE)


@router.message(Command("cleanvia"))
async def cmd_cleanvia(message: Message, command: CommandObject) -> None:
    await _toggle(
        message, command, "clean_via", "Удаление сообщений через inline-режим",
        "Это сообщения, которые человек отправил через @бота (в них есть пометка «via @бот»).",
    )


@router.message(Command("tidy"))
async def cmd_tidy(message: Message, command: CommandObject) -> None:
    await _toggle(
        message, command, "tidy", "Авто-уборка моих ответов",
        f"Мои ответы и команды админов убираются примерно через {TIDY_DELAY_SECONDS} сек.",
    )


# ---------- Ночной режим и часовой пояс ----------

_NIGHT_RE = re.compile(r"(\d{1,2}:\d{2})\s*[-–—]\s*(\d{1,2}:\d{2})")


@router.message(Command("night"))
async def cmd_night(message: Message, command: CommandObject) -> None:
    if not await check_admin(message):
        return
    chat_id = message.chat.id
    arg = (command.args or "").strip()
    opts = st.get_options(chat_id)

    if arg.lower() in ("off", "выкл", "0"):
        st.set_option(chat_id, "night_start", "")
        st.set_option(chat_id, "night_end", "")
        await reply(message, "🌙 Ночной режим выключен.")
        return

    m = _NIGHT_RE.search(arg)
    if not m or parse_hm(m.group(1)) is None or parse_hm(m.group(2)) is None:
        current = (
            f"Сейчас: {opts['night_start']}–{opts['night_end']} ({opts['night_action']})."
            if opts["night_start"] else "Сейчас выключен."
        )
        await reply(
            message,
            f"🌙 {current}\n/night 23:00-08:00 — не удалять ночью (skip)\n"
            "/night 23:00-08:00 fast — ночью удалять быстро\n/night off — выключить\n"
            f"Время считается по поясу чата: {offset_label(int(opts['utc_offset']))} (/timezone).",
        )
        return

    action = "fast" if "fast" in arg.lower() else "skip"
    start, end = m.group(1), m.group(2)
    st.set_option(chat_id, "night_start", f"{int(start.split(':')[0]):02d}:{start.split(':')[1]}")
    st.set_option(chat_id, "night_end", f"{int(end.split(':')[0]):02d}:{end.split(':')[1]}")
    st.set_option(chat_id, "night_action", action)
    what = "сообщения ботов не удаляются" if action == "skip" else "сообщения ботов удаляются быстро"
    await reply(message, f"🌙 Ночной режим {start}–{end} ({offset_label(int(opts['utc_offset']))}): {what}.")


@router.message(Command("timezone"))
async def cmd_timezone(message: Message, command: CommandObject) -> None:
    if not await check_admin(message):
        return
    chat_id = message.chat.id
    arg = (command.args or "").strip()
    if not arg:
        offset = int(st.get_options(chat_id)["utc_offset"])
        await reply(message, f"🕒 Часовой пояс чата: {offset_label(offset)}.\nИзменить: /timezone +5 (Екатеринбург), /timezone +3 (Москва).")
        return
    minutes = parse_utc_offset(arg)
    if minutes is None:
        await reply(message, "Не понял. Пример: /timezone +5 или /timezone -3 или /timezone +5:30")
        return
    st.set_option(chat_id, "utc_offset", str(minutes))
    await reply(message, f"🕒 Часовой пояс чата: {offset_label(minutes)}.")


# ---------- Статистика ----------

@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    if message.chat.type in GROUP_TYPES:
        if not await check_admin(message):
            return
        chat_id = message.chat.id
        offset = int(st.get_options(chat_id)["utc_offset"])
        scope = "этот чат"
    else:
        if not message.from_user or message.from_user.id != OWNER_USER_ID:
            return
        chat_id = None
        offset = int(DEFAULT_UTC_OFFSET_HOURS * 60)
        scope = "все чаты"

    now = st.utcnow()
    local_midnight = (now + timedelta(minutes=offset)).replace(hour=0, minute=0, second=0, microsecond=0)
    since_today = local_midnight - timedelta(minutes=offset)
    since_week = now - timedelta(days=7)
    since_month = now - timedelta(days=30)

    lines = [
        f"📊 <b>Статистика Cleanny</b> ({scope})",
        "",
        f"Сегодня: {st.count_deleted(chat_id, since_today)}",
        f"За 7 дней: {st.count_deleted(chat_id, since_week)}",
        f"За 30 дней: {st.count_deleted(chat_id, since_month)}",
    ]
    top = st.top_bots(chat_id, since_week)
    if top:
        lines += ["", "<b>Кого чищу чаще всего (7 дней):</b>"]
        for i, (name, n) in enumerate(top, 1):
            label = name if name.isdigit() else f"@{name}"
            lines.append(f"{i}. {html.escape(label)} — {n}")
    lines += ["", f"В очереди сейчас: {st.pending_count(chat_id)}", "<i>Считаются сообщения, отправленные на удаление.</i>"]
    await reply(message, "\n".join(lines))


# ---------- Личная команда владельца ----------

@router.message(Command("chats"), F.chat.type == "private")
async def cmd_chats(message: Message) -> None:
    if message.from_user is None or message.from_user.id != OWNER_USER_ID:
        return  # молча игнорируем посторонних

    chats = st.list_known_chats()
    if not chats:
        await message.answer("Пока не знаю ни одного чата — бот ещё нигде не отметился.")
        return

    header = f"🤖 Беседы, где известен бот ({len(chats)})"
    entries = []
    for chat_id, title, status, updated_at in chats:
        emoji = "✅" if status in ACTIVE_STATUSES else "❌"
        entries.append(
            f"{emoji} <b>{html.escape(title or str(chat_id))}</b>\n"
            f"ID: <code>{chat_id}</code> · статус: {status} · обновлено: {updated_at}"
        )

    chunk = header
    for entry in entries:
        candidate = f"{chunk}\n\n{entry}"
        if len(candidate) > 3500:
            await message.answer(chunk)
            chunk = entry
        else:
            chunk = candidate
    if chunk:
        await message.answer(chunk)
