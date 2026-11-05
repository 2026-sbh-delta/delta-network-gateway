# 동규님 platform/deploy/jenkins/controller 구성을 바탕으로 한다(JCasC, 설치 마법사 없음).
FROM amazon/aws-cli@sha256:0aa3ae3c685574d7adc27fb19a0e1f192aa07b1487c16bd3ad03253b6de521ae AS awscli
FROM gcr.io/google.com/cloudsdktool/google-cloud-cli@sha256:18d8c484aaef27d7ef7b54dfb018f73712503d254a16f0a76b2626b4f6003af2 AS gcloud
FROM jenkins/jenkins:2.580.2-lts-jdk21 AS runtime
USER root
# 배포 전용 실행기(내장 노드)가 쓰는 신뢰된 배포 스크립트용
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-cryptography openssh-client ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=awscli /usr/local/aws-cli /usr/local/aws-cli
COPY --from=gcloud /usr/lib/google-cloud-sdk /usr/lib/google-cloud-sdk
ENV PATH="/usr/local/aws-cli/v2/current/bin:/usr/lib/google-cloud-sdk/bin:${PATH}"
# 패키지 이름만 추가하지 않고 실제 실행 가능한 런타임을 빌드 중 확인한다.
RUN aws --version && gcloud --version && ssh -V \
    && python3 -c "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey; import sqlite3"

FROM runtime AS controller
USER jenkins
COPY controller/plugins.txt /usr/share/jenkins/ref/plugins.txt
RUN jenkins-plugin-cli --plugin-file /usr/share/jenkins/ref/plugins.txt
COPY controller/casc.yaml /opt/delta/casc.yaml
COPY controller/Jenkinsfile.build controller/Jenkinsfile.deploy controller/Jenkinsfile.release /opt/delta/
COPY controller/staging_deploy.py controller/release_deploy.py controller/release_transport.py controller/delivery_contract.py controller/compose_host.py /opt/delta/
ENV CASC_JENKINS_CONFIG=/opt/delta/casc.yaml
ENV JAVA_OPTS="-Djenkins.install.runSetupWizard=false -Xmx1g"
