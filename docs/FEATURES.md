# Artifact Buckets: feature list

Every feature has an ID. Tests, the test plan and the test results refer to these IDs.
"Expected" says what must be true for the feature to pass.

Status key: **built** (code exists and is tested here), **lab** (code exists but needs the lab's
Active Directory, Entra or Samba to prove end to end).

## A. Accounts and sign-in

| ID | Feature | Expected | Status |
|---|---|---|---|
| A1 | First admin | On first start with `AB_BOOTSTRAP_PASSWORD` set and no users, an admin account is created. | built |
| A2 | Password sign-in | Right password gives a session cookie (HttpOnly, SameSite=Lax, Secure in prod) and a CSRF value. Wrong password gives 401 and an audit entry with ok=false. Suspended people can't sign in. | built |
| A3 | CSRF | Any cookie-authenticated POST/PUT/PATCH/DELETE without the matching `X-CSRF-Token` header gets 403. | built |
| A4 | Sign-out | Ends the session server-side; the cookie no longer works. | built |
| A5 | Entra ID sign-in (OIDC) | `/auth/oidc/login` → Entra → `/auth/oidc/callback`. Person matched by `onprem_sid` claim, then UPN/email. Unknown or suspended people are refused and audited. | lab |
| A6 | Passwords | argon2id hashes; at least 12 characters. | built |

## B. Folders, files and permissions

| ID | Feature | Expected | Status |
|---|---|---|---|
| B1 | Layout | `/users/<name>/{inbox,projects}` made for each person; `/shared/<group>/projects` for each group. | built |
| B2 | Roles | Viewer (read), Editor (read, write, delete), Owner (Editor plus share). Grants inherit down the tree. Highest role wins. No deny rules. | built |
| B3 | Admin and Auditor | Admin is Owner everywhere. Auditor reads the audit log, access reports and user list, but cannot open files. | built |
| B4 | Private homes | Nobody but the owner and admins can list or open `/users/<name>`. | built |
| B5 | Hidden when no access | A path the person can't reach answers 404 (not 403), so names don't leak. Listings show only folders the person can reach or that lead to something shared with them. | built |
| B6 | Path safety | `..`, `.` names, dot-prefixed names, Windows-invalid characters, names ending in a dot or space, NUL, zero-width and text-direction characters, names Windows reserves (CON, NUL, COM1.txt…), and anything outside `/users` and `/shared` are refused. No symlink escape. | built |
| B7 | Save | Save text or upload a file. Same path again creates a new version. Identical content is a no-op. | built |
| B8 | Versions | List versions; restore one (the restore becomes a new version). Old versions beyond a folder's limit are pruned. | built |
| B9 | Move and rename | Files and folders; grants and version history move with them. A folder can't move into itself. Homes, team folders, each home's `inbox` and `projects`, and each team's `projects` stay where they are (saves are routed there). | built |
| B10 | Trash | Delete moves to trash for 30 days (configurable). Restore puts it back with its grants. Only the person who deleted it (or an admin) sees or restores it. Purged after the retention. | built |
| B11 | Protected folders | `/users`, `/shared`, `/users/<name>` and `/shared/<group>` can't be deleted or moved, by anyone (admins included). | built |
| B12 | Sharing | Owners share a folder with a person or group as Viewer/Editor/Owner, and remove shares. The owner grant on a person's own home can't be removed. | built |
| B13 | Who has access | For any folder: every grant that applies, where it comes from (inherited or direct), plus admins. | built |
| B14 | Quotas | Per person (default 50 GB) and per team folder (default 500 GB), set only on those folders. Current files and their older versions count; trash does not. When a save would go over, the oldest versions of that same file are given up first; if that isn't enough the save is refused (413 quota_exceeded). A save that gave up versions says which in its reply and in the audit log. Restoring from trash is checked too. Admin can change them. | built |
| B15 | Size limit | Single file limit (default 500 MB) → 413. | built |
| B16 | Search | By name, project, tag and folder, only over files the person can open. | built |
| B17 | Previews | Signed short-lived URL that only works in the browser session it was made for; served with a sandbox CSP so an HTML artifact can't run as the app. Downloads are `attachment`. | built |
| B19 | File viewer | `/view?path=` shows a file full page: HTML/SVG sandboxed, PDF, images, Markdown rendered safely (raw HTML shown as text), CSV as a table, JSON formatted, code with line numbers; Preview/Source; versions, rename, move, delete; "Open in Claude Code". | built |
| B20 | Folder actions | Rename, move and delete a folder from the Files page. | built |
| B18 | Large deletes | Over MCP, deleting a folder with more than 50 files is refused with "confirm in the web app". In the web app every folder delete first shows how many files and how much space it holds. | built |

## C. Groups and directory

| ID | Feature | Expected | Status |
|---|---|---|---|
| C1 | Local groups | Admins create groups and add/remove members. Each group gets `/shared/<group>` with Editor for members. | built |
| C2 | AD sync | Syncs AD groups whose name starts with `AB-` (and any extra groups an admin maps), and their members. `AB-Admins` → admin, `AB-Auditors` → auditor. Read-only to AD. People and groups are matched by SID, never by name alone; a name clash gets a separate account and a warning. Computer accounts are skipped; one bad entry is skipped with a warning and never stops the sync. Renamed and deleted AD groups are followed. | built (mock), lab |
| C3 | AD lifecycle | Disabled in AD → suspended. Removed from every synced group → suspended. Back again → reactivated, unless an admin suspended them by hand. | built (mock) |
| C4 | AD groups are AD's | Members of AD groups can't be changed in the app. | built |
| C5 | Map extra AD group | Admin can bring in a group without the prefix. | built (mock) |
| C6 | Suspension ends access | Suspending ends web sessions and revokes every token and device. | built |

## D. Claude Code sign-in and tokens

| ID | Feature | Expected | Status |
|---|---|---|---|
| D1 | Discovery | `/.well-known/oauth-protected-resource` and `/.well-known/oauth-authorization-server` metadata. 401 from `/mcp` carries `WWW-Authenticate: Bearer resource_metadata=…`. | built |
| D2 | Dynamic client registration | Only loopback `http` redirect URIs, or `https` hosts an admin lists in `AB_OAUTH_REDIRECT_HOSTS`; no fragments. | built |
| D3 | Authorize and consent | Not signed in → login page, then back. Signed in → consent page; Allow returns a code to the registered redirect (loopback may use any port). Deny returns `access_denied`. Enter connects. A non-loopback destination is named in a warning. CSRF-checked. | built |
| D4 | PKCE | S256 only; wrong verifier fails; codes are single-use and expire in 5 minutes. | built |
| D5 | Tokens | Access 60 minutes, refresh 30 days with rotation. Reusing an old refresh token revokes the whole sign-in. Revoke endpoint. | built |
| D6 | Devices | Each Claude Code sign-in shows as a device the person (or an admin) can revoke. | built |
| D7 | Personal tokens | For build servers: read or write scope, optional folder list, 1–365 days. Read scope caps at Viewer; folder list limits paths. Created only from the web app (not with a token). Admin powers never work through a personal token. | built |

## E. MCP server (what Claude Code calls)

| ID | Feature | Expected | Status |
|---|---|---|---|
| E1 | Transport | Streamable HTTP, POST `/mcp`, JSON responses; GET/DELETE 405; Origin check. | built |
| E2 | Tools by role | Members see file tools; admins also see admin tools; auditors see `audit_search`. Read-scope tokens see only read-only tools. | built |
| E3 | save_artifact routing | Folder given → there. Else first routing rule whose pattern matches the git remote or project and that the person can write to. Else `/users/<u>/projects/<project>`. Else inbox. Reply says which. | built |
| E4 | File tools | get, search, list, list_writable_folders, create_folder, rename, move, delete, edit_file (exactly one match), versions, restore, trash, share, unshare, who_has_access. | built |
| E5 | Admin tools | list_users, suspend_user, set_quota, list_devices, revoke_device, map_ad_group, grant_folder, routing rules, sync_directory, storage_status, add_volume_to_pool, drain_volume. | built |
| E6 | Errors | Tool failures come back as `isError: true` with a readable message; protocol errors as JSON-RPC errors. | built |
| E8 | Safe save-back | `save_artifact` with `if_version` is refused (conflict, naming who saved the newer version) if the file changed since it was opened, or was moved, renamed or deleted meanwhile. `get_artifact` reports the caller's role, and marks text over 2 MB as `truncated` with a note not to save it back; with `as_base64` returns binary files up to 10 MB. Links returned point to the viewer. | built |
| E7 | Pause | Admin can pause Claude Code saves; web uploads still work. | built |

## F. Routing rules

| ID | Feature | Expected | Status |
|---|---|---|---|
| F1 | Rules | Match on normalized git remote (`git@host:a/b.git` = `https://host/a/b`) or project name, glob patterns, priority order. Target may use `{project}` and `{user}`. | built |
| F2 | Who can add | Admins (any target), or Owners of a team folder (targets inside that team folder only; never a home, never `{user}`). | built |
| F3 | Preview | Show where a given project/remote would land. | built |

## G. Storage pool

| ID | Feature | Expected | Status |
|---|---|---|---|
| G1 | Volumes | Each mounted folder under the volumes root is a volume: pending → active → draining → drained (or missing). A marker file remembers it was added. | built |
| G2 | Add | Admin adds a pending volume; in the lab the mergerfs pool gets it as a branch at once. | built (direct), lab (mergerfs) |
| G3 | Drain | Stop new files on a volume and move its files to the others in the background. The last active volume can't be drained. | built |
| G4 | Placement policy | Most free space / fill in order / keep folders together. | built |

## H. SMB share (Windows Explorer)

| ID | Feature | Expected | Status |
|---|---|---|---|
| H1 | Permissions on disk | Grants are written as POSIX ACLs (Samba shows them as NTFS permissions). Viewer = read, Editor/Owner = modify. AD users/groups only, by uid/gid = 100000 + RID. `other` gets nothing. | built |
| H2 | New files | Files the app saves get their folder's permissions. | built |
| H3 | Drift | Changes made on the server are reported and put back on reconcile. | built |
| H4 | Files dropped over SMB | Picked up and indexed (source "smb") by the background job. | built |
| H5 | Samba | Domain member, Kerberos only, SMB3 with encryption and signing, access-based enumeration, system folders hidden. | lab |

## I. Admin and audit

| ID | Feature | Expected | Status |
|---|---|---|---|
| I1 | Audit log | Sign-ins, saves, reads (downloads and previews), moves, deletes, shares, admin changes, denials (including refused admin attempts and unavailable Claude Code tools). Who, how (web, Claude Code, API), which device, IP. | built |
| I2 | Overview | Counts, last 14 days of saves, pool capacity, things that need attention. | built |
| I3 | Access check | "Can X reach path Y, and why?" Lists every grant on the folder, and with a person, their role and the grant that gives it. | built |

## J. Web app

| ID | Feature | Expected | Status |
|---|---|---|---|
| J1 | Pages | Sign-in, consent, overview, files (browse, upload, preview, versions, share, trash), file viewer, users, groups, storage, incoming (routing rules, pause), connect Claude Code (admins see everyone's devices), directory, SMB, audit (full export). Works on a phone, with a menu and sign-out. | built |
| J2 | Look | Matches the approved mockups; no generic or AI-looking design. Keyboard focus visible everywhere. | built (review with Rob) |

## K. Security baseline

| ID | Feature | Expected | Status |
|---|---|---|---|
| K1 | Headers | CSP, nosniff, frame-ancestors none, no-referrer, HSTS in prod, no-store on API. | built |
| K2 | Secrets | Tokens are stored only as SHA-256 hashes; shown once. The app won't start with a secret key under 16 characters, or with none when cookies are secure; in development with none set it makes a fresh random key each run. | built |
| K3 | Least access | Admin actions need a signed-in session (web or Claude Code), never a personal token. | built |

## L. Claude Code plugin

| ID | Feature | Expected | Status |
|---|---|---|---|
| L1 | Install | The server hands out its own plugin with its address built in (`/claude/marketplace.json`, a sha256-pinned zip). The Connect page shows one line per OS (`irm …/install.ps1 \| iex`, `curl …/install.sh \| sh`) that installs the plugin and opens the browser to sign in; safe to run again. With a private (lab) certificate, the installer checks its fingerprint and asks the OS to trust it once. No environment variable, repository or typed address. | built |
| L5 | Set up by asking Claude | The Connect page also offers a prompt ("Set up Artifact Buckets on this computer by following …/claude/setup.md"). The setup page is written for Claude Code: it explains each step, how to check the lab certificate (fingerprint, not a CA, only the lab address) before trusting it, and how to install, sign in and confirm. | built |
| L4 | Roll out to every computer | Admins see (and download) the Claude Code managed-settings policy that installs and enables the plugin on every machine, with the file and registry locations for Windows, macOS and Linux. | built |
| L2 | Skills | `buckets` (everyone) and `buckets-admin` (admins) teach Claude how to save, find, organize and share, and admin tasks. | built |
| L3 | Open in Claude Code | `/buckets-open <path or words>` finds a file, pulls it into `.buckets/` in the workspace, and saves edits back to the same path as a new version using `if_version`. Large and binary files are copied with shell commands and checked by size, truncated files are never saved back, and viewers are offered a copy in their own inbox. The viewer's button copies the command. | built |
