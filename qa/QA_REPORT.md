# Artifact Buckets: independent QA report (sections A–I, K)

Tester: independent QA (did not write the code). Date: 2026-11-13.
Code under test: `/home/claude/ab/server/app/*.py` as found on that date. Spec: `/home/claude/ab/docs/FEATURES.md`.

## 1. Summary

**What I covered.** Every feature ID in A–I and K. I read all the backend code, ran the developer's suite, and wrote an
independent pytest suite. It covers functional checks and attacker scenarios: path tricks, symlinks, case twins,
moves into and out of shared folders, trash and version restore across people, token scope and folder limits, admin
powers through personal tokens, OAuth redirect, PKCE, code and refresh reuse, CSRF on every unsafe route, 403 vs 404
oracles, quota bypasses, AD sync edge cases, real POSIX ACLs after share, unshare, move and restore, storage drain,
audit completeness and 500s. I also re-ran the five most serious findings against a real `uvicorn` server with
`qa/scripts/live_smoke.py`.

**Test suite.** `/home/claude/ab/qa/` has 210 tests in 6 files plus `conftest.py`. It reuses the developer's fixtures
and adds a fake AD built on ldap3 MOCK_SYNC.

| Run | Pass | Fail |
|---|---|---|
| Developer suite `server/tests` (SQLite) | 47 | 0 |
| Developer suite `server/tests` (PostgreSQL 16) | 47 | 0 |
| QA suite `qa/` (SQLite) | 148 | 62 |
| QA suite `qa/` (PostgreSQL 16) | 149 | 61 |

The two databases give the same results with two exceptions. QA-19 fails only on SQLite (case-insensitive `LIKE`).
QA-27 fails only on PostgreSQL (column length is enforced).

Every failing test shows a defect listed below. The one exception is QA-11 (versions do not count toward quota),
which also needs a spec decision. No test is skipped or marked xfail.

**Re-running:**
```
cd /home/claude/ab/qa && ../.venv/bin/python -m pytest -q .
AB_TEST_DATABASE_URL="postgresql+psycopg://postgres@/qatest?host=/tmp/pg&port=5433" ../.venv/bin/python -m pytest -q .
# live check against a running server:
../.venv/bin/python scripts/live_smoke.py http://127.0.0.1:8099 <bootstrap password>
```

**Overall verdict: not ready.** The basics are solid: password sign-in, CSRF, private homes, roles, versions, OAuth
code+PKCE flow, refresh rotation, MCP transport and tool gating, and ACL mapping for grants on folders. The developer's
suite is green, but it misses several serious problems:
- A plain member can take admins' Claude Code saves.
- An admin's personal token carries full admin powers.
- AD sync can merge identities (it took over the bootstrap admin), suspends renamed people, keeps stale group
  memberships, and stops completely on one unusual account.
- Suspending someone in the app leaves them on the SMB share.

Findings: **1 Critical, 8 High, 12 Medium, 12 Low (33 in all).**

## 2. Findings (by severity)

Test names are `file::test`; files are in `/home/claude/ab/qa/`.

### Critical

| ID | Feature | Title | Reproduction | Expected / Actual | Suggested fix |
|---|---|---|---|---|---|
| QA-1 | F2, E3 | Any member can add a routing rule into their own home and capture every admin's Claude Code saves | `test_e_f_mcp_routing.py::test_F2_member_cannot_route_into_own_home`. Also confirmed live (`scripts/live_smoke.py`): bob POSTs `/api/routing {"match":"project","pattern":"*","target":"/users/bob/inbox/loot","priority":-1000}` and gets 200. The admin then calls MCP `save_artifact(name="minutes.md", project="board")`, the file lands at `/users/bob/inbox/loot/minutes.md`, and bob downloads it ("board-secret"). | Expected: only admins or Owners of the target *team* folder (`/shared/<group>`) can add rules (F2), so 403. Actual: 200. `routing.add_rule` accepts any target whose quota root the caller owns, and everyone owns `/users/<self>`. Admins are Editor everywhere, so `resolve()` sends their saves into the member's folder. | In `add_rule` and `delete_rule`, require `probe` to be under `/shared/<g>` for non-admins. Also consider limiting rule priority for non-admins and skipping rules from non-admins when the saver is not a member of that team (see section 4). |

### High

| ID | Feature | Title | Reproduction | Expected / Actual | Suggested fix |
|---|---|---|---|---|---|
| QA-2 | D7, K3, B3 | An admin's personal token carries admin powers | `test_d_oauth_tokens.py::test_D7_admin_pat_*` and `test_D7_K3_*` (11 tests). Confirmed live. An admin creates a PAT. With `Authorization: Bearer ab_pat_…` it can `GET /api/fs/download?path=/users/alice/inbox/diary.txt` (200, contents), read it over MCP `get_artifact`, `POST /api/fs/share` on any home, `GET /api/users`, `/api/audit`, `/api/access-check`, `/api/directory`, `/api/smb`, `/api/storage`, `/api/overview`, `POST /api/routing` (any target), and `DELETE /api/tokens/<other user's id>`. | Expected: "Admin powers never work through a personal token". Actual: only routes that call `require_admin` refuse a PAT. `perms.base_role` returns `owner` for any admin regardless of token kind. `require_auditor`, `routing.add_rule` (`actor.is_admin`) and `tokens_revoke` (`actor.is_admin`) also ignore the token kind. | Add a single check, such as `Actor.is_admin` returning False (and `is_auditor` treating admin as member) when `token.kind == "personal"`. Then `base_role`, routing, token revoke, the auditor views and MCP all follow from it. |
| QA-3 | C3, C2 | An AD account whose sAMAccountName matches a local account takes it over (the bootstrap admin is demoted) | `test_c_groups_directory.py::test_C3_ad_name_matching_local_account_does_not_take_it_over`. AD has user `admin` (SID …-1500) in `AB-Platform`. After a sync, the local bootstrap `admin` is `source=ad, system_role=member, ad_sid=…-1500` and gets 403 on `/api/users`. | Expected: local accounts are left alone, or the clash is reported. Actual: `directory.sync` falls back to `User.username == sam`, rewrites `source/ad_sid/uid_number`, and then sets `system_role` from AD. The bootstrap admin loses admin rights and the AD person (via Entra username match, `source == "ad"`) can now sign in to that account and its home. | Match AD people by SID only. If the username is taken by a local account or by an account with a different SID, skip that person and record a sync warning. Never change `system_role` of `source=local` users. |
| QA-4 | C3 | A new AD person who reuses a departed person's name inherits their account, home and files | `test_c_groups_directory.py::test_C3_new_person_reusing_a_name_does_not_inherit_old_home`. dana (SID …-1101) syncs, has files, then leaves (suspended). A different dana (SID …-1200) joins. Sync reactivates the old account and writes the new SID and uid onto it. | Expected: a different SID is a different person. Actual: same row, `status=active`, `uid_number=101200`, owns `/users/dana`. | Same as QA-3. When a username row has a different non-empty `ad_sid`, do not reuse it. Create a separate account (for example `dana-2`) or flag it for an admin. |
| QA-5 | C3 | A person renamed in AD (same SID, new sAMAccountName) is suspended on every sync | `test_c_groups_directory.py::test_C3_renamed_ad_user_stays_active`. Sync `dana` (…-1101), then sync `dana.smith` (…-1101) in the same group. Result: `status=suspended`, all tokens revoked. On the next sync they are reactivated, then suspended again at the end of the same run. | Expected: still active. Actual: suspended. The user is found by SID, but `seen_users` is keyed by the new sam and the final loop tests `u.username not in seen_users` (old name). | Key `seen_users` by user id, not by sam. Decide whether to rename `username` and home (spec gap). |
| QA-6 | C2, C3 | When a group is renamed or deleted in AD, the app group keeps its old members, so people removed in AD keep team-folder access | `test_c_groups_directory.py::test_C3_group_renamed_in_ad_keeps_one_group` and `::test_C3_group_deleted_in_ad_loses_members`. `AB-Eng` {dana, eli} is renamed to `AB-Engineering` {dana} (same SID), and eli moves to another AB- group. eli is still in app group `eng` and keeps Editor on `/shared/eng`. A deleted `AB-Old` likewise keeps its members. | Expected: membership follows AD. Actual: groups are looked up by `ad_name == cn`. A renamed group becomes a new group, and groups missing from AD are never touched, so `set_members` is not run on them. | Match groups by `ad_sid` first and update `ad_name`. For `source=ad` groups not seen in a sync, clear their members (or flag them) and report them. |
| QA-7 | C2 | One unusual AD account aborts the whole directory sync | `test_c_groups_directory.py::test_C2_one_odd_account_does_not_stop_the_sync`. A synced group that contains a computer account (`PC01$`, objectClass user) or a user `John Smith` makes `accounts.create_user` raise `BadRequest("Usernames use lower-case …")`, and the sync is rolled back. | Expected: skip and report that account; sync everyone else. Actual: no one is synced. In particular, disabled or removed people are **not suspended** while the bad account remains. In AD a computer account in a group is common. | Exclude computer objects (`objectCategory=person` or not ending in `$`), wrap each member in try/except, and collect warnings in `stats`. |
| QA-8 | C6, H1 | Suspending someone in the app leaves them on the SMB share | `test_g_h_i_storage_smb_audit.py::test_H1_C6_manual_suspension_removes_smb_access`. With SMB enabled and AD synced, `PATCH /api/users/<dana> {"status":"suspended"}` leaves `user:101101:rwx` on `/users/dana` (and on every folder shared with her). Her AD Kerberos login still works, so she keeps share access until an AD sync runs. The sync calls `reconcile_all`, but only when AD is configured. | Expected: suspension ends access (C6). The module docstring says suspended people lose SMB access. Actual: `update_user` → `end_access` never touches ACLs (`acl.desired` already skips suspended users, but nothing re-applies it). | Call `acl.reconcile_all` (or a targeted re-apply of the user's grant folders) from `end_access` and from reactivation. |
| QA-9 | B6, B4 | A symlink inside the tree gives cross-home read and write | `test_b_files_perms.py::test_B6_symlink_inside_tree_cannot_reach_other_home`. With a symlink `/users/bob/projects/link -> <tree>/users/alice`, bob can `GET /api/fs/download?path=/users/bob/projects/link/inbox/diary.txt` (alice's private text) and can save into alice's inbox through it. | Expected: "No symlink escape". Actual: `paths.disk` only checks that the real path stays inside the tree root, and permissions are evaluated on the *logical* path (bob's home). Precondition: a symlink must exist in the tree, for example from SMB with unix extensions or from extracted archives. The app has no API to create one. | Refuse any path whose resolved real path differs from the joined path (`os.path.realpath(full) != os.path.normpath(full)`), or open with `O_NOFOLLOW` on each component. The indexer should also skip symlinks. |

### Medium

| ID | Feature | Title | Reproduction | Expected / Actual | Suggested fix |
|---|---|---|---|---|---|
| QA-10 | B14 | Restoring from trash ignores the quota | `test_b_files_perms.py::test_B14_restore_from_trash_cannot_exceed_quota`. Quota 1000. Save 900, delete, save another 900, restore: 200, and usage is 1800/1000. | Expected 413. `restore_trash` never calls `_check_quota`. | Check the quota against `t.size` before moving back. |
| QA-11 | B14 | Old versions (and trash) don't count toward the quota | `test_b_files_perms.py::test_B14_versions_count_towards_quota`. Quota 1000, 10 different saves of 900 bytes: all 200, and 9000 bytes are kept as versions. A probe showed `used_bytes=900` with 9090 bytes on disk. With no `keep_versions` by default, this is unbounded. | The spec is silent. As built, quota is not a storage limit. **Needs a spec decision.** | Count `FileVersion.size` (and trash) in `usage()`, or set a default version limit. |
| QA-12 | B12 | The home owner's grant can be downgraded through share | `test_b_files_perms.py::test_B12_home_owner_grant_cannot_be_downgraded`. alice shares `/users/alice` with bob as Owner. bob POSTs `/api/fs/share {"path":"/users/alice","kind":"user","name":"alice","role":"viewer"}` and gets 200. alice is now Viewer of her own home. | Expected 403, as for removal. `share()` updates the existing grant without the home-owner guard from `remove_grant`. | Apply the same guard in `share()`: the owner grant on `/users/<u>` must stay `owner`. |
| QA-13 | B11 | Admins can move `/users/<name>` and `/shared/<group>` | `test_b_files_perms.py::test_B11_protected_cannot_be_moved_even_by_admin` (3 cases). Also live: `POST /api/fs/move {"src":"/users/alice","dst":"/users/alice-moved"}` as admin gets 200. alice's home moves away, her owner grant follows, and a new `/users/alice` is not provisioned. Moving `/shared/eng` to `/shared/eng2` detaches the group folder. | Expected: "can't be deleted or moved". Actual: `fs.move` exempts admins. Admins can also move items to top level (`/users/x`), creating fake homes. | Refuse moves of protected paths and to depth < 3 for everyone. Renames of people or groups belong in a dedicated admin action. |
| QA-14 | B5 | Existence oracle through move, share and delete | `test_b_files_perms.py::test_B5_*` (3 tests). bob probing alice's home: `move src=<existing> dst=/users/x` gives **403** "Items go inside…", while a missing src gives **404**. `share` on an existing folder gives 404 "No such file or folder, or you do not have access", while a missing one gives 404 "Sharing works on folders". `delete /users/alice` gives 403 "Home and team folders cannot be deleted here", while `/users/nobody` gives 403 "You need viewer access…" (reveals usernames). | Expected: the same status and message whether or not the item exists. Actual: the structural checks run before the permission check. | Do the permission check (`_need`) first in `move`, `share` and `delete`, then the structural checks. |
| QA-15 | H2, H3 | Files moved or restored by the app keep their old ACL, and file or sub-folder drift is not detected | `test_g_h_i_storage_smb_audit.py::test_H2_moved_file_gets_new_folder_acl`, `::test_H2_restored_file_gets_current_folder_acl`, `::test_H3_drift_on_files_and_subfolders_is_reported`. Moving `plan.md` from `/shared/platform/projects` to `/shared/design/projects` leaves `group:102001:rw-` (platform) and no 102002 (design) on the file. A file restored after a new share lacks the new group. Manual `setfacl` changes on a sub-folder without a grant, or on any file, give `drift == []`. | Expected: files get their folder's permissions, and drift is reported. Actual: `acl.on_change(dst)` calls `apply_dir`, which returns 0 for a file path. `drift()` only inspects `/users`, `/shared` and folders that hold grants. Reconcile does fix it, but nobody is told to run it. | In `on_change`, if the path is a file, apply `on_new_file`. Make `drift()` walk the tree, or a sample of it, including files. |
| QA-16 | C3 | An admin's hand suspension can be undone by sync | `test_c_groups_directory.py::test_C3_admin_suspension_survives_ad_disable_enable` and `::test_C3_admin_suspends_while_sync_flag_set`. (a) Admin suspends dana; AD disables and later re-enables her; she is reactivated. (b) Sync suspends her, admin reactivates by hand, later suspends by hand, then she is re-added in AD; she is reactivated. | Expected: "reactivated, unless an admin suspended them by hand". Actual: the `suspended_by_sync:<id>` flag is set by sync even when she was already suspended by hand, and is never cleared when an admin changes status. | Clear the flag on every admin status change. In sync, set it only when sync itself makes the change from active. |
| QA-17 | I3 | Access check cannot answer "can X reach Y?" | `test_g_h_i_storage_smb_audit.py::test_I3_access_check_answers_for_a_person`. `GET /api/access-check?path=/shared/eng/projects&user=alice` returns the same grant list for alice (no access) and bob (Editor). | Expected: an answer for a person (yes/no, role, which grant). Actual: only "who has access to this folder"; `user` is ignored. MCP has no equivalent tool. | Add `user`: compute `base_role` for that person and return the winning grant or grants and group path. |
| QA-18 | I1 | Denied admin actions and preview reads are not audited | `test_g_h_i_storage_smb_audit.py::test_I1_admin_denials_are_audited`: a member's `POST /api/users`, `PATCH /api/users/<admin>` and `PUT /api/ingest/settings` all get 403 with no audit row. `::test_I1_preview_reads_are_audited`: opening `/preview/<token>/x.html` writes no audit entry. | Expected: "denials" and "reads" are logged. Actual: only file-permission denials (`_need`) are logged. | Log in `require_admin` and `require_auditor` (and the MCP `Tool not available` path), and log `file.preview` in `preview()`. |
| QA-19 | B9, B10 | On SQLite, case-twin folders are mixed up: a grant on `A/sub` moves to `c/sub` when `a` is renamed | `test_b_files_perms.py::test_B9_case_twin_folders_are_not_mixed_up` and `::test_B9_case_twin_delete_keeps_sibling_grants`. Linux allows `projects/a` and `projects/A`. bob has Viewer on `A/sub`. alice renames `a` to `c`. bob can now read `c/sub/lower.txt` and has lost `A/sub`. Deleting `a` deletes bob's grant on `A/sub` and moves `A`'s file rows to trash. | SQLite `LIKE` is case-insensitive, and `_rewrite_prefix`, `_grants_inside`, `delete`, `usage` and `can_traverse` use `LIKE`. **SQLite only**: these tests pass on PostgreSQL. Dev and lab installs that use SQLite are affected. | Use `PRAGMA case_sensitive_like=ON` on SQLite connections, or use `substr(path,1,n) = prefix` instead of `LIKE`. |
| QA-20 | G3 | Draining a volume loses the file owner | `test_g_h_i_storage_smb_audit.py::test_G3_drain_keeps_owner_and_acl`. A file owned by uid 101101 on d1 is owned by uid 0 after the drain (the ACL is kept). | Owners are shown over SMB as file owner. `shutil.copy2` does not chown. | `os.chown(dst, st.st_uid, st.st_gid)` after the copy, or use `os.rename` on the same filesystem. |
| QA-21 | E6 | MCP returns HTTP 500 for non-object `params` | `test_e_f_mcp_routing.py::test_E6_malformed_requests_get_jsonrpc_errors_not_500` (2 cases). Also live: `{"jsonrpc":"2.0","id":1,"method":"tools/call","params":[1]}` gives 500. `"params":"x"` on initialize gives 500. | Expected: JSON-RPC error -32602. Actual: `params.get` raises AttributeError. | Check `isinstance(params, dict)` in `handle_message`. |

### Low

| ID | Feature | Title | Reproduction | Expected / Actual | Suggested fix |
|---|---|---|---|---|---|
| QA-22 | G3 | Drain silently overwrites a file that already exists on the target volume | `test_g_h_i_storage_smb_audit.py::test_G3_drain_does_not_overwrite_existing_file_on_target`. The same relative path on d1 and d2: after the drain, d2's content is replaced with d1's. | Data loss when branches overlap (possible with mergerfs). | If `dst` exists, keep the newer copy or move to a conflict name, and log it. |
| QA-23 | G3 | Drain progress never reaches the total | `test_g_h_i_storage_smb_audit.py::test_G3_drain_moves_everything_and_reports_progress`. 5 files give `{"moved":5,"total":6}`. | `drain_total` counts the marker file but `drain_moved` does not. | Exclude `MARKER` from `drain_total`. |
| QA-24 | B7, B9 | Save, mkdir or move to a path that goes through a file gives 500 | `test_b_files_perms.py::test_B7_save_under_a_file_is_not_500` and `::test_B9_move_into_path_through_file_is_not_500`. `save-text /users/alice/inbox/a.txt/x/y.txt` (a.txt is a file) gives 500 NotADirectoryError. mkdir and move behave the same. | Expected 409 or 400. | Catch `NotADirectoryError` and `FileExistsError` in `ensure_dir`, or check each ancestor. |
| QA-25 | B8, E6, E5 | Out-of-range integers give 500 | `test_b_files_perms.py::test_B8_huge_version_number_is_not_a_500` (REST `restore-version version=1e20`) and `test_e_f_mcp_routing.py::test_E6_out_of_range_numbers_are_tool_errors` (MCP `restore_version`, `revoke_device token_id=1e20`, `set_quota gigabytes=1e300`). | SQLite OverflowError or PostgreSQL DataError leaks as 500. | Bound integers (pydantic `conint`, and range checks in `_validate`). |
| QA-26 | D2 | `/oauth/register` with JSON that is not an object gives 500 | `test_d_oauth_tokens.py::test_D2_register_garbage_is_not_500`. Bodies `[]`, `"x"` and `null` give 500 (confirmed live). | Expected 400 `invalid_client_metadata`. | `if not isinstance(body, dict): return 400`. |
| QA-27 | D4 | An over-long `code_challenge` gives 500 on PostgreSQL | `test_d_oauth_tokens.py::test_D4_long_pkce_challenge_is_not_500` (fails only with `AB_TEST_DATABASE_URL` set). A 300-character challenge on `/oauth/approve` overflows `String(128)`. The same applies to `redirect_uri` over 1024. | Expected `invalid_request`. | Check length in `_check_authorize` (an S256 challenge is 43 characters). |
| QA-28 | B14, E5 | Quota inputs are not validated | `test_b_files_perms.py::test_B14_folder_settings_quota_validation` (`quota_gb` -1 and 0 accepted; negative blocks every save, 0 silently falls back to the default). `::test_B14_quota_on_non_root_folder_is_rejected_not_ignored` (a quota on `/shared/eng/projects` is stored but never enforced). `::test_B14_storage_quota_nan_inf_not_500` (`Infinity`/`NaN` give 500). `test_e_f_mcp_routing.py::test_E5_set_quota_zero_or_negative_refused` (MCP `set_quota folder=… gigabytes=0` accepted despite `exclusiveMinimum`). | Expected 400. | Validate `> 0`, finite, and only on quota roots. Enforce `exclusiveMinimum` in `_validate`. |
| QA-29 | B10 | A folder Owner can restore someone else's deleted item | `test_b_files_perms.py::test_B10_only_deleter_or_admin_restores`. carol (Owner of `/shared/eng`) restores alice's trash id and gets 200. | Spec: only the deleter or an admin. The code also allows folder Owners. Low because the id is a random UUID that only the deleter sees. | Align the code or the spec. |
| QA-30 | F1 | Git remote normalisation misses common forms | `test_e_f_mcp_routing.py::test_F1_remote_normalisation`. `ssh://git@git.lab:22/eng/svc.git` gives `git.lab/22/eng/svc`. `https://git.lab/eng/svc.git/` gives `git.lab/eng/svc.git`. | Both should be `git.lab/eng/svc`. Rules then silently do not match. | Parse with `urlsplit` for URL forms (drop the port). Strip `/` before `.git`. |
| QA-31 | A3 | Sign-out has no CSRF check | `test_a_k_accounts_security.py::test_A3_logout_needs_csrf`. A cookie-only `POST /api/auth/logout` gives 200 and ends the session. | Spec: any cookie-authenticated POST without the header gets 403. Impact is limited (SameSite=Lax, logout only). | Require the header when a session cookie is present. |
| QA-32 | B6 | A trailing space on the last name is silently trimmed | `test_b_files_perms.py::test_B6_bad_paths_refused[/users/alice/inbox/trail ]`. Saved as `trail`, 200. | Spec: names ending in a space are refused (they are for inner parts). `normalize()` strips the whole path first. | Strip only leading `/` and whitespace of the full path, or refuse. |
| QA-33 | I2 | Overview shows 7 days of saves, not 14 | `test_g_h_i_storage_smb_audit.py::test_I2_overview`. `series` has 7 entries by default. | Spec says 14. | Default `days=14`. |

## 3. Coverage matrix

"Pass" means every QA test for the ID passes (plus the developer's tests). "Fail" lists the findings.

| ID | Result | Notes |
|---|---|---|
| A1 | Pass | Also: restart with another bootstrap password does not reset the admin. |
| A2 | Pass | HttpOnly/Lax and Secure+HSTS when `AB_SECURE_COOKIES`; wrong password and unknown user audited with ok=false; suspended user refused. |
| A3 | Fail (QA-31) | All 30 other unsafe `/api` routes return 403 without the header; `/oauth/approve` checks its CSRF field. |
| A4 | Pass | The old cookie value is dead server-side. |
| A5 | Pass (logic only) / lab | `match_oidc_user` (SID, then UPN), unknown or suspended refused and audited, and `next` cannot redirect off-site (tested with a fake Entra client). The real Entra round trip needs the lab. |
| A6 | Pass | argon2id; 12-character minimum on create and change. |
| B1 | Pass | |
| B2 | Pass | Inheritance, highest wins, Viewer and Editor limits. |
| B3 | Pass | Auditor cannot list, open or search others' files. Admin-via-PAT is QA-2. |
| B4 | Pass | Breakable only through QA-2 (admin PAT) and QA-9 (symlink). |
| B5 | Fail (QA-14) | Listings and 404s are otherwise correct. |
| B6 | Fail (QA-9, QA-32) | 20 bad-path cases refused; URL-encoded `..` refused; a symlink leaving the tree is refused. |
| B7 | Fail (QA-24) | Versioning, no-op on identical content and upload versioning are correct. |
| B8 | Fail (QA-25) | List, restore-as-new-version, prune to `keep_versions` and the Viewer restriction are correct. |
| B9 | Fail (QA-19 on SQLite, QA-24) | Grants and versions move; cannot move into itself; folders with their own sharing need Owner to move or delete. |
| B10 | Fail (QA-29) | Grants restored; others cannot list or restore; purge after retention; restore refused after losing access. |
| B11 | Fail (QA-13) | Deletes of protected folders are refused for everyone. |
| B12 | Fail (QA-12) | Share and unshare, bad role or group, file target, and non-owner refusal are correct; removing the home grant is refused. |
| B13 | Pass | Inherited and direct grants plus admins. |
| B14 | Fail (QA-10, QA-11, QA-28) | Person and team quotas and moves between quota roots are enforced. |
| B15 | Pass | REST save, upload and MCP all give 413 too_large. |
| B16 | Pass | Name, project, tag, folder, token folder limit, trash excluded, `%` and `_` literal. |
| B17 | Pass | Sandboxed CSP without allow-same-origin, attachment downloads, tamper and expiry give 403, revoked access gives 404. QA-18 for audit. |
| B18 | Pass | 50 files allowed, 51 refused over MCP. |
| C1 | Pass | |
| C2 | Fail (QA-3, QA-6, QA-7) | Prefix (case-insensitive), roles, several groups, nothing written to AD. |
| C3 | Fail (QA-4, QA-5, QA-6, QA-16) | Basic disable, remove and return works. |
| C4 | Pass | Add or remove members and user-create-into-AD-group refused. |
| C5 | Pass | REST and MCP. |
| C6 | Fail (QA-8) | Sessions, PATs, OAuth access and refresh revoked and not revived on reactivation. |
| D1 | Pass | |
| D2 | Fail (QA-26) | Nine bad redirect forms refused, four good ones accepted. |
| D3 | Pass | Login, then back; consent; any loopback port; unregistered redirect gives 400 (no redirect); deny gives access_denied; no session gives 403. |
| D4 | Fail (QA-27, PostgreSQL) | `plain` refused, wrong verifier, single use, 5-minute expiry, bound to client and redirect. |
| D5 | Pass | 60 minutes and 30 days, rotation, reuse revokes the family, revoke endpoint, refresh token not accepted as access, expiry. |
| D6 | Pass | One device per sign-in across refreshes; another user cannot revoke; admin can. |
| D7 | Fail (QA-2) | Scope and days validation, created only from the web (PAT and OAuth bearer refused), read scope caps every write route, folder list limits every route. |
| E1 | Pass | 405, JSON, 202 for notifications, batch, Origin check. |
| E2 | Pass | Member, admin, auditor, read-scope and PAT tool lists; hidden tools cannot be called. |
| E3 | Fail (QA-1 via F2) | Folder, rule, project, inbox and `routed_by` are correct. |
| E4 | Pass | All 17 file tools; bob cannot use any of them on alice's files. |
| E5 | Pass | All admin tools run (sync without AD gives a readable isError). Validation gaps are QA-28. |
| E6 | Fail (QA-21, QA-25) | -32601, -32602, -32700 and isError for bad arguments are correct. |
| E7 | Pass | save, edit_file and restore_version refused while paused and audited; the web still saves; members cannot pause. |
| F1 | Fail (QA-30) | Globs, priority and `{project}`/`{user}` work. |
| F2 | Fail (QA-1) | Team Owner allowed; Editor refused for add and delete. |
| F3 | Pass | |
| G1 | Pass | pending → active, missing and back, marker survives a database reset, hidden folders ignored. |
| G2 | Pass (direct) / lab | The mergerfs xattr branch update needs the lab. |
| G3 | Fail (QA-20, QA-22, QA-23) | Moves everything; the last active volume cannot be drained; undrain works. |
| G4 | Pass (direct) / lab | Policy saved and validated. That mergerfs applies it needs the lab. |
| H1 | Fail (QA-8) | Viewer r-x/r--, Editor rwx/rw-, AB-Admins owner, `other::---`, local users absent, uid = 100000 + RID. |
| H2 | Fail (QA-15) | Files saved and uploaded through the app (REST, upload, MCP) get the folder ACL. |
| H3 | Fail (QA-15) | Drift on a granted folder is reported and reconciled. |
| H4 | Pass | Indexed with source `smb`; dot files skipped. |
| H5 | Not testable here | Needs Samba, a domain join and Kerberos (lab). |
| I1 | Fail (QA-18) | Sign-ins, saves, reads, downloads, moves, deletes, shares, tokens, OAuth, restores; via web, mcp or api; token name; IP. |
| I2 | Fail (QA-33) | Counts, pool and attention are present; auditor only. |
| I3 | Fail (QA-17) | |
| K1 | Pass | CSP with frame-ancestors none, nosniff, no-referrer and no-store on /api, /oauth and /mcp, including 401 and 404; HSTS when secure. |
| K2 | Pass | PAT, OAuth access and refresh, codes and web sessions stored only as SHA-256; a PAT never appears in listings. |
| K3 | Fail (QA-2) | |

## 4. Unconfirmed suspicions, observations, and gaps in the feature list

### Suspicions and observations (not counted as findings)
1. **The event loop is blocked by MCP and OAuth work.** `mcp_post` and `/oauth/token` are `async def` but do synchronous
   database and disk work. With the real server, a 100 MB MCP save stalled `/healthz` by up to 0.53 s (normally a few ms).
   Under real load, one large save would delay every request. Measured, but impact at scale is not established.
2. **OAuth code single use is not atomic.** There is no row lock or conditional update on `used`. A single process
   serialises requests (see 1), so I could not race it. With several workers, two exchanges of one code could both
   succeed. Not reproduced.
3. **Entra email matching (nOAuth pattern).** `match_oidc_user` matches the `email` and `preferred_username` claims
   against `User.email` for any account, including local admins. Entra `email` can be unverified or user-editable.
   Code reading only (lab).
4. Team Owners' routing rules also capture **admins'** personal Claude Code saves (an admin can write to any team
   folder, so `resolve()` picks the rule). This matches the spec wording of E3 but is probably not intended (see
   gaps). I reproduced it and then removed the test from the suite because it is not a defect against the spec.
5. Members can `GET /api/routing` and see every rule, including other teams' targets and patterns.
6. Nested AD groups are ignored. Members of a group nested inside an `AB-` group are not synced (observed in
   `test_C3_nested_group_members_observation`). The spec does not say whether they should be.
7. A personal token limited to `/users/alice/projects/p` cannot list `/users/alice/projects` to reach it (404). This
   is probably fine.
8. If `POST /api/users` fails on a bad group name, the database rolls back but `/users/<name>` is left on disk (code
   reading).
9. Folder settings rows (`keep_versions`, quota) are not removed when the folder is deleted, so they apply to a new
   folder at the same path (code reading).
10. Windows reserved names (`CON`, `NUL`, `COM1`, `AUX`) are accepted. SMB clients cannot open them. The spec does not
    list them (code reading).
11. A refresh token presented with the wrong `client_id` revokes the legitimate sign-in. This is defensive, but it
    lets anyone holding a stolen token log the victim out. Refresh rotation also gives a sliding 30-day lifetime with
    no absolute cap.
12. Changing a password does not end other sessions or tokens.
13. MCP `delete` can be confirmed only in the web app, but REST with a personal token deletes any size without
    confirmation. This matches the spec (B18 says "over MCP").

### Gaps in the feature list
- **B14:** whether versions and trash count toward the quota (QA-11). With unlimited versions by default this decides
  whether quotas mean anything.
- **D7 and K3:** define "admin powers": file access as Owner everywhere, read-only admin and auditor views, revoking
  other people's tokens and routing. QA-2 assumes all of these.
- **E3 and F2:** whether a team rule should apply to people who can write there only because they are admins, and
  whether rule priority is capped for non-admins.
- **C2 and C3:** say that identity is the AD SID, and what happens on name change, name reuse, local-account clash,
  group rename or delete, nested groups, and computer or invalid accounts.
- **C6:** say whether SMB access ends on suspension (the code docstring says yes).
- **I1:** sign-out, preview opens and denied admin attempts are not listed; retention of the audit log is not stated.
- **I3:** define inputs and output (the person, the answer, the winning grant).
- **E7:** say whether pause also covers MCP move, rename and delete (today only content writes are paused).
- **A2:** no sign-in rate limiting or lockout is specified (none exists).
- **B6:** Windows reserved device names; Unicode normalisation (NFC vs NFD names look identical over SMB).
- **G3:** behaviour when the same path exists on two branches; whether owner and ACLs must be kept.

## Files
- Report: `/home/claude/ab/qa/QA_REPORT.md`
- Suite: `/home/claude/ab/qa/` (`conftest.py`, `test_a_k_accounts_security.py`, `test_b_files_perms.py`,
  `test_c_groups_directory.py`, `test_d_oauth_tokens.py`, `test_e_f_mcp_routing.py`, `test_g_h_i_storage_smb_audit.py`)
- Live re-check: `/home/claude/ab/qa/scripts/live_smoke.py`
