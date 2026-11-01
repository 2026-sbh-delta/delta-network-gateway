FROM jenkins/jenkins:2.580.2-lts-jdk21

USER root
ARG DOCKER_CLI_VERSION=28.4.0
ARG SEMGREP_VERSION=1.97.0
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && arch="$(uname -m)" \
    && curl -fsSL "https://download.docker.com/linux/static/stable/${arch}/docker-${DOCKER_CLI_VERSION}.tgz" \
       | tar -xz --strip-components=1 -C /usr/local/bin docker/docker \
    && python3 -m venv /opt/semgrep \
    && /opt/semgrep/bin/pip install --no-cache-dir "semgrep==${SEMGREP_VERSION}" "setuptools<81" \
    && ln -s /opt/semgrep/bin/semgrep /usr/local/bin/semgrep

COPY plugins.txt /usr/share/jenkins/ref/plugins.txt
RUN jenkins-plugin-cli --plugin-file /usr/share/jenkins/ref/plugins.txt
USER jenkins
