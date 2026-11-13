# Security policy

Artifact Buckets holds people's files and signs people in, so we take reports seriously.

## Reporting a vulnerability

Please **don't open a public issue**. Use GitHub's private reporting instead:
**Security → Report a vulnerability** on this repository. Include what you found, how to reproduce it,
and the version or commit you tested.

We aim to acknowledge a report within 3 working days and to agree on a fix and disclosure date with you.
We're happy to credit you in the release notes.

## Supported versions

Only the latest release gets security fixes. `sudo artifact-buckets upgrade` moves an install to it.

## Scope

In scope: the app (web UI, REST API, MCP endpoint, OAuth sign-in), the Claude Code plugin and its
installers, `install.sh`, and the container and compose files in this repository.

Out of scope: weaknesses that need an admin account to exploit an admin's own install, findings against
a deployment you don't own, and denial of service by volume.

## How an install is hardened

See [Security notes](README.md#security-notes) in the README. In short: HTTPS from the first start,
secrets generated on the host and kept in a mode-600 `.env`, a read-only app container with every
capability dropped except the three it needs, and tokens stored only as hashes.
