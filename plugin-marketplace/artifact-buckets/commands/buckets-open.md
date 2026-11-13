---
description: Pull a file from Artifact Buckets into this session to view or edit it, then save it back as a new version
argument-hint: <path or search words>
---

Bring a file from Artifact Buckets into this Claude Code session so the person can look at it or change it with the usual tools, and save it back to the same place when they're done.

What they asked for: $ARGUMENTS

1. **Find the file.**
   - If it is a full path (starts with `/users/` or `/shared/`), use it as is.
   - Otherwise call `search_artifacts` with those words. If there's one clear match, use it. If several, show the top few (name, folder, who saved it, when) and ask which one. If none, say so and stop.

2. **Fetch it** with `get_artifact`. Note its `path`, `version`, `size`, `role` and `link`.
   - If `truncated` is true, the text is only the first part of the file. Do not make a local copy to edit and never save it back: tell the person the file is too large to edit here, give the `link`, and offer to read or summarise the part you have.
   - For a binary file (`binary: true`), call `get_artifact` again with `as_base64: true`.
   - If `role` is `viewer`, they can read it but not save over it. Say so when you open it, and at save time offer to save their changed copy into their own folder instead (step 5).

3. **Put it in the workspace** at `.buckets/<file name>` in the current folder (make the folder; if a file with that name is already there and isn't this one, add the version, e.g. `report.v3.md`). The copy must be byte-for-byte the file, nothing added or lost:
   - Small text files (under about 100 KB): write `text` exactly as returned.
   - Larger text files and all binary files: don't retype the contents. If Claude Code saved the tool result to a file, extract the field from that file with a shell command (for example `python3 -c "import json,sys; d=json.load(open(sys.argv[1])); open(sys.argv[2],'w',newline='').write(d['text'])" <result file> .buckets/<name>`, or decode `content_base64` with `base64 -d`).
   - Check the copy's size in bytes matches `size`. If it doesn't, delete the copy, say the file couldn't be copied faithfully, and give the `link` instead.
   - If `.buckets/` isn't in `.gitignore` and this is a git repository, add it there so pulled files aren't committed by accident.

4. **Tell the person** in two lines: what you opened (path and version, with the link) and where the local copy is. Then help with whatever they want: read it, explain it, change it, preview it.

5. **Saving back.** When they ask to save, or clearly finish their edits (and you've confirmed), call `save_artifact` with:
   - `path`: the original path from step 2,
   - the new contents (`content` for text; for large or binary files, `content_base64` produced with a shell command such as `base64 -w0`, not retyped),
   - `if_version`: the version you opened.
   It becomes a new version. The previous version goes into the file's history; if the save result has a `note` about older versions being removed to stay within the quota, pass that on.
   - If the save is refused because the file changed meanwhile, say who saved the newer version. Offer to fetch the latest, reapply the person's change to it, and save again, or to save under a new name. Never overwrite without asking.
   - If it is refused because the file no longer exists there (moved, renamed or deleted), say so, offer to search for where it went, or to save their copy under a new name. Don't recreate it at the old path without asking.
   - If they can't edit it (viewer, or the save is refused for permission), offer to save their copy into their own inbox: `save_artifact` with `path: "/users/<their username>/inbox/<file name>"` (their username is in the home folder `list_writable_folders` returns).
   - After a save, give the new version number and the link, and update `if_version` to the new version for any further saves.

Keep it quiet and quick: no extra commentary between steps, and don't paste the whole file into the chat unless asked.
