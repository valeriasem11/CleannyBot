"""Воркер очереди удалений: раз в несколько секунд удаляет созревшие сообщения пачками."""
import asyncio
import html
import logging
import time
from collections import defaultdict

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)

import storage as st
from alerts import notify_owner
from config import QUEUE_POLL_SECONDS

logger = logging.getLogger(__name__)

BATCH_SIZE = 100  # лимит Telegram для deleteMessages


def _is_rights_error(error_text: str) -> bool:
    t = error_text.lower()
    return any(word in t for word in ("rights", "admin", "forbidden", "permission"))


async def _alert_rights(bot: Bot, chat_id: int, reason: str) -> None:
    title = st.get_known_chat_title(chat_id) or str(chat_id)
    await notify_owner(
        bot,
        f"⚠️ Не могу удалять сообщения в «{html.escape(title)}» (<code>{chat_id}</code>).\n"
        f"Причина: <code>{html.escape(reason[:200])}</code>\n"
        "Проверьте, что Cleanny — администратор с правом «Удаление сообщений».",
        key=f"rights:{chat_id}",
        cooldown=3600,
    )


async def process_due(bot: Bot) -> int:
    """Удаляет всё, что созрело. Возвращает число отправленных на удаление сообщений."""
    rows = st.fetch_due(limit=1000)
    if not rows:
        return 0

    by_chat = defaultdict(list)
    for row in rows:
        by_chat[row[0]].append(row)

    done = 0
    for chat_id, items in by_chat.items():
        for i in range(0, len(items), BATCH_SIZE):
            chunk = items[i:i + BATCH_SIZE]
            ids = [r[1] for r in chunk]
            try:
                await bot.delete_messages(chat_id=chat_id, message_ids=ids)
            except TelegramRetryAfter as exc:
                logger.warning("Лимит Telegram, жду %s сек", exc.retry_after)
                await asyncio.sleep(exc.retry_after)
                return done
            except (TelegramNetworkError, TelegramServerError) as exc:
                logger.warning("Сеть/сервер Telegram недоступны, повторю позже: %s", exc)
                return done
            except TelegramForbiddenError as exc:
                logger.warning("Нет доступа к чату %s: %s", chat_id, exc)
                st.drop_chat_pending(chat_id)
                await _alert_rights(bot, chat_id, str(exc))
                break
            except TelegramBadRequest as exc:
                st.remove_pending(chat_id, ids)
                if _is_rights_error(str(exc)):
                    logger.warning("Нет прав на удаление в чате %s: %s", chat_id, exc)
                    await _alert_rights(bot, chat_id, str(exc))
                else:
                    logger.warning("Не удалось удалить пачку в чате %s: %s", chat_id, exc)
                continue

            st.remove_pending(chat_id, ids)
            st.log_deletions([(r[0], r[3], r[4], r[2]) for r in chunk])
            done += len(ids)
            logger.info("Удалено сообщений в чате %s: %s", chat_id, len(ids))
    return done


async def run_worker(bot: Bot) -> None:
    last_prune = float("-inf")
    logger.info("Воркер удаления запущен, в очереди: %s", st.pending_count())
    while True:
        try:
            await process_due(bot)
            if time.monotonic() - last_prune > 6 * 3600:
                last_prune = time.monotonic()
                st.prune_log(90)
        except Exception:
            logger.exception("Ошибка воркера удаления")
        await asyncio.sleep(QUEUE_POLL_SECONDS)
