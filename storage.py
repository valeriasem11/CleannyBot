from datetime import datetime

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from config import DATABASE_URL, DELETE_DELAY_SECONDS

# Ограничение самого Telegram: нельзя удалить сообщение старше 48 часов
MAX_DELAY_SECONDS = 48 * 60 * 60
MIN_DELAY_SECONDS = 5

TIME_FORMAT = "%d.%m.%Y %H:%M"

engine: Engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)


def _init_db() -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS chat_settings (
                    chat_id BIGINT PRIMARY KEY,
                    delay_seconds INTEGER NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS chat_whitelist (
                    chat_id BIGINT NOT NULL,
                    bot_id BIGINT NOT NULL,
                    username TEXT,
                    PRIMARY KEY (chat_id, bot_id)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS known_chats (
                    chat_id BIGINT PRIMARY KEY,
                    title TEXT,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS chat_pause (
                    chat_id BIGINT PRIMARY KEY,
                    paused_until TIMESTAMP NOT NULL
                )
                """
            )
        )


_init_db()


# ---------- Задержка удаления (per-chat) ----------

def get_delay(chat_id: int) -> int:
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT delay_seconds FROM chat_settings WHERE chat_id = :chat_id"),
            {"chat_id": chat_id},
        ).fetchone()
    return row[0] if row else DELETE_DELAY_SECONDS


def set_delay(chat_id: int, delay_seconds: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO chat_settings (chat_id, delay_seconds)
                VALUES (:chat_id, :delay)
                ON CONFLICT (chat_id) DO UPDATE SET delay_seconds = :delay
                """
            ),
            {"chat_id": chat_id, "delay": delay_seconds},
        )


# ---------- Вайтлист ботов (per-chat) ----------

def add_whitelist(chat_id: int, bot_id: int, username: str | None) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO chat_whitelist (chat_id, bot_id, username)
                VALUES (:chat_id, :bot_id, :username)
                ON CONFLICT (chat_id, bot_id) DO UPDATE SET username = :username
                """
            ),
            {"chat_id": chat_id, "bot_id": bot_id, "username": username},
        )


def remove_whitelist(chat_id: int, bot_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM chat_whitelist WHERE chat_id = :chat_id AND bot_id = :bot_id"),
            {"chat_id": chat_id, "bot_id": bot_id},
        )


def list_whitelist(chat_id: int) -> list[tuple[int, str | None]]:
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT bot_id, username FROM chat_whitelist WHERE chat_id = :chat_id"),
            {"chat_id": chat_id},
        ).fetchall()
    return [(r[0], r[1]) for r in rows]


def is_chat_whitelisted(chat_id: int, bot_id: int) -> bool:
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT 1 FROM chat_whitelist WHERE chat_id = :chat_id AND bot_id = :bot_id"
            ),
            {"chat_id": chat_id, "bot_id": bot_id},
        ).fetchone()
    return row is not None


# ---------- Известные боту чаты ----------

def upsert_known_chat_status(chat_id: int, title: str, status: str, updated_at: str) -> None:
    """Достоверное обновление (из события my_chat_member) — статус точный."""
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO known_chats (chat_id, title, status, updated_at)
                VALUES (:chat_id, :title, :status, :updated_at)
                ON CONFLICT (chat_id) DO UPDATE
                    SET title = :title, status = :status, updated_at = :updated_at
                """
            ),
            {"chat_id": chat_id, "title": title, "status": status, "updated_at": updated_at},
        )


def upsert_known_chat_passive(chat_id: int, title: str, updated_at: str) -> None:
    """Пассивное обновление на каждое сообщение — статус не трогаем, если чат уже известен."""
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT status FROM known_chats WHERE chat_id = :chat_id"),
            {"chat_id": chat_id},
        ).fetchone()
        status = row[0] if row else "member"
        conn.execute(
            text(
                """
                INSERT INTO known_chats (chat_id, title, status, updated_at)
                VALUES (:chat_id, :title, :status, :updated_at)
                ON CONFLICT (chat_id) DO UPDATE
                    SET title = :title, updated_at = :updated_at
                """
            ),
            {"chat_id": chat_id, "title": title, "status": status, "updated_at": updated_at},
        )


def list_known_chats() -> list[tuple[int, str, str, str]]:
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT chat_id, title, status, updated_at FROM known_chats ORDER BY updated_at DESC")
        ).fetchall()
    return [(r[0], r[1], r[2], r[3]) for r in rows]


# ---------- Временная пауза (per-chat) ----------

def set_pause(chat_id: int, until: datetime) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO chat_pause (chat_id, paused_until)
                VALUES (:chat_id, :until)
                ON CONFLICT (chat_id) DO UPDATE SET paused_until = :until
                """
            ),
            {"chat_id": chat_id, "until": until},
        )


def clear_pause(chat_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM chat_pause WHERE chat_id = :chat_id"),
            {"chat_id": chat_id},
        )


def get_pause_until(chat_id: int) -> datetime | None:
    """
    Возвращает время окончания паузы, если чат сейчас на паузе,
    иначе None. Если пауза уже истекла — тихо подчищает запись.
    """
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT paused_until FROM chat_pause WHERE chat_id = :chat_id"),
            {"chat_id": chat_id},
        ).fetchone()
        if row is None:
            return None

        paused_until = row[0]
        if isinstance(paused_until, str):
            paused_until = datetime.fromisoformat(paused_until)

        if paused_until <= datetime.now():
            conn.execute(
                text("DELETE FROM chat_pause WHERE chat_id = :chat_id"),
                {"chat_id": chat_id},
            )
            return None

        return paused_until


def is_paused(chat_id: int) -> bool:
    return get_pause_until(chat_id) is not None


# ---------- Разбор пользовательского ввода времени ----------

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
