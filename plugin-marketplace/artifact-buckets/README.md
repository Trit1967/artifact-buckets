# Artifact Buckets plugin for Claude Code

Connects Claude Code to Artifact Buckets so it can save what it makes into your folders, and find, organize and share files there.

## Install

```
/plugin marketplace add https://git.lab.local/tools/claude-plugins.git
/plugin install artifact-buckets@lab-tools
```

Then run `/buckets-setup`. The first time, run `/mcp`, pick **artifact-buckets** and **Authenticate**: your browser opens the Artifact Buckets sign-in, you sign in with your work account and press **Connect**. Nothing to copy or paste.

The server address defaults to `https://buckets.lab.local`. To use another, set `ARTIFACT_BUCKETS_URL` before starting Claude Code.

## What's inside

- MCP server `artifact-buckets` (HTTP, OAuth sign-in through the browser).
- Skill `buckets`: saving, finding, organizing and sharing files.
- Skill `buckets-admin`: admin and audit tasks (only works for admins and auditors).
- Command `/buckets-setup`: connect and check.
- Command `/buckets-open <path or words>`: pull a file into this session (`.buckets/<name>`), work on it with Claude's usual tools, and save it back as a new version. A save is refused if someone else changed the file meanwhile, so nobody's edit is lost.
