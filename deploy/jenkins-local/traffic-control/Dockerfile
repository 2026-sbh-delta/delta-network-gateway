# 빌드 문맥은 deploy/jenkins-local 이다. HAProxy 제어 코드는 파이프라인과 같은 파일을 쓴다
FROM python:3.12-alpine
WORKDIR /app
COPY pipeline/scripts/haproxy_ctl.py traffic-control/control.py traffic-control/server.py ./
COPY traffic-control/static ./static
USER nobody
EXPOSE 8080
HEALTHCHECK --interval=5s --timeout=3s CMD wget -qO- http://127.0.0.1:8080/healthz >/dev/null 2>&1 || exit 1
CMD ["python", "-u", "server.py"]
