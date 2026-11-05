# DAST 상시 서비스 이미지. 헤드리스 Chromium이 찍은 화면을 작업실(127.0.0.1:8765)로 보낸다.
FROM python:3.12-slim-bookworm

# 한글 화면·보고서가 깨지지 않게 CJK 폰트를 넣는다
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates git fonts-noto-cjk \
 && rm -rf /var/lib/apt/lists/*

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright PYTHONUNBUFFERED=1
WORKDIR /app
COPY . .
# 패키지로 설치하지 않고 소스 위치(/app)에서 실행한다. examples·runs·data가 /app 기준이다
RUN pip install --no-cache-dir $(python -c "import tomllib; print(' '.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))") \
 && python -m playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 app \
 && mkdir -p /app/runs /app/data \
 && chown -R app /app/runs /app/data
USER app

CMD ["python", "-m", "dast_agent", "serve", "--port", "8765", "--target-port", "8766"]
