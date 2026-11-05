# 동규님 platform/deploy/jenkins/agent 구성을 바탕으로 한다. 에이전트는 자기 Docker 데몬을 쓴다(호스트 docker.sock 미사용).
FROM docker:28.5.2-dind
RUN apk add --no-cache openjdk21-jre-headless python3 py3-pip aws-cli git curl iptables skopeo \
 && pip install --no-cache-dir --break-system-packages "google-auth[requests]>=2.30,<3"
COPY agent/start.sh /opt/delta/start.sh
COPY agent/ar-token.py /opt/delta/ar-token.py
ENTRYPOINT ["sh", "/opt/delta/start.sh"]
