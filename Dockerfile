FROM node:22-alpine AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_RUNTIME_DATABASE_PATH=/data/runtime-sessions.sqlite3 \
    APP_FRONTEND_DIST_PATH=/app/frontend/dist \
    APP_MAX_REQUEST_BODY_BYTES=2097152 \
    PORT=8000

WORKDIR /app

RUN groupadd --gid 10001 jointassurance \
    && useradd --uid 10001 --gid jointassurance --no-create-home --shell /usr/sbin/nologin jointassurance \
    && mkdir -p /data /app/frontend/dist \
    && chown -R jointassurance:jointassurance /data /app

COPY backend/requirements-runtime.txt /tmp/backend-requirements.txt
RUN pip install --no-cache-dir --requirement /tmp/backend-requirements.txt \
    && rm /tmp/backend-requirements.txt

COPY --chown=jointassurance:jointassurance backend/ ./backend/
COPY --chown=jointassurance:jointassurance data/ ./data/
COPY --from=frontend-build --chown=jointassurance:jointassurance /build/frontend/dist/ ./frontend/dist/

USER jointassurance:jointassurance
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8000') + '/api/v1/health', timeout=3).read()"]

CMD ["python", "-m", "backend.app.production"]
