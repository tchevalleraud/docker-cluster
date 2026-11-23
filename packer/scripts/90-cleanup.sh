#!/usr/bin/env bash
# Make the image generic: every deployed VM must get its own identity.
set -euxo pipefail

# cloud-init only served the build; the appliance configures itself.
# Keep netplan: autoremove would otherwise drop it along with cloud-init.
apt-mark manual netplan.io
apt-get -y purge cloud-init
rm -rf /etc/cloud /var/lib/cloud
rm -f /etc/netplan/*.yaml
install -m 0600 /tmp/rootfs/etc/netplan/00-fallback.yaml /etc/netplan/00-fallback.yaml

# Build-only CA (see 00-build-ca.sh).
if [ -f /usr/local/share/ca-certificates/dc-build-ca.crt ]; then
  rm -f /usr/local/share/ca-certificates/dc-build-ca.crt
  update-ca-certificates --fresh
fi

apt-get -y autoremove --purge
apt-get clean
rm -rf /var/lib/apt/lists/*

# Docker identity and state created while building.
systemctl stop docker.service docker.socket containerd.service || true
rm -f /var/lib/docker/engine-id
rm -rf /var/lib/docker/network /var/lib/docker/swarm

# Per-VM identity.
echo docker-node > /etc/hostname
printf '127.0.0.1 localhost\n127.0.1.1 docker-node\n\n::1 localhost ip6-localhost ip6-loopback\nff02::1 ip6-allnodes\nff02::2 ip6-allrouters\n' > /etc/hosts
rm -f /etc/ssh/ssh_host_*
truncate -s 0 /etc/machine-id
rm -f /var/lib/dbus/machine-id
ln -s /etc/machine-id /var/lib/dbus/machine-id
rm -f /var/lib/systemd/random-seed
rm -rf /var/lib/dc-appliance/*

# Logs, temp files, shell history.
journalctl --rotate || true
journalctl --vacuum-time=1s || true
find /var/log -type f -exec truncate -s 0 {} +
rm -rf /tmp/* /var/tmp/* /root/.bash_history

# Zero free space so the VMDK compresses well.
dd if=/dev/zero of=/zero bs=1M status=none || true
rm -f /zero
sync
