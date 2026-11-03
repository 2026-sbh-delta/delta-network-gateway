#!/bin/bash
# Security tooling host bootstrap: Docker only. Containers are started by deploy/tooling/remote.sh via SSM.
set -euo pipefail
dnf install -y docker
systemctl enable --now docker
install -d -m 0750 /opt/delta /opt/delta/sast-state /opt/delta/dast-runs /opt/delta/dast-data /opt/delta/sources
# Container user (uid 10001) writes scan state and DAST evidence
chown -R 10001:10001 /opt/delta/sast-state /opt/delta/dast-runs /opt/delta/dast-data
