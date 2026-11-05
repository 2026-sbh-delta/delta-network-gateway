#!/bin/sh
set -eu
mkdir -p /home/deployer/agent
curl --cacert /etc/delta/ca.crt --fail --silent --show-error "$JENKINS_URL/jnlpJars/agent.jar" -o /home/deployer/agent.jar
rm -f /home/deployer/controller-trust.p12
keytool -importcert -noprompt -alias controller -file /etc/delta/ca.crt \
  -keystore /home/deployer/controller-trust.p12 -storetype PKCS12 -storepass changeit >/dev/null 2>&1
exec java -Djavax.net.ssl.trustStore=/home/deployer/controller-trust.p12 \
  -Djavax.net.ssl.trustStorePassword=changeit -jar /home/deployer/agent.jar \
  -url "$JENKINS_URL" -name deployer -secret @/etc/delta/agent-secret \
  -webSocket -workDir /home/deployer/agent
