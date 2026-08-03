import sqlite3
from contextlib import closing

from config import JOBS_DB_PATH, DELETE_DELAY_SECONDS

# Ограничение самого Telegram: нельзя удалить сообщение старше 48 часов
MAX_DELAY_SECONDS = 48 * 60 * 60
MIN_DELAY_SECONDS = 5


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(JOBS_DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_settings (
            chat_id INTEGER PRIMARY KEY,
            delay_seconds INTEGER NOT NULL
        )
        """
    )
    return conn


def get_delay(chat_id: int) -> int:
    """Возвращает задержку для конкретного чата, либо значение по умолчанию из .env."""
    with closing(_get_conn()) as conn:
        row = conn.execute(
            "SELECT delay_seconds FROM chat_settings WHERE chat_id = ?", (chat_id,)
        ).fetchone()
    return row[0] if row else DELETE_DELAY_SECONDS


def set_delay(chat_id: int, delay_seconds: int) -> None:
    with closing(_get_conn()) as conn:
        conn.execute(
            """
            INSERT INTO chat_settings (chat_id, delay_seconds)
            VALUES (?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET delay_seconds = excluded.delay_seconds
            """,
            (chat_id, delay_seconds),
        )
        conn.commit()


def parse_delay(text: str) -> int | None:
    """
    Разбирает пользовательский ввод в секунды.
    Поддерживает: "5" (минуты по умолчанию), "30s", "2m", "1h".
    Возвращает None, если формат не распознан.
    """
    text = text.strip().lower()
    if not text:
        return None
    try:
        if text.endswith("h"):
            return int(float(text[:-1]) * 3600)
        if text.endswith("m"):
            return int(float(text[:-1]) * 60)
        if text.endswith("s"):
            return int(float(text[:-1]))
        # без суффикса — считаем, что это минуты
        return int(float(text) * 60)
    except ValueError:
        return None


def format_delay(seconds: int) -> str:
    """Человекочитаемое представление задержки, например '5 мин' или '1 ч 30 мин'."""
    if seconds < 60:
        return f"{seconds} сек"
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    parts = []
    if hours:
        parts.append(f"{hours} ч")
    if minutes or not hours:
        parts.append(f"{minutes} мин")
    return " ".join(parts)
