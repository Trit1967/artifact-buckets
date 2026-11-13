# Lab prototype deploy (one small VM)

Everything runs inside one new VM on the lab host. Nothing else on the host changes.

| | |
|---|---|
| VM | `artifact-buckets`, Ubuntu 24.04 cloud image, 2 vCPU, 3 GB RAM, 20 GB thin disk (about 3 GB used) |
| Storage | ZFS pool `buckets` on a 10 GB sparse file inside the VM; folder tree on `buckets/tree` |
| Restore points | `bucketsd` takes them on its own: hourly (keep 24), daily (14), weekly (8), monthly (6) |
| Web and Claude Code | `http://<lab host>:8090`, forwarded by a small socat container on the host to the VM's port 80 |
| Not yet | Windows share (needs a domain), Entra sign-in, TLS |

## Steps

On the lab host, as the normal user (no sudo):

```bash
mkdir -p ~/artifact-buckets && tar -xzf ab-lab.tar.gz -C ~/artifact-buckets && cd ~/artifact-buckets
./deploy/lab/vm-create.sh ~/.ssh/id_ed25519.pub          # prints the VM's address
VM=192.168.122.xx
scp ab-lab.tar.gz ubuntu@$VM:
ssh ubuntu@$VM 'mkdir -p ab && tar -xzf ab-lab.tar.gz -C ab && cd ab && sudo ./deploy/lab/vm-setup.sh http://<lab host IP>:8090'
# Publish the port through Docker (as other lab apps do) so the host firewall lets the LAN in.
docker run -d --name artifact-buckets-forward --restart unless-stopped -p 8090:8090 \
  alpine/socat tcp-listen:8090,fork,reuseaddr tcp:$VM:80
```

The setup script prints the admin password. Demo data (optional):
`ssh ubuntu@$VM 'cd ab && sudo docker compose exec app python3 scripts/seed_demo.py http://127.0.0.1:8080 <admin password>'`

## Undo everything

```bash
docker rm -f artifact-buckets-forward
virsh destroy artifact-buckets; virsh undefine artifact-buckets --remove-all-storage
virsh vol-delete --pool default ab-noble-base.img
rm -rf ~/artifact-buckets ~/.ssh/artifact-buckets
docker rmi alpine/socat     # optional
```
