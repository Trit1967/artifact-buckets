#!/bin/sh
# Artifact Buckets: one-line install for a Linux host.
#
#   curl -fsSL https://raw.githubusercontent.com/Trit1967/artifact-buckets/main/install.sh | sudo sh
#
# It asks two questions (the address people will use, and an email for the admin and for Let's Encrypt),
# installs Docker if it is missing, and starts the app with HTTPS. Without a terminal, or to skip the
# questions, pass the answers:
#
#   curl -fsSL .../install.sh | sudo sh -s -- --host files.example.com --email you@example.com --yes
#
# Everything lives in one folder (/opt/artifact-buckets) and its containers. Afterwards:
#
#   sudo artifact-buckets status | logs | password | restart | upgrade | uninstall [--purge]
#
# Options (install):
#   --host NAME         the name or IP address people reach the server by
#   --email ADDRESS     the admin's email; also used for Let's Encrypt
#   --tls MODE          auto (default), letsencrypt, self-signed, or files (with --cert and --key)
#   --cert FILE --key FILE   your own certificate and key (PEM), for --tls files
#   --https-port N --http-port N   defaults 443 and 80
#   --zfs POOL[/DATASET]     keep files on ZFS with automatic restore points (asked when ZFS is present)
#   --no-zfs            plain folders even when ZFS is present
#   --image REF         use this app image instead of the published one
#   --build             build the app image from source instead of pulling it
#   --source DIR|URL    source folder or .tar.gz to install from (default: this repository on GitHub)
#   --ca-file FILE      extra CA certificate for builds behind a TLS-inspecting proxy
#   --dir DIR           install folder (default /opt/artifact-buckets)
#   --yes               don't ask; take the defaults
#
# Licensed under the Apache License, Version 2.0.
set -eu
umask 022

# ---- Names: change these to rename the project ---------------------------------------------------------
APP_NAME="Artifact Buckets"
APP_SLUG="artifact-buckets"
REPO="Trit1967/artifact-buckets"
IMAGE_REPO="ghcr.io/trit1967/artifact-buckets"
# ---------------------------------------------------------------------------------------------------------

DIR="${AB_DIR:-/opt/$APP_SLUG}"
# Run as the installed command (/usr/local/bin/artifact-buckets -> <folder>/artifact-buckets): use that folder.
# Only for that installed copy: piped in (curl | sh), $0 is just "sh".
case $0 in
  */*) _self=$(readlink -f "$0" 2>/dev/null || true)
       _sd=$(dirname "${_self:-/}")
       if [ -z "${AB_DIR:-}" ] && [ "$(basename "${_self:-x}")" = "$APP_SLUG" ] && [ -f "$_sd/.$APP_SLUG-install" ]; then DIR=$_sd; fi ;;
esac
REF="${AB_REF:-main}"
CLI="/usr/local/bin/$APP_SLUG"
ZFS_UNIT="$APP_SLUG-zfs.service"
ZFS_GROUP="$APP_SLUG-zfs"

HOST="" EMAIL="" TLS="auto" CERT="" KEY="" HTTPS_PORT="" HTTP_PORT="" ZFS="" NO_ZFS="" IMAGE="" BUILD=""
SOURCE="${AB_SOURCE:-}" CA_FILE="" YES="" PURGE="" HOST_GIVEN="" OLD_IMAGE_ID=""

if [ -t 1 ]; then B=$(printf '\033[1m'); D=$(printf '\033[2m'); R=$(printf '\033[0m'); else B="" D="" R=""; fi
say()  { printf '%s\n' "$*"; }
step() { printf '%s==>%s %s\n' "$B" "$R" "$*"; }
warn() { printf '%sWarning:%s %s\n' "$B" "$R" "$*" >&2; }
die()  { printf '%sError:%s %s\n' "$B" "$R" "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# Questions go to the terminal even when the script itself arrives on stdin (curl | sh).
can_ask() { [ -z "$YES" ] && ( : </dev/tty ) 2>/dev/null; }
ask() { # ask VAR "Question" default
  _d=$3
  if can_ask; then
    if [ -n "$_d" ]; then printf '%s [%s]: ' "$2" "$_d" >/dev/tty; else printf '%s: ' "$2" >/dev/tty; fi
    IFS= read -r _a </dev/tty || _a=""
    [ -n "$_a" ] || _a=$_d
  else
    _a=$_d
  fi
  eval "$1=\$_a"
}
yesno() { # yesno "Question" Y|N  -> status 0 for yes
  if can_ask; then
    if [ "$2" = Y ]; then printf '%s [Y/n]: ' "$1" >/dev/tty; else printf '%s [y/N]: ' "$1" >/dev/tty; fi
    IFS= read -r _a </dev/tty || _a=""
    [ -n "$_a" ] || _a=$2
  else
    _a=$2
  fi
  case $_a in [Yy]*) return 0 ;; *) return 1 ;; esac
}

rand_hex() { # rand_hex BYTES
  if have openssl; then openssl rand -hex "$1"; else od -An -N"$1" -tx1 /dev/urandom | tr -d ' \n'; fi
}
rand_pass() { # 20 letters and digits
  if have openssl; then
    _p=$(openssl rand -base64 30 | tr -dc 'A-Za-z0-9' | cut -c1-20)
  else
    _p=$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 20)
  fi
  printf '%s' "$_p"
}

env_get() { [ -f "$DIR/.env" ] && sed -n "s/^$1=//p" "$DIR/.env" | tail -n 1 || true; }
env_set() { # env_set KEY VALUE (replaces any earlier line)
  _t="$DIR/.env.tmp"
  { [ -f "$DIR/.env" ] && grep -v "^$1=" "$DIR/.env" || true; printf '%s=%s\n' "$1" "$2"; } >"$_t"
  chmod 600 "$_t"; mv "$_t" "$DIR/.env"
}

# One compose project (and local image) per install folder, so two installs never share containers.
project() {
  if [ "$DIR" = "/opt/$APP_SLUG" ]; then printf '%s' "$APP_SLUG"; return; fi
  _h=$(printf '%s' "$DIR" | cksum | cut -d' ' -f1)
  printf '%s-%s-%s' "$APP_SLUG" "$(basename "$DIR" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9_-' '-' | sed 's/-*$//' | cut -c1-20)" "$_h"
}
compose() { (cd "$DIR" && docker compose -p "$(project)" "$@"); }

is_ip() { printf '%s' "$1" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; }
is_private_ip() {
  case $1 in
    10.*|127.*|192.168.*|169.254.*|0.*) return 0 ;;
    172.1[6-9].*|172.2[0-9].*|172.3[01].*) return 0 ;;
    100.6[4-9].*|100.[7-9][0-9].*|100.1[01][0-9].*|100.12[0-7].*) return 0 ;;
  esac
  return 1
}
resolve() { getent ahostsv4 "$1" 2>/dev/null | awk 'NR==1{print $1}'; }
primary_ip() {
  _ip=$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -n 1)
  [ -n "$_ip" ] || _ip=$(hostname -I 2>/dev/null | awk '{print $1}')
  printf '%s' "${_ip:-127.0.0.1}"
}
port_busy() {
  if have ss; then ss -ltnH "sport = :$1" 2>/dev/null | grep -q . && return 0; return 1; fi
  if have netstat; then netstat -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "[:.]$1\$" && return 0; return 1; fi
  # Neither tool: read the kernel's socket tables (state 0A = listening).
  _hex=$(printf '%04X' "$1")
  cat /proc/net/tcp /proc/net/tcp6 2>/dev/null | awk -v p=":$_hex" '$4 == "0A" && substr($2, length($2) - 4) == p { f = 1 } END { exit !f }'
}
free_port() { # free_port START: the first port from START that nothing listens on
  _p=$1; while port_busy "$_p"; do _p=$((_p + 1)); done; printf '%s' "$_p"
}
valid_port() { case $1 in ''|*[!0-9]*) return 1 ;; esac; [ "$1" -ge 1 ] && [ "$1" -le 65535 ]; }
fetch() { # fetch URL FILE
  if have curl; then curl -fsSL "$1" -o "$2"; elif have wget; then wget -qO "$2" "$1"; else die "Needs curl or wget"; fi
}
pkg_install() {
  if have apt-get; then DEBIAN_FRONTEND=noninteractive apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@" >/dev/null
  elif have dnf; then dnf install -y -q "$@"
  elif have yum; then yum install -y -q "$@"
  elif have zypper; then zypper -q install -y "$@"
  elif have apk; then apk add -q "$@"
  else return 1; fi
}

# ---- Steps ----------------------------------------------------------------------------------------------

need_root() {
  [ "$(id -u)" = 0 ] || die "Run as root, for example: curl -fsSL <url> | sudo sh"
  [ "$(uname -s)" = Linux ] || die "This installer is for Linux hosts."
}

ensure_tools() {
  _missing=""
  for t in tar openssl; do have "$t" || _missing="$_missing $t"; done
  have curl || _missing="$_missing curl"
  if [ -n "$_missing" ]; then
    step "Installing$_missing"
    pkg_install $_missing || die "Please install$_missing and run again."
  fi
}

ensure_docker() {
  if ! have docker; then
    say "Docker isn't installed. It's needed to run $APP_NAME (from get.docker.com, the official script)."
    yesno "Install Docker now?" Y || die "Install Docker (https://docs.docker.com/engine/install/) and run again."
    step "Installing Docker"
    _gd=$(mktemp); _log=$(mktemp)
    fetch https://get.docker.com "$_gd" || die "Couldn't download get.docker.com."
    if ! sh "$_gd" >"$_log" 2>&1; then tail -n 20 "$_log" >&2; rm -f "$_gd"; die "Installing Docker failed (log above, full log in $_log)."; fi
    rm -f "$_gd" "$_log"
  fi
  if ! docker info >/dev/null 2>&1; then
    if have systemctl; then systemctl enable --now docker >/dev/null 2>&1 || true; fi
    i=0; while ! docker info >/dev/null 2>&1; do i=$((i + 1)); [ $i -lt 20 ] || die "Docker is installed but not running. Start it (systemctl start docker) and run again."; sleep 1; done
  fi
  if ! docker compose version >/dev/null 2>&1; then
    step "Installing the Docker Compose plugin"
    pkg_install docker-compose-plugin || die "Install the Docker Compose plugin (docker compose) and run again."
  fi
}

# The source: this checkout when the script runs from one, else a .tar.gz of the repository.
fetch_source() {
  _here=""
  case $0 in */*) [ -f "$0" ] && _here=$(cd "$(dirname "$0")" && pwd) ;; esac
  if [ -z "$SOURCE" ] && [ -n "$_here" ] && [ -f "$_here/deploy/install/compose.yml" ] && [ "$_here" != "$DIR/src" ]; then
    SOURCE=$_here
  fi
  [ -n "$SOURCE" ] || SOURCE=$(env_get AB_SOURCE)
  [ -n "$SOURCE" ] || SOURCE="https://codeload.github.com/$REPO/tar.gz/$REF"
  step "Getting $APP_NAME from $SOURCE"
  _new="$DIR/src.new"
  rm -rf "$_new"; mkdir -p "$_new"
  if [ -d "$SOURCE" ]; then
    (cd "$SOURCE" && tar --exclude=.git --exclude=node_modules --exclude=qa-ui --exclude=./.env --exclude=./secrets \
        --exclude=./data --exclude=server/data --exclude=web/dist -cf - .) | tar -C "$_new" -xf -
  else
    case $SOURCE in
      http://*|https://*) fetch "$SOURCE" "$DIR/src.tar.gz" || { rm -rf "$_new" "$DIR/src.tar.gz"; die "Couldn't download $SOURCE (if the repository is private, run install.sh from a clone instead)."; } ;;
      *) [ -f "$SOURCE" ] || { rm -rf "$_new"; die "No such source: $SOURCE"; }; cp "$SOURCE" "$DIR/src.tar.gz" ;;
    esac
    tar -xzf "$DIR/src.tar.gz" -C "$_new" --strip-components=1 2>/dev/null || { rm -rf "$_new" "$DIR/src.tar.gz"; die "$SOURCE isn't a readable .tar.gz."; }
    rm -f "$DIR/src.tar.gz"
  fi
  [ -f "$_new/deploy/install/compose.yml" ] || { rm -rf "$_new"; die "That source doesn't look like $APP_NAME."; }
  # Upgrades fetch from the same place unless told otherwise (GitHub is the default and isn't recorded).
  case $SOURCE in https://codeload.github.com/*) env_set AB_SOURCE "" ;; *) env_set AB_SOURCE "$SOURCE" ;; esac
  rm -rf "$DIR/src"; mv "$_new" "$DIR/src"
  cp "$DIR/src/deploy/install/compose.yml" "$DIR/compose.yml"
  # Copy then rename: this script may be the one running (sudo artifact-buckets upgrade).
  cp "$DIR/src/install.sh" "$DIR/$APP_SLUG.new"; chmod 755 "$DIR/$APP_SLUG.new"; mv -f "$DIR/$APP_SLUG.new" "$DIR/$APP_SLUG"
  mkdir -p "$(dirname "$CLI")"; ln -sf "$DIR/$APP_SLUG" "$CLI"
}

# A published image when there is one, else build it here from the source.
choose_image() {
  _img=$IMAGE
  if [ -z "$_img" ] && [ -z "$BUILD" ]; then
    case $REF in main|master) _tag=latest ;; v*) _tag=${REF#v} ;; *) _tag=$REF ;; esac
    _img="$IMAGE_REPO:$_tag"
  fi
  if [ -n "$_img" ] && [ -z "$BUILD" ]; then
    step "Pulling $_img"
    if docker pull -q "$_img" >/dev/null 2>&1; then env_set AB_IMAGE "$_img"; return; fi
    [ -z "$IMAGE" ] || die "Couldn't pull $IMAGE"
    say "${D}No published image yet; building it here instead (a few minutes, once).${R}"
  fi
  _img="$(project)/app:local"
  OLD_IMAGE_ID=$(docker images -q "$_img" 2>/dev/null || true)
  step "Building the app image"
  if [ -n "$CA_FILE" ]; then
    DOCKER_BUILDKIT=1 docker build -q --secret id=ca,src="$CA_FILE" -t "$_img" "$DIR/src" >/dev/null
  else
    DOCKER_BUILDKIT=1 docker build -q -t "$_img" "$DIR/src" >/dev/null
  fi
  env_set AB_IMAGE "$_img"
}

choose_address() {
  _ip=$(primary_ip)
  if [ -z "$HOST" ]; then
    _d=$(env_get AB_HOSTNAME)
    if [ -z "$_d" ]; then _f=$(hostname -f 2>/dev/null || true); case $_f in *.*) _d=$_f ;; *) _d=$_ip ;; esac; fi
    ask HOST "Address people will use to reach it (name or IP)" "$_d"
  fi
  HOST=${HOST#http://}; HOST=${HOST#https://}; HOST=${HOST%%/*}
  [ -n "$HOST" ] || die "An address is needed (--host)."
  case $HOST in
    *:*:*) die "IPv6 addresses aren't supported yet; use a name or an IPv4 address." ;;
    *:*) die "Give the port separately: --host ${HOST%%:*} --https-port ${HOST#*:}" ;;
  esac
  printf '%s' "$HOST" | grep -Eq '^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$' || die "'$HOST' isn't a valid host name or IPv4 address."
  HOST=$(printf '%s' "$HOST" | tr '[:upper:]' '[:lower:]')
  if is_ip "$HOST"; then HOST_IP=$HOST; else HOST_IP=$(resolve "$HOST"); [ -n "$HOST_IP" ] || HOST_IP=$_ip; fi

  if [ -z "$EMAIL" ]; then
    ask EMAIL "Admin email (also for Let's Encrypt notices; Enter to skip)" "$(env_get AB_BOOTSTRAP_EMAIL)"
  fi

  case $EMAIL in
    '') ;;
    *[!A-Za-z0-9._%+@-]*|*@*@*) die "'$EMAIL' doesn't look like an email address." ;;
    *@*.*) ;;
    *) die "'$EMAIL' doesn't look like an email address." ;;
  esac

  # Ports: keep what an earlier install used.
  [ -n "$HTTPS_PORT" ] || HTTPS_PORT=$(env_get AB_HTTPS_PORT)
  [ -n "$HTTP_PORT" ] || HTTP_PORT=$(env_get AB_HTTP_PORT)
  if [ -z "$HTTPS_PORT" ]; then
    HTTPS_PORT=443
    if port_busy 443; then warn "Port 443 is in use on this machine."; ask HTTPS_PORT "HTTPS port to use instead" "$(free_port 8443)"; fi
  fi
  if [ -z "$HTTP_PORT" ]; then
    HTTP_PORT=80
    if port_busy 80; then warn "Port 80 is in use on this machine."; ask HTTP_PORT "HTTP port to use instead" "$(free_port 8080)"; fi
  fi
  valid_port "$HTTPS_PORT" || die "'$HTTPS_PORT' isn't a port number."
  valid_port "$HTTP_PORT" || die "'$HTTP_PORT' isn't a port number."
  [ "$HTTPS_PORT" != "$HTTP_PORT" ] || die "The HTTPS and HTTP ports must differ."

  # Certificate: Let's Encrypt for a public name on 80/443, else a self-signed one made here.
  if [ "$TLS" = auto ]; then
    TLS=$(env_get AB_TLS_MODE); [ -n "$TLS" ] && [ -z "$HOST_GIVEN" ] || TLS=auto
  fi
  if [ "$TLS" = auto ]; then
    TLS=self-signed
    if ! is_ip "$HOST" && case $HOST in *.*) true ;; *) false ;; esac; then
      case $HOST in *.local|*.lan|*.home|*.internal|*.localdomain|*.home.arpa|*.test|*.corp|*.intranet) ;;
        *) _r=$(resolve "$HOST")
           if [ -n "$_r" ] && ! is_private_ip "$_r" && [ "$HTTPS_PORT" = 443 ] && [ "$HTTP_PORT" = 80 ]; then
             if yesno "Get a free certificate from Let's Encrypt for $HOST? (it must be reachable from the internet on ports 80 and 443)" Y; then TLS=letsencrypt; fi
           fi ;;
      esac
    fi
  fi
  case $TLS in
    letsencrypt)
      [ "$HTTPS_PORT" = 443 ] && [ "$HTTP_PORT" = 80 ] || die "Let's Encrypt needs ports 80 and 443. Free them, or use --tls self-signed."
      ! is_ip "$HOST" || die "Let's Encrypt needs a name, not an IP address. Use --tls self-signed." ;;
    files) [ -f "$CERT" ] && [ -f "$KEY" ] || die "--tls files needs --cert FILE and --key FILE." ;;
    self-signed) ;;
    *) die "Unknown --tls $TLS (auto, letsencrypt, self-signed, files)" ;;
  esac

  if [ "$HTTPS_PORT" = 443 ]; then BASE_URL="https://$HOST"; else BASE_URL="https://$HOST:$HTTPS_PORT"; fi
  if [ "$HTTP_PORT" = 80 ]; then PLAIN_URL="http://$HOST"; else PLAIN_URL="http://$HOST:$HTTP_PORT"; fi
}

make_certificate() {
  mkdir -p "$DIR/data/tls/public"
  case $TLS in
    files)
      install -m 600 "$KEY" "$DIR/data/tls/server.key"
      install -m 644 "$CERT" "$DIR/data/tls/public/server.crt" ;;
    self-signed)
      if is_ip "$HOST"; then _san="IP:$HOST"; else _san="DNS:$HOST,IP:$HOST_IP"; fi
      _have=$(openssl x509 -in "$DIR/data/tls/public/server.crt" -noout -ext subjectAltName 2>/dev/null | sed -n 2p | tr -d ' ' || true)
      _want=$(printf '%s' "$_san" | sed 's/IP:/IPAddress:/g')
      if [ "$_have" != "$_want" ] || [ ! -f "$DIR/data/tls/server.key" ]; then
        step "Making a self-signed certificate for $HOST"
        # A server certificate, not a CA: a computer that trusts it trusts this server and nothing else.
        openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 825 \
          -keyout "$DIR/data/tls/server.key" -out "$DIR/data/tls/public/server.crt" \
          -subj "/CN=$APP_NAME ($HOST)" -addext "subjectAltName=$_san" \
          -addext "basicConstraints=critical,CA:FALSE" -addext "keyUsage=critical,digitalSignature" \
          -addext "extendedKeyUsage=serverAuth" >/dev/null 2>&1 || die "openssl couldn't make the certificate."
        chmod 600 "$DIR/data/tls/server.key"
      fi ;;
  esac
}

write_caddyfile() {
  {
    printf '# Made by install.sh; it is rewritten on reinstall. Mode: %s\n{\n\tadmin off\n' "$TLS"
    [ "$TLS" = letsencrypt ] && [ -n "$EMAIL" ] && printf '\temail %s\n' "$EMAIL"
    if [ "$TLS" = self-signed ]; then printf '\tauto_https disable_redirects\n\tdefault_sni %s\n' "$HOST"; fi
    printf '}\n\n(app) {\n\tencode zstd gzip\n\trequest_body {\n\t\tmax_size 700MB\n\t}\n\treverse_proxy app:8080\n\theader -Server\n}\n\n'
    case $TLS in
      letsencrypt)
        printf '%s {\n\timport app\n}\n' "$HOST" ;;
      files)
        printf 'https://%s {\n\ttls /certs/public/server.crt /certs/server.key\n\timport app\n}\n' "$HOST" ;;
      self-signed)
        if is_ip "$HOST"; then _sites="https://$HOST"; else _sites="https://$HOST, https://$HOST_IP"; fi
        printf '%s {\n\ttls /certs/public/server.crt /certs/server.key\n\timport app\n}\n\n' "$_sites"
        # Plain http only hands out the Claude Code setup files and the certificate (a computer needs them
        # before it trusts the certificate); everything else goes to https.
        printf 'http:// {\n\t@setup path /claude/install.ps1 /claude/install.sh /claude/setup.md /claude/ca.crt\n'
        printf '\thandle @setup {\n\t\treverse_proxy app:8080\n\t}\n\thandle {\n\t\tredir %s{uri} 308\n\t}\n\theader -Server\n}\n' "$BASE_URL" ;;
    esac
  } >"$DIR/Caddyfile"
}

new_secret() { # new_secret NAME BYTES: a random hex value (BYTES 0: a 20-character login password), unless set
  [ -z "$(env_get "$1")" ] || return 0
  if [ "$2" = 0 ]; then env_set "$1" "$(rand_pass)"; else env_set "$1" "$(rand_hex "$2")"; fi
}

write_env() {
  # Secrets are generated here on first install and kept on later runs.
  new_secret AB_SECRET_KEY 32
  new_secret POSTGRES_PASSWORD 24
  new_secret AB_BOOTSTRAP_PASSWORD 0
  [ -n "$(env_get AB_BOOTSTRAP_ADMIN)" ] || env_set AB_BOOTSTRAP_ADMIN admin
  env_set AB_BOOTSTRAP_EMAIL "$EMAIL"
  env_set AB_HOSTNAME "$HOST"
  env_set AB_BASE_URL "$BASE_URL"
  env_set AB_TLS_MODE "$TLS"
  env_set AB_HTTPS_PORT "$HTTPS_PORT"
  env_set AB_HTTP_PORT "$HTTP_PORT"
  env_set AB_SMB_ENABLED false
  env_set AB_SMB_HOST "$HOST"
  [ -z "$CA_FILE" ] || env_set AB_BUILD_CA "$CA_FILE"
  if [ "$TLS" = self-signed ]; then
    env_set AB_INSTALL_URL "$PLAIN_URL"
    env_set AB_INSTALL_CA_FILE /certs/server.crt
  else
    env_set AB_INSTALL_URL ""
    env_set AB_INSTALL_CA_FILE ""
  fi
}

# ZFS: files on their own dataset, with restore points taken by a small root helper (bucketsd) that the
# app reaches over a socket. The app itself never gets ZFS rights.
choose_storage() {
  [ -z "$NO_ZFS" ] || { ZFS=""; return; }
  [ -n "$ZFS" ] || ZFS=$(env_get AB_ZFS_DATASET)
  if [ -z "$ZFS" ] && have zpool && have zfs; then
    _pools=$(zpool list -H -o name 2>/dev/null || true)
    if [ -n "$_pools" ] && can_ask; then
      _first=$(printf '%s\n' "$_pools" | head -n 1)
      say "ZFS is available (pools: $(printf '%s' "$_pools" | tr '\n' ' '))."
      if yesno "Keep files on ZFS, with automatic restore points?" Y; then ask ZFS "Pool to use" "$_first"; fi
    fi
  fi
  [ -n "$ZFS" ] || return 0
  case $ZFS in */*) ;; *) ZFS="$ZFS/$APP_SLUG" ;; esac
  have zfs || die "--zfs was given but the zfs tools aren't installed."
  have python3 || die "ZFS mode needs python3 on the host for its helper."
  have systemctl || die "ZFS mode needs systemd for its helper."
  zpool list -H -o name "${ZFS%%/*}" >/dev/null 2>&1 || die "No ZFS pool named ${ZFS%%/*}."
}

setup_zfs() {
  [ -n "$ZFS" ] || { mkdir -p "$DIR/data/tree"; return 0; }
  _pool=${ZFS%%/*}
  if ! zfs list -H -o name "$ZFS" >/dev/null 2>&1; then
    if [ -d "$DIR/data/tree" ] && [ -n "$(ls -A "$DIR/data/tree" 2>/dev/null)" ]; then
      die "$DIR/data/tree already has files; move them aside before switching to ZFS."
    fi
    step "Creating ZFS dataset $ZFS"
    zfs create -p -o mountpoint="$DIR/data/tree" -o compression=lz4 -o atime=off -o xattr=sa \
      -o acltype=posixacl -o snapdir=hidden "$ZFS"
    env_set AB_ZFS_CREATED 1
  elif [ "$(zfs get -H -o value mountpoint "$ZFS")" != "$DIR/data/tree" ]; then
    die "ZFS dataset $ZFS exists but isn't mounted at $DIR/data/tree. Run: zfs set mountpoint=$DIR/data/tree $ZFS (or give --zfs a new dataset name)."
  fi
  [ "$(zfs get -H -o value mounted "$ZFS")" = yes ] || zfs mount "$ZFS"
  groupadd -f -r "$ZFS_GROUP"
  _gid=$(getent group "$ZFS_GROUP" | cut -d: -f3)
  mkdir -p "$DIR/bin" "$DIR/data/zfs" "$DIR/data/run"
  install -m 0755 "$DIR/src/deploy/zfs/bucketsd.py" "$DIR/bin/bucketsd"
  cat >"/etc/systemd/system/$ZFS_UNIT" <<UNIT
[Unit]
Description=$APP_NAME ZFS helper (restore points, health)
After=zfs-mount.service
[Service]
ExecStart=/usr/bin/env python3 $DIR/bin/bucketsd --pool $_pool --dataset $ZFS --socket $DIR/data/run/zfs.sock --group $_gid --state $DIR/data/zfs/bucketsd.json --files-dir $DIR/data/zfs/vdevs
Restart=on-failure
[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
  systemctl enable --now "$ZFS_UNIT" >/dev/null 2>&1
  systemctl restart "$ZFS_UNIT"
  env_set AB_ZFS_DATASET "$ZFS"
  env_set AB_ZFS_GID "$_gid"
  env_set AB_ZFS_SOCKET /run/buckets/zfs.sock
}

start_app() {
  mkdir -p "$DIR/data/tree" "$DIR/data/volumes" "$DIR/data/db" "$DIR/data/caddy" "$DIR/data/tls/public" "$DIR/data/run"
  chmod 700 "$DIR/data/db" "$DIR/data/tls" 2>/dev/null || true
  step "Starting $APP_NAME"
  compose up -d --remove-orphans --quiet-pull >/dev/null 2>&1 || compose up -d --remove-orphans
  # Recreate the app (a rebuilt image keeps its tag, so compose may not notice) and Caddy (it reads the
  # Caddyfile only at start). The database keeps running.
  compose up -d --force-recreate --no-deps app caddy >/dev/null 2>&1 || compose up -d --force-recreate --no-deps app caddy
  # Drop the image a rebuild replaced (only when nothing uses it any more).
  if [ -n "${OLD_IMAGE_ID:-}" ] && [ "$OLD_IMAGE_ID" != "$(docker images -q "$(env_get AB_IMAGE)" 2>/dev/null)" ]; then
    docker rmi "$OLD_IMAGE_ID" >/dev/null 2>&1 || true
  fi
}

wait_ready() {
  _i=0
  printf 'Waiting for it to answer'
  until compose exec -T app python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/healthz')" >/dev/null 2>&1; do
    _i=$((_i + 1))
    if [ $_i -gt 90 ]; then
      printf '\n'; compose logs --tail 40 app >&2 || true
      die "The app didn't come up. The log is above; 'sudo $APP_SLUG logs' shows more."
    fi
    printf '.'; sleep 2
  done
  printf ' ok\n'
  HTTPS_OK=""
  _j=0
  while [ $_j -lt 15 ]; do
    if curl -fsk --noproxy "*" -o /dev/null --resolve "$HOST:$HTTPS_PORT:127.0.0.1" "$BASE_URL/healthz" 2>/dev/null; then HTTPS_OK=1; break; fi
    _j=$((_j + 1)); sleep 2
  done
}

summary() {
  _pw=$(env_get AB_BOOTSTRAP_PASSWORD)
  _ib=$BASE_URL; [ "$TLS" != self-signed ] || _ib=$PLAIN_URL
  say ""
  say "${B}$APP_NAME is running.${R}"
  say ""
  say "  Open        $BASE_URL"
  say "  Sign in     admin / $_pw"
  say "              (also in $DIR/.env; change it after you sign in)"
  say ""
  say "  Connect Claude Code on a computer:"
  say "    Windows (PowerShell):  irm $_ib/claude/install.ps1 | iex"
  say "    macOS / Linux:         curl -fsSL $_ib/claude/install.sh | sh"
  if [ "$TLS" = self-signed ]; then
    say ""
    say "  ${D}The certificate is self-signed, so browsers warn the first time. The Claude Code setup above"
    say "  offers to trust it on that computer (for this server only).${R}"
  fi
  [ -n "$HTTPS_OK" ] || { say ""; warn "HTTPS didn't answer yet on $BASE_URL. With Let's Encrypt the first certificate can take a minute; 'sudo $APP_SLUG logs caddy' shows why."; }
  [ -z "$ZFS" ] || say "  Files are on ZFS ($ZFS) with automatic restore points."
  say ""
  say "  Manage:  sudo $APP_SLUG status | logs | password | restart | upgrade | uninstall"
  say "  Folder:  $DIR"
}

# ---- Commands -------------------------------------------------------------------------------------------

cmd_install() {
  need_root
  say "${B}$APP_NAME installer${R}  ${D}($DIR)${R}"
  ensure_tools
  if [ -d "$DIR" ] && [ ! -f "$DIR/.$APP_SLUG-install" ] && [ -n "$(ls -A "$DIR" 2>/dev/null)" ]; then
    die "$DIR already has files and isn't a $APP_NAME install. Choose an empty or new folder with --dir."
  fi
  choose_address
  choose_storage
  ensure_docker
  mkdir -p "$DIR"; chmod 755 "$DIR"
  printf 'This folder is a %s install. Remove it with: sudo %s uninstall\n' "$APP_NAME" "$APP_SLUG" >"$DIR/.$APP_SLUG-install"
  fetch_source
  write_env
  make_certificate
  write_caddyfile
  setup_zfs
  choose_image
  start_app
  wait_ready
  summary
}

cmd_upgrade() {
  need_root
  [ -f "$DIR/.$APP_SLUG-install" ] && [ -f "$DIR/.env" ] || die "$APP_NAME isn't installed in $DIR."
  ensure_docker
  fetch_source
  _cur=$(env_get AB_IMAGE)
  case $_cur in *:local) BUILD=1 ;; esac
  [ -n "$CA_FILE" ] || { CA_FILE=$(env_get AB_BUILD_CA); [ -f "$CA_FILE" ] || CA_FILE=""; }
  choose_image
  compose pull -q caddy db >/dev/null 2>&1 || true
  start_app
  HOST=$(env_get AB_HOSTNAME); BASE_URL=$(env_get AB_BASE_URL); HTTPS_PORT=$(env_get AB_HTTPS_PORT)
  wait_ready
  say "Upgraded. $BASE_URL"
}

cmd_uninstall() {
  need_root
  [ -f "$DIR/.$APP_SLUG-install" ] || die "$APP_NAME isn't installed in $DIR (no install marker there), so nothing was removed."
  ZFS=$(env_get AB_ZFS_DATASET)
  if [ -n "$PURGE" ]; then
    say "This removes $APP_NAME and ${B}deletes every file and the database${R} in $DIR${ZFS:+ (and ZFS dataset $ZFS)}."
    if [ -z "$YES" ]; then
      can_ask || die "Add --yes to confirm deleting everything."
      _c=""; ask _c "Type delete to confirm" ""
      [ "$_c" = delete ] || die "Nothing was removed."
    fi
  else
    say "This removes $APP_NAME's containers and program files. Your files, database and settings stay in $DIR/data and $DIR/.env (run the installer again to pick them up)."
    yesno "Continue?" Y || die "Nothing was removed."
  fi
  _img=$(env_get AB_IMAGE)
  if [ -f "$DIR/compose.yml" ] && have docker; then compose down --remove-orphans >/dev/null 2>&1 || true; fi
  case $_img in *:local) docker rmi "$_img" >/dev/null 2>&1 || true ;; esac
  if [ -f "/etc/systemd/system/$ZFS_UNIT" ]; then
    systemctl disable --now "$ZFS_UNIT" >/dev/null 2>&1 || true
    rm -f "/etc/systemd/system/$ZFS_UNIT"; systemctl daemon-reload || true
  fi
  getent group "$ZFS_GROUP" >/dev/null 2>&1 && groupdel "$ZFS_GROUP" 2>/dev/null || true
  [ "$(readlink "$CLI" 2>/dev/null)" = "$DIR/$APP_SLUG" ] && rm -f "$CLI"
  if [ -n "$PURGE" ]; then
    # Only a dataset this installer created; one you brought stays.
    if [ -n "$ZFS" ] && [ "$(env_get AB_ZFS_CREATED)" = 1 ] && have zfs && zfs list -H -o name "$ZFS" >/dev/null 2>&1; then zfs destroy -r "$ZFS"; fi
    rm -rf "$DIR"
    say "Removed $APP_NAME and its data."
  else
    rm -rf "${DIR:?}/src" "${DIR:?}/src.new" "${DIR:?}/bin" "$DIR/compose.yml" "$DIR/Caddyfile" "${DIR:?}/${APP_SLUG:?}"
    # The marker stays with the data so the folder can still be purged later.
    say "Removed $APP_NAME. Your data is still in $DIR (delete it with: sudo rm -rf $DIR${ZFS:+, and zfs destroy -r $ZFS})."
  fi
  say "${D}Docker itself was left installed.${R}"
}

cmd_status() {
  [ -f "$DIR/compose.yml" ] || die "$APP_NAME isn't installed in $DIR."
  say "$(env_get AB_BASE_URL)  ($(env_get AB_IMAGE))"
  compose ps
  [ -z "$(env_get AB_ZFS_DATASET)" ] || systemctl --no-pager --lines=0 status "$ZFS_UNIT" 2>/dev/null | sed -n 1,3p || true
}

usage() {
  cat <<USAGE
$APP_NAME installer.  curl -fsSL https://raw.githubusercontent.com/$REPO/main/install.sh | sudo sh -s -- [options]

  install (default)   --host NAME  --email ADDR  --tls auto|letsencrypt|self-signed|files  --cert F --key F
                      --https-port N  --http-port N  --zfs POOL[/DATASET]  --no-zfs  --image REF  --build
                      --source DIR|URL  --ref REF  --ca-file F  --dir DIR  --yes
  status | logs [service] | password | restart
  upgrade             [--source DIR|URL] [--build] [--image REF]
  uninstall           [--purge] [--yes]   (keeps files and database unless --purge)

Details: https://github.com/$REPO#install
USAGE
}

need() {
  if [ $# -lt 2 ]; then die "$1 needs a value."; fi
  case $2 in --*) die "$1 needs a value." ;; esac
}

main() {
  _cmd=install
  case ${1:-} in install|upgrade|uninstall|status|logs|password|restart|help|-h|--help) _cmd=$1; shift ;; esac
  if [ "$_cmd" = logs ]; then [ -f "$DIR/compose.yml" ] || die "Not installed."; compose logs --tail 200 -f "$@"; return; fi
  while [ $# -gt 0 ]; do
    case $1 in
      --host) need "$@"; HOST=$2; HOST_GIVEN=1; shift 2 ;;
      --email) need "$@"; EMAIL=$2; shift 2 ;;
      --tls) need "$@"; TLS=$2; HOST_GIVEN=1; shift 2 ;;
      --cert) need "$@"; CERT=$2; shift 2 ;;
      --key) need "$@"; KEY=$2; shift 2 ;;
      --https-port) need "$@"; HTTPS_PORT=$2; shift 2 ;;
      --http-port) need "$@"; HTTP_PORT=$2; shift 2 ;;
      --zfs) need "$@"; ZFS=$2; shift 2 ;;
      --no-zfs) NO_ZFS=1; shift ;;
      --image) need "$@"; IMAGE=$2; shift 2 ;;
      --build) BUILD=1; shift ;;
      --source) need "$@"; SOURCE=$2; shift 2 ;;
      --ref) need "$@"; REF=$2; shift 2 ;;
      --ca-file) need "$@"; CA_FILE=$2; shift 2 ;;
      --dir) need "$@"; DIR=$2; shift 2 ;;
      --yes|-y) YES=1; shift ;;
      --purge) PURGE=1; shift ;;
      *) die "Unknown option $1 (try --help)" ;;
    esac
  done
  DIR=$(printf '%s' "$DIR" | tr -s /); DIR=${DIR%/}
  case $DIR in /?*) ;; '') die "--dir can't be /." ;; *) die "--dir must be an absolute folder, not '$DIR'." ;; esac
  case $DIR in
    /bin|/boot|/dev|/etc|/home|/lib|/lib64|/media|/mnt|/opt|/proc|/root|/run|/sbin|/srv|/sys|/tmp|/usr|/usr/*|/var|/var/lib)
      die "--dir $DIR is a system folder; use a folder of its own, such as /opt/$APP_SLUG." ;;
  esac
  case $_cmd in
    install) cmd_install ;;
    upgrade) cmd_upgrade ;;
    uninstall) cmd_uninstall ;;
    status) cmd_status ;;
    restart) need_root; compose restart; say "Restarted." ;;
    password) need_root; [ -f "$DIR/.$APP_SLUG-install" ] || die "$APP_NAME isn't installed in $DIR."; say "admin / $(env_get AB_BOOTSTRAP_PASSWORD)  ${D}(the first password; if it was changed in the app, this one no longer works)${R}" ;;
    help|-h|--help) usage ;;
  esac
}

main "$@"
