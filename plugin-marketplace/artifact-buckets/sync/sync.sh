#!/bin/sh
# Artifact Buckets sync (macOS and Linux). Windows uses sync.ps1 next to this file.
#
# Copies the files Claude Code made on this computer into your Artifact Buckets folders:
#   - it reads Claude Code's own history (~/.claude/projects) for files Claude created or edited,
#   - skips files kept in git (tracked or ignored), secrets, build and dependency folders, and
#     anything under ~/.claude,
#   - files a project's files under projects/<folder or repo name>/ and loose files under inbox/,
#   - sends only what changed since last time.
#
#   sync.sh link [server]   connect this computer: looks for Claude's files, then opens the browser where the
#                           person picks which folders to copy and presses Connect (once)
#   sync.sh scan            show what would be copied, change nothing
#   sync.sh run             copy now
#   sync.sh exclude [dir]   never copy anything under this folder (no folder: list them)
#   sync.sh off | on        pause or resume automatic syncing (after each Claude Code reply)
#   sync.sh status          show the link and the last run
#   sync.sh unlink          disconnect: ends this computer's link (and the sign-in approved with it) on the
#                           server, and forgets it here
#   sync.sh hook            what the Claude Code plugin runs: starts a quiet "run" in the background when
#                           linked and on, and returns at once (so closing Claude Code doesn't cut it short)
set -u
CMD=${1:-run}
QUIET=; case "$CMD" in hook|hook-run) QUIET=1 ;; esac
case "$(uname -s 2>/dev/null)" in
  MINGW*|MSYS*|CYGWIN*) [ -n "$QUIET" ] && exit 0 ;;  # on Windows the PowerShell script does the work
esac

DIR=${ARTIFACT_BUCKETS_HOME:-$HOME/.artifact-buckets}
CONF=$DIR/sync.conf
SKIPF=$DIR/skip-folders   # folders the person unticked when connecting ("projects/<name>" or "inbox"), one a line
STATE=$DIR/state
CLAUDE_DIR=${CLAUDE_CONFIG_DIR:-$HOME/.claude}
umask 077
[ "$CMD" = hook ] || mkdir -p "$STATE"

conf() { [ -f "$CONF" ] && sed -n "s/^$1=//p" "$CONF" | tail -n 1; }
say() { [ -n "$QUIET" ] || printf '  %s\n' "$*"; }
die() { [ -n "$QUIET" ] && exit 0; printf '\n  %s\n\n' "$*" >&2; exit 1; }
b64() { printf '%s' "$1" | base64 | tr -d '\n'; }
sha() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }
human() { awk -v b="$1" 'BEGIN{ split("B KB MB GB",u," "); i=1; while (b>=1024 && i<4) { b/=1024; i++ } printf (i==1 ? "%d %s" : "%.1f %s"), b, u[i] }'; }

SERVER=$(conf server); TOKEN=$(conf token); CA=$(conf ca)
curl_() {
  if [ -n "$CA" ] && [ -f "$CA" ]; then curl --cacert "$CA" "$@"; else curl "$@"; fi
}

# The folders a first copy would fill, as JSON for the Connect page: [{"folder","files","bytes","source"}].
# The server lists up to 2000; any folders past that are written to $DIR/link-overflow and never copied.
folders_json() {
  plan=$STATE/plan.tsv
  : > "$DIR/link-overflow"
  [ -s "$plan" ] || { printf '[]'; return; }
  grep '^send' "$plan" | while IFS="$(printf '\t')" read -r _ where project rel _ f; do
    if [ "$where" = projects ]; then folder="projects/$project"; src=${f%/"$rel"}; else folder=inbox; src=""; fi
    printf '%s\t%s\t%s\n' "$folder" "$(wc -c < "$f" | tr -d ' ')" "$src"
  done | awk -F'\t' -v ov="$DIR/link-overflow" '
    function esc(s) { gsub(/\\/, "\\\\", s); gsub(/"/, "\\\"", s); gsub(/[\001-\037]/, "", s); return s }
    { n[$1]++; b[$1] += $2; if (!($1 in s)) s[$1] = $3; if (!($1 in o)) { o[$1] = ++k; key[k] = $1 } }
    END { printf "["; for (i = 1; i <= k && i <= 2000; i++) { f = key[i]
            printf "%s{\"folder\":\"%s\",\"files\":%d,\"bytes\":%d,\"source\":\"%s\"}", (i > 1 ? "," : ""), esc(f), n[f], b[f], esc(s[f]) }
          printf "]"; for (; i <= k; i++) print key[i] > ov }'
}

# A random id this computer keeps, so two computers with the same name stay apart on the server.
computer_id() {
  id=$(cat "$DIR/computer-id" 2>/dev/null || true)
  case "$id" in ????????????????*) ;; *)
    id=$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n'); printf '%s\n' "$id" > "$DIR/computer-id" ;; esac
  printf '%s' "$id" | tr -cd 'A-Za-z0-9_-' | cut -c1-64
}

link() {
  srv=${1:-$SERVER}
  [ -n "$srv" ] || die "Give the server address: sync.sh link https://buckets.example.com"
  srv=${srv%/}
  [ -f "$DIR/server.crt" ] && CA=$DIR/server.crt
  name=$(hostname 2>/dev/null | cut -d. -f1); [ -n "$name" ] || name="This computer"
  say "Looking for files Claude has already made here (the first time can take a minute)…"
  # A full look, in its own scratch folder: the current link, what was sent and what's skipped stay as they
  # are until the person actually connects in the browser.
  keep_state=$STATE; keep_skip=$SKIPF; q=$QUIET
  STATE=$DIR/link-scan; rm -rf "$STATE"; mkdir -p "$STATE"; SKIPF=/dev/null; QUIET=1
  sync_ dry; folders=$(folders_json)
  rm -rf "$STATE"; STATE=$keep_state; SKIPF=$keep_skip; QUIET=$q
  body=$(printf '{"computer":"%s","computer_id":"%s","folders":%s}' "$(printf '%s' "$name" | tr -d '"\\')" "$(computer_id)" "$folders")
  start=$(printf '%s' "$body" | curl_ -fsS -X POST -H 'content-type: application/json' --data-binary @- "$srv/api/sync/link/start") \
    || die "Couldn't reach $srv"
  code=$(printf '%s' "$start" | sed -n 's/.*"code":"\([^"]*\)".*/\1/p')
  poll=$(printf '%s' "$start" | sed -n 's/.*"poll":"\([^"]*\)".*/\1/p')
  url=$(printf '%s' "$start" | sed -n 's/.*"link":"\([^"]*\)".*/\1/p')
  printf '\n  Finish in your browser: %s\n  (If it asks, the code is %s.)\n\n' "$url" "$code"
  (command -v open >/dev/null 2>&1 && open "$url") || (command -v xdg-open >/dev/null 2>&1 && xdg-open "$url") >/dev/null 2>&1 || true
  i=0
  while [ $i -lt 300 ]; do
    sleep 2; i=$((i + 1))
    r=$(curl_ -fsS -X POST -H 'content-type: application/json' --data "{\"poll\":\"$poll\"}" "$srv/api/sync/link/poll") || continue
    st=$(printf '%s' "$r" | sed -n 's/.*"status":"\([^"]*\)".*/\1/p')
    case "$st" in
      approved|connected)
        tok=$(printf '%s' "$r" | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')
        max=$(printf '%s' "$r" | sed -n 's/.*"max_file_bytes":\([0-9]*\).*/\1/p')
        home=$(printf '%s' "$r" | sed -n 's/.*"home":"\([^"]*\)".*/\1/p')
        # A new link starts afresh: everything it is allowed to copy is copied again (the server skips
        # files it already has).
        rm -f "$STATE"/sent.tsv "$STATE"/last-run "$STATE"/next-run
        : > "$SKIPF.new"
        for x in $(printf '%s' "$r" | sed -n 's/.*"skip_b64":"\([^"]*\)".*/\1/p' | tr ',' ' '); do
          printf '%s' "$x" | { base64 -d 2>/dev/null || base64 -D; } >> "$SKIPF.new"; printf '\n' >> "$SKIPF.new"
        done
        cat "$DIR/link-overflow" >> "$SKIPF.new" 2>/dev/null || true
        mv "$SKIPF.new" "$SKIPF"
        if [ "$st" = approved ]; then en=on; mode=sync; else en=off; mode=connect; fi
        { echo "server=$srv"; echo "token=$tok"; echo "computer=$name"; echo "home=$home"; echo "max=${max:-524288000}"
          echo "enabled=$en"; echo "mode=$mode"; [ -n "$CA" ] && echo "ca=$CA"; } > "$CONF"
        if [ "$mode" = sync ]; then printf '  Connected. Files Claude makes on this computer go to %s\n\n' "$home"
        else printf '  Connected, without syncing. Nothing will be copied from this computer.\n\n'; fi
        return 0 ;;
      denied) die "Not connected: it was declined in the browser." ;;
      expired) die "The link expired. Run this again." ;;
    esac
  done
  die "Timed out waiting for the browser."
}

# Files Claude created or edited, newest cwd for each: "cwd<TAB>path" lines.
candidates() {
  marker=$1
  [ -d "$CLAUDE_DIR/projects" ] || return 0
  if [ -f "$marker" ]; then set -- -newer "$marker"; else set --; fi
  find "$CLAUDE_DIR/projects" -type f -name '*.jsonl' "$@" 2>/dev/null | while IFS= read -r t; do
    grep -F '"structuredPatch"' "$t" 2>/dev/null
  done | awk '
    function unesc(s) { gsub(/\\\\/, "\001", s); gsub(/\\"/, "\"", s); gsub(/\\\//, "/", s); gsub(/\001/, "\\", s); return s }
    {
      fp = ""; cw = ""
      if (match($0, /"filePath":"([^"\\]|\\.)*"/)) fp = unesc(substr($0, RSTART + 12, RLENGTH - 13))
      if (match($0, /"cwd":"([^"\\]|\\.)*"/)) cw = unesc(substr($0, RSTART + 7, RLENGTH - 8))
      if (fp != "" && fp !~ /\\u/) last[fp] = cw
    }
    END { for (f in last) print last[f] "\t" f }'
}

# Decide where one file goes. Prints "where<TAB>project<TAB>rel" or "skip<TAB>reason".
# (The inbox has no project: "-" holds its place, because read with a tab IFS merges empty fields.)
place() {
  cwd=$1; f=$2
  [ -f "$f" ] || { printf 'skip\tgone\n'; return; }
  case "$f" in
    "$CLAUDE_DIR"/*|"$DIR"/*|*/.buckets/*|/tmp/*|/var/folders/*|/private/var/*|"$HOME"/Library/*) printf 'skip\tsystem\n'; return ;;
    */node_modules/*|*/.git/*|*/dist/*|*/build/*|*/.venv/*|*/venv/*|*/__pycache__/*|*/.next/*|*/target/*|*/.cache/*|*/vendor/*)
      printf 'skip\tbuild\n'; return ;;
    */.*) printf 'skip\thidden\n'; return ;;   # any hidden folder on the way: ~/.ssh, ~/.aws, .config, ...
  esac
  if [ -f "$DIR/exclude" ]; then
    real=$(cd "${f%/*}" 2>/dev/null && pwd -P)/${f##*/}
    while IFS= read -r x; do
      x=${x%/}; case "$x" in ""|\#*) continue ;; "~"/*) x=$HOME/${x#"~/"} ;; esac
      case "$f" in "$x"/*|"$x") printf 'skip\texcluded\n'; return ;; esac
      case "$real" in "$x"/*|"$x") printf 'skip\texcluded\n'; return ;; esac
    done < "$DIR/exclude"
  fi
  base=${f##*/}
  lb=$(printf '%s' "$base" | tr 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' 'abcdefghijklmnopqrstuvwxyz')
  case "$lb" in
    .env|.env.*|*.pem|*.key|*.p12|*.pfx|*.jks|*.keystore|*.crt|*.cer|*.der|id_rsa*|id_ed25519*|id_ecdsa*|id_dsa*|.npmrc|.netrc|.pypirc|*.kdbx|*secret*|*credential*|*password*|*.tfstate|*.tfstate.*|*.tfvars)
      printf 'skip\tsecret\n'; return ;;
  esac
  size=$(wc -c < "$f" | tr -d ' ')
  [ "$size" -gt 0 ] || { printf 'skip\tempty\n'; return; }
  [ "$size" -le "${MAX:-524288000}" ] || { printf 'skip\ttoo big\n'; return; }
  d=${f%/*}; top=""
  if command -v git >/dev/null 2>&1; then
    top=$(git -C "$d" rev-parse --show-toplevel 2>/dev/null || true)
    if [ -n "$top" ]; then
      r=${f#"$top"/}
      if git -C "$top" ls-files --error-unmatch -- "$r" >/dev/null 2>&1; then printf 'skip\tin git\n'; return; fi
      if git -C "$top" check-ignore -q -- "$r" 2>/dev/null; then printf 'skip\tignored by git\n'; return; fi
    fi
  fi
  root=""
  if [ -n "$cwd" ] && [ "$cwd" != "$HOME" ] && [ "$cwd" != "/" ]; then
    root=$cwd
    if command -v git >/dev/null 2>&1; then
      t=$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null || true); [ -n "$t" ] && [ "$t" != "$HOME" ] && root=$t
    fi
  fi
  # Made outside the folder Claude Code ran in: file it under its own repository if it has one,
  # otherwise keep its full path in the inbox so files with the same name don't collide.
  [ -n "$root" ] && case "$f" in "$root"/*) ;; *) root="" ;; esac
  [ -n "$root" ] || { [ -n "${top:-}" ] && [ "$top" != "$HOME" ] && root=$top; }
  if [ -n "$root" ]; then where=projects; project=${root##*/}; rel=${f#"$root"/}
  else
    where=inbox; project="-"
    case "$f" in "$HOME"/*) rel=${f#"$HOME"/} ;; *) rel=${f#/} ;; esac
  fi
  case "/$rel" in */.*) printf 'skip\thidden\n'; return ;; esac
  printf '%s\t%s\t%s\n' "$where" "$project" "$rel"
}

sync_() {   # $1 = dry | send
  mode=$1
  [ "$mode" = dry ] || [ -n "$TOKEN" ] || die "This computer isn't linked yet. Run: sync.sh link <server>"
  [ "$mode" = dry ] || [ "$(conf mode)" != connect ] || die "This computer is connected without syncing. To sync, connect again: sync.sh link"
  MAX=$(conf max); home=$(conf home); [ -n "$home" ] || home="your folder"
  marker=$STATE/last-run; next=$STATE/next-run
  : > "$next"
  list=$STATE/candidates.tsv; plan=$STATE/plan.tsv; sent=$STATE/sent.tsv
  touch "$sent"
  candidates "$marker" > "$list"
  : > "$plan"
  n=$(wc -l < "$list" | tr -d ' ')
  [ "$n" -eq 0 ] || say "Checking $n file(s) Claude made…"
  while IFS="$(printf '\t')" read -r cwd f; do
    p=$(place "$cwd" "$f")
    case "$p" in skip*) printf '%s\t%s\n' "$p" "$f" >> "$plan"; continue ;; esac
    where=$(printf '%s' "$p" | cut -f1); project=$(printf '%s' "$p" | cut -f2); rel=$(printf '%s' "$p" | cut -f3-)
    dest="$where/$project/$rel"
    if [ -s "$SKIPF" ]; then
      fo=$( [ "$where" = projects ] && echo "projects/$project" || echo inbox )
      if grep -qxF "$fo" "$SKIPF"; then printf 'skip\tnot chosen\t%s\n' "$f" >> "$plan"; continue; fi
    fi
    s=$(sha "$f" 2>/dev/null)
    [ -n "$s" ] || { printf 'skip\tunreadable\t%s\n' "$f" >> "$plan"; continue; }
    if grep -qxF "$(printf '%s\t%s' "$dest" "$s")" "$sent"; then printf 'skip\tunchanged\t%s\n' "$f" >> "$plan"; continue; fi
    printf 'send\t%s\t%s\t%s\t%s\t%s\n' "$where" "$project" "$rel" "$s" "$f" >> "$plan"
  done < "$list"

  nsend=$(grep -c '^send' "$plan" || true)
  if [ "$mode" = dry ]; then
    if [ "$nsend" -eq 0 ]; then say "Nothing new to copy."; else
      say "Would copy $nsend file(s):"
      grep '^send' "$plan" | while IFS="$(printf '\t')" read -r _ where project rel _ f; do
        printf '%s\t%s\n' "$( [ "$where" = projects ] && echo "projects/$project" || echo inbox )" "$(wc -c < "$f" | tr -d ' ')"
      done | awk -F'\t' '{ n[$1]++; b[$1]+=$2 } END { for (k in n) printf "%s\t%d\t%d\n", k, n[k], b[k] }' | sort |
        while IFS="$(printf '\t')" read -r folder cnt bytes; do say "  $home/$folder  ($cnt, $(human "$bytes"))"; done
    fi
    grep '^skip' "$plan" | cut -f2 | sort | uniq -c | while read -r c why; do
      [ "$why" = unchanged ] || [ "$why" = gone ] || say "Leaving out $c: $why"; done
    return 0
  fi

  ok=0; failed=0; skipped=0
  printf 'authorization: Bearer %s\n' "$TOKEN" > "$STATE/auth"   # read by curl, so the token isn't on its command line
  while IFS="$(printf '\t')" read -r kind where project rel s f; do
    [ "$kind" = send ] || continue
    code=$(curl_ -sS -o "$STATE/resp" -w '%{http_code}' -X PUT --data-binary @"$f" \
      -H @"$STATE/auth" -H "x-sync-where: $where" -H "x-sync-project: $(b64 "${project#-}")" \
      -H "x-sync-path: $(b64 "$rel")" -H 'content-type: application/octet-stream' "$SERVER/api/sync/file" 2>/dev/null) || code=000
    case "$code" in
      200) printf '%s\t%s\n' "$where/$project/$rel" "$s" >> "$sent"; ok=$((ok + 1)); say "Copied $rel" ;;
      401) die "This computer's link was turned off. Run: sync.sh link" ;;
      000) failed=$((failed + 1)); say "Couldn't reach the server for $rel" ;;
      400|403|404|409|413|415|422)   # the server will refuse this file as it is: don't retry until it changes
        printf '%s\t%s\n' "$where/$project/$rel" "$s" >> "$sent"; skipped=$((skipped + 1))
        say "Not copied: $rel ($(sed -n 's/.*"message":"\([^"]*\)".*/\1/p' "$STATE/resp"))" ;;
      *) failed=$((failed + 1)); say "Not copied yet: $rel (the server said $code)" ;;
    esac
  done < "$plan"
  rm -f "$STATE/auth"
  [ "$failed" -eq 0 ] && mv "$next" "$marker"   # next time, only history written after this run began
  if [ "$failed" -gt 0 ]; then say "Done: $ok copied, $failed not copied yet."
  elif [ "$skipped" -gt 0 ]; then say "Done: $ok copied, $skipped refused by the server."; else say "Done: $ok copied."; fi
  [ "$failed" -eq 0 ] || say "Files that weren't copied are tried again next time."
}

lock() {
  if ! mkdir "$DIR/lock" 2>/dev/null; then
    # A run that died more than 30 minutes ago leaves its lock behind.
    if [ -n "$(find "$DIR/lock" -maxdepth 0 -mmin +30 2>/dev/null)" ]; then rmdir "$DIR/lock" 2>/dev/null; mkdir "$DIR/lock" 2>/dev/null || exit 0
    else [ -n "$QUIET" ] && exit 0; die "Another sync is running."; fi
  fi
  trap 'rmdir "$DIR/lock" 2>/dev/null' EXIT
  trap 'exit 130' INT TERM
}

case "$CMD" in
  link) lock; link "${2:-}" ;;
  exclude)
    if [ -n "${2:-}" ]; then
      x=$(cd "$2" 2>/dev/null && pwd -P) || die "No such folder: $2"
      [ "$x" != / ] || die "Excluding everything would stop sync altogether. Use: sync.sh off"
      grep -qxF "$x" "$DIR/exclude" 2>/dev/null || echo "$x" >> "$DIR/exclude"; say "Won't copy anything under $x."
    elif [ -s "$DIR/exclude" ]; then say "Never copied:"; while IFS= read -r x; do say "  $x"; done < "$DIR/exclude"
    else say "No folders excluded. Add one with: sync.sh exclude <folder>"; fi ;;
  scan) lock; sync_ dry ;;
  run) lock; sync_ send ;;
  hook)
    [ -n "$TOKEN" ] && [ "$(conf enabled)" != off ] && [ "$(conf mode)" != connect ] || exit 0
    if command -v setsid >/dev/null 2>&1; then setsid sh "$0" hook-run >/dev/null 2>&1 </dev/null &
    else nohup sh "$0" hook-run >/dev/null 2>&1 </dev/null & fi
    exit 0 ;;
  hook-run) lock; sync_ send >/dev/null 2>&1; exit 0 ;;
  off|on) [ -f "$CONF" ] || die "Not linked."
          [ "$CMD" = off ] || [ "$(conf mode)" != connect ] || die "This computer is connected without syncing. To sync, connect again: sync.sh link"
          sed -i.bak '/^enabled=/d' "$CONF" && rm -f "$CONF.bak"; echo "enabled=$CMD" >> "$CONF"
          say "Automatic sync is $CMD." ;;
  status)
    if [ "$(conf mode)" = connect ]; then say "Connected to $SERVER without syncing."
    elif [ -z "$TOKEN" ]; then say "Not linked."; else
      say "Linked to $SERVER as $(conf home) ($(conf computer)). Automatic sync: $(conf enabled)."
      [ -f "$STATE/last-run" ] && say "Last complete run: $(date -r "$STATE/last-run" 2>/dev/null || ls -l "$STATE/last-run")"
      say "Files copied so far: $(wc -l < "$STATE/sent.tsv" 2>/dev/null | tr -d ' ' || echo 0)"
    fi ;;
  unlink)
    m=$(conf mode); what="sync"; [ "$m" = connect ] && what="connection"
    if [ -n "$TOKEN" ] && [ -n "$SERVER" ]; then
      printf 'authorization: Bearer %s\n' "$TOKEN" > "$STATE/auth"
      if resp=$(curl_ -fsS -X POST -H @"$STATE/auth" "$SERVER/api/sync/unlink" 2>/dev/null); then ended=1; else ended=; fi
      rm -f "$STATE/auth"
    fi
    rm -f "$CONF" "$SKIPF"; rm -rf "$STATE"
    if [ -n "${ended:-}" ]; then
      case "$resp" in *'"signed_out":true'*) say "Disconnected: this computer's $what and its Claude Code sign-in are turned off on the server." ;;
        *) say "Disconnected: this computer's $what is turned off on the server." ;; esac
    else say "Forgot the link here. (If the server couldn't be reached, turn this computer off on the Connect page.)"; fi ;;
  *) die "Unknown command: $CMD (link, scan, run, exclude, off, on, status, unlink)" ;;
esac
