#!/usr/bin/env bash
set -euo pipefail

# Run on the dedicated VM as root. This installs files but does not start exporting.
test "$(id -u)" = 0
test "$(uname -m)" = x86_64
test -f /tmp/delta-onprem-host-collector.yaml
test -f /tmp/delta-onprem-app-collector.yaml
version=0.164.0
expected=f0c1ce9f661399beb4a58b711924716e08ea70c3802db0715fcfde3cbbada2d4
artifact="otelcol-contrib_${version}_linux_amd64.tar.gz"
install -d -m 0755 "/opt/delta-onprem/otel/${version}"
install -d -m 0750 /etc/delta-onprem/otel
if ! test -x "/opt/delta-onprem/otel/${version}/otelcol-contrib"; then
  staging=$(mktemp -d /opt/delta-onprem/otel/download.XXXXXX)
  trap 'rm -rf -- "$staging"' EXIT
  curl --fail --location --retry 2 --output "$staging/$artifact" \
    "https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/v${version}/$artifact"
  actual=$(sha256sum "$staging/$artifact")
  test "${actual%% *}" = "$expected"
  tar -xzf "$staging/$artifact" -C "$staging" otelcol-contrib
  install -m 0755 "$staging/otelcol-contrib" "/opt/delta-onprem/otel/${version}/otelcol-contrib"
fi
if ! id delta-otel >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin delta-otel
fi
install -o root -g delta-otel -m 0640 /tmp/delta-onprem-host-collector.yaml /etc/delta-onprem/otel/host.yaml
install -o root -g delta-otel -m 0640 /tmp/delta-onprem-app-collector.yaml /etc/delta-onprem/otel/app.yaml
chown root:delta-otel /etc/delta-onprem/otel
for component in host app; do
  cat > "/etc/systemd/system/delta-otel-${component}.service" <<EOF
[Unit]
Description=Delta on-premise ${component} telemetry
After=network-online.target
Wants=network-online.target
ConditionPathExists=/etc/delta-onprem/otel/${component}.env

[Service]
User=delta-otel
Group=delta-otel
EnvironmentFile=/etc/delta-onprem/otel/${component}.env
ExecStart=/opt/delta-onprem/otel/${version}/otelcol-contrib --config=/etc/delta-onprem/otel/${component}.yaml
Restart=on-failure
RestartSec=5
MemoryMax=128M
CPUQuota=20%
TasksMax=64
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
UMask=0077

[Install]
WantedBy=multi-user.target
EOF
done
systemctl daemon-reload
"/opt/delta-onprem/otel/${version}/otelcol-contrib" --version
echo 'Collector files installed. Services remain inactive until private TLS gateway is configured.'
