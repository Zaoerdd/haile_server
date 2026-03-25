FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=5000

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .

EXPOSE 5000

CMD ["sh", "-c", "gunicorn -w ${GUNICORN_WORKERS:-2} -k gthread --threads ${GUNICORN_THREADS:-4} --timeout ${GUNICORN_TIMEOUT:-60} -b 0.0.0.0:${PORT:-5000} app:app"]