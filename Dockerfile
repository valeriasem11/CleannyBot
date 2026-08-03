FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# jobs.sqlite будет создаваться здесь — монтируйте volume,
# чтобы очередь удалений не терялась при пересоздании контейнера
VOLUME ["/app/data"]
ENV JOBS_DB_PATH=/app/data/jobs.sqlite

CMD ["python", "bot.py"]
