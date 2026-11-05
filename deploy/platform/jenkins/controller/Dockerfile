# 동규님 platform/deploy/jenkins/controller 구성을 바탕으로 한다(JCasC, 설치 마법사 없음).
FROM jenkins/jenkins:2.580.2-lts-jdk21
USER root
# 배포 전용 실행기(내장 노드)가 쓰는 신뢰된 배포 스크립트용
RUN apt-get update && apt-get install -y --no-install-recommends python3 && rm -rf /var/lib/apt/lists/*
USER jenkins
COPY controller/plugins.txt /usr/share/jenkins/ref/plugins.txt
RUN jenkins-plugin-cli --plugin-file /usr/share/jenkins/ref/plugins.txt
COPY controller/casc.yaml /opt/delta/casc.yaml
COPY controller/Jenkinsfile.build controller/Jenkinsfile.deploy controller/staging_deploy.py /opt/delta/
ENV CASC_JENKINS_CONFIG=/opt/delta/casc.yaml
ENV JAVA_OPTS="-Djenkins.install.runSetupWizard=false -Xmx1g"
