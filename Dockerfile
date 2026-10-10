# Один образ на весь сервис: клиент собирается и раздаётся тем же процессом, что и API.
# Так снаружи нужен один адрес, а хостингу — одна служба.

FROM node:22-alpine AS web

WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
COPY apps/web ./
# Пустой адрес API: клиент обращается к тому же адресу, с которого открыт.
ENV VITE_API_URL=""
RUN npm run build

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN pip install --no-cache-dir uv==0.9.7

WORKDIR /app

COPY services/api/pyproject.toml services/api/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY services/api/app ./app
COPY --from=web /web/dist ./web
COPY data/demo_plan.json ./samples/demo_plan.json
COPY data/network ./samples/network

ENV PATH="/app/.venv/bin:$PATH" \
    OPS_TWIN_DB=/app/storage/plans.sqlite \
    OPS_TWIN_SAMPLE=/app/samples/demo_plan.json \
    OPS_TWIN_WEB=/app/web \
    PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=5 \
    CMD python -c "import os,urllib.request;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health')"

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
