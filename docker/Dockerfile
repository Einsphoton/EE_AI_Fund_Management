# ---------- Stage 1: build frontend ----------
FROM node:20-alpine AS web-builder
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- Stage 2: backend runtime ----------
FROM python:3.11-slim AS runtime
ARG APP_VERSION=local
ARG VCS_REF=local
ARG BUILD_DATE=unknown
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai \
    DATA_DIR=/app/data \
    SKILLS_DIR=/app/skills_installed \
    STATIC_DIR=/app/static \
    APP_VERSION=${APP_VERSION} \
    VCS_REF=${VCS_REF} \
    BUILD_DATE=${BUILD_DATE}
LABEL org.opencontainers.image.title="EE AI Fund Management" \
      org.opencontainers.image.version=${APP_VERSION} \
      org.opencontainers.image.revision=${VCS_REF} \
      org.opencontainers.image.created=${BUILD_DATE}


WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends tzdata wget ca-certificates && \
    ln -sf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone && \
    rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

COPY backend/ /app/

# 拷贝前端构建产物
COPY --from=web-builder /web/dist /app/static

RUN mkdir -p /app/data /app/skills_installed

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD wget -q --spider http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
