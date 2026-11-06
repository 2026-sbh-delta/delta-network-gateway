# 동규님 platform/deploy/jenkins/controller 구성을 바탕으로 한다(JCasC, 설치 마법사 없음).
FROM amazon/aws-cli@sha256:0aa3ae3c685574d7adc27fb19a0e1f192aa07b1487c16bd3ad03253b6de521ae AS awscli
FROM gcr.io/google.com/cloudsdktool/google-cloud-cli@sha256:18d8c484aaef27d7ef7b54dfb018f73712503d254a16f0a76b2626b4f6003af2 AS gcloud
FROM hashicorp/terraform:1.16.4 AS terraform

# 운영 배포기(deploy/platform/release)는 이 빌드 컨텍스트(deploy/platform/jenkins) 밖에 있어 이름 있는 컨텍스트로 받는다:
#   docker buildx build --build-context release=deploy/platform/release -f controller/Dockerfile deploy/platform/jenkins
# 지정하지 않으면 COPY --from=release가 해석되지 않아 빌드가 실패한다(배포기 없는 이미지를 만들지 않는다).

FROM jenkins/jenkins:2.580.2-lts-jdk21 AS runtime
USER root
ARG SSM_PLUGIN_VERSION=1.2.707.0
# 배포 전용 실행기(내장 노드)가 쓰는 신뢰된 배포 스크립트용
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-cryptography python3-venv openssh-client ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=awscli /usr/local/aws-cli /usr/local/aws-cli
COPY --from=gcloud /usr/lib/google-cloud-sdk /usr/lib/google-cloud-sdk
COPY --from=terraform /bin/terraform /usr/local/bin/terraform
# AWS 대상 DB 준비용 SSM 포트 포워딩(aws ssm start-session)에 필요하다. 버전 고정 경로만 받는다.
RUN curl -fsSLo /tmp/session-manager-plugin.deb \
      "https://s3.amazonaws.com/session-manager-downloads/plugin/${SSM_PLUGIN_VERSION}/ubuntu_64bit/session-manager-plugin.deb" \
    && dpkg -i /tmp/session-manager-plugin.deb && rm -f /tmp/session-manager-plugin.deb
ENV PATH="/usr/local/aws-cli/v2/current/bin:/usr/lib/google-cloud-sdk/bin:${PATH}"
# 배포기 Python 의존(boto3·psycopg·google-auth)은 시스템 Python과 분리한 venv에 고정 버전으로 둔다.
COPY --from=release requirements.txt /opt/delta/release-requirements.txt
RUN python3 -m venv /opt/delta/venv \
    && /opt/delta/venv/bin/pip install --no-cache-dir -r /opt/delta/release-requirements.txt
# 패키지 이름만 추가하지 않고 실제 실행 가능한 런타임을 빌드 중 확인한다.
RUN aws --version && gcloud --version && ssh -V && terraform version && session-manager-plugin --version \
    && python3 -c "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey; import sqlite3" \
    && /opt/delta/venv/bin/python -c "import boto3, psycopg, google.auth, google.auth.transport.requests, requests"

FROM runtime AS controller
USER jenkins
COPY controller/plugins.txt /usr/share/jenkins/ref/plugins.txt
RUN jenkins-plugin-cli --plugin-file /usr/share/jenkins/ref/plugins.txt
COPY controller/casc.yaml /opt/delta/casc.yaml
COPY controller/Jenkinsfile.build controller/Jenkinsfile.deploy controller/Jenkinsfile.release /opt/delta/
COPY controller/staging_deploy.py controller/release_deploy.py controller/vm_release.py controller/delivery_contract.py /opt/delta/
# 동규님 방식 운영 배포기: 스크립트와 Terraform 모듈(잠금 파일 포함). 시험 파일은 넣지 않는다.
COPY --from=release *.py /opt/delta/release/
COPY --from=release terraform /opt/delta/release/terraform
ENV CASC_JENKINS_CONFIG=/opt/delta/casc.yaml
ENV JAVA_OPTS="-Djenkins.install.runSetupWizard=false -Xmx1g"
