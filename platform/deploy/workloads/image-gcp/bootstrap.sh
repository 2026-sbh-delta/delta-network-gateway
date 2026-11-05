#!/bin/bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends docker.io python3 curl ca-certificates iptables
install -d /usr/local/lib/docker/cli-plugins
curl --fail --location --retry 3 https://github.com/docker/compose/releases/download/v5.7.0/docker-compose-linux-x86_64 -o /usr/local/lib/docker/cli-plugins/docker-compose
echo '7758048ad38e580f22559603312874fec8bb638d021a9e64182385fc17cc12a2  /usr/local/lib/docker/cli-plugins/docker-compose' | sha256sum --check
chmod 755 /usr/local/lib/docker/cli-plugins/docker-compose
systemctl enable docker
docker compose version
python3 --version
echo 'debian12-docker-compose-v1' > /etc/delta-runtime-image
apt-get clean
rm -f /etc/ssh/ssh_host_*
truncate -s 0 /etc/machine-id
sync
echo DELTA_RUNTIME_IMAGE_READY
