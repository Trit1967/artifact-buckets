# Artifact Buckets: web UI, plugin and deployment QA report

Tester: independent QA (UI, plugin, deployment). Date: 2026-11-13. Build: `web/dist` as served on :8080. Claude Code 2.1.323.
Scope: feature J (web app), L (plugin), deployment files, plus the A–I behaviour as reached through the UI.
Backend API correctness is covered by the other tester. Backend issues I found while testing the UI are marked "(backend)" so they can be handed over.

## Summary and verdict

The UI is well built in most places. It is consistent, dark, typographically careful and close to the mockups. Every page loads for every role with no console errors (apart from one auditor 404). Svelte escaping held up against every XSS payload I could plant: file names, display names, group descriptions, tags, project, git remote, device names and file contents. HTML and SVG previews really are sandboxed: an opaque origin, no cookies, no parent access, and `connect-src 'none'` blocks calls to the app's API. CSRF works on the REST API and on the consent form. Members cannot reach admin data by URL or by API.

**Verdict: not ready to hand to users.** Six High findings break core journeys or are security problems:

- The consent page never shows where the sign-in code will be sent (consent phishing).
- Pressing Enter on the consent page cancels the sign-in.
- The documented `/plugin marketplace add` command is rejected by Claude Code.
- Every `link` Claude gives after a save opens an error page.
- Folders cannot be renamed, moved or deleted in the web app, although the MCP server sends people there to confirm large deletes.
- In the lab compose layout, the storage pool code would hand mergerfs container paths.

Counts: **High 6 · Medium 15 · Low 15 · Polish 9**, plus 6 unconfirmed suspicions.

How I tested: Playwright with Chromium at 1440, 900 and 390 px, deviceScaleFactor 1. Screenshots are in `qa-ui/shots/`. I ran read-only checks against :8080. Every mutating flow ran on my own seeded instance on :8098, with a third instance on :8097 for one config check. I also used curl and Python against the APIs and MCP, `claude plugin validate`, a real `claude plugin marketplace add` and `install` in a scratch HOME, and one `claude -p --model haiku` run with the skills loaded. The demo data on :8080 was not changed.

Re-running: start the private instance (command in the task brief) on :8098, seed it, create `/tmp/qa-ui/vol/vol-{a,b,c}`, run `python3 cb_server.py 33418 &`, then run `BASE=http://localhost:8098 node <script>.js`. `pages_by_role.js` runs against :8080 by default (read-only).

## Findings

### High

| ID | Feature | Title | Repro | Expected vs actual | Suggested fix |
|---|---|---|---|---|---|
| UI-1 | D2/D3, security | Consent page hides where the code goes, which enables consent phishing | `consent_phish.sh`; browser: `journey_consent.js` step D, `shots/c_consent_evil.png` | Expected: a person approving a sign-in can tell the result goes to a foreign site. Actual: anyone can register a client with `https://attacker.example/cb` (D2 allows any https redirect). The consent page says "Claude Code is waiting on this computer". After **Connect** the server answers `302 https://attacker.example/cb?code=…`, and the attacker redeems the code with their own PKCE verifier. The client name ("Claude Code") is also attacker-chosen. | Show the redirect host prominently whenever it isn't loopback ("This will send access to attacker.example"), or allow only loopback redirects for dynamic clients (Claude Code only uses loopback). Don't let a registered `client_name` impersonate "Claude Code". |
| UI-2 | D3, L1 | Pressing Enter in "Name this computer" cancels the sign-in | `journey_consent.js` step C | Expected: Enter connects. Actual: the callback receives `error=access_denied`. The first submit button in the form is **Cancel** (`name=decision value=deny`), and implicit submission uses the first button. People naturally edit the name and press Enter. | Put the Allow button first in the DOM (use CSS `order` or `flex-direction: row-reverse` for looks), or make Cancel `type="button"` and submit the deny decision separately. |
| UI-3 | L1, J1 | The install command shown everywhere is rejected by Claude Code | `HOME=qa-ui/home claude plugin marketplace add git.lab.local/tools/claude-plugins` returns "'git.lab.local/tools/claude-plugins' is not a valid GitHub owner/repo shorthand…" | Expected: the step-1 command on Connect Claude Code works. Actual: it fails. The command is on the Connect page (from `AB_PLUGIN_MARKETPLACE`), in the plugin README, in the root README §5, and is the `.env.example` default. The same plugin installs fine from a local path (`claude plugin marketplace add /home/claude/ab/plugin-marketplace` then `install artifact-buckets@lab-tools` both succeeded). | Use a full clone URL, e.g. `https://git.lab.local/tools/claude-plugins.git`, in the config default, `.env.example` and both READMEs. |
| UI-4 | E3, E4, J1 | The `link` returned by `save_artifact` opens an error page | `link_check.js`, `shots/m_save_link.png`. The haiku run returned `http://localhost:8098/files?path=/shared/platform-eng/projects/scratch/qa-skill-check.md` | Expected: the link shows the saved file. Actual: "This folder doesn't exist, or you don't have access to it." `mcp.link()` puts the **file** path in `path=`, and Files.svelte treats `path` as a folder. The skill tells Claude to always give this link, so every save ends in a broken link. | Server: `link()` should return `/files?path=<parent>&file=<path>`. UI: if `/api/fs/list` 404s and `/api/fs/meta` succeeds, open the parent folder with the file selected. |
| UI-5 | B9, B10, B18, J1 | Folders can't be renamed, moved or deleted in the web app | Files page: folder rows have no actions and there is no detail panel for folders (`Files.svelte`; only `sel` file meta drives Rename/Move/Delete). `sec_checks.py`: MCP `delete` of a 52-file folder returns "needs_confirmation: … Confirm the delete in the web app." | Expected: J1 file management covers folders, and B18's "confirm in the web app" has a place to happen. Actual: no way at all, so large folders can't be deleted by anyone except by admins through the API. | Add a folder context menu or detail panel (rename, move, delete with a confirm that states the file count, Share), and pass `confirm: true` only after that confirm. |
| UI-6 | G2, deployment | Adding a volume would hand mergerfs container paths | Storage page on :8098 shows each volume's `path` as the app's own path (`/tmp/qa-ui/vol/vol-a`). `storage._branches()` writes `v.path` into `user.mergerfs.branches`. In compose the app sees volumes at `/data/volumes/*` while host mergerfs uses `/srv/buckets/volumes/*`. | Expected: Add to pool adds `/srv/buckets/volumes/disk2`. Actual: the branch list would be replaced by `/data/volumes/…=RW:…`, paths that don't exist on the host, so every branch breaks. Not run against real mergerfs; the path mismatch is shown by the UI and the code. | Mount volumes at the same path inside and outside the container (`${AB_VOLUMES}:${AB_VOLUMES}`, `AB_VOLUMES_ROOT=${AB_VOLUMES}`), or add `AB_VOLUMES_HOST_ROOT` and translate before `setxattr`. |

### Medium

| ID | Feature | Title | Repro | Expected vs actual | Suggested fix |
|---|---|---|---|---|---|
| UI-7 | B17 | Preview URLs work as bearer links | `sec_checks.py` "preview link" | Expected: previews are tied to the viewer's session. Actual: `/preview/<signed>/name` returns the file to an anonymous client, and still does after the owner signs out, for 10 minutes. The "open in new tab" button puts it in history and the address bar, where it is easy to paste into chat. | Also require the session cookie (and match its user), or shorten the TTL to about 60 s with a single use. |
| UI-8 | I1 | CSV formula injection in the audit export | `sec_checks.py` (failed login with username `=HYPERLINK(…)`, token named `@SUM(…)`), then `misc_checks.js` exports. See `out_audit_formula.csv` | Expected: cells are safe to open in Excel. Actual: an unauthenticated attacker's typed username lands as a `=` formula in the target column of the admin's export. Token names (`@…`) also pass through. | Prefix cells starting with `= + - @ \t \r` with `'` in `csv()` (Audit.svelte). |
| UI-9 | B7, B8 | File details go stale after a new version | `misc_checks.js` (upload a new `dropped.txt` while it is open). Same in `journey_files.js` "after v2 detail" | Expected: the panel shows v2 and the new content. Actual: it still shows "version 1" and the old preview text. After Versions → Restore, `open(path, same)` is a no-op because `navigate()` ignores same-URL navigation, so the panel doesn't refresh then either. | Re-fetch meta and preview after upload or restore (move the selection load into a function and call it), or add a reload counter to the effect. |
| UI-10 | J2, a11y | No visible keyboard focus on the main controls | `a11y_mobile.js`, `shots/m_focus_rowbtn.png` (focus is on "reports", with nothing visible) | Expected: a focus ring like `.btn` has. Actual: `.rowbtn`, `.seg` (all filters and time ranges), `.mebtn` and `.mi` use `all: unset`, which out-ranks the global `button:focus-visible` rule, so the outline is `none`. Audit and Directory table rows are clickable `<tr>`s that can't be focused, so you can't expand audit details or open a group by keyboard. | Add `:focus-visible` rules inside those components. Use buttons or links inside the rows. |
| UI-11 | J1 | Sign-out is unreachable at ≤900 px | `a11y_mobile.js`: `.mebtn` is not visible at 390 and 900, and is visible at 901 | Expected: a way to sign out on a tablet or phone. Actual: `.foot { display:none }` hides the account menu, so there is no sign-out. | Keep the account button in the mobile header (an icon with a menu). |
| UI-12 | J1 | Phone: tapping a file shows nothing | `a11y_mobile.js` ("390 detail top y 1728", scrollY 0), `shots/m_390_after_click.png` | Expected: the details are visible. Actual: the panel renders below the whole list, off screen, with no scroll and no cue. | At narrow widths, show details as a sheet or modal, or `scrollIntoView` the panel. |
| UI-13 | I2 | The "tokens expire this week" attention item links to a missing page | `journey_admin.js` step "attention token click" → `/ingest` shows "There's no page here." | Expected: Connect Claude Code (or an admin token list). Actual: a 404 page. The link is set in `api.py` overview (`"link": "/ingest"`). | Point it to `/connect`, or to an admin token view (see UI-14). |
| UI-14 | D6, E5 | Admins can't see or revoke other people's devices or tokens in the web app | `grep -rn "tokens?all\|all: true" web/src` finds nothing. Users → Edit has no devices. `/api/tokens?all=true` works for admin (`sec_checks.py` shows member 403) | Expected: D6 says the person "or an admin" can revoke. Actual: only possible through MCP `revoke_device`. Suspending is the only web lever. | Add devices and tokens to the Users edit dialog, or a Users → person view, using `?all=true`. |
| UI-15 | I3 | Access check can't answer "Can X reach Y, and why?" | Groups page "Who can reach a folder?" has only a path box (`journey_admin.js` "access check"). The API supports `?user=` (`api.py access_check`) | Expected: enter a person and a path, and see yes/no plus the reason. Actual: only a list of grants. | Add an optional person field and render `person` from the response. |
| UI-16 | F2 | Team Owners can't add routing rules in the UI | Incoming page: "Add rule" and the remove buttons render only for `session.isAdmin`. The backend allows Owners of the target team folder (`routing._check_team_owner`) | Expected: per F2, Owners can add rules for their team folder. Actual: only admins can in the UI. | Show Add rule to everyone and let the server refuse, or show it when the user owns any `/shared/<team>`. |
| UI-17 | B6, J1, security | Bidi control characters spoof file names | `mcp_seed_hostile.py` saves `‮lmth.exe`; `detail_shot.js`, `shots/d_0.png` lists it as **"exe.html"** | Expected: refuse, or show it neutralised. Actual: the listing reads "exe.html" for an `.exe` (the badge says EXE, but the name is what people read). The download keeps the real name. (Backend: B6 should refuse U+202A–202E and U+2066–2069.) | Refuse bidi controls in `paths.py`. In the UI, wrap names in `<bdi>` or `unicode-bidi: isolate` as defence in depth. |
| UI-18 | H5, J1 | SMB page promises file operations reach the audit log, but nothing ingests them | `grep -rn "full_audit\|smbstatus" server/app` finds only `.smbstatus.json`. `smb.conf.template` sends `full_audit` to syslog `local5`, and the samba container runs no syslog daemon | Expected: "Every file operation logged. Opens, writes, renames and deletes go to the audit log" (Smb.svelte). Actual: those records go nowhere the app reads. | Either ship the full_audit lines to the app (`full_audit:syslog = false` to the log file, plus a reader), or remove the claim. |
| UI-19 | deployment, K2 | Empty `AB_SECRET_KEY` is accepted silently | `.env.example` ships `AB_SECRET_KEY=` (empty). `config.py` uses `os.environ.get(..., default)`, so an empty value becomes `""`. Verified itsdangerous signs happily with `""` | Expected: refuse to start without a real key. Actual: preview links (`{"p": path, "u": id}`) and the OIDC session would be signed with an empty key, so preview links for any file become forgeable. | Fail at startup when `secure_cookies` is on and the key is empty, `dev-only-change-me` or shorter than 32 characters. |
| UI-20 | G1, deployment | fstab glob puts every disk in the pool at boot | README §2 fstab line `/srv/buckets/volumes/* /srv/buckets/pool fuse.mergerfs …` | Expected: pending → active → drained states survive a reboot. Actual: after a host reboot every mounted disk, including **drained** ones (README tells admins to unmount only afterwards) and **pending** ones, becomes an RW branch again, until the app re-applies the branch list. Does the app re-apply on startup? `startup()` only scans; I found no call to apply the pool. | Mount the pool with an explicit branch list kept by the app, or call `apply_pool` on startup. |
| UI-21 | L1 | Connect page says the browser opens on first use; it doesn't | `claude mcp list` after installing the plugin: `plugin:artifact-buckets:artifact-buckets: http://localhost:8098/mcp (HTTP) - ! Needs authentication` | Expected (Connect step 2 and "Without the plugin"): "just ask Claude to save something. Your browser opens". Actual: tools stay unavailable until the person runs `/mcp` → Authenticate (the plugin README and `/buckets-setup` already say this correctly). | Make the Connect page say "Run `/mcp`, choose artifact-buckets, Authenticate", the same as the README. |

### Low

| ID | Feature | Title | Repro | Expected vs actual / fix |
|---|---|---|---|---|
| UI-22 | B10 | Trash retention hard-coded to 30 days | `trash_days.js` on an instance with `AB_TRASH_DAYS=7` | The delete dialog, toast, Trash subtitle and "Kept for" all say 30 days. Expose `trash_days` in `/api/auth/me` and use it. |
| UI-23 | D7 | A 3-day token shows "2 days" | `journey_admin.js` "tokens table" | `expires_in_days` uses `.days` (floor). Round up, or show the date. |
| UI-24 | A, J1 | `next=/\evil.example` leaves the person stuck on the login page | `journey_consent.js` step I: pageerror "replaceState … cannot be created". No off-site redirect happens, but after a successful sign-in the person stays on /login | `safeNext` should reject a backslash as the second character (`/\`), or normalise with `new URL(n, location.origin)` and compare origins. |
| UI-25 | D3 | Bare `/consent` → Connect shows raw FastAPI 422 JSON | `journey_consent.js` step E | Show "This sign-in link is incomplete. Start again from Claude Code." when params are missing. |
| UI-26 | J1 | Drop highlight sticks after dragging out of the window | `misc_checks.js` ("listing … hot"), `shots/m_drag_left.png` | Reset `dragging` on `dragleave` (when `relatedTarget` is null) and on `dragend`. |
| UI-27 | B5, F | Members see every routing rule, including other teams' targets, and team-wide save counts | `sec_checks.py`: member `GET /api/routing` returns 200 with all rules. Incoming shows "Refused, last 24 hours … team-wide" | Filter rules to those whose target the person can reach. Show members their own counts. |
| UI-28 | A3 | Logout has no CSRF check | `sec_checks.py`: `POST /api/auth/logout` with no header returns 200 and ends the session | Minor (forced logout from same-site pages). Add the CSRF header check. |
| UI-29 | A2, I1 (backend) | Failed sign-in with an unknown username logs actor "system", via "system", with no IP | `out_audit_formula.csv` last row | Log `via=web` and the client IP for unknown-user failures (A2/I1 want who and IP). |
| UI-30 | B14, J2 | Members can't see their own quota or usage anywhere | Member pages (`shots/maya_*_1440.png`); the mockup sidebar has a "Your quota 12.4 / 50 GB" card | Add the quota card for members (the data is in `user_info`). |
| UI-31 | I1 | Audit export only contains the rows on screen (max 200); there is no "Windows share" (`smb`) in the Through filter | `journey_admin.js` CSV step (108 rows = what was loaded) | Export server-side with the same filters. Add `smb` to the filter. |
| UI-32 | I2, J2 | Overview chart labels overflow at 390 px and the page scrolls sideways | `out_pages_390.json` (admin /overview hOverflow), `shots/admin_overview_390.png` | Show fewer labels, or rotate them, at narrow widths. Add `overflow:hidden` on `.xl`. |
| UI-33 | G3 | A drained volume reads "Moved 0 / 1" | `journey_admin.js` "after drain wait", `shots/a_storage_drain.png` | When the volume is drained, show the final count, or hide the counter. |
| UI-34 | I1 | Audit sentences say "changed qa-xss3 status" for both suspend and reactivate | `journey_admin.js` "audit user.*" | Include the new value ("suspended", "reactivated", "quota → 0.1 GB"). |
| UI-35 | B3 | Auditor's Files page logs a 404 (`/api/fs/list?path=/shared`) on every visit | `out_pages_1440.json` (lena /files) | Skip the shared and people tree for auditors, or make the call quiet. |
| UI-36 | J1 | Tags and project overflow the detail panel; the project sub-line spills out of the 56 px row | `detail_shot.js` (SPAN.tag right edge 1460 > 1440), `shots/d_0.png` | Let tags wrap (`white-space: normal; max-width: 100%`) and clamp the sub-line to one line with an ellipsis. |

### Polish

| ID | Feature | Title | Repro |
|---|---|---|---|
| UI-37 | J2 | Sidebar "Storage pool" card wraps into "Storage / pool" and "12.3 GB / 252 / GB" | `shots/admin_overview_1440.png` (bottom-left) |
| UI-38 | J2 | Truncated file and folder names have no `title` or tooltip, so long names can't be read | `shots/m_focus_rowbtn.png` ("fold-er-deep-deep-…") |
| UI-39 | J2 | Text glitches: "via rule # 2", "platform-eng· group of 3", "Deleted by" shows a username where other pages show display names | `journey_admin.js` preview and access-check steps |
| UI-40 | B14 | GB vs GiB: entering a quota of 0.1 GB displays "102 MB" | `journey_admin.js` "row after quota" |
| UI-41 | B17 | Markdown previews as raw text. A rendered (sanitised) view would match how people read `.md` artifacts | `shots/j_preview_notes.md.png` |
| UI-42 | J2 | Differences from the mockups: no expandable team-folder tree with counts, no inline "Who can open this" list in the detail panel (a button instead), no LIVE marker on Claude Code saves, no member quota card | `shots/mock_Files.png` vs `shots/j_preview_doc.pdf.png` |
| UI-43 | J2 | Mobile navigation is a wrapped pill cloud about 170 px tall above every page, with no hamburger, account or pool card | `shots/maya_files_390.png`, `shots/admin_overview_390.png` |
| UI-44 | J2 | Volume cards all show the same "12.3 GB used / 252 GB" in direct mode (host disk), which looks like a bug to an admin | `shots/a_storage_added.png`. Label it "shares the host disk" in direct mode |
| UI-45 | J1 | Edit person: a quota below 0.1 GB is silently blocked by the browser's `min` tooltip; the minimum isn't explained | `journey_admin.js` first run |

### Unconfirmed suspicions (not reproduced)

1. **PDF preview is blank.** PDFs render in `<iframe sandbox="allow-scripts">`, and the response also carries a CSP `sandbox`. Chrome's PDF viewer refuses to load in sandboxed frames. Headless Chromium has no PDF viewer, so I saw only a white box (`shots/j_preview_doc.pdf.png`). Check this in a real Chrome.
2. **`nt acl support = no` vs the UI's NTFS claim.** The SMB page says Explorer shows NTFS permissions, but with `nt acl support = no` Samba doesn't map POSIX ACLs to NT ACLs, so the Security tab will likely be empty or generic. Needs the lab.
3. **`[home] path = /srv/tree/users/%U` and username case.** `%U` is the session user as typed or as in AD (it may be mixed case), while the app lowercases usernames. On a case-sensitive filesystem the home share may not resolve. Needs the lab.
4. **Windows reserved names are accepted (backend).** `CON.txt` was accepted by `save_artifact`. That file can't be opened over SMB from Windows (B6 says Windows-invalid names are refused).
5. **Remotes with a port (backend).** `normalize_remote("https://host:8443/a/b")` gives `host/8443/a/b`, because the first `:` is replaced. Routing then won't match the SSH form. I didn't run this through the UI.
6. **Committed placeholder secrets.** `secrets/ad_join_user` and `ad_join_password` are committed placeholder files containing `x`. If an operator forgets them, the join runs as user "x". They should be gitignored, with a README step that creates them (the README already shows the commands).

## Coverage matrix

| Area / journey | Result | Evidence |
|---|---|---|
| All 12 pages × admin, member, auditor: load, no console errors | Pass (one auditor 404, UI-35) | `pages_by_role.js`, `out_pages_1440.json`, `shots/<role>_<page>_1440.png` |
| Role-restricted pages by URL (member) | Pass ("Only admins and auditors…"; API 403/404) | `out_pages_1440.json`, `sec_checks.py` |
| Role-specific controls hidden (auditor has no Add/Edit/Sync/Drain/Pause) | Pass | `shots/lena_*` |
| Sign in / wrong password / sign out | Pass | `journey_consent.js` H |
| Redirect back after sign-in (`next`) | Pass (edge case UI-24) | `journey_consent.js` H, I |
| Session expiry → login with next | Pass | `misc_checks.js` |
| Consent: login-first, Allow → code + state → token exchange | Pass | `journey_consent.js` A |
| Consent: Cancel → access_denied | Pass | step B |
| Consent: Enter key | **Fail** (UI-2) | step C |
| Consent: non-loopback redirect disclosed | **Fail** (UI-1) | `consent_phish.sh` |
| Consent layout at 390 px | Pass | `shots/c_consent_390.png` |
| Browse, breadcrumbs, tree | Pass | `journey_files.js` |
| Upload (chooser, multiple) | Pass | `journey_files.js` |
| Upload drag and drop | Pass (sticky highlight UI-26) | `journey_files.js`, `misc_checks.js` |
| Preview HTML (sandbox, cookies, parent, API) | Pass | `journey_files.js` postMessages: cookie/parent/localStorage SecurityError, origin `null`, fetch blocked |
| Preview SVG / image / text / JSON / markdown | Pass (markdown raw, UI-41) | `shots/j_preview_*` |
| Preview PDF | Not testable headless (suspicion 1) | |
| Download (attachment, same bytes) | Pass | `journey_files.js` |
| New folder | Pass | `journey_files.js` |
| Rename / move / delete file | Pass | `journey_files.js` |
| Rename / move / delete folder | **Fail** (UI-5) | |
| Trash restore | Pass (retention text UI-22) | `journey_files.js`, `trash_days.js` |
| Versions list / restore | Pass (stale panel UI-9) | `journey_files.js` |
| Share / unshare (person) | Pass | `journey_files.js` |
| Search (hits, no hits) | Pass | `journey_files.js` |
| Tokens create (shown once) / revoke | Pass (off-by-one UI-23) | `journey_admin.js` |
| Devices list / disconnect → token 401 | Pass (own devices). Admin view of others: **Fail** (UI-14) | `journey_consent.js` F |
| Routing rule add / remove / "where would it go" | Pass (admin). Owner: **Fail** (UI-16) | `journey_admin.js` |
| Pause / resume saves (banner) | Pass | `journey_admin.js`, `shots/a_incoming_paused.png` |
| Add person / edit role and quota / suspend / reactivate / filters / search | Pass | `journey_admin.js` |
| Groups create / add and remove member / folder grant add and remove | Pass | `journey_admin.js` |
| Access check | Partial (no "can X reach", UI-15) | `journey_admin.js` |
| Storage add / drain / last-volume refusal / undrain button / policy / default quotas persist | Pass (UI-33) | `journey_admin.js`, `shots/a_storage_*` |
| Directory page (not configured) / SMB page (off) | Pass (display only; AD/Entra/Samba not testable here) | `shots/admin_directory_1440.png`, `shots/admin_smb_1440.png` |
| Audit filters (action, person, path, via), row expand, CSV export | Pass (CSV injection UI-8, partial export UI-31) | `journey_admin.js`, `out_audit.csv` |
| Overview KPIs, chart range, attention links | Pass (token link UI-13) | `journey_admin.js` |
| XSS: file and folder names, display names, group descriptions, tags, project, git remote, commit, device names, file contents | Pass (no payload ran; spoofing UI-17) | `mcp_seed_hostile.py`, `xss_view.js`, `journey_admin.js` (`window.__xss` stayed null) |
| CSRF (API without/with wrong header, form upload, consent csrf field) | Pass (logout UI-28) | `sec_checks.py` |
| Member reaching admin data by URL or API | Pass | `sec_checks.py` |
| Layout at 1440 / 900 / 390 | Pass at 1440 and 900. 390: **Fail** on overview overflow, sign-out and detail placement (UI-11, UI-12, UI-32) | `out_pages_*.json`, `a11y_mobile.js` |
| Keyboard focus / modal focus / Escape | **Fail** for focus rings (UI-10). Modal keeps focus and Escape closes: Pass | `a11y_mobile.js` |
| Plugin validate | Pass (warnings only: no icon, no privacy URL) | `claude plugin validate` |
| Plugin marketplace add with the documented URL | **Fail** (UI-3) | |
| Plugin install from local path; MCP server registered | Pass | scratch HOME |
| Skill leads Claude to the right tools and args (haiku) | Pass: passed `project` and `git_remote`, routed by rule, reported path and reason. The link is broken (UI-4) | haiku run in `qa-ui/scratch` |

## Plugin review (L1, L2)

- **Manifests.** `marketplace.json` (name `lab-tools`, source `./artifact-buckets`) and `plugin.json` validate. The install id `artifact-buckets@lab-tools` matches the web UI.
- **`.mcp.json`.** `type: http`, URL `${ARTIFACT_BUCKETS_URL:-https://buckets.lab.local}/mcp`. The default expansion works: `claude mcp list` showed `http://localhost:8098/mcp … Needs authentication` with the variable set, and `https://buckets.lab.local/mcp` without it. Correct.
- **Marketplace URL** is the wrong format everywhere (UI-3).
- **Tool names and arguments in the skills all match `mcp.py`.** I checked: `save_artifact` (name, folder, path, content, content_base64, project, git_remote, git_commit, tags), `search_artifacts` (query, project, tag, folder), `list_folder`, `list_writable_folders`, `create_folder`, `rename` (new_name), `move` (from, to), `delete`, `list_trash`, `restore_from_trash`, `list_versions`, `restore_version`, `who_has_access`, `share_folder` and `unshare_folder` (group/user, role). Admin: `list_users` (query), `suspend_user` (suspend bool), `set_quota` (username/folder, gigabytes), `list_devices`, `revoke_device` (token_id), `grant_folder`, `map_ad_group`, `list_routing_rules`, `add_routing_rule` (match, pattern, target, priority; lower runs first, which matches `order_by(priority)`), `sync_directory`, `storage_status`, `add_volume_to_pool`, `drain_volume` (volume), `audit_search` (action, user, path, limit).
- **Result fields.** `routed_by` and `link` exist. `link` points at a broken page (UI-4).
- **Gaps in the skills:**
  - `buckets` never mentions `edit_file`, the "exactly one match" tool in E4 that suits small text edits. Add a line.
  - `buckets-admin` lists `audit_search` filters, but the MCP tool has no `via` argument, although the skill asks Claude to summarise "through which door". It can still read `via` from the results, so this is fine.
  - There is no MCP tool to remove a routing rule. The admin skill should say "remove rules in the web app".
- **`/buckets-setup`:**
  - Step 2's wording matches what Claude Code actually does (the server needs `/mcp` → Authenticate).
  - Step 3 says "the first folder in the result" is home; `list_writable_folders` puts `/users/<me>` first. Correct.
  - The setup command should also mention that `ARTIFACT_BUCKETS_URL` must be set **before** Claude Code starts. It does.
- **L1 promise** "first tool use opens the browser sign-in" doesn't hold. An explicit `/mcp` → Authenticate is needed. The README is right; the Connect page and FEATURES.md L1 wording are not (UI-21).

## Deployment review

- **Env names match `config.py`.** I checked `AB_BASE_URL`, `AB_DATABASE_URL`, `AB_TREE_ROOT`, `AB_VOLUMES_ROOT`, `AB_STORAGE_MODE`, `AB_SECRET_KEY`, `AB_SECURE_COOKIES`, `AB_BOOTSTRAP_*`, `AB_LDAP_*`, `AB_AD_DOMAIN`, `AB_IDMAP_BASE`, `AB_SMB_ENABLED`, `AB_SMB_HOST`, `AB_OIDC_*`, `AB_PLUGIN_MARKETPLACE` and `AB_STATIC_DIR`. Compose-only variables (`AB_HOSTNAME`, `AB_TLS_MODE`, `AB_TLS_DIR`, `AB_POOL`, `AB_VOLUMES`, `AB_AD_REALM`, `AB_AD_WORKGROUP`, `AB_SMB_NETBIOS`, `POSTGRES_PASSWORD`) are consistent between `.env.example` and compose.
- **High:** container and host volume path mismatch for mergerfs branches (UI-6).
- **Medium:**
  - An empty `AB_SECRET_KEY` is accepted (UI-19).
  - The fstab glob re-adds drained and pending disks at boot (UI-20).
  - Samba full_audit goes nowhere (UI-18).
- **Dockerfile.** Multi-stage, pinned bases, the optional CA secret is handled, the healthcheck works with the venv on PATH, and `--no-server-header` is set. `--forwarded-allow-ips "*"` is acceptable only because the app port isn't published. Add a comment, or set it to the Caddy network, so nobody later publishes 8080 and lets clients spoof audit IPs.
- **Compose:**
  - The app is `read_only` with a `/tmp` tmpfs, `cap_drop: ALL` plus `CHOWN`/`FOWNER`/`DAC_OVERRIDE`, and `no-new-privileges`. Good.
  - The db healthcheck gates the app. Good.
  - An empty `POSTGRES_PASSWORD` makes the postgres image refuse to start, which is fine but unexplained.
  - `AB_SMB_ENABLED=true` is the `.env.example` default even when the smb profile isn't started. It's harmless, but the SMB page will then report "Domain member" while no share exists.
  - Samba runs with `network_mode: host` and `SYS_ADMIN`. That's expected for a domain member, but there is no healthcheck.
- **Caddyfile.** `{$AB_TLS_MODE:internal}` snippet import, `request_body max_size 700MB` (above the 500 MB file limit) and `header -Server` are all correct. HSTS comes from the app (`AB_SECURE_COOKIES=true`). There is no `trusted_proxies` issue.
- **smb.conf.template.** SMB 3.1.1 minimum, encryption required, signing mandatory, NTLM off, guest off, `hide unreadable` for access-based enumeration, the internal dirs vetoed, and rid idmap `${AB_IDMAP_BASE}`, which matches the app formula. Concerns are in suspicions 2 and 3, plus UI-18.
- **entrypoint.sh.** The join password goes over stdin (good). `net ads join` reads it from stdin only when there is no TTY, which is fine in compose.
- **README.** The install command is wrong (UI-3). "Remove `AB_BOOTSTRAP_PASSWORD` after AD admins exist" is right, because bootstrap only runs on an empty database.

## Files

- Scripts:
  - `lib.js`, `pages_by_role.js`, `journey_files.js`, `journey_admin.js`, `journey_consent.js`
  - `misc_checks.js`, `a11y_mobile.js`, `xss_view.js`, `detail_shot.js`, `link_check.js`, `trash_days.js`, `mock_shots.js`
  - `mcp_seed_hostile.py`, `sec_checks.py`, `consent_phish.sh`, `cb_server.py`
- Fixtures: `fixtures/`
- Outputs: `out_*.json`, `out_audit*.csv`
- Screenshots: `shots/` (`mock_*` are the mockups rendered for comparison)
- The private instances on :8098 and :8097 are stopped. Their data stays in `/tmp/qa-ui`.
