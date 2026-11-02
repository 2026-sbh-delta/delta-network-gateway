#!/bin/bash
set -euo pipefail
umask 077
platform_image=$1
postgres_image=$2
region=$3
parameter=$4
bucket=$5
release_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
[[ "$platform_image" == *@sha256:* && "$postgres_image" == *@sha256:* ]]
[ -f /opt/delta-platform/bootstrap-ready ]
mountpoint -q /srv/delta-platform
if [ -f /etc/delta-platform/deploy.env ]; then
  cp /etc/delta-platform/deploy.env /etc/delta-platform/previous-deploy.env
fi
aws ssm get-parameter --region "$region" --name "$parameter" --with-decryption --query Parameter.Value --output text | python3 "$release_dir/scripts/write-runtime.py" "$platform_image" "$postgres_image" "$bucket" "$region"
registry="${platform_image%%/*}"
aws ecr get-login-password --region "$region" | docker login --username AWS --password-stdin "$registry"
ln -sfn "$release_dir" /opt/delta-platform/current
compose=(docker compose --env-file /etc/delta-platform/deploy.env -f /opt/delta-platform/current/compose.yaml)
"${compose[@]}" --profile migration pull
"${compose[@]}" up -d --wait db
"${compose[@]}" --profile migration run --rm migrate
cat > /etc/systemd/system/delta-platform.service <<'UNIT'
[Unit]
Description=Delta platform API, worker and database
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target
RequiresMountsFor=/srv/delta-platform
[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/delta-platform/current
ExecStart=/usr/bin/docker compose --env-file /etc/delta-platform/deploy.env -f /opt/delta-platform/current/compose.yaml up -d --wait
ExecStop=/usr/bin/docker compose --env-file /etc/delta-platform/deploy.env -f /opt/delta-platform/current/compose.yaml stop
TimeoutStartSec=300
TimeoutStopSec=120
[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/delta-platform-backup.service <<'UNIT'
[Unit]
Description=Delta platform database backup
After=delta-platform.service
RequiresMountsFor=/srv/delta-platform
[Service]
Type=oneshot
ExecStart=/bin/bash /opt/delta-platform/current/scripts/backup.sh
UNIT
cat > /etc/systemd/system/delta-platform-backup.timer <<'UNIT'
[Unit]
Description=Daily Delta platform database backup
[Timer]
OnCalendar=*-*-* 03:00:00 UTC
Persistent=true
RandomizedDelaySec=300
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable delta-platform.service delta-platform-backup.timer
systemctl restart delta-platform.service
systemctl start delta-platform-backup.timer
systemctl start delta-platform-backup.service
curl --fail --silent http://127.0.0.1:8088/health/ready
