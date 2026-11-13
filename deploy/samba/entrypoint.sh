#!/bin/sh
# Render smb.conf, join the domain once (credentials from Docker secrets), then run winbind and smbd.
set -eu
: "${AD_REALM:?set AB_AD_REALM}" "${AD_WORKGROUP:?set AB_AD_WORKGROUP}"
SMB_NETBIOS="${SMB_NETBIOS:-BUCKETS}"
AB_IDMAP_BASE="${AB_IDMAP_BASE:-100000}"
IDMAP_TOP=$((AB_IDMAP_BASE + 9999999))
export AD_REALM AD_WORKGROUP SMB_NETBIOS AB_IDMAP_BASE IDMAP_TOP

python3 - <<'PY'
import os, string
t = open("/etc/samba/smb.conf.template").read()
open("/etc/samba/smb.conf", "w").write(string.Template(t).safe_substitute(os.environ))
PY

cat > /etc/krb5.conf <<KRB
[libdefaults]
  default_realm = ${AD_REALM}
  dns_lookup_realm = false
  dns_lookup_kdc = true
  rdns = false
KRB

sed -i 's/^passwd:.*/passwd: files winbind/; s/^group:.*/group: files winbind/' /etc/nsswitch.conf

if ! net ads testjoin >/dev/null 2>&1; then
  user=$(cat /run/secrets/ad_join_user)
  echo "Joining ${AD_REALM} as ${user}..."
  # The password goes over stdin, never on the command line.
  net ads join -U "${user}" --no-dns-updates < /run/secrets/ad_join_password
fi
net ads testjoin

winbindd -D
for i in $(seq 1 30); do wbinfo -p >/dev/null 2>&1 && break; sleep 1; done
wbinfo -t

# Live session list for the app's SMB page.
( while true; do python3 /usr/local/bin/status.py /srv/tree/.smbstatus.json || true; sleep 15; done ) &

exec smbd --foreground --no-process-group
