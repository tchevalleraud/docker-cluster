#!/usr/bin/env bash
# Appliance layer: admin account, first-boot configuration, VM console.
set -euxo pipefail

# Local administrator. Password comes from the OVF properties on first boot.
if ! id admin >/dev/null 2>&1; then
  # Ubuntu already ships an "admin" group (legacy sudo group): reuse it.
  useradd --create-home --shell /bin/bash --gid admin --groups sudo,docker admin
fi
passwd -l admin

install -d -m 0755 /usr/local/lib/dc-appliance /etc/dc-appliance /var/lib/dc-appliance
install -m 0644 /tmp/rootfs/usr/local/lib/dc-appliance/appliance.py /usr/local/lib/dc-appliance/appliance.py
install -m 0755 /tmp/rootfs/usr/local/sbin/dc-appliance /usr/local/sbin/dc-appliance
echo "${APPLIANCE_VERSION}" > /etc/dc-appliance/version

install -m 0644 /tmp/rootfs/etc/systemd/system/dc-firstboot.service /etc/systemd/system/dc-firstboot.service
install -d /etc/systemd/system/getty@tty1.service.d
install -m 0644 /tmp/rootfs/etc/systemd/system/getty@tty1.service.d/dc-console.conf \
  /etc/systemd/system/getty@tty1.service.d/dc-console.conf
install -m 0644 /tmp/rootfs/etc/issue /etc/issue
install -d /etc/systemd/system-preset
install -m 0644 /tmp/rootfs/etc/systemd/system-preset/10-appliance.preset \
  /etc/systemd/system-preset/10-appliance.preset
systemctl enable dc-firstboot.service

# SSH is off until enabled through the OVF properties or the console.
systemctl disable ssh.service ssh.socket || true
