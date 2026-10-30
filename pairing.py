"""Связка «команда человека без @ → ответ бота» для режима smart.

Команда вида /work не говорит, какому боту она адресована. Поэтому в режиме
smart мы запоминаем свежие команды людей и, когда бот отвечает, ищем среди них
ту, на которую он ответил: сначала по reply, затем по близкому времени.
Хранится только в памяти: окно жизни записи — минута.
"""
import time

WINDOW_SECONDS = 20  # насколько «свежей» должна быть команда, чтобы связать по времени
KEEP_SECONDS = 60
MAX_PER_CHAT = 50

_recent: dict = {}


def _fresh(chat_id: int) -> list:
    now = time.monotonic()
    items = [it for it in _recent.get(chat_id, []) if now - it["ts"] <= KEEP_SECONDS]
    _recent[chat_id] = items[-MAX_PER_CHAT:]
    return _recent[chat_id]


def record(chat_id: int, message_id: int, name: str) -> None:
    _fresh(chat_id).append({"mid": message_id, "name": name, "ts": time.monotonic()})


def claim(chat_id: int, reply_to) -> int | None:
    """Находит команду, на которую ответил бот, и возвращает её message_id.

    reply_to — сообщение, на которое ответил бот (или None, если не ответом).
    Найденная команда «забирается»: на одну команду приходится один ответ.
    """
    items = _fresh(chat_id)
    if reply_to is not None:
        for it in items:
            if it["mid"] == reply_to.message_id:
                items.remove(it)
                return it["mid"]
        return None  # бот ответил на что-то другое — это не ответ на команду

    now = time.monotonic()
    for it in reversed(items):
        if now - it["ts"] <= WINDOW_SECONDS:
            items.remove(it)
            return it["mid"]
    return None


def reset() -> None:
    _recent.clear()
