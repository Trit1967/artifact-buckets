"""Hands out the Claude Code plugin from the server itself, with this server's address built in.

People connect Claude Code with one line (an install script for Windows or for macOS and Linux), and IT
can push the same plugin to every computer with Claude Code's managed settings. Nobody sets an
environment variable, clones a repository or types the server address.

  /claude/marketplace.json            plugin marketplace (Claude Code "url" marketplace source)
  /claude/artifact-buckets-<v>.zip    the plugin, .mcp.json pointing at <base_url>/mcp (sha256-pinned)
  /claude/install.ps1, install.sh     one-line installers
  /claude/managed-settings.json       the policy IT pushes (Intune, Group Policy, Jamf)
  /claude/setup.md                    the same steps written for Claude Code, so a person can just ask
                                      "Set up Artifact Buckets from <server>/claude/setup.md"
  /claude/ca.crt                      only when AB_INSTALL_CA_FILE is set: a self-signed server
                                      certificate (not a CA, valid only for this server's address)

All of it is public and holds nothing secret: the plugin is the same for everyone, and signing in
still happens in the browser.
"""
from __future__ import annotations

import functools
import hashlib
import io
import json
import os
import re
import zipfile
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from . import config

MARKETPLACE = "artifact-buckets"
PLUGIN = "artifact-buckets"
SERVER = "artifact-buckets"  # MCP server name inside the plugin
router = APIRouter()


def plugin_dir() -> str:
    return os.path.realpath(config.settings.plugin_dir)


@functools.lru_cache(maxsize=4)
def _build(src: str, base_url: str, mtime: float) -> tuple[bytes, str, str]:
    """Zip the plugin with this server's address baked in. Fixed timestamps and order, so the same
    plugin and address always give the same bytes and the same sha256 (no needless updates)."""
    with open(os.path.join(src, ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
        version = json.load(f)["version"]
    files: list[tuple[str, bytes]] = []
    for root, dirs, names in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d not in ("__pycache__",))
        for n in sorted(names):
            full = os.path.join(root, n)
            rel = os.path.relpath(full, src).replace(os.sep, "/")
            with open(full, "rb") as f:
                data = f.read()
            if rel == ".mcp.json":
                data = (json.dumps({"mcpServers": {SERVER: {"type": "http", "url": f"{base_url}/mcp"}}}, indent=2) + "\n").encode()
            files.append((rel, data))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, data in files:
            info = zipfile.ZipInfo(rel, date_time=(2020, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    blob = buf.getvalue()
    return blob, hashlib.sha256(blob).hexdigest(), version


def bundle() -> tuple[bytes, str, str]:
    src = plugin_dir()
    mtime = max((os.path.getmtime(os.path.join(r, n)) for r, _, ns in os.walk(src) for n in ns), default=0.0)
    return _build(src, config.settings.base_url, mtime)


def zip_name(version: str) -> str:
    return f"{PLUGIN}-{version}.zip"


def marketplace_url() -> str:
    return config.settings.plugin_marketplace or f"{config.settings.base_url}/claude/marketplace.json"


def install_base() -> str:
    """Where the one-line installers are fetched from. Normally the server itself; a server with its own certificate sets a plain
    http address here so a computer can fetch the installer before it trusts that certificate."""
    return (config.settings.install_url or config.settings.base_url).rstrip("/")


def _ca() -> tuple[bytes, str] | None:
    path = config.settings.install_ca_file
    if not path or not os.path.isfile(path):
        return None
    with open(path, "rb") as f:
        pem = f.read()
    import base64
    import re
    m = re.search(rb"-----BEGIN CERTIFICATE-----(.+?)-----END CERTIFICATE-----", pem, re.S)
    if not m:
        return None
    der = base64.b64decode(b"".join(m.group(1).split()))
    return pem, hashlib.sha256(der).hexdigest().upper()


def managed_settings() -> dict[str, Any]:
    return {
        "extraKnownMarketplaces": {MARKETPLACE: {"source": {"source": "url", "url": marketplace_url()}, "autoUpdate": True}},
        "enabledPlugins": {f"{PLUGIN}@{MARKETPLACE}": True},
    }


def setup_info() -> dict[str, Any]:
    """What the Connect page shows."""
    b = install_base()
    return {
        "windows": f"irm {b}/claude/install.ps1 | iex",
        "prompt": f"Set up Artifact Buckets on this computer by following {b}/claude/setup.md",
        "unix": f"curl -fsSL {b}/claude/install.sh | sh",
        "uninstall_windows": f"irm {b}/claude/uninstall.ps1 | iex",
        "uninstall_unix": f"curl -fsSL {b}/claude/uninstall.sh | sh",
        "remove_prompt": f"Remove Artifact Buckets from this computer by following {b}/claude/setup.md",
        "marketplace": marketplace_url(),
        "plugin": f"{PLUGIN}@{MARKETPLACE}",
        "managed_settings": managed_settings(),
        "private_ca": _ca() is not None,
    }


@router.get("/claude/marketplace.json")
def marketplace() -> JSONResponse:
    blob, sha, version = bundle()
    body = {
        "name": MARKETPLACE,
        "owner": {"name": "Artifact Buckets"},
        "metadata": {"description": f"Claude Code plugin for the Artifact Buckets server at {config.settings.base_url}"},
        "plugins": [{
            "name": PLUGIN,
            "version": version,
            "description": "Save what Claude Code makes into Artifact Buckets, and find, organize and share it.",
            "source": {"source": "archive", "url": f"{config.settings.base_url}/claude/{zip_name(version)}", "sha256": sha},
        }],
    }
    return JSONResponse(body, headers={"Cache-Control": "no-cache"})


@router.get("/claude/managed-settings.json")
def managed() -> JSONResponse:
    return JSONResponse(managed_settings(), headers={"Cache-Control": "no-cache"})


@router.get("/claude/ca.crt")
def ca() -> Response:
    c = _ca()
    if c is None:
        return JSONResponse({"error": "not_found", "message": "This server uses a public certificate"}, status_code=404)
    return Response(c[0], media_type="application/x-x509-ca-cert",
                    headers={"Content-Disposition": 'attachment; filename="artifact-buckets-ca.crt"'})


@router.get("/claude/install.ps1")
def install_ps1() -> PlainTextResponse:
    return PlainTextResponse(_script(PS1), headers={"Cache-Control": "no-cache"})


@router.get("/claude/setup.md")
def setup_md() -> PlainTextResponse:
    return PlainTextResponse(_setup_md(), media_type="text/markdown; charset=utf-8", headers={"Cache-Control": "no-cache"})


def _setup_md() -> str:
    c = _ca()
    b = install_base()
    base = config.settings.base_url
    host = base.split("://", 1)[1].split("/", 1)[0].rsplit(":", 1)[0]
    sync_sha = _file_sha(os.path.join(plugin_dir(), "sync", "sync.ps1")).upper()
    mcp = f"plugin:{PLUGIN}:{SERVER}"
    out = [f"""# Connect Claude Code to Artifact Buckets

These steps are written for Claude Code, run on the person's own computer. Artifact Buckets is the team file
server at {base}. When this is done, Claude Code can save files to it and find, open and share them, and
(if the person wants) the files Claude already made here are copied to their folders.

Keep it light for the person: say in a sentence or two what you're about to do, then do it. Every step is safe
to repeat. The person makes one choice, on one page in their browser; don't ask them questions in the terminal
that the page already asks. If they told you up front to keep some folders out (a client's work, say), remind
them to untick those on that page.

The same steps are in a script, for anyone who'd rather paste one line: Windows PowerShell
`irm {b}/claude/install.ps1 | iex`, or macOS and Linux `curl -fsSL {b}/claude/install.sh | sh`.
To remove everything later, see the last section.
"""]
    n = 1
    if c:
        out.append(f"""## {n}. Check this server's certificate and point Claude Code at it

This server uses its own certificate: a self-signed server certificate, not a certificate authority. Its Basic
Constraints say CA:FALSE and it names only this server (`{host}` and its IP addresses), so it can vouch for
this one server and nothing else. Nothing is added to the system's or the browser's trust store.

- Download {b}/claude/ca.crt to `~/.artifact-buckets/server.crt` (on Windows `%USERPROFILE%\\.artifact-buckets\\server.crt`).
- Its SHA-256 fingerprint (of the DER bytes) must be `{c[1]}`.
- It must not be a CA, and its subject alternative names must be only `{host}` and IP addresses. On Windows:
  `$c = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 <path>; $c.Extensions | % {{ $_.Format($false) }}`;
  elsewhere `openssl x509 -in <path> -noout -text`.
- If any check fails, stop and tell the person. Change nothing.
- Claude Code reads extra certificates from `NODE_EXTRA_CA_CERTS` when it starts. Set it for the person's
  account to the file's full path (Windows: `[Environment]::SetEnvironmentVariable('NODE_EXTRA_CA_CERTS', '<path>', 'User')`;
  macOS and Linux: the line `export NODE_EXTRA_CA_CERTS="<path>"  # Artifact Buckets server certificate` in
  `~/.profile`, plus `~/.zshrc` on macOS and `~/.bashrc` if it exists). If it's already set to another file,
  don't overwrite it: tell the person. Also set it in the environment of the commands below.

The browser will warn once about this certificate when the page in step {n + 2} opens; that's expected.
""")
        n += 1
    out.append(f"""## {n}. Install the plugin

```
claude plugin marketplace add {marketplace_url()}
claude plugin install {PLUGIN}@{MARKETPLACE}
```

If the marketplace is already there, run `claude plugin marketplace update {MARKETPLACE}` instead of adding it.
The plugin has an MCP server entry pointing at {base}/mcp, two skills (`buckets`, `buckets-admin`), two
commands (`/buckets-setup`, `/buckets-open`) and one background hook. The hook runs the plugin's sync script
(`sync/sync.sh` or `sync/sync.ps1`) when a session starts and after each reply; until this computer is
connected with syncing on, it exits straight away and does nothing.

## {n + 1}. Check whether it's already connected

If `claude mcp list` already shows `{mcp}` as Connected, skip to the last step, unless the person asked to
turn on syncing (or sync again): then do the "Connect this computer" step and the copy step, and skip signing in.

## {n + 2}. Connect this computer (one page in the browser)

1. Save the sync script to `~/.artifact-buckets/` (on Windows `%USERPROFILE%\\.artifact-buckets\\`).
   Windows: download {b}/claude/sync.ps1; its SHA-256 must be `{sync_sha}` (otherwise delete it and stop).
   macOS and Linux: download {base}/claude/sync.sh. It's the same script the plugin ships.
2. Run it with `link {base}` (Windows:
   `powershell -NoProfile -ExecutionPolicy Bypass -File "$HOME\\.artifact-buckets\\sync.ps1" link {base}`;
   macOS and Linux: `sh ~/.artifact-buckets/sync.sh link {base}`). It first looks through Claude Code's history
   for files Claude made here that aren't in git (this can take a minute the first time), then opens a page in
   the browser and waits, up to ten minutes. Run it with a ten-minute timeout or in the background.
3. Tell the person what the page shows: the folders Claude's files would be copied from, each with a tick
   box. They untick anything that shouldn't leave this computer and press **Connect and copy**, or
   **Connect without syncing** to only connect Claude Code. Files kept in git, secrets (`.env`, keys,
   certificates, anything named secret or credential), build folders and hidden folders are always left out,
   and the computer can only ever add files to the person's own `projects` and `inbox` folders.
4. The command prints "Connected" when they press it, or stops if they decline (then stop too).

## {n + 3}. Sign in

Run `claude mcp login {mcp}`. Because the person just connected this computer in the same browser, it goes
through without another click. (If it shows a sign-in or Connect page anyway, they press Connect.)
`claude mcp list` should now show `{mcp}` as Connected.

## {n + 4}. Copy the existing files

If `~/.artifact-buckets/sync.conf` has a `token=` line (they chose to sync), run the script with `run`.
It copies the folders they kept and prints what it copied. From now on the plugin's hook copies new and
changed files after each reply. The person can pause it with `off`, resume with `on`, or turn the computer
off under Connect Claude Code in the web app.

Tell the person it's done and where their files went (their folders are at {base}), and that a new Claude Code
window (or `/reload-plugins` in this one) picks up the plugin. Something to try: "save this to my buckets".

## Removing Artifact Buckets from this computer

Only when the person asks. Files already on the server stay there. The script
`irm {b}/claude/uninstall.ps1 | iex` (Windows) or `curl -fsSL {b}/claude/uninstall.sh | sh` does all of this:

1. If `~/.artifact-buckets/sync.ps1` or `sync.sh` exists, run it with `unlink`. That ends this computer's sync
   and its Claude Code sign-in on the server.
2. `claude plugin uninstall {PLUGIN}@{MARKETPLACE}`, then `claude plugin marketplace remove {MARKETPLACE}`.
3. Remove `NODE_EXTRA_CA_CERTS` if it points at `.artifact-buckets/server.crt`, and the
   `# Artifact Buckets server certificate` line from shell profiles. Older versions of setup also added the
   certificate to the person's own trust list (Windows `Cert:\\CurrentUser\\Root`, the macOS login keychain);
   remove only a certificate whose SHA-256 fingerprint is {('`' + c[1] + '`') if c else 'this server certificate'}'s.
4. Delete `~/.artifact-buckets`.
""")
    return "\n".join(out)


@router.get("/claude/uninstall.ps1")
def uninstall_ps1() -> PlainTextResponse:
    return PlainTextResponse(_script(UNINSTALL_PS1), headers={"Cache-Control": "no-cache"})


@router.get("/claude/uninstall.sh")
def uninstall_sh() -> PlainTextResponse:
    return PlainTextResponse(_script(UNINSTALL_SH), headers={"Cache-Control": "no-cache"})


@router.get("/claude/install.sh")
def install_sh() -> PlainTextResponse:
    return PlainTextResponse(_script(SH), headers={"Cache-Control": "no-cache"})


@router.get("/claude/sync.ps1")
def sync_ps1() -> Response:
    return _sync_script("sync.ps1")


@router.get("/claude/sync.sh")
def sync_sh() -> Response:
    return _sync_script("sync.sh")


def _sync_script(name: str) -> Response:
    """The sync script from the plugin, for setup to save in ~/.artifact-buckets before the plugin is in use."""
    path = os.path.join(plugin_dir(), "sync", name)
    if not os.path.isfile(path):
        return JSONResponse({"error": "not_found", "message": "Not found"}, status_code=404)
    with open(path, "rb") as f:
        data = f.read()
    return Response(data, media_type="text/plain; charset=utf-8", headers={"Cache-Control": "no-cache"})


@router.get("/claude/{name}")
def plugin_zip(name: str) -> Response:
    blob, sha, version = bundle()
    if name != zip_name(version):
        return JSONResponse({"error": "not_found", "message": "Not found"}, status_code=404)
    return Response(blob, media_type="application/zip", headers={"Cache-Control": "public, max-age=300", "ETag": f'"{sha}"'})


def _file_sha(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return ""


def _script(template: str) -> str:
    c = _ca()
    vals = {
        "SERVER": config.settings.base_url,
        "MARKETPLACE_URL": marketplace_url(),
        "MARKETPLACE": MARKETPLACE,
        "PLUGIN": f"{PLUGIN}@{MARKETPLACE}",
        "MCP_NAME": f"plugin:{PLUGIN}:{SERVER}",
        "CA_URL": f"{install_base()}/claude/ca.crt" if c else "",
        "CA_SHA": c[1] if c else "",
        "INSTALL_BASE": install_base(),
        "PLUGIN_RE": re.escape(f"{PLUGIN}@{MARKETPLACE}"),
        "SYNC_PS1_SHA": _file_sha(os.path.join(plugin_dir(), "sync", "sync.ps1")).upper(),
    }
    for k, v in vals.items():
        template = template.replace("{{" + k + "}}", v.replace("'", ""))
    return template


PS1 = r"""# Artifact Buckets: connect Claude Code on this computer. Safe to run again.
# Works in Windows PowerShell 5.1 and PowerShell 7. Asks nothing in the terminal: the one choice (which of
# Claude's existing files to copy) is made on a page in the browser.
$Server = '{{SERVER}}'
$CaUrl = '{{CA_URL}}'
$CaSha = '{{CA_SHA}}'

function Step($m) { Write-Host "  $m" }
function Stop-Setup($m) { Write-Host ""; Write-Host "  $m" -ForegroundColor Yellow; Write-Host "" }

Write-Host ""
Write-Host "  Artifact Buckets" -ForegroundColor White
Write-Host "  Connecting Claude Code to $Server" -ForegroundColor DarkGray
Write-Host ""

if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
  Stop-Setup "Claude Code isn't installed yet. Install it first (irm https://claude.ai/install.ps1 | iex), then run this again."; return
}
$dir = Join-Path $HOME '.artifact-buckets'
New-Item -ItemType Directory -Force -Path $dir | Out-Null

if ($CaUrl) {
  # Only for a server with its own (self-signed) certificate. Claude Code is told about it through
  # NODE_EXTRA_CA_CERTS; nothing goes into the Windows certificate store, so Windows asks nothing.
  try {
    $ca = Join-Path $dir 'server.crt'
    Invoke-WebRequest -UseBasicParsing -Uri $CaUrl -OutFile $ca -ErrorAction Stop
    $cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 $ca
    $sha = [BitConverter]::ToString([System.Security.Cryptography.SHA256]::Create().ComputeHash($cert.RawData)).Replace('-', '')
    if ($sha -ne $CaSha) { Remove-Item -LiteralPath $ca -Force; Stop-Setup "The server's certificate didn't match what was expected. Nothing was changed."; return }
    $cur = [Environment]::GetEnvironmentVariable('NODE_EXTRA_CA_CERTS', 'User')
    if ($cur -and $cur -ne $ca) { Stop-Setup "NODE_EXTRA_CA_CERTS is already set to $cur. Add this server's certificate ($ca) to that file, then run this again."; return }
    [Environment]::SetEnvironmentVariable('NODE_EXTRA_CA_CERTS', $ca, 'User')
    $env:NODE_EXTRA_CA_CERTS = $ca
  } catch { Stop-Setup "Couldn't set up the server's certificate: $($_.Exception.Message)"; return }
  Step "Server certificate checked"
}

$known = (claude plugin marketplace list 2>&1 | Out-String)
if ($known -match '{{MARKETPLACE}}') { claude plugin marketplace update {{MARKETPLACE}} 2>&1 | Out-Null }
else { claude plugin marketplace add '{{MARKETPLACE_URL}}' 2>&1 | Out-Null }
if ($LASTEXITCODE -ne 0) { Stop-Setup "Couldn't reach $Server from Claude Code. Check the address opens in your browser, then run this again."; return }
claude plugin install {{PLUGIN}} 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Stop-Setup "Couldn't install the plugin. Run: claude plugin install {{PLUGIN}}  to see why."; return }
Step "Plugin installed"

$sync = Join-Path $dir 'sync.ps1'
$ps = Join-Path $PSHOME 'powershell.exe'; if (-not (Test-Path $ps)) { $ps = 'powershell.exe' }
function Sync-Do { & $ps -NoProfile -ExecutionPolicy Bypass -File $sync @args }
# From the same place as this script, checked against the fingerprint this script carries.
try { Invoke-WebRequest -UseBasicParsing -Uri '{{INSTALL_BASE}}/claude/sync.ps1' -OutFile $sync -ErrorAction Stop }
catch { Stop-Setup "Couldn't download the sync script from {{INSTALL_BASE}} ($($_.Exception.Message))."; return }
if ((Get-FileHash -LiteralPath $sync -Algorithm SHA256).Hash -ne '{{SYNC_PS1_SHA}}') {
  Remove-Item -LiteralPath $sync -Force; Stop-Setup "The sync script didn't match what was expected. Nothing was changed."; return
}

$status = (claude mcp list 2>&1 | Out-String)
if ($status -match ([regex]::Escape('{{MCP_NAME}}') + '[^\r\n]*Connected')) { Step "Already connected" }
else {
  # One page in the browser: connect this computer, and pick which of Claude's files to bring in.
  Sync-Do link $Server
  if ($LASTEXITCODE -ne 0) { Stop-Setup "Not connected. Run this again when you're ready."; return }
  # The page just approved this computer, so Claude Code's sign-in goes through without another click.
  Step "Finishing sign-in..."
  claude mcp login '{{MCP_NAME}}' | Out-Null
  if ($LASTEXITCODE -ne 0) { Stop-Setup "Sign-in didn't finish. In Claude Code, run /mcp, choose artifact-buckets, then Authenticate."; return }
  Step "Signed in"
  $conf = Join-Path $dir 'sync.conf'
  if ((Test-Path $conf) -and ((Get-Content -Raw $conf) -match '(?m)^token=.')) { Write-Host ""; Sync-Do run }
}

Write-Host ""
Write-Host "  Done. Claude Code can now save to Artifact Buckets." -ForegroundColor Green
Write-Host "  Open a new Claude Code window and try: save this to my buckets" -ForegroundColor DarkGray
Write-Host "  To remove it later: irm {{INSTALL_BASE}}/claude/uninstall.ps1 | iex" -ForegroundColor DarkGray
Write-Host ""
"""


SH = r"""#!/bin/sh
# Artifact Buckets: connect Claude Code on this computer. Safe to run again.
# Asks nothing in the terminal: the one choice (which of Claude's existing files to copy) is made in the browser.
set -eu
SERVER='{{SERVER}}'
CA_URL='{{CA_URL}}'
CA_SHA='{{CA_SHA}}'

say() { printf '  %s\n' "$1"; }
quit() { printf '\n  %s\n\n' "$1" >&2; exit 1; }

printf '\n  Artifact Buckets\n  Connecting Claude Code to %s\n\n' "$SERVER"
command -v claude >/dev/null 2>&1 || quit "Claude Code isn't installed yet. Install it first (curl -fsSL https://claude.ai/install.sh | bash), then run this again."
dir="$HOME/.artifact-buckets"; mkdir -p "$dir"
CURL_CA=

if [ -n "$CA_URL" ]; then
  # Only for a server with its own (self-signed) certificate. Claude Code reads NODE_EXTRA_CA_CERTS only at
  # startup, so it goes in the shell profile (a settings.json env entry comes too late for TLS). Nothing is
  # added to the system or keychain trust store, so nothing asks for a password.
  ca="$dir/server.crt"
  curl -fsSL "$CA_URL" -o "$ca"
  sha=$(openssl x509 -in "$ca" -outform der | openssl dgst -sha256 -r | cut -d' ' -f1 | tr a-f A-F)
  [ "$sha" = "$CA_SHA" ] || { rm -f "$ca"; quit "The server's certificate didn't match what was expected. Nothing was changed."; }
  if [ -n "${NODE_EXTRA_CA_CERTS:-}" ] && [ "$NODE_EXTRA_CA_CERTS" != "$ca" ]; then
    quit "NODE_EXTRA_CA_CERTS is already set to $NODE_EXTRA_CA_CERTS. Add this server's certificate ($ca) to that file, then run this again."
  fi
  line="export NODE_EXTRA_CA_CERTS=\"$ca\"  # Artifact Buckets server certificate"
  for rc in "$HOME/.profile" "$HOME/.bashrc" "$HOME/.zshrc"; do
    [ -f "$rc" ] || [ "$rc" = "$HOME/.profile" ] || { [ "$rc" = "$HOME/.zshrc" ] && [ "$(uname)" = Darwin ]; } || continue
    grep -qsE "Artifact Buckets (lab|server) certificate" "$rc" || printf '\n%s\n' "$line" >> "$rc"
  done
  export NODE_EXTRA_CA_CERTS="$ca"
  CURL_CA="--cacert $ca"
  say "Server certificate checked"
fi

if claude plugin marketplace list 2>/dev/null | grep -q '{{MARKETPLACE}}'; then
  claude plugin marketplace update {{MARKETPLACE}} >/dev/null 2>&1 || quit "Couldn't reach $SERVER from Claude Code."
else
  claude plugin marketplace add '{{MARKETPLACE_URL}}' >/dev/null 2>&1 || quit "Couldn't reach $SERVER from Claude Code. Check the address opens in your browser, then run this again."
fi
claude plugin install {{PLUGIN}} >/dev/null 2>&1 || quit "Couldn't install the plugin. Run: claude plugin install {{PLUGIN}}  to see why."
say "Plugin installed"

sync="$dir/sync.sh"
# shellcheck disable=SC2086
curl -fsSL $CURL_CA "$SERVER/claude/sync.sh" -o "$sync" || quit "Couldn't download the sync script from $SERVER."

if claude mcp list 2>/dev/null | grep -F '{{MCP_NAME}}' | grep -q Connected; then
  say "Already connected"
else
  # One page in the browser: connect this computer, and pick which of Claude's files to bring in.
  sh "$sync" link "$SERVER" </dev/null || quit "Not connected. Run this again when you're ready."
  # The page just approved this computer, so Claude Code's sign-in goes through without another click.
  say "Finishing sign-in..."
  claude mcp login '{{MCP_NAME}}' </dev/null >/dev/null || quit "Sign-in didn't finish. In Claude Code, run /mcp, choose artifact-buckets, then Authenticate."
  say "Signed in"
  if grep -qs '^token=.' "$dir/sync.conf"; then printf '\n'; sh "$sync" run || true; fi
fi

printf '\n  Done. Claude Code can now save to Artifact Buckets.\n  Open a new terminal, start Claude Code and try: save this to my buckets\n'
printf '  To remove it later: curl -fsSL {{INSTALL_BASE}}/claude/uninstall.sh | sh\n\n'
"""


UNINSTALL_PS1 = r"""# Artifact Buckets: remove it from Claude Code on this computer. Safe to run again.
# Files already saved to the server stay there.
$Server = '{{SERVER}}'
$CaSha = '{{CA_SHA}}'
function Step($m) { Write-Host "  $m" }

Write-Host ""
Write-Host "  Artifact Buckets" -ForegroundColor White
Write-Host "  Removing it from Claude Code on this computer" -ForegroundColor DarkGray
Write-Host ""
$dir = Join-Path $HOME '.artifact-buckets'
$sync = Join-Path $dir 'sync.ps1'
$ps = Join-Path $PSHOME 'powershell.exe'; if (-not (Test-Path $ps)) { $ps = 'powershell.exe' }

# 1. End this computer's sync link and sign-in on the server (while the certificate is still here).
if (Test-Path -LiteralPath $sync) { & $ps -NoProfile -ExecutionPolicy Bypass -File $sync unlink }

# 2. The plugin (its MCP connection, skills and sync hook) and the marketplace it came from.
if (Get-Command claude -ErrorAction SilentlyContinue) {
  $list = (claude plugin list 2>&1 | Out-String)
  if ($list -match '{{PLUGIN_RE}}') { claude plugin uninstall {{PLUGIN}} 2>&1 | Out-Null; Step "Plugin removed" }
  $known = (claude plugin marketplace list 2>&1 | Out-String)
  if ($known -match '{{MARKETPLACE}}') { claude plugin marketplace remove {{MARKETPLACE}} 2>&1 | Out-Null; Step "Plugin source removed" }
}

# 3. The server certificate: the NODE_EXTRA_CA_CERTS setting if it points here, and (from older versions of
#    setup) the copy in your own Windows trust list, matched by fingerprint so nothing else is touched.
$ca = Join-Path $dir 'server.crt'
if ([Environment]::GetEnvironmentVariable('NODE_EXTRA_CA_CERTS', 'User') -eq $ca) {
  [Environment]::SetEnvironmentVariable('NODE_EXTRA_CA_CERTS', $null, 'User'); Step "Certificate setting removed"
}
$shas = @($CaSha)
if (Test-Path -LiteralPath $ca) {
  try { $c = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 $ca
        $shas += [BitConverter]::ToString([System.Security.Cryptography.SHA256]::Create().ComputeHash($c.RawData)).Replace('-', '') } catch { }
}
foreach ($c in @(Get-ChildItem Cert:\CurrentUser\Root -ErrorAction SilentlyContinue)) {
  $h = [BitConverter]::ToString([System.Security.Cryptography.SHA256]::Create().ComputeHash($c.RawData)).Replace('-', '')
  if ($shas -contains $h) {
    Step "Removing the certificate from your trusted list. Windows will ask you to confirm: choose Yes."
    Step "(The box can open behind this window. If nothing shows, press Alt+Tab.)"
    try { Remove-Item -LiteralPath "Cert:\CurrentUser\Root\$($c.Thumbprint)" -ErrorAction Stop; Step "Certificate removed" } catch { Step "Left the certificate in place ($($_.Exception.Message))" }
  }
}

# 4. Its own folder: the sync script, its settings and what it remembers.
if (Test-Path -LiteralPath $dir) { Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction SilentlyContinue; Step "Removed $dir" }

Write-Host ""
Write-Host "  Done. Artifact Buckets is removed from this computer." -ForegroundColor Green
Write-Host "  Your files on $Server are still there. Close open Claude Code windows to finish." -ForegroundColor DarkGray
Write-Host ""
"""


UNINSTALL_SH = r"""#!/bin/sh
# Artifact Buckets: remove it from Claude Code on this computer. Safe to run again.
# Files already saved to the server stay there.
set -u
SERVER='{{SERVER}}'
CA_SHA='{{CA_SHA}}'
say() { printf '  %s\n' "$1"; }
printf '\n  Artifact Buckets\n  Removing it from Claude Code on this computer\n\n'
dir="$HOME/.artifact-buckets"

# 1. End this computer's sync link and sign-in on the server (while the certificate is still here).
[ -f "$dir/sync.sh" ] && sh "$dir/sync.sh" unlink

# 2. The plugin (its MCP connection, skills and sync hook) and the marketplace it came from.
if command -v claude >/dev/null 2>&1; then
  if claude plugin list 2>/dev/null | grep -qE '{{PLUGIN_RE}}'; then claude plugin uninstall {{PLUGIN}} >/dev/null 2>&1 && say "Plugin removed"; fi
  if claude plugin marketplace list 2>/dev/null | grep -q '{{MARKETPLACE}}'; then
    claude plugin marketplace remove {{MARKETPLACE}} >/dev/null 2>&1 && say "Plugin source removed"; fi
fi

# 3. The certificate line setup added to the shell profile, and (from older versions of setup on macOS) the
#    keychain entry, matched by fingerprint so nothing else is touched.
for rc in "$HOME/.profile" "$HOME/.bashrc" "$HOME/.zshrc"; do
  if grep -qsE "Artifact Buckets (lab|server) certificate" "$rc"; then
    sed -i.ab-bak -E '/Artifact Buckets (lab|server) certificate/d' "$rc" && rm -f "$rc.ab-bak" && say "Removed the certificate line from $rc"
  fi
done
if [ "$(uname)" = Darwin ] && [ -n "$CA_SHA" ]; then
  sha1=$(security find-certificate -a -Z "$HOME/Library/Keychains/login.keychain-db" 2>/dev/null | awk '/SHA-256 hash:/{s=$3} /SHA-1 hash:/{h=$3} /^keychain:/{ if (s == "'"$CA_SHA"'") print h; s="" } END { if (s == "'"$CA_SHA"'") print h }' | head -n 1)
  if [ -n "$sha1" ]; then security delete-certificate -Z "$sha1" "$HOME/Library/Keychains/login.keychain-db" >/dev/null 2>&1 && say "Removed the certificate from your keychain"; fi
fi

# 4. Its own folder: the sync script, its settings and what it remembers.
[ -d "$dir" ] && rm -rf "$dir" && say "Removed $dir"

printf '\n  Done. Artifact Buckets is removed from this computer.\n  Your files on %s are still there. Open a new terminal to finish.\n\n' "$SERVER"
"""
