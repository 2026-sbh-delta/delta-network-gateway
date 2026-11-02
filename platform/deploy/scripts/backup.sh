#!/bin/bash
set -euo pipefail
umask 077
source /etc/delta-platform/backup.env
export AWS_DEFAULT_REGION
mountpoint -q /srv/delta-platform
mkdir -p /srv/delta-platform/backups
file=$(mktemp /srv/delta-platform/backups/database-XXXXXXXX.dump)
trap 'rm -f "$file"' EXIT
/usr/bin/docker compose --env-file /etc/delta-platform/deploy.env -f /opt/delta-platform/current/compose.yaml exec -T db pg_dump -U platform -d platform -Fc > "$file"
key="database/$(date -u +%Y/%m/%d/%H%M%S)-$(basename "$file")"
aws s3 cp "$file" "s3://$PLATFORM_BACKUP_BUCKET/$key" --sse AES256 --checksum-algorithm SHA256 --only-show-errors
printf 'Backup stored: %s\n' "$key"
