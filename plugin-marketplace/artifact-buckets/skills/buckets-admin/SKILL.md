---
name: buckets-admin
description: Administer Artifact Buckets as an admin or auditor through the artifact-buckets MCP tools. Use it when an admin asks to list or suspend people, set quotas, see or revoke signed-in devices and tokens, give a group access to a folder, add routing rules for where Claude Code saves land, sync Active Directory or map an AD group, check storage or add or drain a volume, or when an auditor asks what happened in the audit log.
---

# Artifact Buckets administration

These tools exist only for admins (and `audit_search` for auditors), and only when signed in through the browser sign-in, never with a personal token. If a tool is missing, the person is not an admin, or is using a personal token; say so.

Admin actions affect other people. Before any change below, state exactly what will happen and get a clear yes:
`suspend_user`, `revoke_device`, `set_quota`, `grant_folder`, `add_routing_rule`, `map_ad_group`, `sync_directory`, `add_volume_to_pool`, `drain_volume`.

## People

- `list_users` (optionally with `query`) shows role, groups, storage used and quota, last activity.
- `suspend_user` with `suspend: true` signs the person out everywhere at once (browser, every Claude Code device, every token). Their files stay. `suspend: false` reactivates.
- People from Active Directory are managed in AD: add them to an `AB-` group and run `sync_directory`. `AB-Admins` makes admins, `AB-Auditors` makes auditors. Don't try to change their role here.
- `set_quota` with `username` or a team `folder` (`/shared/<team>`) and `gigabytes`.

## Devices and tokens

- `list_devices` (optionally for one `username`) shows Claude Code sign-ins and personal tokens, with last use.
- `revoke_device` with `token_id` ends that sign-in or token immediately.

## Access

- `grant_folder` gives a `group` or `user` a role (`viewer`, `editor`, `owner`) on any folder; it applies to everything below. Use `who_has_access` first to see what is already there, and prefer groups over individual people.
- `map_ad_group` brings in an AD group whose name doesn't start with `AB-`; then `sync_directory`.

## Where Claude Code saves land

- `list_routing_rules`, then `add_routing_rule` with `match` (`git_remote` or `project`), a glob `pattern` (for example `git.example.com/platform/*`), a `target` that may use `{project}` and `{user}` (for example `/shared/platform/projects/{project}`), and an optional `priority` (lower runs first).
- Remotes are compared without protocol, user and `.git`, so SSH and HTTPS forms match the same rule.
- A rule only applies to people who can write to its target; others fall back to their own projects folder.

## Storage

- `storage_status` shows the pool, each volume's state and free space, and the placement policy.
- `add_volume_to_pool` with a pending `volume` name adds it straight away; nobody's paths change.
- `drain_volume` stops new files landing on a volume and moves its files to the others in the background. The last active volume can't be drained. When `storage_status` shows it drained, it can be unmounted.

## Audit (admins and auditors)

- `audit_search` with `action` (wildcards like `file.*`, `grant.*`, `signin.*`, `denied`), `user`, `path` prefix, and `limit`.
- Summarize what happened in plain words: who, what, when, through which door (browser, Claude Code, API) and from which device.
