# 빌드 컨텍스트는 services/ 다: docker build -f services/delta-diff/Dockerfile services
# 매핑 판단은 delta-ai 코드·knowledge 를 그대로 쓴다(editable 설치라 knowledge/ 를 소스 트리에서 찾는다).
FROM python:3.12-slim-bookworm

ENV UV_LINK_MODE=copy PYTHONUNBUFFERED=1

WORKDIR /src
RUN pip install --no-cache-dir "uv==0.13.6"
COPY delta-ai/pyproject.toml delta-ai/uv.lock ./delta-ai/
COPY delta-ai/delta_ai ./delta-ai/delta_ai
COPY delta-ai/knowledge ./delta-ai/knowledge
COPY delta-diff/pyproject.toml delta-diff/uv.lock ./delta-diff/
COPY delta-diff/delta_diff ./delta-diff/delta_diff

WORKDIR /src/delta-diff
RUN uv sync --frozen --no-dev --no-cache

RUN useradd --create-home --uid 10001 delta && mkdir -p var && chown delta:delta var
USER delta
VOLUME /src/delta-diff/var
EXPOSE 8091

HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
    CMD ["/src/delta-diff/.venv/bin/python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8091/healthz', timeout=2)"]
CMD ["/src/delta-diff/.venv/bin/uvicorn", "delta_diff.api:app", "--host", "0.0.0.0", "--port", "8091"]
