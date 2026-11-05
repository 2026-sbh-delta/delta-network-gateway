# 빌드 경로: services/ (delta-pipeline 을 함께 넣는다)
FROM python:3.13-slim
WORKDIR /app
COPY delta-pipeline/pyproject.toml delta-pipeline/README.md ./delta-pipeline/
COPY delta-pipeline/delta_pipeline ./delta-pipeline/delta_pipeline
COPY delta-orchestrator/pyproject.toml ./delta-orchestrator/
COPY delta-orchestrator/delta_orchestrator ./delta-orchestrator/delta_orchestrator
RUN pip install --no-cache-dir ./delta-pipeline ./delta-orchestrator && useradd --uid 10001 --create-home app
USER app
ENV PYTHONUNBUFFERED=1
CMD ["uvicorn", "delta_orchestrator.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--log-level", "warning"]
