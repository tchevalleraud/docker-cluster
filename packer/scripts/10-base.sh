#!/usr/bin/env bash
# Base system: updates, VMware guest support, small set of admin tools.
set -euxo pipefail

apt-get update
apt-get -y full-upgrade

# The appliance does not use snaps.
apt-get -y purge snapd || true
rm -rf /snap /var/snap /var/lib/snapd /root/snap

apt-get -y install --no-install-recommends \
  open-vm-tools \
  ca-certificates curl gnupg \
  less nano \
  openssh-server \
  netplan.io \
  systemd-timesyncd

# Make sure the ESXi drivers (paravirtual SCSI, vmxnet3) are in the initramfs:
# the image is built on QEMU/virtio but boots on ESXi.
cat > /etc/initramfs-tools/conf.d/appliance.conf <<'CONF'
MODULES=most
CONF
for m in vmw_pvscsi mptspi vmxnet3; do
  grep -qx "$m" /etc/initramfs-tools/modules || echo "$m" >> /etc/initramfs-tools/modules
done
update-initramfs -u -k all

# Boot console on the VM screen (tty1), no serial-only output, short menu.
rm -f /etc/default/grub.d/40-force-partuuid.cfg
install -m 0644 /tmp/rootfs/etc/default/grub.d/99-appliance.cfg /etc/default/grub.d/99-appliance.cfg
update-grub
