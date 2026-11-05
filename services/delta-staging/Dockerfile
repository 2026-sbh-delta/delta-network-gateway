FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY delta_staging ./delta_staging
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home app && mkdir -p /app/var && chown app /app/var
USER app
ENV PYTHONUNBUFFERED=1
CMD ["uvicorn", "delta_staging.api:app", "--host", "0.0.0.0", "--port", "8091", "--log-level", "warning"]
