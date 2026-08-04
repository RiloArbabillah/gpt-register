FROM node:22-alpine AS frontend-build

WORKDIR /build/webui/frontend
COPY webui/frontend/package*.json ./
RUN npm ci
COPY webui/frontend/ ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WEBUI_DATA_DIR=/data \
    OPENAI_SENTINEL_CACHE_DIR=/data/sentinel \
    OPENAI_SENTINEL_NODE_PATH=node \
    OPENAI_SENTINEL_AUTO_DISCOVER=1 \
    OPENAI_SENTINEL_RETRY_COUNT=2 \
    OPENAI_SENTINEL_TIMEOUT_MS=45000 \
    AUTH_COOKIE_SECURE=0

WORKDIR /app
COPY requirements.txt ./
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt
COPY . ./
COPY --from=frontend-build /build/webui/static ./webui/static

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data/sentinel \
    && chown -R appuser:appuser /app /data
USER appuser

EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=3)"

CMD ["uvicorn", "webui.app:app", "--host", "0.0.0.0", "--port", "8765"]
