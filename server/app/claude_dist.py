"""Hands out the Claude Code plugin from the server itself, with this server's address built in.

People connect Claude Code with one line (an install script for Windows or for macOS and Linux), and IT
can push the same plugin to every computer with Claude Code's managed settings. Nobody sets an
environment variable, clones a repository or types the server address.

  /claude/marketplace.json            plugin marketplace (Claude Code "url" marketplace source)
  /claude/artifact-buckets-<v>.zip    the plugin, .mcp.json pointing at <base_url>/mcp (sha256-pinned)
  /claude/install.ps1, install.sh     one-line installers
  /claude/managed-settings.json       the policy IT pushes (Intune, Group Policy, Jamf)
  /claude/ca.crt                      only when AB_INSTALL_CA_FILE is set: a private (lab) root certificate

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
    """Where the one-line installers are fetched from. Normally the server itself; the lab sets a plain
    http address here so a computer can fetch the installer before it trusts the lab certificate."""
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


@router.get("/claude/install.sh")
def install_sh() -> PlainTextResponse:
    return PlainTextResponse(_script(SH), headers={"Cache-Control": "no-cache"})


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
  # Lab only: the server uses its own certificate, so this computer has to trust it once.
  try {
    $dir = Join-Path $HOME '.artifact-buckets'
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $ca = Join-Path $dir 'ca.crt'
    Invoke-WebRequest -UseBasicParsing -Uri $CaUrl -OutFile $ca -ErrorAction Stop
    $cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 $ca
    $sha = [BitConverter]::ToString([System.Security.Cryptography.SHA256]::Create().ComputeHash($cert.RawData)).Replace('-', '')
    if ($sha -ne $CaSha) { Stop-Setup "The server's certificate didn't match what was expected. Nothing was changed."; return }
    if (-not (Get-ChildItem Cert:\CurrentUser\Root | Where-Object { $_.Thumbprint -eq $cert.Thumbprint })) {
      Step "Trusting the server's certificate. Windows will ask you to confirm: choose Yes."
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
if ($status -match ([regex]::Escape('{{MCP_NAME}}') + '[^\r\n]*Connected')) {
  Write-Host ""; Write-Host "  Already signed in. Claude Code can save to Artifact Buckets." -ForegroundColor Green; Write-Host ""; return
}
Step "Opening your browser to sign in. Sign in, then press Connect."
claude mcp login '{{MCP_NAME}}'
if ($LASTEXITCODE -ne 0) { Stop-Setup "Sign-in didn't finish. In Claude Code, run /mcp, choose artifact-buckets, then Authenticate."; return }

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
  dir="$HOME/.artifact-buckets"; mkdir -p "$dir"; ca="$dir/ca.crt"
  curl -fsSL "$CA_URL" -o "$ca"
  sha=$(openssl x509 -in "$ca" -outform der | openssl dgst -sha256 -r | cut -d' ' -f1 | tr a-f A-F)
  [ "$sha" = "$CA_SHA" ] || quit "The server's certificate didn't match what was expected. Nothing was changed."
  if [ "$(uname)" = Darwin ]; then
    say "Trusting the server's certificate. macOS will ask for your password."
    security add-trusted-cert -r trustRoot -k "$HOME/Library/Keychains/login.keychain-db" "$ca"
  else
    # Linux has no per-user trust store, and Claude Code reads NODE_EXTRA_CA_CERTS only at startup,
    # so it goes in the shell profile (a settings.json env entry comes too late for TLS).
    line="export NODE_EXTRA_CA_CERTS=\"$ca\"  # Artifact Buckets lab certificate"
    for rc in "$HOME/.profile" "$HOME/.bashrc" "$HOME/.zshrc"; do
      [ -f "$rc" ] || [ "$rc" = "$HOME/.profile" ] || continue
      grep -qs "Artifact Buckets lab certificate" "$rc" || printf '\n%s\n' "$line" >> "$rc"
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
  printf '\n  Already signed in. Claude Code can save to Artifact Buckets.\n\n'; exit 0
fi
say "Opening your browser to sign in. Sign in, then press Connect."
claude mcp login '{{MCP_NAME}}' </dev/tty || quit "Sign-in didn't finish. In Claude Code, run /mcp, choose artifact-buckets, then Authenticate."

printf '\n  Done. Claude Code can now save to Artifact Buckets.\n  Open Claude Code and try: save this to my buckets\n\n'
"""
