FROM python:3.12.14-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --requirement requirements.txt \
    && useradd --system --uid 10001 --create-home appuser
COPY --chown=10001:10001 app ./app
COPY --chown=10001:10001 migrate.py ./migrate.py
COPY --chown=10001:10001 sql ./sql
COPY --chown=10001:10001 scripts ./scripts
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"]
