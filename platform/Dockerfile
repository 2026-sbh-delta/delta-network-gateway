FROM node:24-alpine AS web
WORKDIR /web
COPY platform/web/package.json platform/web/package-lock.json ./
RUN npm ci
COPY platform/web/ ./
RUN npm run build

FROM python:3.12-slim AS python-build
WORKDIR /app/backend
RUN pip install --no-cache-dir uv==0.10.0
COPY platform/backend/pyproject.toml platform/backend/uv.lock ./
COPY platform/backend/src ./src
COPY services/delta-staging/pyproject.toml /services/delta-staging/pyproject.toml
COPY services/delta-staging/delta_staging /services/delta-staging/delta_staging
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/app/backend/.venv/bin:$PATH" PLATFORM_WEB_DIST=/app/web/dist
WORKDIR /app/backend
RUN groupadd -g 10001 platform && useradd -u 10001 -g platform -M platform
COPY --from=python-build /app/backend/.venv /app/backend/.venv
COPY --from=web /web/dist /app/web/dist
COPY platform/backend/alembic.ini ./
COPY platform/backend/migrations ./migrations
RUN mkdir -p /var/lib/delta-platform/tmp && chown 10001:10001 /var/lib/delta-platform/tmp
USER 10001:10001
EXPOSE 8088
CMD ["uvicorn", "delta_platform.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8088", "--no-proxy-headers", "--no-access-log"]
