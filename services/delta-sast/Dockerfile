# SAST 상시 서비스 이미지. 서버에서는 호스트 네트워크로 127.0.0.1:8790에만 연다.
FROM python:3.12-slim-bookworm

ARG TARGETARCH
ARG OPENGREP_VERSION=1.33.1
ARG OPENGREP_SHA256_ARM64=d1433f63a90b8bc0b6ad4b498a13b372b856ce9a754a43484ba4920fe2d76dcf
ARG OPENGREP_SHA256_AMD64=46e8133cc490f9f50518061590ce2f6be5a08afaeda034471c0ceda4b3899808

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl git \
 && rm -rf /var/lib/apt/lists/*

# Opengrep 공식 릴리스 바이너리를 받아 검증값을 확인한다
RUN set -eux; \
    case "$TARGETARCH" in \
      arm64) asset=opengrep_manylinux_aarch64; sum="$OPENGREP_SHA256_ARM64" ;; \
      amd64) asset=opengrep_manylinux_x86; sum="$OPENGREP_SHA256_AMD64" ;; \
      *) echo "지원하지 않는 아키텍처: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /usr/local/bin/opengrep "https://github.com/opengrep/opengrep/releases/download/v${OPENGREP_VERSION}/${asset}"; \
    echo "${sum}  /usr/local/bin/opengrep" | sha256sum -c -; \
    chmod 0755 /usr/local/bin/opengrep; \
    opengrep --version

WORKDIR /app
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir $(python -c "import tomllib; print(' '.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))")
COPY delta_sast ./delta_sast

RUN useradd --create-home --uid 10001 app && mkdir -p /var/lib/delta-sast && chown app /var/lib/delta-sast
USER app
ENV DELTA_SAST_STATE=/var/lib/delta-sast PYTHONUNBUFFERED=1

CMD ["uvicorn", "delta_sast.api:app", "--host", "127.0.0.1", "--port", "8790", "--log-level", "warning"]
