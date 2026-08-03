from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from config import DELETE_DELAY_SECONDS, WHITELIST_BOT_IDS

router = Router()

WELCOME_TEXT = (
    "👋 Привет! Я <b>Cleanny</b> — бот для чистки чата от сообщений других ботов.\n\n"
    "<b>Как я работаю:</b>\n"
    "Слежу за сообщениями в группе. Если сообщение написал бот (не человек) — "
    f"я автоматически удаляю его примерно через {DELETE_DELAY_SECONDS // 60} мин.\n\n"
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
    "/status — текущие настройки бота\n\n"
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
    text = (
        "<b>Текущие настройки Cleanny</b>\n\n"
        f"⏱ Задержка перед удалением: {DELETE_DELAY_SECONDS} сек. "
        f"(~{DELETE_DELAY_SECONDS // 60} мин.)\n"
        f"🚫 Вайтлист ботов (их сообщения не трогаю): {whitelist}"
    )
    await message.answer(text)
