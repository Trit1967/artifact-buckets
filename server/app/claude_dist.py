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
    out = [f"""# Connect Claude Code to Artifact Buckets

These steps are written for Claude Code, run on the person's own computer. Artifact Buckets is the team file
server at {base}. When this is done, Claude Code can save files to it and find, open and share them.
Explain each step to the person before you run it. Every step is safe to repeat.

If the person would rather do it in one go, the same steps are in a script:
Windows PowerShell `irm {b}/claude/install.ps1 | iex`, or macOS and Linux `curl -fsSL {b}/claude/install.sh | sh`.
"""]
    n = 1
    if c:
        out.append(f"""## {n}. Trust this server's certificate

This server uses its own certificate. It is a self-signed server certificate, not a certificate authority:
its Basic Constraints say CA:FALSE and it names only this server (`{host}`, and its IP address), so trusting it
lets it vouch for this one server and nothing else. Check that before trusting it:

- Download {b}/claude/ca.crt to `~/.artifact-buckets/server.crt` (on Windows `%USERPROFILE%\\.artifact-buckets\\server.crt`).
- Its SHA-256 fingerprint (of the DER bytes) must be `{c[1]}`.
- It must not be a CA, and its subject alternative names must be only `{host}` and this server's IP address. On Windows:
  `$c = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 <path>; $c.Extensions | % {{ $_.Format($false) }}`;
  elsewhere `openssl x509 -in <path> -noout -text`.
- If any check fails, stop and tell the person. Change nothing.

Then:
- Claude Code reads extra certificates from `NODE_EXTRA_CA_CERTS` when it starts. Set it for the person's
  account to the file's full path (Windows: `[Environment]::SetEnvironmentVariable('NODE_EXTRA_CA_CERTS', '<path>', 'User')`;
  macOS and Linux: an `export NODE_EXTRA_CA_CERTS=...` line in the shell profile). If it's already set to another
  file, don't overwrite it: tell the person. Also set it in the environment of the commands below.
- So the browser sign-in page shows no warning, add it to the person's own trust store (Windows:
  `Import-Certificate -FilePath <path> -CertStoreLocation Cert:\\CurrentUser\\Root`, and Windows asks them to confirm;
  macOS: `security add-trusted-cert -r trustAsRoot -k ~/Library/Keychains/login.keychain-db <path>`).
  This is optional: without it the browser warns once at sign-in.
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
(`sync/sync.sh` or `sync/sync.ps1`) when a session starts and after each reply. Until the person links this
computer in the last step, the script finds no link and exits straight away, doing nothing.

## {n + 1}. Sign in

Run `claude mcp login plugin:{PLUGIN}:{SERVER}`. It opens the browser at this server's sign-in page.
The person signs in and presses **Connect**; the command finishes by itself. They never copy a key.

## {n + 2}. Check

`claude mcp list` should show `plugin:{PLUGIN}:{SERVER}` as Connected.

## {n + 3}. Offer to bring in their existing work

Claude Code keeps a history of the files it created and edited on this computer. The sync script reads that
history and copies those files to the person's own Artifact Buckets folders: a project's files go under
`projects/<project folder name>/`, anything else under `inbox/`. It leaves out files kept in git (tracked or
ignored), secrets (`.env`, keys, certificates, anything named secret or credential), build and dependency
folders, anything in a hidden folder (such as `~/.ssh`), app data, and anything under `.claude`. It only ever adds files to the person's own `projects` and
`inbox` folders; it can't read, change or delete anything on the server.

1. Save the script to `~/.artifact-buckets/` (on Windows `%USERPROFILE%\\.artifact-buckets\\`):
   Windows {base}/claude/sync.ps1, macOS and Linux {base}/claude/sync.sh. It's the same script the plugin ships.
2. Show what it would copy, changing nothing. Windows:
   `powershell -NoProfile -ExecutionPolicy Bypass -File "$HOME\\.artifact-buckets\\sync.ps1" scan`;
   macOS and Linux: `sh ~/.artifact-buckets/sync.sh scan`.
   The first scan reads all of Claude Code's history and can take a minute.
3. Show the person the summary (folders, counts, sizes, and what's left out) and ask whether to copy them and
   keep new files in sync from now on. Ask too whether any folder should never be copied, such as a
   client's or another employer's work; for each, run the script with `exclude <folder>` and scan again.
   If they say no to copying, stop here: nothing has been sent.
4. If yes, run the script with `link {base}`. It prints a code and opens the browser at a page that asks the
   person to allow this computer; the codes must match. The command waits until they press **Allow**, up to
   ten minutes, so run it with a long timeout (ten minutes) or in the background and check its output.
5. Then run it with `run`. From now on the plugin's hook copies new and changed files after each reply.
   The person can pause it with `off`, resume with `on`, or turn this computer off on the Connect page.

Tell the person it's done, where their files went, and that a new Claude Code window (or `/reload-plugins`
in this one) picks up the plugin. Something to try: "save this to my buckets".
""")
    return "\n".join(out)


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
    }
    for k, v in vals.items():
        template = template.replace("{{" + k + "}}", v.replace("'", ""))
    return template


PS1 = r"""# Artifact Buckets: connect Claude Code on this computer. Safe to run again.
# Works in Windows PowerShell 5.1 and PowerShell 7.
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

if ($CaUrl) {
  # Only for a server with its own (self-signed) certificate: this computer has to trust it once.
  try {
    $dir = Join-Path $HOME '.artifact-buckets'
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $ca = Join-Path $dir 'server.crt'
    Invoke-WebRequest -UseBasicParsing -Uri $CaUrl -OutFile $ca -ErrorAction Stop
    $cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 $ca
    $sha = [BitConverter]::ToString([System.Security.Cryptography.SHA256]::Create().ComputeHash($cert.RawData)).Replace('-', '')
    if ($sha -ne $CaSha) { Stop-Setup "The server's certificate didn't match what was expected. Nothing was changed."; return }
    if (-not (Get-ChildItem Cert:\CurrentUser\Root | Where-Object { $_.Thumbprint -eq $cert.Thumbprint })) {
      Step "Trusting the server's certificate (valid for this server only). Windows will ask you to confirm: choose Yes."
      Import-Certificate -FilePath $ca -CertStoreLocation Cert:\CurrentUser\Root -ErrorAction Stop | Out-Null
    }
    # Claude Code also reads NODE_EXTRA_CA_CERTS; set it for new windows too, unless it's already in use.
    $cur = [Environment]::GetEnvironmentVariable('NODE_EXTRA_CA_CERTS', 'User')
    if (-not $cur -or $cur -eq $ca) {
      [Environment]::SetEnvironmentVariable('NODE_EXTRA_CA_CERTS', $ca, 'User')
      $env:NODE_EXTRA_CA_CERTS = $ca
    }
  } catch { Stop-Setup "Couldn't set up the server's certificate: $($_.Exception.Message)"; return }
  Step "Certificate trusted"
}

$known = (claude plugin marketplace list 2>&1 | Out-String)
if ($known -match '{{MARKETPLACE}}') { claude plugin marketplace update {{MARKETPLACE}} 2>&1 | Out-Null }
else { claude plugin marketplace add '{{MARKETPLACE_URL}}' 2>&1 | Out-Null }
if ($LASTEXITCODE -ne 0) { Stop-Setup "Couldn't reach $Server from Claude Code. Check the address opens in your browser, then run this again."; return }
claude plugin install {{PLUGIN}} 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Stop-Setup "Couldn't install the plugin. Run: claude plugin install {{PLUGIN}}  to see why."; return }
Step "Plugin installed"

$status = (claude mcp list 2>&1 | Out-String)
if ($status -match ([regex]::Escape('{{MCP_NAME}}') + '[^\r\n]*Connected')) { Step "Already signed in" }
else {
  Step "Opening your browser to sign in. Sign in, then press Connect."
  claude mcp login '{{MCP_NAME}}'
  if ($LASTEXITCODE -ne 0) { Stop-Setup "Sign-in didn't finish. In Claude Code, run /mcp, choose artifact-buckets, then Authenticate."; return }
  Step "Signed in"
}

# Bring in what Claude has already made here, if the person wants it.
$dir = Join-Path $HOME '.artifact-buckets'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$sync = Join-Path $dir 'sync.ps1'
$conf = Join-Path $dir 'sync.conf'
$ps = Join-Path $PSHOME 'powershell.exe'; if (-not (Test-Path $ps)) { $ps = 'powershell.exe' }
function Sync-Do { & $ps -NoProfile -ExecutionPolicy Bypass -File $sync @args }
$linked = (Test-Path $conf) -and ((Get-Content -Raw $conf) -match '(?m)^token=.')
if (-not $linked) {
  try {
    Invoke-WebRequest -UseBasicParsing -Uri "$Server/claude/sync.ps1" -OutFile $sync -ErrorAction Stop
    Write-Host ""
    Step "Looking for files Claude has already made on this computer..."
    $scan = (Sync-Do scan | Out-String)
    Write-Host $scan.TrimEnd()
    if ($scan -notmatch 'Nothing new to copy') {
      Write-Host ""
      $ex = Read-Host "  Any folders to keep out, such as a client's work? Type them separated by ; or press Enter"
      if ($ex -and $ex.Trim()) {
        foreach ($x in $ex.Split(';')) { if ($x.Trim()) { Sync-Do exclude $x.Trim().Trim('"') } }
        $scan = (Sync-Do scan | Out-String); Write-Host $scan.TrimEnd(); Write-Host ""
      }
      $a = Read-Host "  Copy these to your Artifact Buckets folders, and keep new ones in sync? (Y/n)"
      if ($a -notmatch '^\s*[nN]') {
        Sync-Do link $Server
        if ($LASTEXITCODE -eq 0) { Sync-Do run }
      } else { Step "Skipped. Nothing was copied. You can do this any time from the Connect page." }
    }
  } catch { Step "Couldn't look for existing files ($($_.Exception.Message)). You can do this later from the Connect page." }
}

Write-Host ""
Write-Host "  Done. Claude Code can now save to Artifact Buckets." -ForegroundColor Green
Write-Host "  Open a new Claude Code window and try: save this to my buckets" -ForegroundColor DarkGray
Write-Host ""
"""

SH = r"""#!/bin/sh
# Artifact Buckets: connect Claude Code on this computer. Safe to run again.
set -eu
SERVER='{{SERVER}}'
CA_URL='{{CA_URL}}'
CA_SHA='{{CA_SHA}}'

say() { printf '  %s\n' "$1"; }
quit() { printf '\n  %s\n\n' "$1" >&2; exit 1; }

printf '\n  Artifact Buckets\n  Connecting Claude Code to %s\n\n' "$SERVER"
command -v claude >/dev/null 2>&1 || quit "Claude Code isn't installed yet. Install it first (curl -fsSL https://claude.ai/install.sh | bash), then run this again."

if [ -n "$CA_URL" ]; then
  dir="$HOME/.artifact-buckets"; mkdir -p "$dir"; ca="$dir/server.crt"
  curl -fsSL "$CA_URL" -o "$ca"
  sha=$(openssl x509 -in "$ca" -outform der | openssl dgst -sha256 -r | cut -d' ' -f1 | tr a-f A-F)
  [ "$sha" = "$CA_SHA" ] || quit "The server's certificate didn't match what was expected. Nothing was changed."
  if [ "$(uname)" = Darwin ]; then
    say "Trusting the server's certificate (valid for this server only). macOS will ask for your password."
    security add-trusted-cert -r trustAsRoot -k "$HOME/Library/Keychains/login.keychain-db" "$ca"
  else
    # Linux has no per-user trust store, and Claude Code reads NODE_EXTRA_CA_CERTS only at startup,
    # so it goes in the shell profile (a settings.json env entry comes too late for TLS).
    line="export NODE_EXTRA_CA_CERTS=\"$ca\"  # Artifact Buckets server certificate"
    for rc in "$HOME/.profile" "$HOME/.bashrc" "$HOME/.zshrc"; do
      [ -f "$rc" ] || [ "$rc" = "$HOME/.profile" ] || continue
      grep -qsE "Artifact Buckets (lab|server) certificate" "$rc" || printf '\n%s\n' "$line" >> "$rc"
    done
    export NODE_EXTRA_CA_CERTS="$ca"
  fi
  say "Certificate trusted"
fi

if claude plugin marketplace list 2>/dev/null | grep -q '{{MARKETPLACE}}'; then
  claude plugin marketplace update {{MARKETPLACE}} >/dev/null 2>&1 || quit "Couldn't reach $SERVER from Claude Code."
else
  claude plugin marketplace add '{{MARKETPLACE_URL}}' >/dev/null 2>&1 || quit "Couldn't reach $SERVER from Claude Code. Check the address opens in your browser, then run this again."
fi
claude plugin install {{PLUGIN}} >/dev/null 2>&1 || quit "Couldn't install the plugin. Run: claude plugin install {{PLUGIN}}  to see why."
say "Plugin installed"

if claude mcp list 2>/dev/null | grep -F '{{MCP_NAME}}' | grep -q Connected; then
  say "Already signed in"
else
  say "Opening your browser to sign in. Sign in, then press Connect."
  claude mcp login '{{MCP_NAME}}' </dev/tty || quit "Sign-in didn't finish. In Claude Code, run /mcp, choose artifact-buckets, then Authenticate."
  say "Signed in"
fi

# Bring in what Claude has already made here, if the person wants it.
dir="$HOME/.artifact-buckets"; mkdir -p "$dir"; sync="$dir/sync.sh"
if ! grep -qs '^token=.' "$dir/sync.conf"; then
  if [ -n "$CA_URL" ]; then got=$(curl -fsSL --cacert "$dir/server.crt" "$SERVER/claude/sync.sh" -o "$sync" && echo y || true)
  else got=$(curl -fsSL "$SERVER/claude/sync.sh" -o "$sync" && echo y || true); fi
  if [ "$got" = y ]; then
    printf '\n'; say "Looking for files Claude has already made on this computer..."
    scan=$(sh "$sync" scan 2>&1 || true); printf '%s\n' "$scan"
    if ! printf '%s' "$scan" | grep -q 'Nothing new to copy'; then
      printf "\n  Any folders to keep out, such as a client's work? Type them separated by ; or press Enter: "
      ex=$(head -n 1 </dev/tty 2>/dev/null || true)
      if [ -n "$(printf '%s' "$ex" | tr -d ' ;')" ]; then
        printf '%s' "$ex" | tr ';' '\n' | while IFS= read -r x; do
          x=$(printf '%s' "$x" | sed 's/^ *//; s/ *$//'); [ -z "$x" ] || sh "$sync" exclude "$x" || true
        done
        sh "$sync" scan 2>&1 || true
      fi
      printf '\n  Copy these to your Artifact Buckets folders, and keep new ones in sync? (Y/n) '
      a=$(head -n 1 </dev/tty 2>/dev/null || true)
      case "$a" in
        [nN]*) say "Skipped. Nothing was copied. You can do this any time from the Connect page." ;;
        *) sh "$sync" link "$SERVER" </dev/tty && sh "$sync" run || true ;;
      esac
    fi
  else
    say "Couldn't look for existing files. You can do this later from the Connect page."
  fi
fi

printf '\n  Done. Claude Code can now save to Artifact Buckets.\n  Open Claude Code and try: save this to my buckets\n\n'
"""
