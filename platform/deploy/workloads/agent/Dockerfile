FROM hashicorp/terraform:1.16.4 AS terraform

FROM python:3.12-slim AS dependencies
WORKDIR /app/backend
RUN pip install --no-cache-dir uv==0.10.0
COPY platform/backend/pyproject.toml platform/backend/uv.lock ./
COPY platform/backend/src ./src
COPY services/delta-staging/pyproject.toml /services/delta-staging/pyproject.toml
COPY services/delta-staging/delta_staging /services/delta-staging/delta_staging
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends openjdk-21-jre-headless curl ca-certificates && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 10001 deployer && useradd -u 10001 -g deployer -m deployer
COPY --from=terraform /bin/terraform /usr/local/bin/terraform
COPY --from=dependencies /app/backend/.venv /app/backend/.venv
RUN mkdir -p /opt/delta && ln -s /app/backend/.venv /opt/delta/venv
COPY platform/deploy/workloads/*.py /opt/delta/workloads/
COPY platform/deploy/workloads/terraform/ /opt/delta/workloads/terraform/
COPY platform/deploy/databases/ /opt/delta/databases/
COPY platform/deploy/workloads/agent/start.sh /opt/delta/start.sh
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 AWS_DEFAULT_REGION=ap-northeast-2
USER 10001:10001
WORKDIR /home/deployer
ENTRYPOINT ["/bin/sh", "/opt/delta/start.sh"]
