from aiogram import Router, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from config import OWNER_USER_ID, WHITELIST_BOT_IDS
from storage import (
    MAX_DELAY_SECONDS,
    MIN_DELAY_SECONDS,
    add_whitelist,
    format_delay,
    get_delay,
    list_known_chats,
    list_whitelist,
    parse_delay,
    remove_whitelist,
    set_delay,
)

router = Router()

WELCOME_TEXT = (
    "👋 Привет! Я <b>Cleanny</b> — бот для чистки чата от сообщений других ботов.\n\n"
    "<b>Как я работаю:</b>\n"
    "Слежу за сообщениями в группе. Если сообщение написал бот (не человек) — "
    "я автоматически удаляю его через заданное время (по умолчанию 5 мин, "
    "можно настроить через /setdelay).\n\n"
    "<b>Чтобы я заработал в группе, нужно:</b>\n"
    "1. Добавить меня в группу.\n"
    "2. Назначить администратором с правом «Удаление сообщений».\n"
    "3. Всё — дальше я работаю автоматически, ничего писать не нужно.\n\n"
    "Список команд: /help"
)

HELP_TEXT = (
    "<b>Команды Cleanny</b>\n\n"
    "/start — приветствие и краткая инструкция\n"
    "/help — список команд (то, что вы видите сейчас)\n"
    "/status — текущие настройки бота в этом чате\n"
    "/setdelay &lt;время&gt; — изменить задержку перед удалением "
    "(только для админов группы)\n"
    "/whitelist add|remove|list — управлять списком ботов, которых "
    "не нужно чистить (только для админов группы)\n\n"
    "<b>Примеры /setdelay:</b>\n"
    "/setdelay 2 — 2 минуты\n"
    "/setdelay 30s — 30 секунд\n"
    "/setdelay 1h — 1 час\n\n"
    "<b>Примеры /whitelist:</b>\n"
    "/whitelist add @CoupleStoryBot\n"
    "/whitelist remove @CoupleStoryBot\n"
    "/whitelist list\n"
    "Также можно ответить командой /whitelist add на сообщение бота — "
    "тогда юзернейм указывать не нужно.\n\n"
    "Cleanny не реагирует на обычные сообщения людей — он только следит "
    "за сообщениями от других ботов в группах, где у него есть права "
    "администратора, и удаляет их с задержкой."
)


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(WELCOME_TEXT)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT)


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    whitelist = ", ".join(str(x) for x in WHITELIST_BOT_IDS) or "пусто"

    if message.chat.type in ("group", "supergroup"):
        delay = get_delay(message.chat.id)
        delay_line = f"⏱ Задержка удаления в этом чате: {format_delay(delay)}"
    else:
        delay_line = "⏱ Задержка удаления: настраивается отдельно в каждой группе (/setdelay)"

    text = (
        "<b>Текущие настройки Cleanny</b>\n\n"
        f"{delay_line}\n"
        f"🚫 Вайтлист ботов (их сообщения не трогаю): {whitelist}"
    )
    await message.answer(text)


@router.message(Command("setdelay"))
async def cmd_setdelay(message: Message, command: CommandObject) -> None:
    if message.chat.type not in ("group", "supergroup"):
        await message.answer("Эта команда работает только в группах.")
        return

    member = await message.bot.get_chat_member(message.chat.id, message.from_user.id)
    if member.status not in ("administrator", "creator"):
        await message.answer("Менять задержку может только администратор группы.")
        return

    if not command.args:
        await message.answer(
            "Укажите время. Примеры:\n"
            "/setdelay 2 — 2 минуты\n"
            "/setdelay 30s — 30 секунд\n"
            "/setdelay 1h — 1 час"
        )
        return

    delay = parse_delay(command.args)
    if delay is None:
        await message.answer(
            "Не понял формат. Примеры: /setdelay 5 (минуты), "
            "/setdelay 30s (секунды), /setdelay 1h (часы)"
        )
        return

    if delay < MIN_DELAY_SECONDS:
        await message.answer(f"Минимальная задержка — {MIN_DELAY_SECONDS} сек.")
        return

    if delay > MAX_DELAY_SECONDS:
        await message.answer(
            "Telegram не позволяет удалять сообщения старше 48 часов — "
            "поставьте значение поменьше."
        )
        return

    set_delay(message.chat.id, delay)
    await message.answer(
        f"✅ Готово! Теперь сообщения от ботов в этом чате будут "
        f"удаляться через {format_delay(delay)}."
    )


async def _check_is_admin(message: Message) -> bool:
    if message.chat.type not in ("group", "supergroup"):
        await message.answer("Эта команда работает только в группах.")
        return False

    member = await message.bot.get_chat_member(message.chat.id, message.from_user.id)
    if member.status not in ("administrator", "creator"):
        await message.answer("Управлять вайтлистом может только администратор группы.")
        return False

    return True


@router.message(Command("whitelist"))
async def cmd_whitelist(message: Message, command: CommandObject) -> None:
    if not await _check_is_admin(message):
        return

    args = (command.args or "").strip().split(maxsplit=1)
    action = args[0].lower() if args else ""

    if action == "list":
        entries = list_whitelist(message.chat.id)
        if not entries:
            await message.answer(
                "Вайтлист этого чата пуст.\n"
                "Также действует общий вайтлист из настроек хостинга "
                "(WHITELIST_BOT_IDS)."
            )
        else:
            lines = "\n".join(
                f"• @{username}" if username else f"• ID {bot_id}"
                for bot_id, username in entries
            )
            await message.answer(f"<b>Боты в вайтлисте этого чата:</b>\n{lines}")
        return

    if action in ("add", "remove"):
        target_id: int | None = None
        target_username: str | None = None

        if message.reply_to_message and message.reply_to_message.from_user:
            replied_user = message.reply_to_message.from_user
            if not replied_user.is_bot:
                await message.answer("Это сообщение не от бота.")
                return
            target_id = replied_user.id
            target_username = replied_user.username
        elif len(args) > 1:
            username = args[1].strip().lstrip("@")
            try:
                chat = await message.bot.get_chat(f"@{username}")
            except Exception:
                await message.answer(
                    f"Не нашла бота с юзернеймом @{username}. Проверьте "
                    "написание, либо ответьте этой командой на сообщение "
                    "бота вместо указания юзернейма."
                )
                return
            target_id = chat.id
            target_username = chat.username or username
        else:
            await message.answer(
                "Укажите юзернейм бота или ответьте этой командой на его "
                "сообщение.\nПример: /whitelist add @CoupleStoryBot"
            )
            return

        display_name = f"@{target_username}" if target_username else str(target_id)

        if action == "add":
            add_whitelist(message.chat.id, target_id, target_username)
            await message.answer(
                f"✅ Бот {display_name} добавлен в вайтлист этого чата — "
                "его сообщения больше не будут удаляться."
            )
        else:
            remove_whitelist(message.chat.id, target_id)
            await message.answer(
                f"🗑 Бот {display_name} убран из вайтлиста этого чата."
            )
        return

    await message.answer(
        "<b>Использование:</b>\n"
        "/whitelist add @username — добавить бота в вайтлист\n"
        "/whitelist remove @username — убрать бота из вайтлиста\n"
        "/whitelist list — показать текущий список\n\n"
        "Также можно ответить командой на сообщение бота вместо "
        "указания юзернейма."
    )


_ACTIVE_STATUSES = {"member", "administrator", "creator"}


@router.message(Command("chats"), F.chat.type == "private")
async def cmd_chats(message: Message) -> None:
    # Команда личная — молча игнорируем всех, кроме владельца,
    # чтобы не палить наличие команды посторонним
    if message.from_user is None or message.from_user.id != OWNER_USER_ID:
        return

    chats = list_known_chats()
    if not chats:
        await message.answer("Пока не знаю ни одного чата — бот ещё нигде не отметился.")
        return

    header = f"🤖 Беседы, где известен бот ({len(chats)})"
    entries = []
    for chat_id, title, status, updated_at in chats:
        emoji = "✅" if status in _ACTIVE_STATUSES else "❌"
        entries.append(
            f"{emoji} <b>{title}</b>\n"
            f"ID: <code>{chat_id}</code> · статус: {status} · обновлено: {updated_at}"
        )

    # Разбиваем на несколько сообщений, если список большой
    # (лимит Telegram на одно сообщение — 4096 символов)
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
