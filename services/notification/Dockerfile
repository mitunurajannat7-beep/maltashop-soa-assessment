FROM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN python -m pip wheel --no-cache-dir --wheel-dir=/wheels -r requirements.txt

FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=8000 DB_PATH=/data/service.db
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app \
    && mkdir /data && chown app:app /data
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels pika==1.3.2 \
    && rm -rf /wheels
WORKDIR /app
COPY --chown=app:app app ./app
COPY --chown=app:app contracts ./contracts
COPY --chown=app:app scripts ./scripts
USER app
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=12 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2)"

FROM base AS test
COPY --chown=app:app tests ./tests
CMD ["python", "-m", "unittest", "discover", "-v"]

FROM base AS runtime
ENV SERVICE_NAME=notification
CMD ["python", "-m", "app.main"]
