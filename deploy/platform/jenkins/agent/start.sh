#!/bin/sh
set -eu
dockerd-entrypoint.sh > /tmp/dockerd.log 2>&1 &
daemon_pid=$!
trap 'kill "$daemon_pid" 2>/dev/null || true' EXIT INT TERM
for _ in $(seq 1 60); do
  docker info >/dev/null 2>&1 && break
  sleep 1
done
docker info >/dev/null
mkdir -p /home/jenkins/agent
until curl --fail --silent --show-error "$JENKINS_URL/jnlpJars/agent.jar" -o /tmp/agent.jar; do sleep 5; done
exec java -jar /tmp/agent.jar -url "$JENKINS_URL" -name builder \
  -secret @/run/secrets/agent_secret -webSocket -workDir /home/jenkins/agent
