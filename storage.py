"""Хранилище: настройки чатов, очередь удалений, статистика.

Работает и на PostgreSQL (боевой режим), и на SQLite (локальная разработка).
Все времена хранятся в UTC (naive datetime).
"""
import time
from datetime import datetime, timezone

from sqlalchemy import bindparam, create_engine, text

from config import DATABASE_URL, DEFAULT_UTC_OFFSET_HOURS, DELETE_DELAY_SECONDS

# Ограничение самого Telegram: нельзя удалить сообщение старше 48 часов
MAX_DELAY_SECONDS = 48 * 60 * 60
MIN_DELAY_SECONDS = 5

engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS chat_settings ("
    " chat_id BIGINT PRIMARY KEY, delay_seconds INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS chat_options ("
    " chat_id BIGINT NOT NULL, opt_key TEXT NOT NULL, opt_value TEXT NOT NULL,"
    " PRIMARY KEY (chat_id, opt_key))",
    "CREATE TABLE IF NOT EXISTS chat_whitelist ("
    " chat_id BIGINT NOT NULL, bot_id BIGINT NOT NULL, username TEXT,"
    " PRIMARY KEY (chat_id, bot_id))",
    "CREATE TABLE IF NOT EXISTS chat_targets ("
    " chat_id BIGINT NOT NULL, bot_id BIGINT NOT NULL, username TEXT,"
    " PRIMARY KEY (chat_id, bot_id))",
    "CREATE TABLE IF NOT EXISTS chat_bot_delays ("
    " chat_id BIGINT NOT NULL, bot_id BIGINT NOT NULL, username TEXT,"
    " delay_seconds INTEGER NOT NULL, PRIMARY KEY (chat_id, bot_id))",
    "CREATE TABLE IF NOT EXISTS chat_phrases ("
    " chat_id BIGINT NOT NULL, phrase_key TEXT NOT NULL, phrase TEXT NOT NULL,"
    " added_at TIMESTAMP NOT NULL, PRIMARY KEY (chat_id, phrase_key))",
    "CREATE TABLE IF NOT EXISTS chat_pause ("
    " chat_id BIGINT PRIMARY KEY, paused_until TIMESTAMP NOT NULL)",
    "CREATE TABLE IF NOT EXISTS known_chats ("
    " chat_id BIGINT PRIMARY KEY, title TEXT, status TEXT NOT NULL,"
    " updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS seen_bots ("
    " bot_id BIGINT PRIMARY KEY, username TEXT, last_seen TIMESTAMP NOT NULL)",
    "CREATE TABLE IF NOT EXISTS pending_deletions ("
    " chat_id BIGINT NOT NULL, message_id BIGINT NOT NULL,"
    " delete_at TIMESTAMP NOT NULL, kind TEXT NOT NULL,"
    " bot_id BIGINT, username TEXT, PRIMARY KEY (chat_id, message_id))",
    "CREATE INDEX IF NOT EXISTS idx_pending_due ON pending_deletions (delete_at)",
    "CREATE TABLE IF NOT EXISTS delete_log ("
    " chat_id BIGINT NOT NULL, bot_id BIGINT, username TEXT,"
    " kind TEXT NOT NULL, deleted_at TIMESTAMP NOT NULL)",
    "CREATE INDEX IF NOT EXISTS idx_log_chat_time ON delete_log (chat_id, deleted_at)",
)


def _init_db() -> None:
    with engine.begin() as conn:
        for ddl in _SCHEMA:
            conn.execute(text(ddl))


_init_db()


# ---------- Опции чата ----------

DEFAULT_OPTIONS = {
    "clean_commands": "0",  # удалять команды людей вида /cmd@bot
    "clean_via": "0",       # удалять сообщения, отправленные через inline-режим бота
    "tidy": "1",            # убирать ответы Cleanny и команды админов
    "mode": "all",          # all — чистить всех, only — только ботов из /targets
    "night_start": "",      # "23:00"
    "night_end": "",        # "08:00"
    "night_action": "skip", # skip — не удалять, fast — удалять быстрее
    "utc_offset": str(int(DEFAULT_UTC_OFFSET_HOURS * 60)),  # минуты от UTC
}


# Настройки и фразы читаются на каждое сообщение в группе, поэтому держим их
# в памяти 20 секунд; при любом изменении кэш чата сбрасывается сразу.
_CACHE_TTL = 20.0
_OPTS_CACHE: dict = {}
_PHRASE_CACHE: dict = {}


def get_options(chat_id: int) -> dict:
    now = time.monotonic()
    hit = _OPTS_CACHE.get(chat_id)
    if hit and now - hit[0] < _CACHE_TTL:
        return dict(hit[1])
    opts = dict(DEFAULT_OPTIONS)
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT opt_key, opt_value FROM chat_options WHERE chat_id = :c"),
            {"c": chat_id},
        ).fetchall()
    for key, value in rows:
        opts[key] = value
    _OPTS_CACHE[chat_id] = (now, opts)
    return dict(opts)


def set_option(chat_id: int, key: str, value: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO chat_options (chat_id, opt_key, opt_value)"
                " VALUES (:c, :k, :v)"
                " ON CONFLICT (chat_id, opt_key) DO UPDATE SET opt_value = :v"
            ),
            {"c": chat_id, "k": key, "v": value},
        )
    _OPTS_CACHE.pop(chat_id, None)


def option_on(opts: dict, key: str) -> bool:
    return opts.get(key) == "1"


# ---------- Фразы для удаления ----------

MAX_PHRASES = 50
MIN_PHRASE_LEN = 3
MAX_PHRASE_LEN = 100


def phrase_add(chat_id: int, phrase: str) -> str:
    """Возвращает 'ok', 'exists' или 'limit'."""
    key = phrase.casefold()
    current = phrase_list(chat_id)
    if any(p.casefold() == key for p in current):
        return "exists"
    if len(current) >= MAX_PHRASES:
        return "limit"
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO chat_phrases (chat_id, phrase_key, phrase, added_at)"
                " VALUES (:c, :k, :p, :t)"
            ),
            {"c": chat_id, "k": key, "p": phrase, "t": utcnow()},
        )
    _PHRASE_CACHE.pop(chat_id, None)
    return "ok"


def phrase_remove(chat_id: int, phrase: str) -> bool:
    with engine.begin() as conn:
        res = conn.execute(
            text("DELETE FROM chat_phrases WHERE chat_id = :c AND phrase_key = :k"),
            {"c": chat_id, "k": phrase.casefold()},
        )
    _PHRASE_CACHE.pop(chat_id, None)
    return res.rowcount > 0


def phrase_list(chat_id: int) -> list:
    """Фразы чата в порядке добавления (как их ввели)."""
    now = time.monotonic()
    hit = _PHRASE_CACHE.get(chat_id)
    if hit and now - hit[0] < _CACHE_TTL:
        return list(hit[1])
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT phrase FROM chat_phrases WHERE chat_id = :c ORDER BY added_at, phrase_key"),
            {"c": chat_id},
        ).fetchall()
    phrases = [r[0] for r in rows]
    _PHRASE_CACHE[chat_id] = (now, phrases)
    return list(phrases)


def phrase_match(chat_id: int, message_text: str) -> str | None:
    """Первая фраза чата, которая входит в текст (без учёта регистра), либо None."""
    if not message_text:
        return None
    haystack = message_text.casefold()
    for phrase in phrase_list(chat_id):
        if phrase.casefold() in haystack:
            return phrase
    return None


# ---------- Общая задержка чата ----------

def get_delay(chat_id: int) -> int:
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT delay_seconds FROM chat_settings WHERE chat_id = :c"),
            {"c": chat_id},
        ).fetchone()
    return row[0] if row else DELETE_DELAY_SECONDS


def set_delay(chat_id: int, delay_seconds: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO chat_settings (chat_id, delay_seconds) VALUES (:c, :d)"
                " ON CONFLICT (chat_id) DO UPDATE SET delay_seconds = :d"
            ),
            {"c": chat_id, "d": delay_seconds},
        )


# ---------- Списки ботов (вайтлист / цели) ----------

_LIST_TABLES = {"whitelist": "chat_whitelist", "targets": "chat_targets"}


def list_add(kind: str, chat_id: int, bot_id: int, username: str | None) -> None:
    table = _LIST_TABLES[kind]
    with engine.begin() as conn:
        conn.execute(
            text(
                f"INSERT INTO {table} (chat_id, bot_id, username) VALUES (:c, :b, :u)"
                " ON CONFLICT (chat_id, bot_id) DO UPDATE SET username = :u"
            ),
            {"c": chat_id, "b": bot_id, "u": username},
        )


def list_remove(kind: str, chat_id: int, bot_id: int) -> None:
    table = _LIST_TABLES[kind]
    with engine.begin() as conn:
        conn.execute(
            text(f"DELETE FROM {table} WHERE chat_id = :c AND bot_id = :b"),
            {"c": chat_id, "b": bot_id},
        )


def list_get(kind: str, chat_id: int) -> list:
    table = _LIST_TABLES[kind]
    with engine.begin() as conn:
        rows = conn.execute(
            text(f"SELECT bot_id, username FROM {table} WHERE chat_id = :c"),
            {"c": chat_id},
        ).fetchall()
    return [(r[0], r[1]) for r in rows]


def list_has(kind: str, chat_id: int, bot_id: int) -> bool:
    table = _LIST_TABLES[kind]
    with engine.begin() as conn:
        row = conn.execute(
            text(f"SELECT 1 FROM {table} WHERE chat_id = :c AND bot_id = :b"),
            {"c": chat_id, "b": bot_id},
        ).fetchone()
    return row is not None


def list_has_username(kind: str, chat_id: int, username: str) -> bool:
    table = _LIST_TABLES[kind]
    with engine.begin() as conn:
        row = conn.execute(
            text(f"SELECT 1 FROM {table} WHERE chat_id = :c AND LOWER(username) = :u"),
            {"c": chat_id, "u": username.lower()},
        ).fetchone()
    return row is not None


# ---------- Своя задержка для конкретного бота ----------

def set_bot_delay(chat_id: int, bot_id: int, username: str | None, seconds: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO chat_bot_delays (chat_id, bot_id, username, delay_seconds)"
                " VALUES (:c, :b, :u, :d)"
                " ON CONFLICT (chat_id, bot_id) DO UPDATE SET username = :u, delay_seconds = :d"
            ),
            {"c": chat_id, "b": bot_id, "u": username, "d": seconds},
        )


def remove_bot_delay(chat_id: int, bot_id: int) -> bool:
    with engine.begin() as conn:
        res = conn.execute(
            text("DELETE FROM chat_bot_delays WHERE chat_id = :c AND bot_id = :b"),
            {"c": chat_id, "b": bot_id},
        )
    return res.rowcount > 0


def get_bot_delay(chat_id: int, bot_id: int) -> int | None:
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT delay_seconds FROM chat_bot_delays WHERE chat_id = :c AND bot_id = :b"),
            {"c": chat_id, "b": bot_id},
        ).fetchone()
    return row[0] if row else None


def list_bot_delays(chat_id: int) -> list:
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT bot_id, username, delay_seconds FROM chat_bot_delays WHERE chat_id = :c"),
            {"c": chat_id},
        ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


# ---------- Боты, которых мы уже видели (для поиска по @username) ----------

def remember_bot(bot_id: int, username: str | None) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO seen_bots (bot_id, username, last_seen) VALUES (:b, :u, :t)"
                " ON CONFLICT (bot_id) DO UPDATE SET username = :u, last_seen = :t"
            ),
            {"b": bot_id, "u": username, "t": utcnow()},
        )


def find_bot(username: str):
    """Ищет бота среди уже виденных. Возвращает (id, username) или None."""
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT bot_id, username FROM seen_bots WHERE LOWER(username) = :u"),
            {"u": username.lower().lstrip("@")},
        ).fetchone()
    return (row[0], row[1]) if row else None


# ---------- Пауза ----------

def set_pause(chat_id: int, until: datetime) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO chat_pause (chat_id, paused_until) VALUES (:c, :u)"
                " ON CONFLICT (chat_id) DO UPDATE SET paused_until = :u"
            ),
            {"c": chat_id, "u": until},
        )


def clear_pause(chat_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM chat_pause WHERE chat_id = :c"), {"c": chat_id})


def get_pause_until(chat_id: int) -> datetime | None:
    """Время окончания паузы (UTC) или None. Просроченную запись подчищает."""
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT paused_until FROM chat_pause WHERE chat_id = :c"),
            {"c": chat_id},
        ).fetchone()
        if row is None:
            return None
        until = row[0]
        if isinstance(until, str):
            until = datetime.fromisoformat(until)
        if until <= utcnow():
            conn.execute(text("DELETE FROM chat_pause WHERE chat_id = :c"), {"c": chat_id})
            return None
        return until


# ---------- Очередь удалений ----------

def enqueue_deletion(chat_id: int, message_id: int, delete_at: datetime,
                     kind: str, bot_id: int | None, username: str | None) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO pending_deletions"
                " (chat_id, message_id, delete_at, kind, bot_id, username)"
                " VALUES (:c, :m, :t, :k, :b, :u)"
                " ON CONFLICT (chat_id, message_id) DO UPDATE SET"
                " delete_at = :t, kind = :k, bot_id = :b, username = :u"
            ),
            {"c": chat_id, "m": message_id, "t": delete_at, "k": kind, "b": bot_id, "u": username},
        )


def cancel_deletion(chat_id: int, message_id: int) -> bool:
    with engine.begin() as conn:
        res = conn.execute(
            text("DELETE FROM pending_deletions WHERE chat_id = :c AND message_id = :m"),
            {"c": chat_id, "m": message_id},
        )
    return res.rowcount > 0


def cancel_chat_deletions(chat_id: int) -> int:
    """Отменяет все запланированные удаления чата, кроме служебных (tidy)."""
    with engine.begin() as conn:
        res = conn.execute(
            text("DELETE FROM pending_deletions WHERE chat_id = :c AND kind <> 'tidy'"),
            {"c": chat_id},
        )
    return res.rowcount


def cancel_bot_deletions(chat_id: int, bot_id: int) -> int:
    with engine.begin() as conn:
        res = conn.execute(
            text("DELETE FROM pending_deletions WHERE chat_id = :c AND bot_id = :b"),
            {"c": chat_id, "b": bot_id},
        )
    return res.rowcount


def drop_chat_pending(chat_id: int) -> int:
    with engine.begin() as conn:
        res = conn.execute(
            text("DELETE FROM pending_deletions WHERE chat_id = :c"), {"c": chat_id}
        )
    return res.rowcount


def pending_count(chat_id: int | None = None) -> int:
    with engine.begin() as conn:
        if chat_id is None:
            row = conn.execute(text("SELECT COUNT(*) FROM pending_deletions")).fetchone()
        else:
            row = conn.execute(
                text("SELECT COUNT(*) FROM pending_deletions WHERE chat_id = :c"),
                {"c": chat_id},
            ).fetchone()
    return int(row[0])


def fetch_due(limit: int = 1000) -> list:
    """Строки (chat_id, message_id, kind, bot_id, username), срок которых наступил."""
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT chat_id, message_id, kind, bot_id, username FROM pending_deletions"
                " WHERE delete_at <= :now ORDER BY delete_at LIMIT :lim"
            ),
            {"now": utcnow(), "lim": limit},
        ).fetchall()
    return [tuple(r) for r in rows]


def remove_pending(chat_id: int, message_ids: list) -> None:
    stmt = text(
        "DELETE FROM pending_deletions WHERE chat_id = :c AND message_id IN :ids"
    ).bindparams(bindparam("ids", expanding=True))
    with engine.begin() as conn:
        conn.execute(stmt, {"c": chat_id, "ids": list(message_ids)})


# ---------- Журнал удалений и статистика ----------

def log_deletions(rows: list) -> None:
    """rows: [(chat_id, bot_id, username, kind)]. Служебные (tidy) не пишем."""
    now = utcnow()
    data = [
        {"c": c, "b": b, "u": u, "k": k, "t": now}
        for (c, b, u, k) in rows
        if k != "tidy"
    ]
    if not data:
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO delete_log (chat_id, bot_id, username, kind, deleted_at)"
                " VALUES (:c, :b, :u, :k, :t)"
            ),
            data,
        )


def prune_log(days: int = 90) -> None:
    from datetime import timedelta
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM delete_log WHERE deleted_at < :t"),
            {"t": utcnow() - timedelta(days=days)},
        )


def _log_filter(chat_id: int | None, since: datetime):
    where = "deleted_at >= :since"
    params = {"since": since}
    if chat_id is not None:
        where += " AND chat_id = :c"
        params["c"] = chat_id
    return where, params


def count_deleted(chat_id: int | None, since: datetime) -> int:
    where, params = _log_filter(chat_id, since)
    with engine.begin() as conn:
        row = conn.execute(text(f"SELECT COUNT(*) FROM delete_log WHERE {where}"), params).fetchone()
    return int(row[0])


def top_bots(chat_id: int | None, since: datetime, limit: int = 5) -> list:
    where, params = _log_filter(chat_id, since)
    # удаления по фразам и команд без известного бота в топ не попадают
    where += " AND (username IS NOT NULL OR bot_id IS NOT NULL)"
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT COALESCE(username, CAST(bot_id AS TEXT)) AS name, COUNT(*) AS n"
                f" FROM delete_log WHERE {where} GROUP BY 1 ORDER BY 2 DESC LIMIT {int(limit)}"
            ),
            params,
        ).fetchall()
    return [(r[0], int(r[1])) for r in rows if r[0] is not None]


# ---------- Известные боту чаты (для /chats владельца) ----------

def upsert_known_chat_status(chat_id: int, title: str, status: str, updated_at: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO known_chats (chat_id, title, status, updated_at)"
                " VALUES (:c, :t, :s, :u)"
                " ON CONFLICT (chat_id) DO UPDATE SET title = :t, status = :s, updated_at = :u"
            ),
            {"c": chat_id, "t": title, "s": status, "u": updated_at},
        )


def upsert_known_chat_passive(chat_id: int, title: str, updated_at: str) -> None:
    """Пассивное обновление: статус не трогаем, если чат уже известен."""
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT status FROM known_chats WHERE chat_id = :c"), {"c": chat_id}
        ).fetchone()
        status = row[0] if row else "member"
        conn.execute(
            text(
                "INSERT INTO known_chats (chat_id, title, status, updated_at)"
                " VALUES (:c, :t, :s, :u)"
                " ON CONFLICT (chat_id) DO UPDATE SET title = :t, updated_at = :u"
            ),
            {"c": chat_id, "t": title, "s": status, "u": updated_at},
        )


def list_known_chats() -> list:
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT chat_id, title, status, updated_at FROM known_chats ORDER BY updated_at DESC")
        ).fetchall()
    return [(r[0], r[1], r[2], r[3]) for r in rows]


def get_known_chat_title(chat_id: int) -> str | None:
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT title FROM known_chats WHERE chat_id = :c"), {"c": chat_id}
        ).fetchone()
    return row[0] if row else None
