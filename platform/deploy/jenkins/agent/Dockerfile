FROM docker:28.5.2-dind
RUN apk add --no-cache openjdk21-jre-headless python3 aws-cli git curl iptables
COPY builds/build.py /opt/delta/build_common.py
COPY jenkins/agent/build.py /opt/delta/jenkins-build.py
COPY jenkins/agent/start.sh /opt/delta/start.sh
ENTRYPOINT ["sh", "/opt/delta/start.sh"]
