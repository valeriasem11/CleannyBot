import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN не задан. Скопируйте .env.example в .env и укажите токен "
        "(на хостинге — добавьте переменную окружения BOT_TOKEN)."
    )

# Задержка удаления по умолчанию (секунды), если в чате не задана своя
DELETE_DELAY_SECONDS = int(os.getenv("DELETE_DELAY_SECONDS", "300"))

# ID ботов, которых не трогать во ВСЕХ чатах (через запятую)
_whitelist_raw = os.getenv("WHITELIST_BOT_IDS", "")
WHITELIST_BOT_IDS = {int(x.strip()) for x in _whitelist_raw.split(",") if x.strip()}

# Владелец бота: личная команда /chats, глобальная /stats, оповещения об ошибках
OWNER_USER_ID = int(os.getenv("OWNER_USER_ID", "828533150"))

# Часовой пояс по умолчанию (часы от UTC) для ночного режима и отображения времени.
# В конкретном чате меняется командой /timezone.
DEFAULT_UTC_OFFSET_HOURS = float(os.getenv("DEFAULT_UTC_OFFSET_HOURS", "5"))

# Через сколько секунд убирать ответы Cleanny и команды админов (режим /tidy)
TIDY_DELAY_SECONDS = int(os.getenv("TIDY_DELAY_SECONDS", "90"))

# Ускоренная задержка ночного режима «fast»
NIGHT_FAST_DELAY_SECONDS = int(os.getenv("NIGHT_FAST_DELAY_SECONDS", "30"))

# Как часто воркер проверяет очередь удалений
QUEUE_POLL_SECONDS = float(os.getenv("QUEUE_POLL_SECONDS", "3"))

# Сторож: раз в N секунд смотрит, не копятся ли необработанные апдейты
WATCHDOG_INTERVAL_SECONDS = int(os.getenv("WATCHDOG_INTERVAL_SECONDS", "300"))
WATCHDOG_PENDING_THRESHOLD = int(os.getenv("WATCHDOG_PENDING_THRESHOLD", "30"))

# База данных. На bothost — строка подключения PostgreSQL в DATABASE_URL.
# Без неё используется локальный SQLite-файл (не переживает передеплой!).
JOBS_DB_PATH = os.getenv("JOBS_DB_PATH", "jobs.sqlite")
_db_url = os.getenv("DATABASE_URL") or f"sqlite:///{JOBS_DB_PATH}"
if _db_url.startswith("postgres://"):
    _db_url = "postgresql://" + _db_url[len("postgres://"):]
if _db_url.startswith("postgresql://"):
    # Драйвер указываем явно: в новых SQLAlchemy "postgresql://" означает другой
    # драйвер (psycopg 3), которого у нас нет в requirements.
    _db_url = "postgresql+psycopg2://" + _db_url[len("postgresql://"):]
DATABASE_URL = _db_url
