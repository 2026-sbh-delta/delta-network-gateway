#!/bin/bash
set -euo pipefail
for attempt in $(seq 1 60); do
  if dnf install -y iptables-services; then break; fi
  sleep 10
done
command -v iptables
printf 'net.ipv4.ip_forward=1\n' > /etc/sysctl.d/90-platform-nat.conf
sysctl --system
interface=$(ip -4 route show default | awk '{print $5; exit}')
iptables -t nat -C POSTROUTING -o "$interface" -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -o "$interface" -j MASQUERADE
iptables -P FORWARD ACCEPT
service iptables save
systemctl enable iptables
systemctl enable --now amazon-ssm-agent
