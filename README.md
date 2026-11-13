# Artifact Buckets

A file manager for the things people make with Claude Code. Claude Code saves straight into it. People
browse, preview, version, share and organize in the browser, or open the same folders from Windows.

- **Folders:** one home folder per person, one folder per team, with Viewer / Editor / Owner roles that
  apply to everything below. AD groups decide who is in which team.
- **Claude Code:** a plugin adds the connection and two skills. People sign in once through the browser.
  Saves land in the person's project folder, or a team folder when a routing rule says so.
- **Windows:** the same tree is shared over SMB from a domain-joined Samba. The app's grants become
  the files' permissions.
- **Storage:** disks are pooled with mergerfs. You can add a disk or drain one without anyone's paths changing.
- **Audit:** every sign-in, save, open, move, delete and share is logged.

```
                    ┌──────────── Docker host (Linux) ─────────────────────────────┐
 Browser ──HTTPS──▶ │ caddy ──▶ app (FastAPI: web UI, REST, /mcp, OAuth) ──▶ postgres │
 Claude Code ─HTTPS▶│              │ writes files + POSIX ACLs                      │
                    │              ▼                                                │
 Windows ──SMB3───▶ │ samba (AD member) ──▶ /srv/buckets/pool  (mergerfs)           │
                    │                         └─ /srv/buckets/volumes/{disk1,disk2…}│
                    └──────────────────────────────────────────────────────────────┘
          AD (LDAPS) ◀── app: people and AB- groups      Entra ID ◀── app: browser sign-in
```

## Repository layout

| Path | What |
|---|---|
| `server/` | Python app (FastAPI). `app/` is the code, `tests/` the test suite, `scripts/seed_demo.py` fills a demo team. |
| `web/` | Web UI (Svelte + Vite). Built into the app image. |
| `plugin-marketplace/` | Claude Code marketplace with the `artifact-buckets` plugin. Publish it as a git repo. |
| `deploy/caddy`, `deploy/samba` | TLS front end, and the domain-joined Samba container. |
| `docs/FEATURES.md` | Every feature with an ID, what it must do, and its status. |
| `docs/TEST_RESULTS.md` | What was tested, how, and the results, including the independent QA pass. |
| `docker-compose.yml`, `.env.example` | Lab deployment. |

## Lab deployment

### 1. Before you start

- A Linux host (Ubuntu 24.04 tested) with Docker Engine and the compose plugin, and the disks you want to pool.
- DNS: `buckets.lab.local` → the host.
- A TLS certificate for that name from the lab CA (or use Caddy's internal CA for a first try).
- In AD:
  - a read-only service account for LDAP (for example `svc-buckets`);
  - an account allowed to join computers, used once for the Samba join;
  - groups named `AB-<team>` (for example `AB-Platform`), plus `AB-Admins` and `AB-Auditors`.
- In Entra (optional, for browser single sign-on): an app registration.
  - Redirect URI: `https://buckets.lab.local/auth/oidc/callback`.
  - A client secret.
  - Add the optional ID token claim `onprem_sid`.

### 2. Disks and the pool (on the host)

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

### 3. Configure

```bash
cp .env.example .env          # fill in every value; openssl rand -hex 32 for secrets
mkdir -p secrets
printf '%s' 'joinadmin'        > secrets/ad_join_user       # used once for the domain join
printf '%s' 'the-password'     > secrets/ad_join_password
chmod 600 secrets/*
# With the lab certificate: AB_TLS_MODE=files and put fullchain.pem + privkey.pem in AB_TLS_DIR.
```

### 4. Start

```bash
docker compose up -d --build              # web app, Claude Code endpoint, database, TLS
docker compose --profile smb up -d        # plus the Windows share (joins the domain on first start)
docker compose logs -f app samba
```

- Sign in at `https://buckets.lab.local` as the bootstrap admin, then open **Directory** and press **Sync now**.
  People in `AB-` groups appear with their home folders. Members of `AB-Admins` are admins.
- When AD admins can sign in, remove `AB_BOOTSTRAP_PASSWORD` from `.env`. The local admin stays as a
  break-glass account; its password can be reset from the database if needed.
- Map drives by Group Policy:
  - `H:` → `\\buckets.lab.local\home`
  - `S:` → `\\buckets.lab.local\shared`

### 5. Claude Code

The server hands out its own Claude Code plugin, already pointed at its address. There is nothing to host or edit.

- **Each person:** open **Connect Claude Code** in the web app and paste the one line it shows into PowerShell (Windows) or a terminal (macOS, Linux):
  ```
  irm https://buckets.lab.local/claude/install.ps1 | iex
  curl -fsSL https://buckets.lab.local/claude/install.sh | sh
  ```
  It installs the plugin, then opens the browser to sign in once. Running it again is safe.
- **Every computer at once:** push `https://buckets.lab.local/claude/managed-settings.json` as Claude Code's managed settings (Intune, Group Policy, Jamf; the Connect page shows the paths). Claude Code installs the plugin at its next start and asks each person to sign in once.

Claude Code signs in only over https, with a certificate the computer trusts. Domain-joined PCs trust a certificate from your own certificate authority without any extra step.

### Demo data

```bash
docker compose exec app python3 scripts/seed_demo.py http://127.0.0.1:8080 <bootstrap-password>   # only without AB_SECURE_COOKIES
# or from any machine that trusts the certificate:
python server/scripts/seed_demo.py https://buckets.lab.local <bootstrap-password>
```

The script creates six people and four teams, shares some folders, and saves files the way Claude Code would.
Every demo account uses the password `demo-password-123`.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r server/requirements.txt -r server/requirements-dev.txt
cd web && npm ci && npm run build && cd ..
cd server && AB_BOOTSTRAP_PASSWORD=change-me-now-please AB_STATIC_DIR=../web/dist ../.venv/bin/uvicorn app.main:app --port 8080
# tests (SQLite); add AB_TEST_DATABASE_URL=postgresql+psycopg://... to run them on PostgreSQL
cd server && ../.venv/bin/python -m pytest -q
```

`cd web && npm run dev` serves the UI on :5173 with hot reload. It proxies the API to :8080.

## Security notes

- **Tokens.** They are stored only as SHA-256 hashes.
  - Claude Code sign-ins: 60-minute access tokens and 30-day rotating refresh tokens. Reusing an old refresh token revokes the whole sign-in.
  - Admin powers never work through a personal token.
- **Browser sessions.** HttpOnly, Secure, SameSite=Lax cookies plus a CSRF header on every change.
- **Previews.** Shown from short-lived signed links that only work in the signed-in browser session they were made for, with a sandbox CSP, so an HTML file can't act as the app. Downloads are always attachments.
- **Claude Code sign-in.** Redirects go only to Claude Code's loopback listener, unless an admin lists another https host in `AB_OAUTH_REDIRECT_HOSTS`; the consent page names any other destination.
- **Secret key.** With secure cookies on (as in compose), the app refuses to start without an `AB_SECRET_KEY` of at least 16 characters. In development with no key set, it makes a fresh random key each run, so sign-ins end on restart.
- **Paths.** One normalizer refuses `..`, hidden names, Windows-invalid names and symlink escapes, and every operation checks the same grants.
- **Windows share.** SMB 3.1.1 only, encryption and signing required, Kerberos only, no guest. Access-based enumeration is on, and system folders are hidden. Permissions can't be changed from Explorer, and the app reports and repairs drift.
- **App container.**
  - Runs as root, which it needs so it can give files to AD users' uids and write ACLs.
  - Every capability is dropped except CHOWN, FOWNER and DAC_OVERRIDE.
  - The root filesystem is read-only, with no-new-privileges.

## Known limits of this proof of concept

- Backups are out of scope.
- Nested AD groups are not followed. Put people directly in the `AB-` groups.
- Local (non-AD) accounts don't appear on the Windows share; they use the browser and Claude Code.
- Items in the trash don't count toward quotas until they are restored (a restore that would go over is refused).
- Suspending someone in the app removes their own entries from the share's permissions at once. Access they have through an AD group ends at the next sync, or at once if they are disabled in AD.
- The domain join, Entra sign-in and mergerfs pool were checked here with stand-ins: a fake LDAP directory, a test of the sign-in flow, and direct-disk storage mode. They need a first run in the lab.
