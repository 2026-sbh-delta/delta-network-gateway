# 스테이징 점검 대상 게시판(의도적으로 취약). 운영에 배포하지 않는다.
FROM python:3.12-slim-bookworm
RUN pip install --no-cache-dir "flask>=3,<4" "gunicorn>=23,<24"
WORKDIR /srv
COPY app ./app
COPY wsgi.py ./
RUN useradd --uid 10001 app
USER app
EXPOSE 8080
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "wsgi:app"]
