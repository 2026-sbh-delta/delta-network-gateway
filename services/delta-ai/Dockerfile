FROM python:3.12-slim-bookworm

ENV UV_LINK_MODE=copy PYTHONUNBUFFERED=1 PYTHONPATH=/app

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir "uv==0.13.6" \
    && uv sync --frozen --no-dev --no-install-project --no-cache
COPY delta_ai ./delta_ai
COPY knowledge ./knowledge

RUN useradd --create-home --uid 10001 delta && mkdir -p var && chown delta:delta var
USER delta
VOLUME /app/var
EXPOSE 8090

HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
    CMD ["/app/.venv/bin/python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8090/healthz', timeout=2)"]
CMD ["/app/.venv/bin/uvicorn", "delta_ai.api:app", "--host", "0.0.0.0", "--port", "8090"]
