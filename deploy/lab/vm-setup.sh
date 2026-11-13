#!/usr/bin/env bash
# Inside the VM, as root, from the unpacked Artifact Buckets folder:  sudo ./deploy/lab/vm-setup.sh <lab host IP>
#   e.g. sudo ./deploy/lab/vm-setup.sh 192.168.1.143
# People use https://<lab host IP>:8443 (Caddy's own lab certificate; Claude Code signs in only over https).
# http://<lab host IP>:8090 only hands out the one-line Claude Code installer and the lab certificate,
# and sends everything else to https. Safe to run again: it updates an existing install in place.
# Makes a small ZFS pool on a sparse file (10 GB, takes space only as files arrive), puts the folder tree on it,
# starts the ZFS helper (automatic restore points) and the app.
set -euo pipefail
IP=${1:?give the lab host IP people reach, e.g. 192.168.1.143}
IP=${IP#http://}; IP=${IP#https://}; IP=${IP%%[:/]*}
URL=https://$IP:8443
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
  cat > .env <<ENV
AB_HOSTNAME=$IP
AB_TLS_MODE=internal
AB_SECRET_KEY=$(openssl rand -hex 32)
POSTGRES_PASSWORD=$(openssl rand -hex 24)
AB_POOL=/srv/buckets/tree
AB_VOLUMES=/srv/buckets/volumes
AB_STORAGE_MODE=direct
AB_ZFS_SOCKET=/run/buckets/zfs.sock
AB_BOOTSTRAP_ADMIN=admin
AB_BOOTSTRAP_PASSWORD=$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-20)
AB_SMB_ENABLED=false
ENV
  chmod 600 .env
fi
# Addresses (also updates an install made before the lab moved to https).
sed -i '/^LAB_IP=/d; /^AB_PUBLIC_URL=/d; /^AB_INSTALL_URL=/d' .env
printf 'LAB_IP=%s\nAB_PUBLIC_URL=%s\nAB_INSTALL_URL=http://%s:8090\n' "$IP" "$URL" "$IP" >> .env
# Server certificate: self-signed, not a CA (CA:FALSE), valid only for the lab address. Trusting it on a PC
# lets it vouch for this server and nothing else. Made again if the address changes.
mkdir -p /srv/buckets/tls
if ! openssl x509 -in /srv/buckets/tls/lab.crt -noout -ext subjectAltName 2>/dev/null | grep -qx "    IP Address:$IP"; then
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 825 \
    -keyout /srv/buckets/tls/lab.key -out /srv/buckets/tls/lab.crt -subj "/CN=Artifact Buckets lab ($IP)" \
    -addext "subjectAltName=IP:$IP" -addext "basicConstraints=critical,CA:FALSE" \
    -addext "keyUsage=critical,digitalSignature" -addext "extendedKeyUsage=serverAuth"
  chmod 600 /srv/buckets/tls/lab.key
fi
docker compose -f docker-compose.yml -f deploy/lab/compose.lab.yml up -d --build
echo
echo "Up at $URL"
echo "Admin sign-in: admin / $(grep ^AB_BOOTSTRAP_PASSWORD .env | cut -d= -f2)"
