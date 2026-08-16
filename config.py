import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN не задан. Скопируйте .env.example в .env и укажите токен."
    )

# Через сколько секунд после отправки удалять сообщение от бота
DELETE_DELAY_SECONDS = int(os.getenv("DELETE_DELAY_SECONDS", "300"))

# ID ботов, которые не нужно трогать (через запятую в .env)
_whitelist_raw = os.getenv("WHITELIST_BOT_IDS", "")
WHITELIST_BOT_IDS = {
    int(x.strip()) for x in _whitelist_raw.split(",") if x.strip()
}

# ID владельца бота — только этому пользователю доступна команда /chats
OWNER_USER_ID = int(os.getenv("OWNER_USER_ID", "828533150"))

# Строка подключения к базе данных.
# На bothost — возьмите connection string вашего PostgreSQL из панели
# и пропишите его сюда как DATABASE_URL. Если переменная не задана —
# используется локальный SQLite-файл (удобно для разработки на своём
# компьютере, но НЕ переживает передеплой на облачных платформах).
JOBS_DB_PATH = os.getenv("JOBS_DB_PATH", "jobs.sqlite")
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{JOBS_DB_PATH}")
