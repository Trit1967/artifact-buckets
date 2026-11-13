# Lab prototype deploy (one small VM)

Everything runs inside one new VM on the lab host. Nothing else on the host changes.

| | |
|---|---|
| VM | `artifact-buckets`, Ubuntu 24.04 cloud image, 2 vCPU, 3 GB RAM, 20 GB thin disk (about 3 GB used) |
| Storage | ZFS pool `buckets` on a 10 GB sparse file inside the VM; folder tree on `buckets/tree` |
| Restore points | `bucketsd` takes them on its own: hourly (keep 24), daily (14), weekly (8), monthly (6) |
| Web and Claude Code | `https://<lab host>:8443` (Caddy's own lab certificate), forwarded by a small socat container on the host to the VM's port 443 |
| Installer | `http://<lab host>:8090` hands out only the one-line Claude Code installer and the lab certificate, and sends everything else to https |
| Not yet | Windows share (needs a domain), Entra sign-in, a certificate from a real CA |

## Steps

On the lab host, as the normal user (no sudo):

```bash
mkdir -p ~/artifact-buckets && tar -xzf ab-lab.tar.gz -C ~/artifact-buckets && cd ~/artifact-buckets
./deploy/lab/vm-create.sh ~/.ssh/id_ed25519.pub          # prints the VM's address
VM=192.168.122.xx
scp ab-lab.tar.gz ubuntu@$VM:
ssh ubuntu@$VM 'mkdir -p ab && tar -xzf ab-lab.tar.gz -C ab && cd ab && sudo ./deploy/lab/vm-setup.sh <lab host IP>'
# Publish the port through Docker (as other lab apps do) so the host firewall lets the LAN in.
docker run -d --name artifact-buckets-forward --restart unless-stopped -p 8090:8090 \
  alpine/socat tcp-listen:8090,fork,reuseaddr tcp:$VM:80
docker run -d --name artifact-buckets-forward-https --restart unless-stopped -p 8443:8443 \
  alpine/socat tcp-listen:8443,fork,reuseaddr tcp:$VM:443
```

Connect Claude Code on a Windows PC (PowerShell): `irm http://<lab host IP>:8090/claude/install.ps1 | iex`.
It asks Windows to trust the lab certificate once (choose Yes), installs the plugin and opens the browser to sign in.

## Update an existing VM

```bash
ssh ubuntu@$VM 'cd ab && git pull && sudo ./deploy/lab/vm-setup.sh <lab host IP>'
```
(or copy a new tarball over the folder first). The script keeps the existing secrets and data.

The setup script prints the admin password. Demo data (optional):
`ssh ubuntu@$VM 'cd ab && sudo docker compose exec app python3 scripts/seed_demo.py http://127.0.0.1:8080 <admin password>'`

## Undo everything

```bash
docker rm -f artifact-buckets-forward artifact-buckets-forward-https
virsh destroy artifact-buckets; virsh undefine artifact-buckets --remove-all-storage
virsh vol-delete --pool default ab-noble-base.img
rm -rf ~/artifact-buckets ~/.ssh/artifact-buckets
docker rmi alpine/socat     # optional
```
