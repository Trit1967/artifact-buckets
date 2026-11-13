#!/usr/bin/env bash
# Create the Artifact Buckets VM on the lab host. Run on the host as a user who can use virsh (no sudo).
# Thin disks: the 20 GB OS disk is a copy-on-write layer over the Ubuntu cloud image, so it only takes
# the space actually written (about 3 GB after setup).
#
#   ./vm-create.sh ~/.ssh/id_ed25519.pub
#
# Undo: virsh destroy artifact-buckets; virsh undefine artifact-buckets --remove-all-storage
#       virsh vol-delete --pool default ab-noble-base.img
set -euo pipefail
NAME=artifact-buckets
POOL=default
KEY=$(cat "${1:?give the path of an SSH public key}")
BASE=ab-noble-base.img
URL=https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img
HERE=$(cd "$(dirname "$0")" && pwd)
export LIBVIRT_DEFAULT_URI=${LIBVIRT_DEFAULT_URI:-qemu:///system}

if virsh dominfo "$NAME" >/dev/null 2>&1; then echo "$NAME already exists"; exit 1; fi

if ! virsh vol-info --pool "$POOL" "$BASE" >/dev/null 2>&1; then
  tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
  curl -fL --retry 3 -o "$tmp/base.img" "$URL"
  size=$(stat -c %s "$tmp/base.img")
  virsh vol-create-as "$POOL" "$BASE" "$size" --format qcow2
  virsh vol-upload --pool "$POOL" "$BASE" "$tmp/base.img"
fi
basepath=$(virsh vol-path --pool "$POOL" "$BASE")
virsh vol-create-as "$POOL" "$NAME.qcow2" 20G --format qcow2 --backing-vol "$basepath" --backing-vol-format qcow2

ud=$(mktemp); sed "s|SSH_KEY|$KEY|" "$HERE/user-data.yaml" > "$ud"
virt-install --name "$NAME" --memory 3072 --vcpus 2 --cpu host-passthrough \
  --disk vol="$POOL/$NAME.qcow2",bus=virtio \
  --os-variant ubuntu24.04 --network network=default,model=virtio \
  --cloud-init user-data="$ud" --graphics none --noautoconsole --import
rm -f "$ud"
echo "Waiting for an address..."
for i in $(seq 1 60); do
  ip=$(virsh domifaddr "$NAME" 2>/dev/null | awk '/ipv4/ {split($4,a,"/"); print a[1]; exit}')
  [ -n "${ip:-}" ] && break; sleep 5
done
echo "VM $NAME is at ${ip:-unknown}"
