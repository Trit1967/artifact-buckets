# Artifact Buckets: UI retest report

Tester: independent QA. I did not write this code. Date: 2026-11-13. Build: `web/dist` as built at 05:58. No file under `web/src` is newer than the build. Claude Code 2.1.323.

## How I tested

I ran my own instances:

- **:8096.** The command from the brief, seeded with `seed_demo.py`.
- **:8095.** Used for config checks, with `AB_TRASH_DAYS=7` and `AB_OAUTH_REDIRECT_HOSTS=partner.example`.

I did not touch :8080. Both of my instances and the callback listener are now stopped. Data is in `/tmp/qa-retest`.

Every verdict below comes from exercising the behaviour, not from reading the diff:

- Playwright with Chromium, at 1440, 900 and 390 px.
- Python against the REST API and MCP.
- `claude plugin validate`.
- A real `claude plugin marketplace add` and `install` in a scratch HOME.
- Two real `claude -p --model haiku` runs of `/buckets-open` against :8096.

Scripts are in `qa-ui/retest/`, outputs in `out_*.json` / `out_*.txt`, and screenshots in `shots/`. To re-run: start the instance from the brief on :8096, seed it, run `python3 cb_server.py 33419 &`, then run `env -u HTTPS_PROXY -u HTTP_PROXY BASE=http://localhost:8096 node <script>.js`. For the viewer tests, run `python3 seed_viewer.py` first.

## Status of UI-1 … UI-45

| ID | Status | Evidence (script → observation) |
|---|---|---|
| UI-1 consent phishing | **Fixed** | `backend_retest.py`: register `https://attacker.example/cb` → 400. These also return 400: `http://attacker.example`, `localhost.attacker.example`, `127.0.0.1.nip.io`, `https://127.0.0.1@attacker.example`, `javascript://localhost`, a fragment. Loopback (`127.0.0.1`, `[::1]`, `LOCALHOST`) → 201. On :8095 the listed `partner.example` → 201, and the consent page shows a red "This sign-in goes to partner.example, not to Claude Code on this computer" (`second_instance.js`, `shots/consent_listed_host.png`). A hand-made consent URL for an unregistered host is refused at approve ("Redirect URI is not registered"). |
| UI-2 Enter cancels | **Fixed** | `ui2_enter.js` with a real loopback listener: Enter in "Name this computer" → `code` returned. Connect → code. Cancel → `access_denied`. (Side effect: N-9.) |
| UI-3 marketplace command | **Fixed** | `/api/auth/me`, the Connect page, the root README, the plugin README and `.env.example` all show `https://git.lab.local/tools/claude-plugins.git`. `claude plugin marketplace add <that URL>` now gets as far as `git clone` (it fails only because this sandbox has no network). It no longer fails with "not a valid owner/repo shorthand". Adding and installing from the local path still work. |
| UI-4 broken `link` | **Fixed** | `save_artifact` and `get_artifact` return `…/view?path=/users/maya/inbox/retest-note.md`. That URL opens the viewer with the file (`ui_fixes.js` UI-4). Old-style `/files?path=<file>` links still land on an empty listing (not needed any more). |
| UI-5 folder rename/move/delete | **Fixed** (see N-4, N-5) | `folders.js`: rename, move, "move into itself" (refused), move to `/users/jonas` (refused) and delete all work. Deleting a 55-file folder works and it restores from Trash. A group editor on a folder with its own sharing gets "only an Owner can delete it". |
| UI-6 mergerfs container paths | **Fixed** | Python check of `storage._branches` with `AB_VOLUMES_HOST_ROOT=/srv/buckets/volumes`: `/srv/buckets/volumes/disk1=RW:/srv/buckets/volumes/disk2=RW`. The fstab line uses host paths. Compose sets `AB_VOLUMES_HOST_ROOT: ${AB_VOLUMES}`. Not run against real mergerfs. |
| UI-7 preview bearer links | **Fixed** | `backend_retest.py`: own session → 200, anonymous → 403, another signed-in user → 403, after sign-out → 403 (the cookie is required). Note: it is bound to the user, not the session. A second session of the same user gets 200. That's acceptable. |
| UI-8 CSV formula injection | **Fixed** | `/api/audit/export.csv` (`out_audit_export.csv`): `'=HYPERLINK(…)`, `'-2+3+cmd|x` and `'@SUM(…)` are prefixed with `'`. (The one `[FAIL]` line in `out_backend.txt` was a bug in my script: it counted empty cells. The script is corrected.) |
| UI-9 stale details | **Fixed** | `ui_fixes.js`: after an upload the panel goes "version 1" → "version 2" with the new preview. After Restore it shows "version 3" with the v1 text. |
| UI-10 focus rings / keyboard rows | **Partly fixed** | Tab to `.rowbtn`, `.mebtn`, `.btn`, `.leaf` or `.seg` → `outline: solid 2px` (`shots/focus_rowbtn.png`). Not fixed: Audit rows are still `tr.click` with `tabIndex -1`, and 80 Tabs never reach the table, so rows can't be expanded by keyboard. Directory group rows have the same markup. |
| UI-11 sign-out ≤900 px | **Fixed** | A hamburger drawer holds the account button at 390 and 900 px. Sign out is visible and clickable, for both member and admin, at 390×700 (`shots/m390_drawer.png`, `a390_drawer.png`). |
| UI-12 phone tap | **Fixed** | Tapping a file at 390 px opens `/view?path=…` (`shots/m390_viewer.png`). |
| UI-13 attention link | **Fixed** | "personal token(s) expire this week" → `/connect`. |
| UI-14 admin view of devices | **Fixed** | Connect → "Everyone" toggle lists all people's tokens with the username. Revoking maya's `three-day` token as admin worked: count 1 → 0 (`ui14_admin_tokens.js`). |
| UI-15 "can X reach Y" | **Fixed** | Groups → path plus Person: "maya can open and change it as Owner, from a direct share on /shared/platform-eng". "diego can't reach it. No grant…". |
| UI-16 Owners add rules | **Fixed** | maya (Owner of /shared/platform-eng) sees Add rule and adds a rule. A rule targeting /shared/finance is refused with a clear toast. |
| UI-17 bidi names | **Fixed** | `save_artifact` with U+202E or U+2067 → "contains a character that is not allowed". (Not covered: U+200B zero-width is still accepted, N-15.) |
| UI-18 SMB audit claim | **Partly fixed** | The SMB page no longer says the app's audit log. It now says "Samba records … in its own log for your SIEM". But `smb.conf.template` still sends `full_audit` to syslog `local5` (no `full_audit:syslog = false`), the container still has no syslog daemon, and the template comment still says "goes to the log the app's audit view reads". By configuration analysis, the records still go nowhere. Needs the lab to confirm. |
| UI-19 empty secret key | **Partly fixed** | `''`, `short` and a 15-character key → "Application startup failed". A 16-character key starts. Leaving AB_SECRET_KEY **unset** still starts with the public default `dev-only-change-me`, unless `AB_SECURE_COOKIES=true`. So the brief's statement "refuses to start without AB_SECRET_KEY" is not true outside compose. Compose (secure cookies on, empty `.env` value) is protected. |
| UI-20 fstab glob | **Fixed (by documentation)** | README §2 now uses an explicit branch list, and Storage shows a ready fstab line to paste after add or drain. Startup still does not call `apply_pool`, so a stale fstab line is only corrected when the admin pastes the new one. |
| UI-21 Connect "browser opens" | **Fixed** | The Connect page says "/mcp → artifact-buckets → Authenticate". |
| UI-22 trash days | **Fixed** | On :8095 (`AB_TRASH_DAYS=7`) the dialog, toast, Trash subtitle and "Kept for" column all say 7 days. |
| UI-23 3-day token "2 days" | **Fixed** | `expires_in_days = 3`. |
| UI-24 `next=/\evil` | **Fixed** | After sign-in the person lands on `/files`. There is no page error. |
| UI-25 bare /consent | **Fixed** | "This sign-in link is incomplete. Start again from Claude Code…" |
| UI-26 sticky drop highlight | **Fixed** | `ui26_drag.js`: `hot` is removed on a window dragleave and on dragend, and kept on an inner dragleave. |
| UI-27 members see all rules | **Fixed** | diego and priya (not in platform-eng) see 0 rules. Members of platform-eng see its rules. `can_add` is correct. |
| UI-28 logout CSRF | **Fixed** | Logout with no header → 403, and the session survives. |
| UI-29 unknown-user audit | **Fixed** | via=`web`, ip=`127.0.0.1` (actor stays "system", with the typed name as target). |
| UI-30 member quota | **Fixed** | Sidebar "YOUR SPACE 2.2 MB of 50 GB". It only refreshes on reload. |
| UI-31 partial export, no smb filter | **Fixed** | 200 shown vs 233 exported. The Through filter has "Windows share". |
| UI-32 overview overflow at 390 | **Not fixed** | `overflow390.js`: admin `/overview` scrolls 71 px sideways. The culprits are the chart labels `.xl span` "Nov 12" (right edge 412) and "Nov 13" (right edge 461). Every other page is 0. |
| UI-33 "Moved 0 / 1" | **Fixed** | A drained volume reads "Drained, safe to unmount … All files moved off · 1 file" (`shots/admin_storage_drained.png`). |
| UI-34 audit sentences | **Fixed** | "admin suspended priya", "admin reactivated priya", "changed priya quota → 0.10 GB". |
| UI-35 auditor 404 | **Fixed** | lena on /files makes no failing requests. |
| UI-36 tags overflow | **Fixed in Files** | The detail panel has nothing overflowing, even with 40-character tags. The same bug exists in the new viewer rail (N-7). |
| UI-37 pool card wrap | **Fixed** | "12.5 GB of 252 GB" on one line (`shots/sidebar_pool_card.png`). |
| UI-38 no titles | **Fixed** | Row `title` holds the full name. |
| UI-39 text glitches | **Partly fixed** | "rule # 2" is fixed. Still present: the access-check list reads "platform-eng· group of 3" (no space; Svelte drops it before `{#if}` in Groups.svelte:164), and the Trash "Deleted by" column shows the username `admin` although the API returns `deleted_by_name`. |
| UI-40 GB/GiB | Not addressed (as stated) | — |
| UI-41 markdown raw | **Fixed** | Rendered in the Files panel and in the viewer. Safety is covered under "New features". |
| UI-42 mockup gaps | Not addressed (as stated) | — |
| UI-43 mobile pill cloud | **Fixed** | The mobile header is 57 px with a hamburger (`shots/m390_files.png`). |
| UI-44 identical volume sizes | **Fixed** | Cards say "shares the host disk" in direct mode. |
| UI-45 quota min tooltip | Not addressed (as stated) | — |

Tally of the 42 findings claimed fixed: **36 Fixed, 5 Partly fixed (UI-10, 18, 19, 36, 39), 1 Not fixed (UI-32)**.

My unconfirmed suspicion 1 from the first report (blank PDF) is resolved. With new-headless Chromium, which has the built-in viewer, the PDF renders inside `/view` despite the CSP `sandbox` (`pdf_check.js`, `shots/pdf_in_viewer.png`).

## New features

### What works

**Viewer (`viewer.js`).** Each file type renders as it should:

- Markdown → `article.md`
- CSV → a table with right-aligned numbers, a 500-row cap and a note
- JSON → formatted
- Code → line numbers
- HTML and SVG → `iframe sandbox="allow-scripts"`. The HTML script reports `cookie=blocked:SecurityError parent:SecurityError`, and the page title was not changed.
- PNG → `img`
- PDF → iframe
- Text over 2 MB, binaries and files without an extension → a download placeholder

Other viewer behaviour:

- Preview/Source works for Markdown, CSV and HTML.
- The versions rail and "All versions" → Restore work (v4 shows the v1 content).
- Esc returns to `/files?path=<folder>&file=<file>` with the file selected. Esc is ignored while a dialog is open, and inside the rename input it closes the dialog.
- "Open in Claude Code" copies `/buckets-open /users/maya/inbox/viewer/hostile.md`.
- Rename and Move follow the file. Delete returns to the folder with a "7/30 days" toast. A `../escape.md` rename is refused.
- Missing files, other people's files, a folder path and `/../../etc/passwd` each show a clear message.
- A viewer-only person (jonas) gets no Rename/Move/Delete.
- At 390 px nothing overflows.

**Hostile Markdown.** No payload ran. `window.__xss` stayed unset, even after clicking every link, on both `/view` and the Files panel. The rendered article contains zero `script`, `img`, `iframe` or `svg` elements and no `on*` attributes. These links are all refused and rendered as text: `javascript:`, `JaVaScRiPt:`, `&#106;avascript:`, `data:`, `vbscript:` and protocol-relative `//evil`. Raw HTML and HTML inside tables, quotes and tasks shows as text. Three levels of nested quotes render.

**Folder menu (`folders.js`).** Covered under UI-5 above.

**MCP (`backend_retest.py`):**

- `if_version` with the matching version saves.
- `if_version` with a stale version → `conflict: … is now version 2 (saved by Maya Okafor) …`, and the file is unchanged.
- `if_version=0` and `"2"` are rejected by the schema.
- `as_base64` round-trips 323 binary bytes exactly and refuses other users' files.
- Both new arguments appear in `tools/list`.
- `link` points to `/view`.

**Quota counts versions.** With a 1 MiB quota and five 300 KiB saves to one file, the versions kept are `[5,4,3]`, so the oldest are dropped. A new 300 KiB file in another name is refused with `quota_exceeded`, because other files' versions are not evicted. A file bigger than the quota is refused. `used_bytes` includes versions (900 KiB).

**Plugin.** `claude plugin validate` passes for the marketplace and the plugin. The only warnings are no LICENSE, no icon and no privacy URL. `/buckets-open` was run for real with haiku in `cc_ws/`:

- It fetched data.csv, wrote `.buckets/data.csv` byte-identical, and added `.buckets/` to `.gitignore`.
- Sam then saved v2 in the web app. Asked to add a row and save, Claude passed `if_version: 1`, was refused, said "now version 2, saved by Sam Whitfield", and offered to reapply or save under a new name. That is exactly as specified.
- The tool names and arguments in the command match `mcp.py`: `search_artifacts`, `get_artifact` (path, as_base64), and `save_artifact` (path, content, content_base64, if_version). The fields it relies on all exist: `path`, `version`, `link`, `text`, `binary`, `content_base64`, `saved_by`, `updated_at`.

### New findings

#### High

None found.

#### Medium

| ID | Area | Finding | Repro |
|---|---|---|---|
| N-1 | Viewer, JSON | The **Source** toggle for JSON shows the re-formatted text, not the file. `lines` uses `pretty(text)` for JSON whatever the mode. Both views silently change data: `12345678901234567890` is shown as `12345678901234567000`, and `1.10` as `1.1`. There is no way to see the real bytes short of downloading. For IDs and hashes in JSON artifacts this shows wrong values with no warning. | `viewer.js` "toggle data.json": the Source view's first line is `{` (the file is one line), "big int verbatim" is false, and the view shows `"big": 12345678901234567000`. Fixture: `fixtures/data.json`. |
| N-2 | `/buckets-open` | The command ignores `truncated`, and it has no strategy for files bigger than what Claude can retype. `get_artifact` cuts text at 2 MB and sets `truncated: true` (2.31 MB file → 2,097,152 chars). The command says "write the exact contents" and later saves `content` back to the same path, which would replace the file with its first 2 MB. Even at 300 KB it fails in practice: Claude Code spilled the tool result to a file and haiku could not write `.buckets/medium.csv` (`out_cc_medium.json`, "The local copy is not at .buckets/medium.csv"). | Tell the command: if `truncated` or the size is over about 100 KB, fetch with `as_base64` and decode with a shell command, or refuse and send the person to the web download. Never save back a truncated copy. Consider a size cap in `save_artifact` with `if_version`. |
| N-3 | MCP `if_version` | A file that was **moved or deleted** since it was opened is not detected. `save_artifact(path=…, if_version=3)` on a path with no file saves it as a new v1. In the `/buckets-open` flow, a file someone moved or trashed meanwhile is silently recreated at the old path. | `backend_retest.py` "if_version=3 on a missing file -> ok=True". Expected: a conflict ("this file no longer exists here; it was moved or deleted"). |

#### Low

| ID | Area | Finding | Repro |
|---|---|---|---|
| N-4 | Folder menu | The ⋯ menu (and the API) let a person rename, move or delete their own `inbox` and `projects` folders, and editors do the same to team `…/projects` folders (routing targets). After renaming `/users/maya/inbox`, the sidebar "Inbox" opens "This folder doesn't exist". | `folders.js` "rename /users/maya/inbox via UI" → toast "Renamed". `backend_retest.py`: moving `/users/maya/inbox` → 200. |
| N-5 | Folder delete | The confirm text is the same for 3 files and for 55 ("The folder and everything in it…"), and the web always sends `confirm: true`. The count appears only in the toast afterwards. So the "confirm in the web app" step that MCP sends people to never shows the count. | `folders.js` "delete dialog (55 files)". |
| N-6 | Markdown renderer | Rendering is wrong, but nothing is exploitable: <ul><li>The emphasis regexes run over the generated `<a href>`, so URLs with `_x_` or `*` break (`href="https://ok.example/<em>x</em>"`).</li><li>`window.__xss` and `__init__` lose their underscores and turn bold.</li><li>Blockquotes decode entities twice (literal `&lt;img…&gt;` shows as `<img…>` text), while paragraphs show `&amp;` literally.</li><li>A link whose URL contains `"` gets a double-escaped href.</li></ul> | `viewer.js` "hostile md" (hrefs, quoteText); `shots/view_hostile_md.png`. |
| N-7 | Viewer rail | Long tags overflow the rail to the left (the tag spans 1093–1381 px against a rail at 1120–1400 px) and hide the "Tags" label. The folder link breaks mid-word ("viewe / r"). | `tags_overflow.js`; `shots/viewer_rail_tags.png`, `shots/pdf_in_viewer.png`. |
| N-8 | Folder menu a11y | There is no arrow-key navigation. After Esc, focus goes to `body` instead of back to the "Folder actions" button. | `folders.js` "menu keyboard". |
| N-9 | Consent a11y | Fixing UI-2 put Connect first in the DOM, and Cancel is moved with `order:-1`. Tab order is now Connect → Cancel while the screen shows Cancel → Connect (WCAG 2.4.3). | `ui2_enter.js` "buttons(DOM order, x): allow@x=768 deny@x=685". |
| N-10 | Quota | Old versions dropped for quota leave no trace: nothing in the save result and no audit entry. Restoring the oldest kept version also drops that version from history (versions `[5,4,3]` → `[6,5,4]`). `/buckets-open` tells people "the old one stays in its history", which is not guaranteed near the quota. | `backend_retest.py` quota block. |
| N-11 | `/buckets-open` | "Open in Claude Code" is offered to viewer-only people. The command's save-back will then be refused (no editor role), and the command has no "save a copy to your own folder" path for that case. | `viewer.js` "jonas (viewer)": the buttons include "Open in Claude Code". |
| N-12 | Viewer | Esc does nothing while focus is inside the HTML or PDF iframe. The browser tab title stays "Artifact Buckets" on `/view` instead of the file name. | `viewer.js` "Esc with focus inside the HTML preview iframe". |
| N-13 | Config (UI-19) | With `AB_SECRET_KEY` unset, the app starts with the public dev key whenever `AB_SECURE_COOKIES` is off. | Startup matrix in the UI-19 row. |
| N-14 | Docs | The comment in `smb.conf.template` still claims the app's audit view reads `full_audit`. | See UI-18. |
| N-15 | Names (backend) | U+200B zero-width space is accepted in file names, a lesser spoofing vector next to the bidi characters that are now refused. `CON.txt` is still accepted (the earlier suspicion). | `backend_retest.py`. |

## Verdict

Most of the fixes are real. I confirmed 36 of the 42 claimed fixes by exercising them, including all six Highs and all fifteen Mediums except the keyboard half of UI-10 and the UI-18/UI-19 caveats:

- Consent phishing is closed at registration, and the consent page now names any foreign host.
- Enter connects.
- The plugin URL is accepted.
- Every MCP link opens the new viewer.
- Folders can be managed.
- Preview links are bound to the user.

The new viewer is solid and, importantly, safe: no Markdown, HTML or SVG payload ran. `/buckets-open` with `if_version` worked end to end in a real Claude Code run, including the conflict hand-off.

What remains:

- One Low that was claimed fixed is not (UI-32, the overview overflowing at 390 px).
- Five fixes are partial (UI-10 audit rows by keyboard, UI-18 Samba log destination, UI-19 unset key, UI-39 two text glitches, UI-36 regressed in the new rail).
- Three Medium issues are new:
  - The JSON viewer's Source toggle does nothing, and both views alter big numbers.
  - `/buckets-open` can save back a truncated copy and does not handle files beyond a few hundred KB.
  - `if_version` does not catch a file that was moved or deleted meanwhile.

None of these blocks a pilot. I would fix N-1 to N-3 before telling people to rely on "Open in Claude Code" for real documents.
