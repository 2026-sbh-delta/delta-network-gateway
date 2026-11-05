FROM python:3.13-slim
RUN useradd --uid 10001 --create-home app
COPY server.py /app/server.py
USER app
CMD ["python", "/app/server.py"]
