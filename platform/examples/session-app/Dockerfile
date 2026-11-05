FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/app/.venv/bin:$PATH
WORKDIR /app
RUN pip install --no-cache-dir uv==0.10.0
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY . ./
RUN useradd --uid 10001 --create-home app && chmod 700 /home/app
USER 10001
EXPOSE 8080
CMD ["gunicorn", "demo.wsgi:application", "--bind=0.0.0.0:8080", "--workers=1", "--access-logfile=-", "--timeout=30"]
