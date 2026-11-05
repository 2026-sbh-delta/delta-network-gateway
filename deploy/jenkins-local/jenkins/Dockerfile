FROM jenkins/jenkins:2.580.2-lts-jdk21

USER root
ARG DOCKER_CLI_VERSION=28.4.0
ARG SEMGREP_VERSION=1.97.0
# 이전 파이프라인이 대상 CPU 구조(arm64 → amd64)로 빌드한다. 레거시 빌더는 다른 구조 빌드를 못 한다
ARG BUILDX_VERSION=0.17.1
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && arch="$(uname -m)" \
    && curl -fsSL "https://download.docker.com/linux/static/stable/${arch}/docker-${DOCKER_CLI_VERSION}.tgz" \
       | tar -xz --strip-components=1 -C /usr/local/bin docker/docker \
    && buildx_arch="$(case "$arch" in aarch64) echo arm64;; x86_64) echo amd64;; *) echo "$arch";; esac)" \
    && mkdir -p /usr/local/lib/docker/cli-plugins \
    && curl -fsSL -o /usr/local/lib/docker/cli-plugins/docker-buildx \
       "https://github.com/docker/buildx/releases/download/v${BUILDX_VERSION}/buildx-v${BUILDX_VERSION}.linux-${buildx_arch}" \
    && chmod 755 /usr/local/lib/docker/cli-plugins/docker-buildx \
    && python3 -m venv /opt/semgrep \
    && /opt/semgrep/bin/pip install --no-cache-dir "semgrep==${SEMGREP_VERSION}" "setuptools<81" \
    && ln -s /opt/semgrep/bin/semgrep /usr/local/bin/semgrep

COPY plugins.txt /usr/share/jenkins/ref/plugins.txt
RUN jenkins-plugin-cli --plugin-file /usr/share/jenkins/ref/plugins.txt
USER jenkins
