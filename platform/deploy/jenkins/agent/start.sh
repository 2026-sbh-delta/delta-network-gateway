#!/bin/sh
set -eu
# This agent owns its Docker daemon. Never mount the controller/host docker.sock.
dockerd-entrypoint.sh > /tmp/dockerd.log 2>&1 &
daemon_pid=$!
trap 'kill "$daemon_pid" 2>/dev/null || true' EXIT INT TERM
for attempt in $(seq 1 60); do
  if docker info >/dev/null 2>&1; then break; fi
  sleep 1
done
docker info >/dev/null
mkdir -p /home/jenkins/agent
if [ -n "${JENKINS_CA_FILE:-}" ]; then
  curl --cacert "$JENKINS_CA_FILE" --fail --silent --show-error "$JENKINS_URL/jnlpJars/agent.jar" -o /tmp/agent.jar
  rm -f /tmp/jenkins-trust.p12
  keytool -importcert -noprompt -alias controller -file "$JENKINS_CA_FILE" \
    -keystore /tmp/jenkins-trust.p12 -storetype PKCS12 -storepass changeit >/dev/null 2>&1
  export JAVA_TOOL_OPTIONS="-Djavax.net.ssl.trustStore=/tmp/jenkins-trust.p12 -Djavax.net.ssl.trustStorePassword=changeit"
else
  curl --fail --silent --show-error "$JENKINS_URL/jnlpJars/agent.jar" -o /tmp/agent.jar
fi
java -jar /tmp/agent.jar -url "$JENKINS_URL" -name image-builder \
  -secret @/run/secrets/agent_secret -webSocket -workDir /home/jenkins/agent
