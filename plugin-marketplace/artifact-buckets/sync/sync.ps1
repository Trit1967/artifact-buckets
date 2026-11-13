# Artifact Buckets sync (Windows). macOS and Linux use sync.sh next to this file.
#
# Copies the files Claude Code made on this computer into your Artifact Buckets folders:
#   - it reads Claude Code's own history (%USERPROFILE%\.claude\projects) for files Claude created or edited,
#   - skips files kept in git (tracked or ignored), secrets, build and dependency folders, and
#     anything under .claude,
#   - files a project's files under projects\<folder or repo name>\ and loose files under inbox\,
#   - sends only what changed since last time.
#
#   sync.ps1 link [server]   connect this computer: looks for Claude's files, then opens the browser where the
#                            person picks which folders to copy and presses Connect (once)
#   sync.ps1 scan            show what would be copied, change nothing
#   sync.ps1 run             copy now
#   sync.ps1 exclude [dir]   never copy anything under this folder (no folder: list them)
#   sync.ps1 off | on        pause or resume automatic syncing (after each Claude Code reply)
#   sync.ps1 status          show the link and the last run
#   sync.ps1 unlink          disconnect: ends this computer's link (and the sign-in approved with it) on the
#                            server, and forgets it here
#   sync.ps1 hook            what the Claude Code plugin runs: starts a quiet "run" in a hidden window when
#                            linked and on, and returns at once (so closing Claude Code doesn't cut it short)
#
# Written for Windows PowerShell 5.1 (powershell.exe), which every Windows 10 and 11 computer has.
param([string]$Cmd = 'run', [string]$Arg = '')
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Quiet = ($Cmd -eq 'hook' -or $Cmd -eq 'hook-run')
if ($env:OS -ne 'Windows_NT') { if ($Quiet) { exit 0 }; Write-Host '  On macOS and Linux, use sync.sh.'; exit 1 }

$Dir = if ($env:ARTIFACT_BUCKETS_HOME) { $env:ARTIFACT_BUCKETS_HOME } else { Join-Path $HOME '.artifact-buckets' }
$Conf = Join-Path $Dir 'sync.conf'
$State = Join-Path $Dir 'state'
$ClaudeDir = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $HOME '.claude' }
$Utf8 = New-Object System.Text.UTF8Encoding $false
if ($Cmd -ne 'hook') { New-Item -ItemType Directory -Force -Path $State | Out-Null }

function Say([string]$m) { if (-not $Quiet) { Write-Host "  $m" } }
function Die([string]$m) { if ($script:HaveLock) { Remove-Item -LiteralPath (Join-Path $Dir 'lock') -Force -Recurse -ErrorAction SilentlyContinue }; if ($Quiet) { exit 0 }; Write-Host ''; Write-Host "  $m" -ForegroundColor Yellow; Write-Host ''; exit 1 }
function B64([string]$s) { [Convert]::ToBase64String($Utf8.GetBytes($s)) }
function Human([double]$b) {
  $u = 'B', 'KB', 'MB', 'GB'; $i = 0
  while ($b -ge 1024 -and $i -lt 3) { $b /= 1024; $i++ }
  if ($i -eq 0) { "{0} {1}" -f [int]$b, $u[$i] } else { "{0:N1} {1}" -f $b, $u[$i] }
}

function Read-Conf {
  $c = @{}
  if (Test-Path -LiteralPath $Conf) {
    foreach ($l in [IO.File]::ReadAllLines($Conf, $Utf8)) { $i = $l.IndexOf('='); if ($i -gt 0) { $c[$l.Substring(0, $i)] = $l.Substring($i + 1) } }
  }
  $c
}
function Write-Conf($c) {
  $lines = foreach ($k in 'server', 'token', 'computer', 'home', 'max', 'enabled', 'mode', 'ca') { if ($c[$k]) { "$k=$($c[$k])" } }
  [IO.File]::WriteAllLines($Conf, [string[]]$lines, $Utf8)
}
$C = Read-Conf

# ---- the compiled helper: reading history, git checks, uploads, the pinned certificate ----------------
$Helper = @'
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Security;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Text.RegularExpressions;

public static class ABSync1 {
  static readonly Regex Fp = new Regex("\"filePath\":\"((?:[^\"\\\\]|\\\\.)*)\"");
  static readonly Regex Cw = new Regex("\"cwd\":\"((?:[^\"\\\\]|\\\\.)*)\"");

  // JSON string body -> text. Paths with \u escapes or control characters are left out (null).
  static string Unesc(string s) {
    var b = new StringBuilder(s.Length);
    for (int i = 0; i < s.Length; i++) {
      char ch = s[i];
      if (ch != '\\') { b.Append(ch); continue; }
      if (++i >= s.Length) return null;
      char n = s[i];
      if (n == '\\' || n == '"' || n == '/') b.Append(n); else return null;
    }
    return b.ToString();
  }

  // Files Claude created or edited in these transcripts: path -> the cwd of its newest edit.
  public static Dictionary<string, string> Scan(string[] files) {
    var last = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
    foreach (var f in files) {
      try {
        using (var fs = new FileStream(f, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete, 1 << 16))
        using (var r = new StreamReader(fs, new UTF8Encoding(false), false, 1 << 16)) {
          string line;
          while ((line = r.ReadLine()) != null) {
            if (line.IndexOf("\"structuredPatch\"", StringComparison.Ordinal) < 0) continue;
            var m = Fp.Match(line);
            if (!m.Success) continue;
            var p = Unesc(m.Groups[1].Value);
            if (string.IsNullOrEmpty(p)) continue;
            var c = Cw.Match(line);
            last[p] = c.Success ? (Unesc(c.Groups[1].Value) ?? "") : "";
          }
        }
      } catch (IOException) { } catch (UnauthorizedAccessException) { }
    }
    return last;
  }

  // Run git with UTF-8 in and out. Returns null when git isn't there or fails.
  public static string Git(string dir, string args, string input) {
    try {
      var psi = new ProcessStartInfo("git", "-C \"" + dir.TrimEnd('\\') + "\" " + args);
      psi.UseShellExecute = false; psi.CreateNoWindow = true;
      psi.RedirectStandardOutput = true; psi.RedirectStandardError = true; psi.RedirectStandardInput = input != null;
      psi.StandardOutputEncoding = new UTF8Encoding(false);
      using (var p = Process.Start(psi)) {
        System.Threading.Tasks.Task feed = null;
        if (input != null) {
          var w = new StreamWriter(p.StandardInput.BaseStream, new UTF8Encoding(false));
          feed = System.Threading.Tasks.Task.Factory.StartNew(() => { try { w.Write(input); w.Close(); } catch (IOException) { } });
        }
        p.StandardError.ReadToEndAsync();
        string o = p.StandardOutput.ReadToEnd();
        if (feed != null) feed.Wait();
        p.WaitForExit();
        return (p.ExitCode == 0 || p.ExitCode == 1) ? o : null;  // check-ignore exits 1 when nothing matched
      }
    } catch (Exception) { return null; }
  }

  // Trust the server's own certificate (saved at link time) even if Windows doesn't.
  static byte[] pin;
  public static void Pin(string certFile) {
    pin = new X509Certificate2(certFile).RawData;
    ServicePointManager.ServerCertificateValidationCallback = Check;
  }
  static bool Check(object s, X509Certificate c, X509Chain ch, SslPolicyErrors e) {
    if (e == SslPolicyErrors.None) return true;
    if (pin == null || c == null) return false;
    var raw = c.GetRawCertData();
    if (raw.Length != pin.Length) return false;
    for (int i = 0; i < raw.Length; i++) if (raw[i] != pin[i]) return false;
    return true;
  }

  // PUT a file. Returns the HTTP status (0 when the server can't be reached) and the response body.
  public static int Put(string url, string file, string[] headers, out string body) {
    body = "";
    try {
      var rq = (HttpWebRequest)WebRequest.Create(url);
      rq.Method = "PUT"; rq.ContentType = "application/octet-stream"; rq.Timeout = 300000;
      rq.ReadWriteTimeout = 300000; rq.AllowWriteStreamBuffering = false;
      for (int i = 0; i + 1 < headers.Length; i += 2) rq.Headers[headers[i]] = headers[i + 1];
      using (var src = new FileStream(file, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete)) {
        rq.ContentLength = src.Length;
        using (var dst = rq.GetRequestStream()) src.CopyTo(dst);
      }
      using (var rs = (HttpWebResponse)rq.GetResponse())
      using (var rd = new StreamReader(rs.GetResponseStream())) { body = rd.ReadToEnd(); return (int)rs.StatusCode; }
    } catch (WebException we) {
      var rs = we.Response as HttpWebResponse;
      if (rs == null) return 0;
      using (var rd = new StreamReader(rs.GetResponseStream())) body = rd.ReadToEnd();
      return (int)rs.StatusCode;
    } catch (IOException) { return -1; }
  }
}
'@

function Load-Helper {
  if ('ABSync1' -as [type]) { return }
  if ($ExecutionContext.SessionState.LanguageMode -ne 'FullLanguage') {
    Die 'Windows security settings on this computer block the sync helper (PowerShell Constrained Language). Ask IT to allow it.'
  }
  $lib = Join-Path $Dir 'lib'
  $dll = Join-Path $lib 'ABSync1.dll'
  try {
    if (-not (Test-Path -LiteralPath $dll)) {
      New-Item -ItemType Directory -Force -Path $lib | Out-Null
      Add-Type -TypeDefinition $Helper -OutputAssembly $dll -OutputType Library -ReferencedAssemblies System, System.Core
    }
    if (-not ('ABSync1' -as [type])) { Add-Type -LiteralPath $dll }
  } catch {
    Remove-Item -LiteralPath $dll -Force -ErrorAction SilentlyContinue
    Add-Type -TypeDefinition $Helper
  }
}

function Use-Network {
  [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
  Load-Helper
  $ca = $C['ca']
  if ($ca -and (Test-Path -LiteralPath $ca)) { [ABSync1]::Pin($ca) }
}

# ---- link -----------------------------------------------------------------------------------
function Link([string]$srv) {
  if (-not $srv) { $srv = $C['server'] }
  if (-not $srv) { Die 'Give the server address: sync.ps1 link https://buckets.example.com' }
  $srv = $srv.TrimEnd('/')
  $crt = Join-Path $Dir 'server.crt'
  if (Test-Path -LiteralPath $crt) { $C['ca'] = $crt }
  $name = if ($env:COMPUTERNAME) { $env:COMPUTERNAME } else { 'This computer' }
  # Look first, so the browser can show which folders would be copied and the person can pick.
  Say 'Looking for files Claude has already made here (the first time can take a minute)...'
  # A full look, in its own scratch folder: the current link, what was sent and what's skipped stay as they
  # are until the person actually connects in the browser.
  $keepState = $script:State; $keepSkip = $script:SkipFolders; $q = $script:Quiet
  $script:State = Join-Path $Dir 'link-scan'
  Remove-Item -LiteralPath $script:State -Recurse -Force -ErrorAction SilentlyContinue
  New-Item -ItemType Directory -Force -Path $script:State | Out-Null
  $script:SkipFolders = @(); $script:Quiet = $true
  try { Sync-Files 'dry' } finally {
    Remove-Item -LiteralPath $script:State -Recurse -Force -ErrorAction SilentlyContinue
    $script:State = $keepState; $script:SkipFolders = $keepSkip; $script:Quiet = $q
  }
  $folders = @($script:LastSend | Group-Object { if ($_.Where -eq 'projects') { "projects/$($_.Project)" } else { 'inbox' } } |
    Select-Object -First 200 | ForEach-Object {
      $g = $_.Group
      $src = if ($g[0].Where -eq 'projects') { $g[0].File.Substring(0, $g[0].File.Length - $g[0].Rel.Length - 1) } else { '' }
      @{ folder = $_.Name; files = $g.Count; bytes = [long](($g | ForEach-Object { (New-Object IO.FileInfo $_.File).Length } | Measure-Object -Sum).Sum); source = $src }
    })
  Use-Network
  try {
    $s = Invoke-RestMethod -Method Post -Uri "$srv/api/sync/link/start" -ContentType 'application/json; charset=utf-8' `
      -Body ($Utf8.GetBytes((@{ computer = $name; folders = $folders } | ConvertTo-Json -Depth 4 -Compress))) -UseBasicParsing
  } catch { Die "Couldn't reach $srv ($($_.Exception.Message))" }
  Write-Host ''
  Write-Host "  Finish in your browser: $($s.link)"
  Write-Host "  (If it asks, the code is $($s.code).)"
  Write-Host ''
  if ("$($s.link)" -match '^https?://') { try { Start-Process $s.link } catch { } }
  for ($i = 0; $i -lt 300; $i++) {
    Start-Sleep -Seconds 2
    try {
      $r = Invoke-RestMethod -Method Post -Uri "$srv/api/sync/link/poll" -ContentType 'application/json' `
        -Body (@{ poll = $s.poll } | ConvertTo-Json) -UseBasicParsing
    } catch { continue }
    switch ($r.status) {
      { $_ -in 'approved', 'connected' } {
        $max = if ($r.max_file_bytes) { $r.max_file_bytes } else { 524288000 }
        # A new link starts afresh: everything it is allowed to copy is copied again (the server skips files
        # it already has).
        Remove-Item -LiteralPath (Join-Path $State 'sent.tsv'), (Join-Path $State 'last-run'), (Join-Path $State 'next-run') -Force -ErrorAction SilentlyContinue
        $skip = @($r.skip | Where-Object { $_ })
        [IO.File]::WriteAllLines($SkipFile, [string[]]$skip, $Utf8)
        $sync = $r.status -eq 'approved'
        $script:C = @{ server = $srv; token = $r.token; computer = $name; home = $r.home; max = "$max"
                       enabled = $(if ($sync) { 'on' } else { 'off' }); mode = $(if ($sync) { 'sync' } else { 'connect' }); ca = $C['ca'] }
        Write-Conf $script:C
        if ($sync) { Write-Host "  Connected. Files Claude makes on this computer go to $($r.home)" }
        else { Write-Host '  Connected, without syncing. Nothing will be copied from this computer.' }
        Write-Host ''
        return
      }
      'denied' { Die 'Not connected: it was declined in the browser.' }
      'expired' { Die 'The link expired. Run this again.' }
    }
  }
  Die 'Timed out waiting for the browser.'
}

# ---- deciding what goes where ----------------------------------------------------------------
$BuildDirs = @('node_modules', '.git', 'dist', 'build', '.venv', 'venv', '__pycache__', '.next', 'target', '.cache', 'vendor')
$SecretNames = @('.env', '.env.*', '*.pem', '*.key', '*.p12', '*.pfx', '*.jks', '*.keystore', '*.crt', '*.cer', '*.der',
  'id_rsa*', 'id_ed25519*', 'id_ecdsa*', 'id_dsa*', '.npmrc', '.netrc', '.pypirc', '*.kdbx', '*secret*', '*credential*',
  '*password*', '*.tfstate', '*.tfstate.*', '*.tfvars')
$SystemDirs = @($ClaudeDir, $Dir, $env:TEMP, $env:TMP, $env:WINDIR, (Join-Path $HOME 'AppData')) |
  Where-Object { $_ } | ForEach-Object { [IO.Path]::GetFullPath($_).TrimEnd('\') + '\' }

$ExcludeFile = Join-Path $Dir 'exclude'
# Folders the person unticked on the Connect page ("projects/<name>" or "inbox").
$SkipFile = Join-Path $Dir 'skip-folders'
$SkipFolders = @()
if (Test-Path -LiteralPath $SkipFile) { $SkipFolders = @([IO.File]::ReadAllLines($SkipFile, $Utf8) | Where-Object { $_.Trim() }) }
$Excludes = @()
if (Test-Path -LiteralPath $ExcludeFile) {
  $Excludes = @([IO.File]::ReadAllLines($ExcludeFile, $Utf8) | Where-Object { $_.Trim() -and -not $_.StartsWith('#') } |
    ForEach-Object { $x = $_.Trim().TrimEnd('\'); if ($x.StartsWith('~')) { $x = $HOME + $x.Substring(1) }; $x })
}

function Under([string]$path, [string]$dir) { $path.StartsWith($dir.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) }

$TopCache = @{}
function Git-Top([string]$d) {
  $k = $d.ToLowerInvariant()
  if (-not $TopCache.ContainsKey($k)) {
    $o = if ($HasGit) { [ABSync1]::Git($d, 'rev-parse --show-toplevel', $null) } else { $null }
    $TopCache[$k] = if ($o -and $o.Trim()) { $o.Trim().Replace('/', '\').TrimEnd('\') } else { '' }
  }
  $TopCache[$k]
}

# Why a file is left out before git is asked, or '' to keep going.
function Pre-Skip([string]$f, [long]$max) {
  if (-not [IO.File]::Exists($f)) { return 'gone' }
  foreach ($s in $SystemDirs) { if ($f.StartsWith($s, [StringComparison]::OrdinalIgnoreCase)) { return 'system' } }
  $parts = $f.Split('\')
  if ($parts -contains '.buckets') { return 'system' }
  foreach ($p in $parts[0..($parts.Length - 2)]) { if ($BuildDirs -contains $p) { return 'build' } }
  # Any hidden folder on the way (~\.ssh, ~\.aws, .config, ...) or a hidden file.
  foreach ($p in $parts[1..($parts.Length - 1)]) { if ($p.StartsWith('.')) { return 'hidden' } }
  foreach ($x in $Excludes) { if ($f.Equals($x, [StringComparison]::OrdinalIgnoreCase) -or (Under $f $x)) { return 'excluded' } }
  $base = $parts[-1]
  foreach ($n in $SecretNames) { if ($base -like $n) { return 'secret' } }
  $len = (New-Object IO.FileInfo $f).Length
  if ($len -le 0) { return 'empty' }
  if ($len -gt $max) { return 'too big' }
  ''
}

function Plan-Files($cands, [long]$max) {
  $home_ = [IO.Path]::GetFullPath($HOME).TrimEnd('\')
  $items = New-Object System.Collections.ArrayList
  foreach ($kv in $cands.GetEnumerator()) {
    $f = $kv.Key; $cwd = $kv.Value
    try { $f = [IO.Path]::GetFullPath($f) } catch { continue }
    $why = Pre-Skip $f $max
    [void]$items.Add([pscustomobject]@{ File = $f; Cwd = $cwd; Skip = $why; Top = ''; Where = ''; Project = ''; Rel = '' })
  }
  # Ask git once per repository: which of these are tracked, which are ignored.
  $byTop = @{}
  foreach ($it in $items) {
    if ($it.Skip) { continue }
    $top = Git-Top ([IO.Path]::GetDirectoryName($it.File))
    if (-not $top -or -not (Under $it.File $top)) { continue }   # junctions or subst drives: git names another path
    $it.Top = $top
    if (-not $byTop.ContainsKey($top)) { $byTop[$top] = New-Object System.Collections.ArrayList }
    [void]$byTop[$top].Add($it)
  }
  foreach ($top in $byTop.Keys) {
    $tracked = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    $o = [ABSync1]::Git($top, 'ls-files -z', $null)
    if ($o) { foreach ($p in $o.Split([char]0)) { if ($p) { [void]$tracked.Add($p) } } }
    $rels = foreach ($it in $byTop[$top]) { $it.File.Substring($top.Length + 1).Replace('\', '/') }
    $ignored = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    $o = [ABSync1]::Git($top, 'check-ignore --stdin -z', (($rels -join [char]0) + [char]0))
    if ($o) { foreach ($p in $o.Split([char]0)) { if ($p) { [void]$ignored.Add($p) } } }
    foreach ($it in $byTop[$top]) {
      $r = $it.File.Substring($top.Length + 1).Replace('\', '/')
      if ($tracked.Contains($r)) { $it.Skip = 'in git' } elseif ($ignored.Contains($r)) { $it.Skip = 'ignored by git' }
    }
  }
  # Where each remaining file goes: its project (the repo, or the folder Claude Code ran in), or the inbox.
  foreach ($it in $items) {
    if ($it.Skip) { continue }
    $root = ''
    $cwd = $it.Cwd
    if ($cwd) { try { $cwd = [IO.Path]::GetFullPath($cwd).TrimEnd('\') } catch { $cwd = '' } }
    if ($cwd -and $cwd -ne $home_ -and $cwd -notmatch '^[A-Za-z]:$') {
      $root = $cwd
      $t = Git-Top $cwd
      if ($t -and $t -ne $home_ -and ((Under $cwd $t) -or $cwd -eq $t)) { $root = $t }
    }
    # Made outside the folder Claude Code ran in: file it under its own repository if it has one,
    # otherwise keep its full path in the inbox so files with the same name don't collide.
    if ($root -and -not (Under $it.File $root)) { $root = '' }
    if (-not $root) {
      $t = Git-Top ([IO.Path]::GetDirectoryName($it.File))
      if ($t -and $t -ne $home_ -and (Under $it.File $t)) { $root = $t }
    }
    if ($root) {
      $it.Where = 'projects'; $it.Project = Split-Path -Leaf $root; $it.Rel = $it.File.Substring($root.Length + 1).Replace('\', '/')
    } else {
      $it.Where = 'inbox'; $it.Project = ''
      $r = if (Under $it.File $home_) { $it.File.Substring($home_.Length + 1) } else { $it.File -replace '^[A-Za-z]:\\', '' -replace '^\\\\[^\\]+\\[^\\]+\\', '' }
      $it.Rel = $r.Replace('\', '/')
    }
    if (('/' + $it.Rel) -match '/\.') { $it.Skip = 'hidden' }
  }
  $items
}

# ---- scan and run --------------------------------------------------------------------------
function Sync-Files([string]$mode) {
  if ($mode -ne 'dry' -and -not $C['token']) { Die "This computer isn't linked yet. Run: sync.ps1 link <server>" }
  if ($mode -ne 'dry' -and $C['mode'] -eq 'connect') { Die 'This computer is connected without syncing. To sync, connect again: sync.ps1 link' }
  if ($mode -eq 'dry') { Load-Helper } else { Use-Network }
  $script:HasGit = [bool](Get-Command git -ErrorAction SilentlyContinue)
  $max = if ($C['max']) { [long]$C['max'] } else { 524288000 }
  $destHome = if ($C['home']) { $C['home'] } else { 'your folder' }
  $marker = Join-Path $State 'last-run'; $next = Join-Path $State 'next-run'
  $sentFile = Join-Path $State 'sent.tsv'
  [IO.File]::WriteAllText($next, '')
  $since = if (Test-Path -LiteralPath $marker) { (Get-Item -LiteralPath $marker).LastWriteTimeUtc } else { [datetime]::MinValue }
  $proj = Join-Path $ClaudeDir 'projects'
  $transcripts = if (Test-Path -LiteralPath $proj) {
    [string[]](Get-ChildItem -LiteralPath $proj -Recurse -File -Filter '*.jsonl' -ErrorAction SilentlyContinue |
      Where-Object { $_.LastWriteTimeUtc -gt $since } | ForEach-Object { $_.FullName })
  } else { [string[]]@() }
  if (-not $transcripts) { $transcripts = [string[]]@() }
  $cands = [ABSync1]::Scan($transcripts)
  if ($cands.Count -gt 0) { Say "Checking $($cands.Count) file(s) Claude made..." }

  $sent = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
  if (Test-Path -LiteralPath $sentFile) { foreach ($l in [IO.File]::ReadAllLines($sentFile, $Utf8)) { [void]$sent.Add($l) } }
  $items = Plan-Files $cands $max
  $send = New-Object System.Collections.ArrayList
  foreach ($it in $items) {
    if ($it.Skip) { continue }
    $it | Add-Member -NotePropertyName Dest -NotePropertyValue ("$($it.Where)/$($it.Project)/$($it.Rel)")
    $fo = if ($it.Where -eq 'projects') { "projects/$($it.Project)" } else { 'inbox' }
    if ($SkipFolders -contains $fo) { $it.Skip = 'not chosen'; continue }
    $it | Add-Member -NotePropertyName Sha -NotePropertyValue ((Get-FileHash -LiteralPath $it.File -Algorithm SHA256).Hash.ToLowerInvariant())
    if ($sent.Contains("$($it.Dest)`t$($it.Sha)")) { $it.Skip = 'unchanged'; continue }
    [void]$send.Add($it)
  }

  $script:LastSend = $send
  if ($mode -eq 'dry') {
    if ($send.Count -eq 0) { Say 'Nothing new to copy.' } else {
      Say "Would copy $($send.Count) file(s):"
      $send | Group-Object { if ($_.Where -eq 'projects') { "projects/$($_.Project)" } else { 'inbox' } } | Sort-Object Name |
        ForEach-Object {
          $bytes = ($_.Group | ForEach-Object { (New-Object IO.FileInfo $_.File).Length } | Measure-Object -Sum).Sum
          Say "  $destHome/$($_.Name)  ($($_.Count), $(Human $bytes))"
        }
    }
    $items | Where-Object { $_.Skip -and $_.Skip -ne 'unchanged' -and $_.Skip -ne 'gone' } | Group-Object Skip | Sort-Object Name |
      ForEach-Object { Say "Leaving out $($_.Count): $($_.Name)" }
    return
  }

  $ok = 0; $failed = 0; $skipped = 0
  $url = $C['server'].TrimEnd('/') + '/api/sync/file'
  foreach ($it in $send) {
    $h = [string[]]@('Authorization', "Bearer $($C['token'])", 'X-Sync-Where', $it.Where, 'X-Sync-Path', (B64 $it.Rel))
    if ($it.Where -eq 'projects') { $h += @('X-Sync-Project', (B64 $it.Project)) }
    $body = ''
    $code = [ABSync1]::Put($url, $it.File, $h, [ref]$body)
    switch ($code) {
      200 { [IO.File]::AppendAllText($sentFile, "$($it.Dest)`t$($it.Sha)`r`n", $Utf8); $ok++; Say "Copied $($it.Rel)" }
      401 { Die "This computer's link was turned off. Run: sync.ps1 link" }
      0 { $failed++; Say "Couldn't reach the server for $($it.Rel)" }
      -1 { $failed++; Say "Couldn't read $($it.Rel)" }
      { $_ -in 400, 403, 404, 409, 413, 415, 422 } {
        # The server will refuse this file as it is: don't retry until it changes.
        [IO.File]::AppendAllText($sentFile, "$($it.Dest)`t$($it.Sha)`r`n", $Utf8); $skipped++
        $msg = try { ($body | ConvertFrom-Json).message } catch { "error $code" }
        Say "Not copied: $($it.Rel) ($msg)"
      }
      default { $failed++; Say "Not copied yet: $($it.Rel) (the server said $code)" }
    }
  }
  # Next time, only history written after this run began. Anything that failed is tried again.
  if ($failed -eq 0) { Move-Item -LiteralPath $next -Destination $marker -Force }
  if ($failed -gt 0) { Say "Done: $ok copied, $failed not copied yet."; Say "Files that weren't copied are tried again next time." }
  elseif ($skipped -gt 0) { Say "Done: $ok copied, $skipped refused by the server." }
  else { Say "Done: $ok copied." }
}

# ---- one run at a time ---------------------------------------------------------------------
$Lock = Join-Path $Dir 'lock'
function Take-Lock {
  try { New-Item -ItemType Directory -Path $Lock -ErrorAction Stop | Out-Null; $script:HaveLock = $true; return }
  catch {
    # A run that died more than 30 minutes ago leaves its lock behind.
    $l = Get-Item -LiteralPath $Lock -ErrorAction SilentlyContinue
    if ($l -and $l.LastWriteTime -lt (Get-Date).AddMinutes(-30)) {
      Remove-Item -LiteralPath $Lock -Force -Recurse -ErrorAction SilentlyContinue
      try { New-Item -ItemType Directory -Path $Lock -ErrorAction Stop | Out-Null; $script:HaveLock = $true; return } catch { }
    }
    if ($Quiet) { exit 0 }
    Die 'Another sync is running.'
  }
}
function Drop-Lock { Remove-Item -LiteralPath $Lock -Force -Recurse -ErrorAction SilentlyContinue; $script:HaveLock = $false }

switch ($Cmd) {
  'link' { Take-Lock; try { Link $Arg } finally { Drop-Lock } }
  'exclude' {
    if ($Arg) {
      $x = try { (Resolve-Path -LiteralPath $Arg -ErrorAction Stop).ProviderPath.TrimEnd('\') } catch { Die "No such folder: $Arg" }
      if ($x -match '^[A-Za-z]:$' -or -not $x) { Die 'Excluding a whole drive would stop sync there altogether. Use: sync.ps1 off' }
      if (-not ($Excludes -contains $x)) { [IO.File]::AppendAllText($ExcludeFile, "$x`r`n", $Utf8) }
      Say "Won't copy anything under $x."
    } elseif ($Excludes.Count) { Say 'Never copied:'; foreach ($x in $Excludes) { Say "  $x" } }
    else { Say 'No folders excluded. Add one with: sync.ps1 exclude <folder>' }
  }
  'scan' { Take-Lock; try { Sync-Files 'dry' } finally { Drop-Lock } }
  'run' { Take-Lock; try { Sync-Files 'send' } finally { Drop-Lock } }
  'hook' {
    if (-not $C['token'] -or $C['enabled'] -eq 'off' -or $C['mode'] -eq 'connect') { exit 0 }
    $ps = Join-Path $PSHOME 'powershell.exe'; if (-not (Test-Path -LiteralPath $ps)) { $ps = 'powershell.exe' }
    try {
      Start-Process -FilePath $ps -WindowStyle Hidden -ArgumentList @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-WindowStyle', 'Hidden', '-File', "`"$PSCommandPath`"", 'hook-run') | Out-Null
    } catch { }
    exit 0
  }
  'hook-run' {
    if (-not $C['token'] -or $C['enabled'] -eq 'off' -or $C['mode'] -eq 'connect') { exit 0 }
    Take-Lock
    try { Sync-Files 'send' *> $null } catch { } finally { Drop-Lock }
    exit 0
  }
  { $_ -in 'off', 'on' } {
    if (-not $C['token']) { Die 'Not linked.' }
    if ($Cmd -eq 'on' -and $C['mode'] -eq 'connect') { Die 'This computer is connected without syncing. To sync, connect again: sync.ps1 link' }
    $C['enabled'] = $Cmd; Write-Conf $C; Say "Automatic sync is $Cmd."
  }
  'status' {
    if ($C['mode'] -eq 'connect') { Say "Connected to $($C['server']) without syncing." }
    elseif (-not $C['token']) { Say 'Not linked.' } else {
      Say "Linked to $($C['server']) as $($C['home']) ($($C['computer'])). Automatic sync: $($C['enabled'])."
      $m = Join-Path $State 'last-run'
      if (Test-Path -LiteralPath $m) { Say "Last complete run: $((Get-Item -LiteralPath $m).LastWriteTime)" }
      $s = Join-Path $State 'sent.tsv'
      $n = if (Test-Path -LiteralPath $s) { [IO.File]::ReadAllLines($s).Count } else { 0 }
      Say "Files copied so far: $n"
    }
  }
  'unlink' {
    $ended = $false
    if ($C['token'] -and $C['server']) {
      try {
        Use-Network
        $u = Invoke-RestMethod -Method Post -Uri "$($C['server'].TrimEnd('/'))/api/sync/unlink" -UseBasicParsing `
          -Headers @{ Authorization = "Bearer $($C['token'])" }
        $ended = $true
      } catch { }
    }
    Remove-Item -LiteralPath $Conf, $SkipFile -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $State -Force -Recurse -ErrorAction SilentlyContinue
    $what = if ($C['mode'] -eq 'connect') { 'connection' } else { 'sync' }
    if ($ended -and $u.signed_out) { Say "Disconnected: this computer's $what and its Claude Code sign-in are turned off on the server." }
    elseif ($ended) { Say "Disconnected: this computer's $what is turned off on the server." }
    else { Say "Forgot the link here. (If the server couldn't be reached, turn this computer off on the Connect page.)" }
  }
  default { Die "Unknown command: $Cmd (link, scan, run, exclude, off, on, status, unlink)" }
}
