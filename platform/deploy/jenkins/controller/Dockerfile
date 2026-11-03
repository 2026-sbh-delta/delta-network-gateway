FROM jenkins/jenkins:2.580.2-lts-jdk21
COPY controller/plugins.txt /usr/share/jenkins/ref/plugins.txt
RUN jenkins-plugin-cli --plugin-file /usr/share/jenkins/ref/plugins.txt
COPY controller/casc.yaml /opt/delta/casc.yaml
COPY pipeline/Jenkinsfile /opt/delta/Jenkinsfile
ENV CASC_JENKINS_CONFIG=/opt/delta/casc.yaml
ENV JAVA_OPTS="-Djenkins.install.runSetupWizard=false -Xmx1g"
