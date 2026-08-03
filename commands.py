from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from config import WHITELIST_BOT_IDS
from storage import (
    MAX_DELAY_SECONDS,
    MIN_DELAY_SECONDS,
    format_delay,
    get_delay,
    parse_delay,
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
    "(только для админов группы)\n\n"
    "<b>Примеры /setdelay:</b>\n"
    "/setdelay 2 — 2 минуты\n"
    "/setdelay 30s — 30 секунд\n"
    "/setdelay 1h — 1 час\n\n"
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
