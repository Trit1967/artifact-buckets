# Auto-sync and first-run welcome (M1–M8): independent QA report

Tested 2026-11-13 against the working tree at commit 148f7f0. The sync.sh and sync.ps1 changes in that commit were re-tested: hidden folders anywhere in the path, ~/Library and AppData, the fallback to the file's own repository, full paths in the inbox, and `exclude`.
Server: own instance on 127.0.0.1:8096 (sqlite, scratch under /home/claude/qa-sync/srv). Product code was not changed.

## How to re-run

| Suite | Command | Result |
|---|---|---|
| Backend (in-process) | `cd qa && ../.venv/bin/python -m pytest -q test_m_sync.py` | **108 passed, 12 xfailed** (120 cases) |
| sync.sh / hooks (live server) | `cd qa-ui/sync && ../../.venv/bin/python -m pytest -q test_sync_sh.py` (needs the server on :8096, or set `AB_QA_SERVER`) | **26 passed, 12 xfailed** (38 cases) |
| UI journeys (Playwright) | `BASE=http://127.0.0.1:8096 node qa-ui/sync/journey_sync.js` | **102/102 checks** at 1440, 900 and 390 px; screenshots in `qa-ui/sync/shots/` |
| Regression: developer suite | `cd server && ../.venv/bin/python -m pytest -q tests` | 72 passed |
| Regression: full QA suite | `cd qa && ../.venv/bin/python -m pytest -q` | 318 passed, 12 xfailed (the xfails are this report's) |

Each `xfail(strict=True)` test states the expected behaviour and fails today. Each one names its finding below. Once a finding is fixed, its test turns XPASS and fails the suite, so the marker gets removed.

A transcript from the real Claude Code CLI (2.1.323) was made under `/home/claude/qa-sync/cc` (one Write, one Edit, one Write in a subfolder, and one Read that was refused). `test_real_claude_code_transcript` checks that sync.sh picks up exactly the 2 edited files. The synthetic transcript lines in the tests copy the key layout of a real 2.1 line: `cwd`, then `toolUseResult` with `filePath`, `structuredPatch` and `originalFile`.

## Findings (by severity)

### High

**S3. A sync token can overwrite, and sometimes destroy, files it didn't make. The UI says it can only add files.**
- Steps: alice saves `/users/alice/projects/proj/plan.md` on the web (3000 B) with a 4000 B quota. A linked computer then PUTs `plan.md` into project `proj` (3000 B).
- Expected: a sync token "can't open, change, delete … anything already here" (Link.svelte:80) and "can only add files" (Welcome.svelte:116, Connect.svelte:147). At the very least, the original should stay as a version.
- Actual: 200, version 2. Because of the quota, `_plan_quota` drops the replaced copy, so the web-made original is gone for good and the version list holds only v2. Without quota pressure the original is kept as v1, but it is still replaced. A folder's `keep_versions` setting prunes old versions the same way.
- Impact: a stolen or phished sync token (see S11), or a buggy computer, can wipe files in projects/ and inbox/.
- Where: server/app/sync.py:179-183 calls `fs.save_file` with no check on who made the existing file (`File.source`); server/app/fs.py:248-273 (`_plan_quota` with `drop_replaced`).
- Fix ideas: only replace files whose current version came from sync (`source == "sync"`), or never let a sync save drop older versions. Otherwise change the wording on all three pages.
- Tests: `test_sync_cannot_destroy_existing_file`, `test_sync_cannot_change_files_it_did_not_make`.

**S1 (fixed in 148f7f0, re-tested). Files in hidden folders outside a project went to the inbox.**
Before 148f7f0, `~/.ssh/config`, `~/.kube/config`, `~/.docker/config.json` and `~/.config/gh/hosts.yml` were uploaded to `inbox/`. I reproduced it on the live server. They are now skipped as "hidden". `test_hidden_folders_outside_a_project_are_skipped` (now a normal test) and `test_hidden_anywhere_in_path` cover it.

### Medium

**S2. The secret rules miss common secrets, and sync.sh and sync.ps1 disagree.**
- In sync.sh:117 the patterns are case-sensitive, so `SECRET.md`, `Prod-SECRETS.yaml` and `CREDENTIALS.json` are uploaded. sync.ps1:235 uses `-like`, which ignores case, so Windows skips them. The two scripts don't match.
- M1 says "certificates", but `.crt`, `.cer` and `.der` are uploaded by both scripts. So is `terraform.tfstate.backup`, though `*.tfstate` is skipped.
- Test: `test_secret_names[...]` (7 cases).

**S4. A file the server always refuses blocks progress forever.**
- Steps: Claude makes `aux.md`, `a:b.md`, `notes.` or `q"uote.md`. The server rightly refuses Windows-reserved or invalid names (400).
- Actual: `failed > 0`, so `last-run` is never written (sync.sh:202, sync.ps1 `Move-Item` at the end of Sync-Files). After every Claude Code reply, the hook re-reads the whole history since the last good run, re-checks git for every file and re-sends the bad one. It prints "Files that weren't copied are tried again next time" every time. Every later run is slower and nothing tells the person.
- Expected: a 4xx that will never succeed (400, 403, 413, quota) is recorded as left out. Only network errors and 5xx are retried.
- Test: `test_permanently_refused_file_does_not_block_progress`.

**S5. Linking the same computer again leaves the old token working.**
- Running `sync.sh link` (or setup with `link`) twice gives two "Sync on LAPTOP" rows, and both tokens work for a year. sync.sh overwrites `sync.conf`, so the first token is orphaned but still valid. Nothing revokes it (server/app/sync.py:119).
- setup.md doesn't tell Claude Code to check `status` before `link`.
- Test: `test_relink_same_computer_replaces_old_token`.

**S6. Racing polls each get a token.**
- `link_poll` reads `status == "approved"`, issues a token, then sets `done`, with no lock and no conditional update (server/app/sync.py:108-125). In 8 rounds, 4 concurrent polls on an approved code produced `[2,4,4,4,4,4,…]` tokens: 27 tokens where there should be 8.
- Only the poll-secret holder can race it, but it breaks the "token handed out once" promise (M3), and the extra tokens all appear as computers.
- Fix: `UPDATE … SET status='done' WHERE id=? AND status='approved'` and check the row count.
- Test: `test_concurrent_polls_issue_one_token`.

**S11. Device-code phishing: whoever presses Allow owns the token, and the page shows nothing the attacker can't choose.**
- The Link page shows only the computer name, which the requester picks: up to 64 characters, full-width and homoglyph characters allowed (e.g. "IT-Helpdesk laptop"). It doesn't show when or where the request came from (IP, time).
- If someone is talked into opening an attacker's `/link?code=…` and presses Allow, the attacker's machine gets write access to their projects/ and inbox/. Combined with S3, it can overwrite their files.
- The reverse also works: if a colleague approves your code first (it's in the URL, on screen shares, in terminal scrollback), your computer quietly uploads all your Claude Code output into their account. The terminal does print "Files … go to /users/<them>", but the installers don't stop to ask.
- `test_approver_owns_the_token` documents this behaviour.
- Fix ideas: show the requesting IP and time on the Link page; have sync.sh and sync.ps1 ask "Linked as X, continue?" before the first `run`; apply NFKC to computer names.

**S7. Re-linking to another account or server sends nothing already sent.**
- `link` keeps `state/sent.tsv` and `last-run` (sync.sh:46ff). After linking to a different account or server, every file already sent under the old link counts as "unchanged" and is never sent to the new one.
- Test: `test_relink_to_another_account_sends_again`.

**S10. The sync token is visible in the process list.**
- sync.sh:193 passes `-H "authorization: Bearer $TOKEN"` on curl's command line. On a shared Linux or macOS machine, any local user can read it with `ps` or `/proc/<pid>/cmdline` while an upload is running.
- Fix: pass the header with `-H @file` or `--config -` (stdin).
- Test: `test_token_not_on_command_line` (a wrapper `curl` logs argv).

**S15. Welcome on phones: a stray green dot at the very top of the page.**
- The `@media (max-width:820px) .pip { position:absolute; top:6px; right:calc(50% - 20px) }` rule (web/src/pages/Welcome.svelte:261) is meant for the step rail, but it also matches the `.pip.big` in the "arrivals" header. Once a computer is linked, that dot floats at the top-centre of the screen, above the brand bar.
- Seen in `shots/welcome-work-live-390.png`.
- Fix: scope the rule to `.st .pip`.

**P1 (review only). sync.ps1 can deadlock on big repositories.**
- `ABSync1.Git` (sync.ps1:108-125) writes all of stdin (`check-ignore --stdin -z`, one entry per candidate) before it reads stdout. git check-ignore writes as it reads. Once the output passes the pipe buffer, both sides block.
- The hook-run then hangs and holds the lock for 30 minutes, again and again.
- Fix: read stdout asynchronously before writing stdin, as is already done for stderr.

**P2 (review only). sync.ps1: a path mismatch kills every run silently.**
- `$it.File.Substring($top.Length + 1)` (sync.ps1:300, 305) runs without checking that the file is under `$top`. git reports the real path. A repo reached through a junction, a `subst` drive, an 8.3 short name or a mapped drive gives a mismatch. Then `ArgumentOutOfRangeException` plus `$ErrorActionPreference='Stop'` aborts the whole run.
- In `hook-run` the `catch {}` swallows it, so nothing syncs and nothing says why.

**P3 (review only). sync.ps1 opens whatever link the server sends.**
- `Start-Process $s.link` (sync.ps1:210) runs the server-supplied value. A malicious server, or a man-in-the-middle on an `http://` server, can return a local program path or a `file:`/`ms-…:` URI instead of a web page.
- Fix: only open values that start with `https://` or `http://` and match `$srv`. The same applies to `open`/`xdg-open` in sync.sh:57.

**S17 (review only). setup.md's `link` step can outlive Claude Code's command timeout.**
- `link` waits up to 10 minutes for Allow. Claude Code's Bash tool times out after 2 minutes by default, so a person who takes a while to sign in can have the link killed halfway.
- setup.md should tell Claude Code to run it with a long timeout or in the background, then check `status`.

### Low

- **S8 (fixed in 148f7f0).** Two loose files with the same name used to share `inbox/<name>`. They now keep their HOME-relative paths (`test_inbox_same_name_no_collision`).
- **S9.** `x-sync-session` is stored without a length check in `File.session_id String(128)` (server/app/sync.py:183). That works on SQLite (600 characters stored) but would be a 500 on Postgres. Test: `test_session_header_is_bounded`.
- **S12.** `_clean_computer` (server/app/sync.py:72) doesn't remove U+061C, U+2028/2029, U+0085, U+00AD, U+180E or C1 controls such as `\x9b`. Full-width or homoglyph names aren't normalized either. Test: `test_computer_name_other_invisibles` (7 cases).
- **S13.** The hook creates `~/.artifact-buckets/state` on every computer with the plugin, even if it is never linked: sync.sh:32 runs `mkdir -p "$STATE"` before checking for a link. M6 says the hook "does nothing until the computer is linked". Test: `test_hook_when_not_linked_leaves_no_trace`.
- **S14. `exclude` edge cases:**
  - `sync.sh exclude /` says "Won't copy anything under /" but excludes nothing: `${x%/}` makes it empty, and the entry is skipped. Test: `test_exclude_root`.
  - Excluding through a symlink records the logical path (`pwd`, not `pwd -P`), so the real path isn't excluded. Test: `test_exclude_relative_and_symlinked_paths`.
  - sync.sh refuses a file path ("No such folder"); sync.ps1 `Resolve-Path` accepts files.
- **S18.** The one-line installers don't offer `exclude`, while setup.md (since 148f7f0) asks about folders to exclude. This is a gap for M7 (server/app/claude_dist.py `SH`/`PS1` sync blocks).
- **S19.** The installer's scan summary, shown before linking, says `your folder/projects/p`. A non-technical person doesn't learn which folder that is. Seen in the install.sh run below.
- **S20.** The stale-lock check takes a lock older than 30 minutes (sync.sh:210, sync.ps1 Take-Lock). A first run that copies a lot for more than 30 minutes can be joined by a second run. The lock's mtime is never refreshed.
- **S21.** Server error messages are parsed with `sed 's/"message":"\([^"]*\)"/'` (sync.sh:199), so the message is cut at the first escaped quote: "Not copied: … (Name 'sp ace's \)".
- **S22.** `GET /api/sync/link/{code}` also accepts a personal token, and any signed-in person can look up any pending code's computer name (`test_pat_can_read_pending_code_info`). This is informational: codes are 32^8 and expire in 10 minutes, so brute force isn't practical, and there is no rate limit.
- **S25.** The welcome shows for every existing user after the upgrade, including admins who have used the app for months: `welcomed` is a new, empty table and `welcome = not welcomed`.
- **P4 (review only).** sync.ps1 runs `check-ignore` once per repository. If git exits 128 (for example a path inside a submodule, or beyond a symlink), `Git()` returns null and nothing in that repository counts as ignored, so ignored files are uploaded. sync.sh checks per file and only loses that one file.
- **P5 (review only).** sync.ps1: `Get-FileHash` on a locked file, or an `UnauthorizedAccessException` in `Put` (only `IOException` is caught), stops the whole run under `ErrorActionPreference=Stop`.
- **P6 (review only).** sync.ps1 `Pin`: the callback returns true for any cert Windows already trusts (`e == None`), as well as the pinned one. M5 says the script "trusts only that saved certificate". sync.sh's `--cacert` matches the spec.
- **P7 (review only).** hooks.json chains `sh …; powershell …; echo ok` with `;`. This depends on Claude Code running hooks under Git Bash on Windows, and on macOS/Linux it prints "powershell: not found" to stderr after every reply. A separate hook per OS, or `command -v powershell >/dev/null &&`, would be cleaner.
- **Design and copy:**
  - "last Just now" on the welcome and Connect pages (the capital J comes from `ago()`).
  - With a long computer name at 1440 px, the arrivals header wraps into three ragged rows: the dot alone, then the name, then the count. See `shots/welcome-work-live-1440.png`.
  - The arrivals feed truncates the end of long paths, which hides the file name, the part that matters. Truncate in the middle instead, or show the file name first.
  - On step 2, "I'll do this later" is the amber primary button, so skipping looks like the recommended action.
  - "Turn off" (Connect) acts at once, with no confirm and no undo. It matches "Disconnect", but there's no way back short of re-linking.
  - The Link page and welcome step 3 say what's left out ("git, secrets like .env and keys, build folders") but not hidden folders, app data or excluded folders.

## What passed

**M4, the token's limits.**
- I walked every registered route with a sync token. Every non-public route refuses it (401/403/405); there are no 2xx and no 5xx.
- Specific checks: fs list, download, meta, search, delete, save-text and share; tokens; users; audit; sync status; welcome; MCP GET/POST/DELETE and tools/call; `/oauth/token` refresh with a sync token.
- An admin's sync token gets 401 on `/api/users`, `/api/overview`, `/api/storage`, `/api/audit`, `/api/tokens?all` and `/api/directory`, and can't write into another home or /shared.
- 33 bad file paths and 14 bad project names are all refused or kept inside the person's own home: traversal, backslashes, absolute and drive paths, dot names, reserved names, bidi and zero-width characters, NUL/CR/LF/TAB, a 256-character name, a 600-level path, empty.
- Also checked: bad base64 and non-UTF-8 headers are refused; URL-safe and unpadded base64 are accepted; a folder in the way returns 409.
- Max file size returns 413 and quota returns 507/413.
- Turning a computer off (owner or admin) stops it at once; another member gets 404. Suspending the owner stops it, and reactivating doesn't bring it back. An expired token is refused, and the lifetime is 365 days. Personal tokens, garbage tokens and the cookie session alone are refused on `PUT /api/sync/file`.

**M3, linking.**
- Codes look like `XXXX-XXXX` from the 32-character alphabet with no lookalikes. The poll secret is at least 40 characters, and the link URL is right.
- Approving needs the web session and CSRF; a personal token can't approve.
- Codes expire at 10 minutes, for GET, allow and poll. If a code is approved but polled late, no token is issued.
- A code works once: allow/deny after use returns 400. The token is handed out once: later polls get `done` (in serial polling; see S6 for concurrent polls).
- Deny works. An unknown decision returns 404. Case and surrounding spaces in a code are tolerated. Suspending the approver before the poll gives `denied`. Old rows are cleaned up. The audit log records `sync.approve`, `sync.link` and `sync.deny`.
- Computer names have control, bidi and zero-width characters removed and are cut to 64 characters; an HTML name is shown as text in the UI (no dialog fired).

**M1, M2, M5, M6: sync.sh against the live server.**
- `scan` before linking and after linking changes nothing on the server and writes no `last-run`.
- Files are placed correctly: a repo subfolder cwd goes to the repo project; a plain folder becomes its own project; HOME as cwd goes to the inbox; a file outside the cwd goes to its own repo's project or to the inbox with its HOME-relative path; outside HOME it goes to the inbox with its full path. Names with unicode, spaces and apostrophes work.
- Everything that should be left out is: git-tracked and git-ignored files, build folders (node_modules, dist, build, .venv, target, __pycache__), hidden folders anywhere in the path, `.env*`, keys, `id_rsa`, `*secret*`, `*credential*`, tfstate and tfvars, empty files, too-big files, `.claude`, the sync folder, ~/Library, /tmp, deleted files, and Read results.
- Unchanged files aren't re-sent and old history isn't re-read; a changed file becomes version 2. A run that can't reach the server sends the file on the next run.
- A computer turned off mid-run gets a 401: `run` exits 1 with "link was turned off", the quiet hook-run exits 0, and no lock is left.
- `off`, `on`, `status`, `unlink` and `exclude` (add, no duplicates, list, `~/` entries and comments, prefix-safe) all work. `sync.conf` is mode 0600. A denied link exits 1. An unknown command is refused.
- One run at a time: "Another sync is running"; hook-run stays silent; a stale lock (over 30 minutes) is taken over.
- Odd names (tab, newline, quote, backslash) don't break the run.
- The real Claude Code transcript is handled.

**M6, hooks.** The hooks.json command, with the plugin root filled in and run under bash:
- exits 0 and prints only `ok`;
- takes under 1.5 s;
- does nothing when not linked or when sync is off;
- copies 25 files in a detached background process and leaves no lock behind;
- survives its process group being killed: 15 files still arrived.

**M7, installers.**
- I ran install.sh as served, with a fake `claude` binary and a pty.
- The scan summary is shown and the person is asked. Answering "n" copies nothing and doesn't link.
- Answering "y" links (approved through the API) and copies.
- Running it again once linked doesn't ask again. Running it again while unlinked asks again.
- With no tty, nothing is copied, but two raw "cannot open /dev/tty" errors are printed.
- `/claude/sync.sh` as served is byte-identical to the plugin's copy.

**M8 and the web pages, at 1440, 900 and 390 px.**
- The first sign-in goes to `/welcome`. Skip or Close marks it seen, and the next sign-in skips it. "Getting started" in the account menu (including the drawer at 900 and 390) reopens it.
- A first sign-in that started on `/link?code=…` or on `/oauth/authorize` goes to the link page or the consent page, not the welcome.
- Step 3 shows the linked computer and arriving files within about 1.5 s. Step 4 shows the person's real project folder.
- The Link page shows the same code, and Allow, Don't allow, used, unknown/expired and missing codes each show the right message. Allow is reachable by keyboard and has a visible focus style. Body and note text contrast is at least 4.5:1.
- Connect lists "Computers that sync", and "Turn off" removes the row and the token stops working.
- No horizontal overflow on any of these pages at any width, even with 70-character computer names and long paths.
- Not tested: the live update on welcome step 2 when Claude Code signs in.

## Files
- `qa/test_m_sync.py`: backend tests.
- `qa-ui/sync/test_sync_sh.py`: sync.sh and hook tests against the live server.
- `qa-ui/sync/journey_sync.js`: Playwright journeys. Results are in `out_journey_sync.json` and screenshots in `shots/`.
- Scratch data (fake homes, the real transcript, the server DB) is in `/home/claude/qa-sync/`.


## Fix status (builder, after this report)

| Finding | Status |
|---|---|
| S3 sync token replaces web files | Fixed: sync updates only files it put there itself; otherwise saves `name (from <computer>).ext` beside the original (sync.py `_own_place`). |
| S2 secret names | Fixed: any case; adds .crt/.cer/.der, id_dsa*, *password*, *.tfstate.* (both scripts). |
| S4 refused file blocks progress | Fixed: 400/403/404/409/413/415/422 are recorded and not retried until the file changes. |
| S5 re-link leaves old token | Fixed: same computer name replaces the earlier sync token. |
| S6 racing polls | Fixed: atomic approved→done update before issuing. |
| S7 re-link keeps sent record | Fixed: `link` clears the state. |
| S9 session header | Fixed: validated, else dropped. |
| S10 token on command line | Fixed: header read from a 0600 file. |
| S11 approving someone else's code | Mitigated: Link page warns when the request came from another network address than the browser. |
| S12 invisible characters | Fixed: control, format and separator characters removed after NFKC. |
| S13 state folder on unlinked computers | Fixed. |
| S14 exclude / and symlinks | Fixed: root refused; real path matched. |
| S15 floating dot on phones | Fixed. |
| S17 link step timeout | setup.md tells Claude to use a long timeout or the background. |
| S18 installers don't offer exclude | Fixed: both installers ask which folders to keep out. |
| S22 personal token reads codes | Fixed: web session only. |
| P1 git deadlock, P2 junction paths, P3 opening links | Fixed in sync.ps1. |
| Design nits | "last copied just now", arrivals header no longer wraps, file names stay visible, secondary "Skip for now", Turn off asks in the app's own dialog. |
| Open (low) | S19 installer summary says "your folder" before linking; S20 runs over 30 minutes; S21 messages cut at a quote; S25 existing users see the welcome once after upgrade (intended). |

Re-run: qa/test_m_sync.py 120 passed (SQLite and Postgres; Postgres also caught `bytes` being returned as a string, fixed); qa-ui/sync/test_sync_sh.py 38 passed; journey_sync.js 102/102.
