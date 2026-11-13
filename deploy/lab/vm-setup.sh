#!/usr/bin/env bash
# Inside the VM, as root, from the unpacked Artifact Buckets folder:  sudo ./deploy/lab/vm-setup.sh <public-url>
#   e.g. sudo ./deploy/lab/vm-setup.sh http://192.168.1.143:8090
# Makes a small ZFS pool on a sparse file (10 GB, takes space only as files arrive), puts the folder tree on it,
# starts the ZFS helper (automatic restore points) and the app.
set -euo pipefail
URL=${1:?give the URL people will use, e.g. http://192.168.1.143:8090}
HERE=$(cd "$(dirname "$0")/../.." && pwd)
cd "$HERE"
until [ -e /var/lib/cloud/instance/buckets-ready ]; do echo "waiting for first-boot setup"; sleep 5; done

modprobe zfs
mkdir -p /var/lib/buckets/vdevs
if ! zpool list buckets >/dev/null 2>&1; then
  truncate -s 10G /var/lib/buckets/vdevs/disk0.img
  zpool create -o ashift=12 -O compression=lz4 -O atime=off -O xattr=sa -O acltype=posixacl \
    -O mountpoint=none buckets /var/lib/buckets/vdevs/disk0.img
  zfs create -o mountpoint=/srv/buckets/tree -o snapdir=hidden buckets/tree
fi
# The pool lives on a file, so import it from there at boot.
cat > /etc/systemd/system/buckets-pool.service <<UNIT
[Unit]
Description=Import the Artifact Buckets ZFS pool (file-backed)
DefaultDependencies=no
Before=zfs-mount.service docker.service
After=systemd-modules-load.service local-fs.target
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c 'zpool list buckets >/dev/null 2>&1 || zpool import -d /var/lib/buckets/vdevs buckets'
[Install]
WantedBy=zfs-mount.service multi-user.target
UNIT

groupadd -f -g 1500 buckets-zfs
install -m 0755 deploy/zfs/bucketsd.py /usr/local/sbin/bucketsd
cat > /etc/systemd/system/bucketsd.service <<UNIT
[Unit]
Description=Artifact Buckets ZFS helper (restore points, health, backups)
After=buckets-pool.service zfs-mount.service
Requires=buckets-pool.service
[Service]
ExecStart=/usr/bin/python3 /usr/local/sbin/bucketsd --pool buckets --dataset buckets/tree --socket /run/buckets/zfs.sock --group 1500
Restart=on-failure
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now buckets-pool.service bucketsd.service

mkdir -p /srv/buckets/volumes
if [ ! -f .env ]; then
  host=$(echo "$URL" | sed -E 's#^https?://##; s#[:/].*$##')
  cat > .env <<ENV
AB_HOSTNAME=$host
AB_TLS_MODE=internal
AB_SECRET_KEY=$(openssl rand -hex 32)
POSTGRES_PASSWORD=$(openssl rand -hex 24)
AB_POOL=/srv/buckets/tree
AB_VOLUMES=/srv/buckets/volumes
AB_STORAGE_MODE=direct
AB_ZFS_SOCKET=/run/buckets/zfs.sock
AB_BOOTSTRAP_ADMIN=admin
AB_BOOTSTRAP_PASSWORD=$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-20)
AB_PUBLIC_URL=$URL
AB_SMB_ENABLED=false
ENV
  chmod 600 .env
fi
docker compose -f docker-compose.yml -f deploy/lab/compose.lab.yml up -d --build
echo
echo "Up at $URL"
echo "Admin sign-in: admin / $(grep ^AB_BOOTSTRAP_PASSWORD .env | cut -d= -f2)"
