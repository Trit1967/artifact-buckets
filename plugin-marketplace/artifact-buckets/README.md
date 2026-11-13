# Artifact Buckets plugin for Claude Code

Connects Claude Code to Artifact Buckets so it can save what it makes into your folders, and find, organize and share files there.

## Install

The Artifact Buckets server hands out this plugin with its own address built in. Open **Connect Claude Code** in the web app and paste the one line it shows (PowerShell on Windows, a terminal on macOS and Linux). It installs the plugin and opens your browser to sign in once. Your IT team can also push it to every computer with Claude Code's managed settings.

This folder is the source the server packages. To try it against a local development server, `ARTIFACT_BUCKETS_URL` overrides the address in `.mcp.json`.

## What's inside

- MCP server `artifact-buckets` (HTTP, OAuth sign-in through the browser).
- Skill `buckets`: saving, finding, organizing and sharing files.
- Skill `buckets-admin`: admin and audit tasks (only works for admins and auditors).
- Command `/buckets-setup`: connect and check.
- Command `/buckets-open <path or words>`: pull a file into this session (`.buckets/<name>`), work on it with Claude's usual tools, and save it back as a new version. A save is refused if someone else changed the file meanwhile, so nobody's edit is lost.
