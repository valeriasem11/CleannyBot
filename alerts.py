"""Оповещения владельца: потеря прав, ошибки, зависание приёма сообщений."""
import asyncio
import html
import logging
import time

from aiogram import Bot
from aiogram.types import ErrorEvent

from config import OWNER_USER_ID, WATCHDOG_INTERVAL_SECONDS, WATCHDOG_PENDING_THRESHOLD

logger = logging.getLogger(__name__)

_last_sent: dict = {}


async def notify_owner(bot: Bot, text: str, key: str | None = None, cooldown: float = 0) -> bool:
    """Пишет владельцу. С key+cooldown не чаще раза в cooldown секунд на ключ."""
    now = time.monotonic()
    if key and cooldown and now - _last_sent.get(key, float("-inf")) < cooldown:
        return False
    if key:
        _last_sent[key] = now
    try:
        await bot.send_message(OWNER_USER_ID, text)
        return True
    except Exception as exc:  # владелец мог не нажать /start или заблокировать бота
        logger.warning("Не удалось написать владельцу: %s", exc)
        return False


async def on_error(event: ErrorEvent, bot: Bot) -> bool:
    """Глобальный перехватчик ошибок хендлеров."""
    exc = event.exception
    logger.error("Необработанная ошибка в хендлере: %r", exc, exc_info=exc)
    name = type(exc).__name__
    await notify_owner(
        bot,
        f"⚠️ Ошибка в Cleanny:\n<code>{html.escape(name)}: {html.escape(str(exc))[:500]}</code>",
        key=f"err:{name}",
        cooldown=600,
    )
    return True


async def run_watchdog(bot: Bot) -> None:
    """Раз в WATCHDOG_INTERVAL_SECONDS проверяет getWebhookInfo.

    Если число необработанных апдейтов у Telegram держится выше порога — бот
    не успевает/не может их разбирать (как было при падении разбора rich-сообщений).
    Если связь с Telegram пропадала на несколько проверок подряд — сообщает о восстановлении.
    """
    fail_streak = 0
    strikes = 0
    while True:
        await asyncio.sleep(WATCHDOG_INTERVAL_SECONDS)
        try:
            info = await bot.get_webhook_info()
        except Exception as exc:
            fail_streak += 1
            logger.warning("Сторож: нет связи с Telegram (%s подряд): %s", fail_streak, exc)
            continue

        if fail_streak >= 3:
            minutes = fail_streak * WATCHDOG_INTERVAL_SECONDS // 60
            await notify_owner(bot, f"✅ Связь с Telegram восстановилась (была недоступна ~{minutes} мин).")
        fail_streak = 0

        pending = info.pending_update_count or 0
        strikes = strikes + 1 if pending >= WATCHDOG_PENDING_THRESHOLD else 0
        if strikes >= 2:
            await notify_owner(
                bot,
                f"⚠️ У Telegram накопилось необработанных обновлений для Cleanny: {pending}. "
                "Бот не успевает их разбирать или не может (например, из-за устаревшей версии aiogram). "
                "Проверьте логи.",
                key="watchdog",
                cooldown=3600,
            )
