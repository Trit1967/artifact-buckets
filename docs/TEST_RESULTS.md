# Artifact Buckets: test results

This page is the single place to check what was tested, by whom, what was found and what happened to each finding. Every claim below points at a test file, a report or a script you can re-run.

## How testing was done

There were three layers, and two of them were written by an independent tester who worked only from `docs/FEATURES.md` and the running app, not from the build notes.

| Layer | Written by | What it covers | Where |
|---|---|---|---|
| Build tests | Builder | Core behaviour of each module while it was written, plus a test for each re-test fix | `server/tests/` (74 tests) |
| Backend QA suite | Independent tester | Every feature line A–I and K in FEATURES.md, through the HTTP API and MCP, including hostile input | `qa/` (210 tests), report `qa/QA_REPORT.md` |
| UI, plugin and deployment QA | Independent tester | Every page at 1440, 900 and 390 px, for each role; consent and OAuth journeys in a real browser; the Claude Code plugin installed into a real Claude Code; Docker and README steps | `qa-ui/` (Playwright journeys and scripts), report `qa-ui/QA_UI_REPORT.md` |
| Re-test | Independent tester (fresh run) | Each fixed UI finding re-checked against the fix, plus the new file viewer and Claude Code round-trip | `qa-ui/retest/`, report `qa-ui/retest/RETEST_REPORT.md` |

## Current results

| Suite | SQLite | PostgreSQL 16 |
|---|---|---|
| Build tests (`server/tests`) | 74 / 74 pass | 74 / 74 pass |
| Backend QA (`qa/`), including auto-sync (`qa/test_m_sync.py`, 120) | 330 / 330 pass | 330 / 330 pass |
| Sync script tests (`qa-ui/sync/test_sync_sh.py`, live server) | 38 / 38 pass | n/a |
| Auto-sync and welcome browser journey (`qa-ui/sync/journey_sync.js`, 1440/900/390 px) | 102 / 102 checks | n/a |
| Docker image build and compose smoke test (`/healthz`, `/view`, `/api/auth/me`) | n/a | pass |

To re-run:

```
cd server && ../.venv/bin/pytest -q tests                      # build tests
cd qa && ../.venv/bin/pytest -q                                # backend QA, SQLite
AB_TEST_DATABASE_URL='postgresql+psycopg://…' ../.venv/bin/pytest -q   # backend QA, Postgres
```

The UI journeys need a running server; each script's header says which port and seed it expects (`qa-ui/lib.js`).

## Backend QA findings (33)

All 33 findings in `qa/QA_REPORT.md` are fixed, and each one has a test in the QA suite that failed before the fix and passes now. The highlights:

- **Critical:** personal tokens could carry admin powers. Now admin and auditor powers never work through a personal token, whatever the person's role.
- **High:** symlinks and bidi or control characters in names, team Owners able to route into homes, AD sync merging two people with the same name, protected folders movable by admins, quota bypass through versions.
- **Decision QA-11:** older versions count toward quota. When a save would go over, the oldest versions of that same file are dropped first, and the save is refused only if that still isn't enough. Rob's goal of "super easy" storage drove this choice.

## UI, plugin and deployment findings (45)

| Finding | Status |
|---|---|
| UI-1 consent phishing | Fixed. https redirects only for hosts an admin lists in `AB_OAUTH_REDIRECT_HOSTS`; the consent page names any non-local destination in a warning. **Spec change** to D2. |
| UI-2 Enter cancels sign-in | Fixed. Connect is the first button. |
| UI-3 install command rejected | Fixed. Full git URL everywhere. |
| UI-4 save link opens an error | Fixed. Links go to the new file viewer, `/view?path=`. |
| UI-5 folders can't be managed | Fixed. Folder menu with rename, move, delete (delete states the file count). |
| UI-6 container paths sent to mergerfs | Fixed. `AB_VOLUMES_HOST_ROOT` translates paths. |
| UI-7 preview links work as bearer links | Fixed. Previews also require the session cookie of the person they were made for. **Spec change** to B17. |
| UI-8 CSV formula injection | Fixed. Export is server-side and escapes `= + - @` and tab/CR. |
| UI-9 stale details after a new version | Fixed. |
| UI-10 focus not visible | Fixed. Focus rings on all custom controls. Audit rows open with Enter or Space; Directory group names are links. (Keyboard rows were missed in the first fix; fixed after the re-test.) |
| UI-11 no sign-out on tablet or phone | Fixed. Mobile top bar with account menu. |
| UI-12 phone tap shows nothing | Fixed. Tapping a file on a narrow screen opens the full viewer. |
| UI-13 tokens attention link 404 | Fixed. Goes to Connect Claude Code. |
| UI-14 admins can't see others' devices | Fixed. Mine / Everyone switch for admins. |
| UI-15 access check has no person | Fixed. Optional person field with a yes/no verdict and the reason. |
| UI-16 team Owners can't add routing rules | Fixed. |
| UI-17 bidi spoofed names | Fixed. Server refuses bidi and direction marks. |
| UI-18 SMB audit claim | Fixed. The page says Samba keeps its own log for a SIEM, and Samba now writes those lines to its log files (`full_audit:syslog = false`) in a `samba-logs` volume. To be confirmed on the lab's Samba. |
| UI-19 empty secret key accepted | Fixed. Startup refuses a key under 16 characters, and refuses a missing key when cookies are secure. With no key in development, a fresh random key is made each run, so nothing is ever signed with a known value. **Spec change** to K2. |
| UI-20 fstab glob at boot | Fixed. README and the Storage page give an explicit branch list to paste into fstab, and the app re-applies its own branch list at startup. |
| UI-21 Connect page wrong about first use | Fixed. Says `/mcp` → Authenticate. |
| UI-22 to UI-36 (low) | All fixed. UI-32 (overview at 390 px) and UI-36 (tags in the new viewer's side panel) needed a second fix after the re-test. |
| UI-37 to UI-44 (polish) | Fixed (UI-39's last two text glitches after the re-test), with two notes. UI-40: sizes use binary units throughout, so 0.1 GB shows as 102 MB on purpose (0.1 × 1024 MB). UI-42: the mockup's inline "Who can open this" list is a button that opens the access panel. |
| UI-45 quota minimum unexplained | Open (polish). The browser's own message explains it; a custom message is left for later. |

## Re-test

A fresh independent run checked every fix by exercising it, plus the new viewer and a real `/buckets-open` run in Claude Code (`qa-ui/retest/RETEST_REPORT.md`).

**First re-test:** of the 42 fixes, 36 were confirmed, 5 were partial (UI-10, 18, 19, 36, 39) and 1 was not fixed (UI-32). The viewer was found safe against hostile Markdown, HTML and SVG, and `/buckets-open` handled a conflicting save correctly in a real Claude Code run. It found 3 new medium and 12 low issues, and no high ones.

**After the follow-up fixes** (the tester's own scripts re-run against a fresh server, plus new build tests):

| ID | Finding | Now |
|---|---|---|
| N-1 | JSON Source showed reformatted text, and big numbers were changed | Source shows the file exactly; Formatted re-indents without touching values (`12345678901234567890` stays as written) |
| N-2 | `/buckets-open` could save back a truncated copy | `get_artifact` marks truncated text with a "don't save back" note; the command refuses to edit truncated files, copies large and binary files with shell commands, and checks the copy's size |
| N-3 | `if_version` missed a moved or deleted file | Refused with "no longer exists: it was moved, renamed or deleted" |
| N-4 | Inbox and projects folders could be renamed or deleted | They stay put, for everyone; the folder menu isn't shown on them |
| N-5 | Folder delete didn't show the file count | The dialog says "the folder and the 55 files in it (…)" before anything happens |
| N-6 | Markdown rendering slips (emphasis inside links, double-decoded quotes, quotes in URLs) | Fixed; links and code are set aside before emphasis |
| N-7 | Long tags overflowed the viewer's side panel | Fixed; folder names no longer break mid-word |
| N-8 | Folder menu had no arrow keys, and Esc lost focus | Arrow, Home and End keys work; Esc returns focus to the menu button |
| N-9 | Consent tab order didn't match the screen | Cancel then Connect in both; Enter still connects |
| N-10 | Versions dropped for quota left no trace | The save reply and the audit log list them |
| N-11 | Viewers offered "Open in Claude Code" with no way to save | The command tells viewers up front and offers a copy in their own inbox |
| N-12 | Browser tab title stayed generic in the viewer | Shows the file name. Esc inside an embedded HTML or PDF preview still can't be caught (the preview is sandboxed on purpose) |
| N-13 | No key set started with a known dev key | See UI-19 |
| N-14 | Samba template comment was wrong | See UI-18 |
| N-15 | Zero-width characters and `CON.txt` accepted | Both refused |

## One-line Claude Code setup (real Claude Code)

Run with Claude Code 2.1.323 in a clean home folder, against the server behind the lab's own Caddy configuration (`deploy/lab/Caddyfile.lab`: https with Caddy's lab certificate on a bare IP, plain http only for the installer):

- `curl -fsSL http://<server>:8090/claude/install.sh | sh` fetched the lab certificate and checked its fingerprint, added the marketplace, installed the plugin, and ran `claude mcp login`. Signing in and pressing Connect in a browser finished it; `claude mcp list` then showed `plugin:artifact-buckets:artifact-buckets … Connected`, also from a new login shell.
- Running it again changed nothing and said "Already signed in".
- Claude Code refuses to sign in to a plain `http://` server that isn't on the same computer, which is why the lab now uses https.
- The PowerShell installer is the same steps; it is first run on a real Windows PC in the lab.
- Asking Claude Code instead (the Connect page's prompt, which points at `/claude/setup.md`): a fresh Claude Code on the lab's Windows PC, in plan mode, read the steps, checked the plugin had no hooks or programs, proved Node reached the server with only `NODE_EXTRA_CA_CERTS`, and planned the install and sign-in. It refused to put the lab's first certificate (Caddy's own root CA) into Windows' trusted roots, because a CA could vouch for any site. The lab now uses a self-signed server certificate that is not a CA (CA:FALSE) and names only the lab address, and the setup page tells Claude how to check that before trusting it.

## What only the lab can test

These depend on infrastructure that doesn't exist in the build container. The code paths are written and unit-tested against fakes; they need a real run in the lab.

- Joining the AD domain and reading users and groups from a real DC; Entra sign-in (OIDC) against the real tenant.
- Samba: mounting the share from Windows and macOS, ACLs as seen by real clients, Previous Versions.
- Storage on real disks: mergerfs today, ZFS at lab deploy (scheduled snapshots, Previous Versions, zfs-send backups, disk health, Add and Replace disk, point-in-time Restore, all behind the Storage page).
- TLS through Caddy with the lab's certificate, and Claude Code signing in from people's own machines.

## Auto-sync findings (independent tester, 2026-11-13)

Report: `qa-ui/sync/SYNC_QA_REPORT.md`. A dry run of the Windows script on Rob's PC against 6.5 GB of real Claude Code history found that `~/.ssh/config`, files in hidden folders and app data would be copied, and that same-named files collided in the inbox; all fixed and re-checked on the PC. The tester's one high finding (a linked computer could replace a file made on the web, and quota pruning could drop the original) is fixed: sync now saves its copy beside such a file. Medium findings S2, S4, S5, S6, S7, S9, S10 and S11 are fixed, as are low S12, S13, S14, S17, S18 and S22, and the review-only P1–P3. Each has a test that failed before the fix.
