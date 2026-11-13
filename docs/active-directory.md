# Full deployment: Active Directory, Entra and the Windows share

The [one-line install](../README.md#quick-start) gives you local accounts, the web app and Claude Code. This guide adds
people and groups from Active Directory, browser sign-in with Entra ID, pooled disks and a domain-joined Windows
share, using `docker-compose.yml` and `.env.example` at the root of the repository.

## 1. Before you start

- A Linux host (Ubuntu 24.04 tested) with Docker Engine and the compose plugin, and the disks you want to pool.
- DNS: `buckets.example.com` → the host.
- A TLS certificate for that name from your own CA (or use Caddy's internal CA for a first try).
- In AD:
  - a read-only service account for LDAP (for example `svc-buckets`);
  - an account allowed to join computers, used once for the Samba join;
  - groups named `AB-<team>` (for example `AB-Platform`), plus `AB-Admins` and `AB-Auditors`.
- In Entra (optional, for browser single sign-on): an app registration.
  - Redirect URI: `https://buckets.example.com/auth/oidc/callback`.
  - A client secret.
  - Add the optional ID token claim `onprem_sid`.

## 2. Disks and the pool (on the host)

```bash
sudo apt install mergerfs acl attr
sudo mkdir -p /srv/buckets/volumes/disk1 /srv/buckets/pool
# Mount each disk under /srv/buckets/volumes/<name> (ext4 or xfs), e.g. in /etc/fstab:
#   UUID=...  /srv/buckets/volumes/disk1  ext4  defaults,acl  0 2
# Then the pool:
# List each disk explicitly (not a * glob), so a drained or not-yet-added disk stays out of the pool after a reboot.
echo '/srv/buckets/volumes/disk1=RW /srv/buckets/pool fuse.mergerfs allow_other,cache.files=off,category.create=mfs,func.getattr=newest,dropcacheonclose=true,xattr=passthrough,posix_acl=true,fsname=buckets 0 0' | sudo tee -a /etc/fstab
sudo mount /srv/buckets/pool
```

To add a disk later:
1. Mount it under `/srv/buckets/volumes/<name>`.
2. In the app, open **Storage** and press **Add to pool**. The app updates the running pool through mergerfs's control file. Nothing restarts.
3. Storage then shows the pool line for `/etc/fstab` with the new disk list. Paste it over the old line so the pool comes back the same after a reboot. Do the same after a drain.

## 3. Configure

```bash
cp .env.example .env          # fill in every value; openssl rand -hex 32 for secrets
mkdir -p secrets
printf '%s' 'joinadmin'        > secrets/ad_join_user       # used once for the domain join
printf '%s' 'the-password'     > secrets/ad_join_password
chmod 600 secrets/*
# With your own certificate: AB_TLS_MODE=files and put fullchain.pem + privkey.pem in AB_TLS_DIR.
```

## 4. Start

```bash
docker compose up -d --build              # web app, Claude Code endpoint, database, TLS
docker compose --profile smb up -d        # plus the Windows share (joins the domain on first start)
docker compose logs -f app samba
```

- Sign in at `https://buckets.example.com` as the bootstrap admin, then open **Directory** and press **Sync now**.
  People in `AB-` groups appear with their home folders. Members of `AB-Admins` are admins.
- When AD admins can sign in, remove `AB_BOOTSTRAP_PASSWORD` from `.env`. The local admin stays as a
  break-glass account; its password can be reset from the database if needed.
- Map drives by Group Policy:
  - `H:` → `\\buckets.example.com\home`
  - `S:` → `\\buckets.example.com\shared`

## 5. Claude Code

The server hands out its own Claude Code plugin, already pointed at its address. There is nothing to host or edit.

- **Each person:** open **Connect Claude Code** in the web app and paste the one line it shows into PowerShell (Windows) or a terminal (macOS, Linux):
  ```
  irm https://buckets.example.com/claude/install.ps1 | iex
  curl -fsSL https://buckets.example.com/claude/install.sh | sh
  ```
  It installs the plugin, then opens the browser to sign in once. Running it again is safe.
- **Every computer at once:** push `https://buckets.example.com/claude/managed-settings.json` as Claude Code's managed settings (Intune, Group Policy, Jamf; the Connect page shows the paths). Claude Code installs the plugin at its next start and asks each person to sign in once.

Claude Code signs in only over https, with a certificate the computer trusts. Domain-joined PCs trust a certificate from your own certificate authority without any extra step.

## Demo data

```bash
docker compose exec app python3 scripts/seed_demo.py http://127.0.0.1:8080 <bootstrap-password>   # only without AB_SECURE_COOKIES
# or from any machine that trusts the certificate:
python server/scripts/seed_demo.py https://buckets.example.com <bootstrap-password>
```

The script creates six people and four teams, shares some folders, and saves files the way Claude Code would.
Every demo account uses the password `demo-password-123`.
