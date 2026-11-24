#!/usr/bin/env bash
# Package the Packer output (qcow2) into an OVA for ESXi 6.5+.
#
# Usage: ova/build-ova.sh <system.qcow2> <output-dir> [version]
# Env:   DATA_DISK_GB (default 50), CPUS (2), MEMORY_MB (4096), VM_NAME (docker-cluster)
set -euo pipefail

SRC=${1:?usage: build-ova.sh <system.qcow2> <output-dir> [version]}
OUT=${2:?usage: build-ova.sh <system.qcow2> <output-dir> [version]}
VERSION=${3:-dev}
DATA_DISK_GB=${DATA_DISK_GB:-50}
CPUS=${CPUS:-2}
MEMORY_MB=${MEMORY_MB:-4096}
VM_NAME=${VM_NAME:-docker-cluster}

HERE=$(cd "$(dirname "$0")" && pwd)
BASE="docker-cluster-${VERSION}"
WORK="${OUT}/${BASE}"
SYSTEM_VMDK="${BASE}-system.vmdk"
DATA_VMDK="${BASE}-data.vmdk"

rm -rf "$WORK"
mkdir -p "$WORK"

# ESXi only imports streamOptimized VMDKs whose header says version 3.
# Recent qemu-img already writes 3; older releases wrote 1, so force it.
to_stream_vmdk() {
  qemu-img convert -O vmdk -o subformat=streamOptimized,adapter_type=lsilogic,compat6 "$1" "$2"
  printf '\x03' | dd of="$2" bs=1 seek=4 count=1 conv=notrunc status=none
}

echo "==> System disk"
to_stream_vmdk "$SRC" "$WORK/$SYSTEM_VMDK"
SYSTEM_DISK_BYTES=$(qemu-img info --output=json "$SRC" | python3 -c 'import json,sys;print(json.load(sys.stdin)["virtual-size"])')
SYSTEM_DISK_GB=$(( (SYSTEM_DISK_BYTES + 1073741823) / 1073741824 ))

echo "==> Data disk (${DATA_DISK_GB} GiB, empty)"
qemu-img create -q -f qcow2 "$WORK/data.qcow2" "${DATA_DISK_GB}G"
to_stream_vmdk "$WORK/data.qcow2" "$WORK/$DATA_VMDK"
rm -f "$WORK/data.qcow2"

# Two OVAs share the same disks:
#  - docker-cluster-<v>.ova            asks for the settings at deployment (OVF properties)
#  - docker-cluster-<v>-noprops.ova    no OVF properties: settings are entered on the
#                                      VM console. For old ESXi Host Clients that fail
#                                      on the properties page.
package() {
  local name=$1 with_props=$2
  local ovf="$WORK/${name}.ovf" transport=""
  echo "==> ${name}.ova"
  [ "$with_props" = yes ] && transport=' ovf:transport="com.vmware.guestInfo"'
  sed -e "s|@VERSION@|${VERSION}|g" \
      -e "s|@VM_NAME@|${VM_NAME}|g" \
      -e "s|@CPUS@|${CPUS}|g" \
      -e "s|@MEMORY_MB@|${MEMORY_MB}|g" \
      -e "s|@SYSTEM_VMDK@|${SYSTEM_VMDK}|g" \
      -e "s|@SYSTEM_VMDK_SIZE@|$(stat -c %s "$WORK/$SYSTEM_VMDK")|g" \
      -e "s|@SYSTEM_DISK_GB@|${SYSTEM_DISK_GB}|g" \
      -e "s|@DATA_VMDK@|${DATA_VMDK}|g" \
      -e "s|@DATA_VMDK_SIZE@|$(stat -c %s "$WORK/$DATA_VMDK")|g" \
      -e "s|@DATA_DISK_GB@|${DATA_DISK_GB}|g" \
      -e "s|@TRANSPORT@|${transport}|g" \
      "$HERE/appliance.ovf.in" > "$ovf"
  if [ "$with_props" != yes ]; then
    sed -i '/<!-- PROPERTIES-BEGIN -->/,/<!-- PROPERTIES-END -->/d' "$ovf"
  fi
  if grep -q '@[A-Z_]*@' "$ovf"; then
    echo "unfilled placeholder in $ovf" >&2
    exit 1
  fi
  python3 -c 'import sys,xml.dom.minidom as m; m.parse(sys.argv[1])' "$ovf"

  # SHA1 manifest: the format every ESXi 6.5 client accepts.
  (
    cd "$WORK"
    for f in "${name}.ovf" "$SYSTEM_VMDK" "$DATA_VMDK"; do
      echo "SHA1($f)= $(sha1sum "$f" | cut -d' ' -f1)"
    done
  ) > "$WORK/${name}.mf"

  # OVA = tar with the descriptor first, then the manifest, then the disks.
  tar -C "$WORK" --format=ustar -cf "${OUT}/${name}.ova" \
    "${name}.ovf" "${name}.mf" "$SYSTEM_VMDK" "$DATA_VMDK"
  (cd "$OUT" && sha256sum "${name}.ova" > "${name}.ova.sha256")
  echo "    ${OUT}/${name}.ova ($(du -h "${OUT}/${name}.ova" | cut -f1))"
}

package "${BASE}" yes
package "${BASE}-noprops" no
