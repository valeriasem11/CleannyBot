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

# Файл SQLite, где APScheduler хранит запланированные задачи
# (переживают перезапуск бота)
JOBS_DB_PATH = os.getenv("JOBS_DB_PATH", "jobs.sqlite")
