"""Write the current SMB sessions to a JSON file the app reads (smbstatus --json, trimmed)."""
import json
import os
import subprocess
import sys

out = sys.argv[1]
raw = json.loads(subprocess.run(["smbstatus", "--json"], capture_output=True, text=True, check=True).stdout or "{}")
opens = {}
for f in (raw.get("open_files") or {}).values():
    for o in (f.get("opens") or {}).values():
        pid = str(o.get("server_id", {}).get("pid", ""))
        opens[pid] = opens.get(pid, 0) + 1
sessions = []
for s in (raw.get("sessions") or {}).values():
    pid = str(s.get("server_id", {}).get("pid", ""))
    enc = s.get("encryption", {}).get("cipher", "")
    sessions.append({"user": s.get("username"), "machine": s.get("remote_machine") or s.get("hostname"),
                     "protocol": s.get("session_dialect"), "encrypted": bool(enc and enc != "-"),
                     "open_files": opens.get(pid, 0)})
tmp = out + ".tmp"
with open(tmp, "w") as fh:
    json.dump({"sessions": sessions}, fh)
os.replace(tmp, out)
