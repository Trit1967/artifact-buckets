---
name: buckets
description: Use Artifact Buckets, the team file manager, through the artifact-buckets MCP tools. Use it whenever the person asks to save, keep, store, upload or publish a file, report, dashboard, diagram, page or other artifact "to buckets", "to my folder", "to the team folder" or "to the file manager"; to find, open, list, search, move, rename, delete, restore or share files there; or to see who can open a folder.
---

# Artifact Buckets

Artifact Buckets keeps the files people make with Claude Code in one place, with folders per person and per team, permissions, versions and a trash. Every tool call acts as the signed-in person, with exactly their permissions.

## Where things live

- `/users/<name>`: the person's home. `inbox/` for loose files, `projects/<project>/` for work from a repository.
- `/shared/<team>`: team folders. Members are usually Editors.
- Roles on a folder apply to everything inside it: **Viewer** (open, download), **Editor** (also save, rename, move, delete), **Owner** (also share).

## Saving

Use `save_artifact`.

- Always pass `project` (the repository folder name) and `git_remote` (from `git remote get-url origin`) when working in a repository. The server then picks the right folder: a team routing rule if one matches, otherwise `/users/<name>/projects/<project>`.
- Only pass `folder` when the person names a place ("save it to the platform team folder" → find the path with `list_writable_folders`, then pass it).
- Pass `name` (just the file name) unless the person gave a full path; then pass `path`.
- Send text as `content`. Send binary files (images, PDFs) as `content_base64`.
- Add `git_commit` (current `git rev-parse HEAD`) and short `tags` when useful.
- Saving the same path again makes a new version; nothing is overwritten for good.
- After saving, tell the person where it went (the `path`), why (`routed_by`), and give the `link`.

If a save is refused, say why in plain words and what to do:
- no access → offer `list_writable_folders` and ask where instead;
- over quota or too large → say the limit from the message;
- paused → saves from Claude Code are paused by an admin; keep the file locally and try later.

## Finding and reading

- `search_artifacts` by text, `project`, `tag` or `folder`. It only returns files the person can open.
- `list_folder` to browse (no path = their home). `get_artifact` to read a text file (`as_base64: true` for binary).

## Working on a file that lives in buckets

When the person wants to edit or look closely at a file from buckets ("open the latency dashboard", "fix the typo in our runbook"), follow `/buckets-open`: fetch it with `get_artifact`, write it to `.buckets/<name>` in the workspace, work on that copy with the normal tools, and save back to the same `path` with `if_version` set to the version you opened. If the save is refused because someone changed, moved or deleted it meanwhile, say so and offer to reapply or save under a new name rather than overwriting. Never save back text that `get_artifact` marked `truncated`. For a one-line text change, `edit_file` is quicker.

## Organizing

- `create_folder`, `rename` (new name only), `move` (`from`, `to` are full paths).
- `delete` moves to the trash for 30 days. Folders with very many files must be deleted in the web app; say so if the tool asks for confirmation.
- `list_trash` and `restore_from_trash` to bring things back. `list_versions` and `restore_version` for older versions.
- Before deleting or moving something the person didn't clearly point at, confirm with them.

## Sharing

- `who_has_access` shows every grant that applies to a folder and where it comes from.
- `share_folder` gives a `group` or a `user` a role on a folder the person owns; `unshare_folder` removes it. Share folders, not single files.
- Prefer the smallest role that works (Viewer unless they asked for edit rights), and repeat back what you shared with whom.

## Good habits

- Don't save secrets, credentials or `.env` files.
- One artifact per file with a clear name (`latency-dashboard.html`, not `output.html`).
- When unsure where something belongs, save to the project folder and tell the person; they can move it.
