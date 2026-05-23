# syntax=docker/dockerfile:1.6
# 多阶段构建：
#   stage 1 - 前端 Vue 3 + Vite 编译产物
#   stage 2 - Python 3.11 后端 + 把前端 dist copy 进来

# ========== Stage 1: Frontend ==========
FROM node:20-alpine AS frontend-builder

WORKDIR /build

COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --prefer-offline --no-audit --no-fund

COPY frontend/ ./
RUN npm run build


# ========== Stage 2: Backend ==========
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 系统级依赖（curl 用于 health check + libpq for psycopg fallback）
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        libpq5 \
 && rm -rf /var/lib/apt/lists/*

# 先装依赖，利用 docker layer cache
COPY requirements.txt .
RUN pip install -r requirements.txt \
 && pip install gunicorn

# 拷代码
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY scripts ./scripts

# 拷前端构建产物（来自 stage 1）
COPY --from=frontend-builder /build/dist ./frontend/dist

# 数据目录（SQLite 模式时挂载用；PG 模式时不使用）
RUN mkdir -p /app/data

# 非 root 用户跑
RUN groupadd -r app && useradd -r -g app -d /app -s /sbin/nologin app \
 && chown -R app:app /app
USER app

EXPOSE 8011

# 健康检查 — 给 Docker / docker compose 用
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8011/healthz || exit 1

# Gunicorn + Uvicorn worker × 4
CMD ["gunicorn", \
     "-k", "uvicorn.workers.UvicornWorker", \
     "-w", "4", \
     "-b", "0.0.0.0:8011", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--timeout", "180", \
     "--graceful-timeout", "30", \
     "--max-requests", "5000", \
     "--max-requests-jitter", "500", \
     "app.main:app"]
