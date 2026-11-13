#!/usr/bin/env python3
"""bucketsd: the small root helper that runs ZFS for Artifact Buckets on the host.

The app container never gets ZFS rights. It talks to this helper over a Unix socket and can only ask for
the fixed operations below. The helper also runs the schedule on its own: restore points (snapshots),
pruning, a monthly scrub, and the nightly backup when one is set up.

Protocol: one JSON object per line, {"op": "...", "args": {...}} -> {"ok": true, "result": ...} or
{"ok": false, "error": "..."}.

    bucketsd.py --pool buckets --dataset buckets/tree --socket /run/buckets/zfs.sock
    bucketsd.py --fake /tmp/fakezfs --socket /tmp/fakezfs/zfs.sock     # development and tests: no ZFS needed

Standard library only, Python 3.10+.
"""
from __future__ import annotations

import argparse
import datetime as dt
import grp
import json
import os
import re
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import time
import uuid
from typing import Any

UTC = dt.timezone.utc
# ab-<kind>.<UTC time>: the single "." lets Samba's Previous Versions split the name from the time.
SNAP_RE = re.compile(r"^ab-(hourly|daily|weekly|monthly|manual)\.(\d{8}-\d{4})$")
DEFAULT_KEEP = {"hourly": 24, "daily": 14, "weekly": 8, "monthly": 6}
SAFE_REL = re.compile(r"^[^\x00]*$")


class Refused(Exception):
    pass


def now() -> dt.datetime:
    return dt.datetime.now(UTC)


def stamp(t: dt.datetime) -> str:
    return t.strftime("%Y%m%d-%H%M")


def parse_stamp(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, "%Y%m%d-%H%M").replace(tzinfo=UTC)


def clean_rel(rel: str) -> str:
    """A path inside the tree, as the app names it (/users/maya/x.md). Never lets '..' or the system folders through."""
    if not isinstance(rel, str) or not SAFE_REL.match(rel):
        raise Refused("bad path")
    parts = [p for p in rel.split("/") if p]
    if not parts or any(p in (".", "..") or p.startswith(".") for p in parts) or parts[0] not in ("users", "shared"):
        raise Refused("bad path")
    return "/".join(parts)


# ---- state kept by the helper ----------------------------------------------------------------------------

class State:
    def __init__(self, path: str):
        self.path = path
        self.lock = threading.Lock()
        self.data: dict[str, Any] = {"keep": dict(DEFAULT_KEEP), "backup": None, "events": [], "last_scrub": None}
        if os.path.exists(path):
            with open(path) as fh:
                self.data.update(json.load(fh))

    def save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(self.data, fh, indent=1)
        os.replace(tmp, self.path)

    def event(self, kind: str, text: str, ok: bool = True) -> None:
        with self.lock:
            self.data["events"] = ([{"ts": now().isoformat(), "kind": kind, "text": text, "ok": ok}] + self.data["events"])[:200]
            self.save()


# ---- the real thing ----------------------------------------------------------------------------------------

def run(*cmd: str, timeout: int = 120, input_: bytes | None = None) -> str:
    r = subprocess.run(cmd, capture_output=True, timeout=timeout, input=input_)
    if r.returncode != 0:
        raise Refused((r.stderr or r.stdout).decode(errors="replace").strip()[:500] or f"{cmd[0]} failed")
    return r.stdout.decode(errors="replace")


class Zfs:
    """Runs zpool/zfs. Every argument is checked or comes from ZFS itself, never straight from the request."""

    def __init__(self, pool: str, dataset: str, files_dir: str):
        self.pool, self.dataset, self.files_dir = pool, dataset, files_dir

    # what is there
    def mountpoint(self) -> str:
        return run("zfs", "get", "-Hp", "-o", "value", "mountpoint", self.dataset).strip()

    def status(self) -> dict[str, Any]:
        props = run("zpool", "list", "-Hp", "-o", "name,size,alloc,free,health,frag,cap,dedup", self.pool).split("\t")
        used = run("zfs", "get", "-Hp", "-o", "value", "used,available,compressratio", self.dataset).split()
        vdevs = self._vdevs()
        scrub = self._scrub()
        return {"pool": self.pool, "size": int(props[1]), "alloc": int(props[2]), "free": int(props[3]),
                "health": props[4], "cap_pct": int(props[6].rstrip("%") or 0), "dataset_used": int(used[0]),
                "dataset_avail": int(used[1]), "compression": used[2], "vdevs": vdevs, "scrub": scrub}

    def _vdevs(self) -> list[dict[str, Any]]:
        """Parse `zpool status -P` into groups (mirror-0, or a single disk/file) and their members."""
        out = run("zpool", "status", "-P", "-p", self.pool)
        lines = out.split("\n")
        try:
            start = next(i for i, l in enumerate(lines) if l.strip().startswith("NAME") and "STATE" in l)
        except StopIteration:
            return []
        groups: list[dict[str, Any]] = []
        base = None
        for l in lines[start + 1:]:
            if not l.strip() or l.strip().startswith("errors:"):
                break
            indent = len(l) - len(l.lstrip("\t ").lstrip())
            cols = l.split()
            name, health = cols[0], cols[1] if len(cols) > 1 else "?"
            errs = [int(c) if c.isdigit() else 0 for c in cols[2:5]] if len(cols) >= 5 else [0, 0, 0]
            if base is None:
                base = indent  # the pool line
                continue
            if name in ("logs", "cache", "spares", "special", "dedup"):
                break
            dev = {"id": name, "name": os.path.basename(name), "health": health, "read": errs[0], "write": errs[1],
                   "cksum": errs[2], "file": name.startswith(self.files_dir), "size": self._size(name)}
            if indent <= base + 2:  # a top-level vdev
                if re.match(r"^(mirror|raidz\d?)-\d+$", name):
                    groups.append({"type": name.split("-")[0], "name": name, "health": health, "disks": []})
                else:
                    groups.append({"type": "single", "name": name, "health": health, "disks": [dev]})
            elif groups:
                groups[-1]["disks"].append(dev)
        return groups

    def _size(self, dev: str) -> int:
        try:
            if os.path.isfile(dev):
                return os.path.getsize(dev)
            return int(run("lsblk", "-bdno", "SIZE", dev).strip() or 0)
        except Exception:
            return 0

    def _scrub(self) -> dict[str, Any]:
        out = run("zpool", "status", self.pool)
        m = re.search(r"scan: (.*?)\n(?:config:|\s*\n)", out, re.S)
        text = " ".join(m.group(1).split()) if m else "none requested"
        return {"text": text, "running": "in progress" in text}

    def free_disks(self) -> list[dict[str, Any]]:
        """Whole disks with nothing on them: no partitions, not mounted, not in any pool, not the system disk."""
        data = json.loads(run("lsblk", "-J", "-b", "-o", "NAME,PATH,SIZE,TYPE,MOUNTPOINT,FSTYPE,MODEL,SERIAL,ROTA,RO"))
        in_pool = run("zpool", "status", "-P")
        out = []
        for d in data.get("blockdevices", []):
            if d.get("type") != "disk" or d.get("ro") or d.get("children") or d.get("mountpoint") or d.get("fstype"):
                continue
            if d["path"] in in_pool:
                continue
            by_id = self._by_id(d["path"])
            out.append({"id": by_id or d["path"], "name": d["name"], "size": int(d["size"]),
                        "model": (d.get("model") or "").strip(), "serial": (d.get("serial") or "").strip(),
                        "kind": "hdd" if d.get("rota") else "ssd"})
        return out

    def _by_id(self, path: str) -> str | None:
        base = "/dev/disk/by-id"
        if not os.path.isdir(base):
            return None
        real = os.path.realpath(path)
        for n in sorted(os.listdir(base)):
            if n.startswith(("wwn-", "nvme-eui")) and os.path.realpath(os.path.join(base, n)) == real:
                return os.path.join(base, n)
        for n in sorted(os.listdir(base)):
            if os.path.realpath(os.path.join(base, n)) == real and "-part" not in n:
                return os.path.join(base, n)
        return None

    def _check_free(self, ids: list[str]) -> list[str]:
        free = {d["id"] for d in self.free_disks()}
        for i in ids:
            if i not in free:
                raise Refused(f"{i} isn't a free disk")
        return ids

    # changes
    def add(self, disks: list[str], layout: str) -> None:
        self._check_free(disks)
        if layout == "mirror":
            if len(disks) != 2:
                raise Refused("A mirror needs two disks")
            run("zpool", "add", self.pool, "mirror", *disks)
        else:
            if len(disks) != 1:
                raise Refused("Pick one disk")
            run("zpool", "add", "-f", self.pool, disks[0])

    def attach(self, existing: str, new: str) -> None:
        self._check_member(existing)
        self._check_free([new])
        run("zpool", "attach", self.pool, existing, new)

    def replace(self, old: str, new: str) -> None:
        self._check_member(old)
        self._check_free([new])
        run("zpool", "replace", self.pool, old, new)

    def grow_file(self, dev: str, add_bytes: int) -> None:
        self._check_member(dev)
        if not dev.startswith(self.files_dir) or not os.path.isfile(dev):
            raise Refused("Only file-backed space can be grown this way")
        with open(dev, "r+b") as fh:
            fh.truncate(os.path.getsize(dev) + add_bytes)  # sparse: takes no space until used
        run("zpool", "online", "-e", self.pool, dev)

    def _check_member(self, dev: str) -> None:
        members = {d["id"] for g in self._vdevs() for d in g["disks"]}
        if dev not in members:
            raise Refused(f"{dev} isn't in the pool")

    def scrub(self) -> None:
        run("zpool", "scrub", self.pool)

    # restore points
    def snapshots(self) -> list[dict[str, Any]]:
        out = run("zfs", "list", "-Hp", "-t", "snapshot", "-o", "name,creation,used,referenced", "-s", "creation", "-d", "1", self.dataset)
        snaps = []
        for l in out.strip().split("\n"):
            if not l:
                continue
            name, created, used, ref = l.split("\t")
            short = name.split("@", 1)[1]
            m = SNAP_RE.match(short)
            if not m:
                continue
            snaps.append({"name": short, "kind": m.group(1), "created": dt.datetime.fromtimestamp(int(created), UTC).isoformat(),
                          "used": int(used), "referenced": int(ref)})
        return snaps

    def snapshot(self, name: str) -> None:
        run("zfs", "snapshot", f"{self.dataset}@{name}")

    def destroy_snapshot(self, name: str) -> None:
        if not SNAP_RE.match(name):
            raise Refused("Not one of ours")
        run("zfs", "destroy", f"{self.dataset}@{name}")

    def snapshot_root(self, name: str) -> str:
        return os.path.join(self.mountpoint(), ".zfs", "snapshot", name)

    # backup
    def send_backup(self, target: dict[str, Any], last: str | None, snap: str) -> None:
        dest = target["dataset"]
        if not re.match(r"^[A-Za-z0-9_.:/-]+$", dest):
            raise Refused("bad backup dataset")
        send = ["zfs", "send", "-w"] + (["-i", f"@{last}"] if last else []) + [f"{self.dataset}@{snap}"]
        if target["kind"] == "ssh":
            host = target["host"]
            if not re.match(r"^[A-Za-z0-9_.@-]+$", host):
                raise Refused("bad backup host")
            recv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, "zfs", "receive", "-u", "-F", dest]
        else:
            recv = ["zfs", "receive", "-u", "-F", dest]
        p1 = subprocess.Popen(send, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        p2 = subprocess.run(recv, stdin=p1.stdout, capture_output=True, timeout=6 * 3600)
        p1.stdout.close()
        p1.wait()
        if p1.returncode or p2.returncode:
            raise Refused((p1.stderr.read() + p2.stderr).decode(errors="replace").strip()[:500] or "backup failed")


# ---- a pretend ZFS for development and tests ----------------------------------------------------------------

class FakeZfs(Zfs):
    """Keeps the same shape as Zfs, with snapshots stored as plain copies. For the dev server and tests only."""

    def __init__(self, root: str, tree: str):
        self.root, self.tree = root, tree
        self.files_dir = os.path.join(root, "files")
        os.makedirs(os.path.join(root, "snaps"), exist_ok=True)
        os.makedirs(self.files_dir, exist_ok=True)
        self.pool, self.dataset = "buckets", "buckets/tree"
        meta = os.path.join(root, "pool.json")
        if not os.path.exists(meta):
            first = os.path.join(self.files_dir, "disk0.img")
            open(first, "wb").truncate(10 * 1024 ** 3)
            json.dump({"vdevs": [{"type": "single", "name": first, "disks": [first]}],
                       "free": [{"id": "/dev/disk/by-id/scsi-0QEMU_HARDDISK_lab-spare-1", "name": "sdb", "size": 20 * 1024 ** 3,
                                 "model": "QEMU HARDDISK", "serial": "lab-spare-1", "kind": "ssd"},
                                {"id": "/dev/disk/by-id/scsi-0QEMU_HARDDISK_lab-spare-2", "name": "sdc", "size": 20 * 1024 ** 3,
                                 "model": "QEMU HARDDISK", "serial": "lab-spare-2", "kind": "ssd"}],
                       "faulted": [], "snaps": {}, "scrub": "scrub repaired 0B with 0 errors"}, open(meta, "w"))
        self.meta_path = meta

    def _m(self) -> dict[str, Any]:
        return json.load(open(self.meta_path))

    def _w(self, m: dict[str, Any]) -> None:
        json.dump(m, open(self.meta_path, "w"))

    def mountpoint(self) -> str:
        return self.tree

    def _dev_size(self, d: str, m: dict[str, Any]) -> int:
        if os.path.isfile(d):
            return os.path.getsize(d)
        return next((x["size"] for x in m.get("used_disks", []) if x["id"] == d), 20 * 1024 ** 3)

    def _vdevs(self) -> list[dict[str, Any]]:
        m = self._m()
        out = []
        for v in m["vdevs"]:
            disks = [{"id": d, "name": os.path.basename(d), "health": "FAULTED" if d in m["faulted"] else "ONLINE",
                      "read": 0, "write": 3 if d in m["faulted"] else 0, "cksum": 12 if d in m["faulted"] else 0,
                      "file": d.startswith(self.files_dir), "size": self._dev_size(d, m)} for d in v["disks"]]
            bad = any(d["health"] != "ONLINE" for d in disks)
            health = ("DEGRADED" if v["type"] == "mirror" and len(disks) > 1 else "FAULTED") if bad else "ONLINE"
            out.append({"type": v["type"], "name": v["name"] if v["type"] != "single" else v["disks"][0], "health": health, "disks": disks})
        return out

    def status(self) -> dict[str, Any]:
        vd = self._vdevs()
        size = sum(min(d["size"] for d in v["disks"]) if v["type"] == "mirror" else v["disks"][0]["size"] for v in vd)
        used = 0
        for dp, _, fs in os.walk(self.tree):
            for f in fs:
                try:
                    used += os.path.getsize(os.path.join(dp, f))
                except OSError:
                    pass
        health = "ONLINE" if all(v["health"] == "ONLINE" for v in vd) else "DEGRADED"
        return {"pool": "buckets", "size": size, "alloc": used, "free": size - used, "health": health,
                "cap_pct": int(used * 100 / size) if size else 0, "dataset_used": used, "dataset_avail": size - used,
                "compression": "1.62x", "vdevs": vd, "scrub": {"text": self._m()["scrub"], "running": False}}

    def free_disks(self) -> list[dict[str, Any]]:
        return self._m()["free"]

    def _take(self, m: dict[str, Any], ids: list[str]) -> None:
        free = {d["id"]: d for d in m["free"]}
        for i in ids:
            if i not in free:
                raise Refused(f"{i} isn't a free disk")
        m.setdefault("used_disks", []).extend(free[i] for i in ids)
        m["free"] = [d for d in m["free"] if d["id"] not in ids]

    def add(self, disks: list[str], layout: str) -> None:
        m = self._m()
        if layout == "mirror" and len(disks) != 2:
            raise Refused("A mirror needs two disks")
        if layout != "mirror" and len(disks) != 1:
            raise Refused("Pick one disk")
        self._take(m, disks)
        n = sum(1 for v in m["vdevs"] if v["type"] == "mirror")
        m["vdevs"].append({"type": "mirror", "name": f"mirror-{n}", "disks": disks} if layout == "mirror"
                          else {"type": "single", "name": disks[0], "disks": disks})
        self._w(m)

    def attach(self, existing: str, new: str) -> None:
        m = self._m()
        v = next((v for v in m["vdevs"] if existing in v["disks"]), None)
        if v is None:
            raise Refused(f"{existing} isn't in the pool")
        self._take(m, [new])
        v["disks"].append(new)
        if v["type"] == "single":
            v["type"], v["name"] = "mirror", f"mirror-{sum(1 for x in m['vdevs'] if x['type'] == 'mirror')}"
        self._w(m)

    def replace(self, old: str, new: str) -> None:
        m = self._m()
        v = next((v for v in m["vdevs"] if old in v["disks"]), None)
        if v is None:
            raise Refused(f"{old} isn't in the pool")
        self._take(m, [new])
        v["disks"] = [new if d == old else d for d in v["disks"]]
        m["faulted"] = [d for d in m["faulted"] if d != old]
        if v["type"] == "single":
            v["name"] = new
        self._w(m)

    def grow_file(self, dev: str, add_bytes: int) -> None:
        if not dev.startswith(self.files_dir) or not os.path.isfile(dev):
            raise Refused("Only file-backed space can be grown this way")
        with open(dev, "r+b") as fh:
            fh.truncate(os.path.getsize(dev) + add_bytes)

    def scrub(self) -> None:
        m = self._m()
        m["scrub"] = f"scrub repaired 0B in 00:00:04 with 0 errors on {now():%a %b %d %H:%M:%S %Y}"
        self._w(m)

    def snapshots(self) -> list[dict[str, Any]]:
        m = self._m()
        out = []
        for name, created in sorted(m["snaps"].items(), key=lambda x: x[1]):
            k = SNAP_RE.match(name)
            out.append({"name": name, "kind": k.group(1) if k else "manual", "created": created, "used": 4096, "referenced": 0})
        return out

    def snapshot(self, name: str) -> None:
        m = self._m()
        dst = os.path.join(self.root, "snaps", name)
        shutil.copytree(self.tree, dst, symlinks=True, ignore=shutil.ignore_patterns(".tmp", ".restore"))
        m["snaps"][name] = now().isoformat()
        self._w(m)

    def destroy_snapshot(self, name: str) -> None:
        m = self._m()
        shutil.rmtree(os.path.join(self.root, "snaps", name), ignore_errors=True)
        m["snaps"].pop(name, None)
        self._w(m)

    def snapshot_root(self, name: str) -> str:
        return os.path.join(self.root, "snaps", name)

    def send_backup(self, target: dict[str, Any], last: str | None, snap: str) -> None:
        if target.get("host") == "unreachable.example":
            raise Refused("ssh: connect to host unreachable.example port 22: No route to host")
        os.makedirs(os.path.join(self.root, "backup"), exist_ok=True)
        open(os.path.join(self.root, "backup", snap), "w").write(last or "full")

    # test hook
    def fault(self, dev: str) -> None:
        m = self._m()
        m["faulted"].append(dev)
        self._w(m)


# ---- the helper itself -----------------------------------------------------------------------------------------

class Helper:
    def __init__(self, z: Zfs, state: State, tree: str):
        self.z, self.s, self.tree = z, state, tree
        self.lock = threading.Lock()  # one change at a time

    # requests
    def handle(self, op: str, a: dict[str, Any]) -> Any:
        fn = getattr(self, "op_" + op, None)
        if fn is None:
            raise Refused(f"unknown operation {op!r}")
        return fn(**a)

    def op_status(self) -> dict[str, Any]:
        st = self.z.status()
        snaps = self.z.snapshots()
        b = self.s.data.get("backup")
        return {**st, "free_disks": self.z.free_disks(), "keep": self.s.data["keep"],
                "restore_points": {"count": len(snaps), "oldest": snaps[0]["created"] if snaps else None,
                                   "newest": snaps[-1]["created"] if snaps else None},
                "backup": b, "events": self.s.data["events"][:30], "last_scrub": self.s.data.get("last_scrub")}

    def op_snapshots(self) -> list[dict[str, Any]]:
        return list(reversed(self.z.snapshots()))

    def op_snapshot_now(self) -> dict[str, Any]:
        name = f"ab-manual.{stamp(now())}"
        with self.lock:
            if name not in {s["name"] for s in self.z.snapshots()}:
                self.z.snapshot(name)
        self.s.event("snapshot", "Restore point made by hand")
        return {"name": name}

    def op_list_at(self, snapshot: str, path: str) -> list[dict[str, Any]]:
        """What a folder looked like at a restore point (names, sizes, folder or file)."""
        root = self._snap(snapshot)
        rel = clean_rel(path)
        d = os.path.join(root, rel)
        if os.path.islink(d) or not os.path.isdir(d):
            raise Refused("That folder didn't exist then")
        out = []
        for e in sorted(os.scandir(d), key=lambda e: e.name):
            if e.name.startswith(".") or e.is_symlink():
                continue
            out.append({"name": e.name, "folder": e.is_dir(), "size": e.stat().st_size if e.is_file() else None,
                        "mtime": dt.datetime.fromtimestamp(e.stat().st_mtime, UTC).isoformat()})
        return out

    def op_stage(self, snapshot: str, path: str) -> dict[str, Any]:
        """Copy a file or folder as it was at a restore point into the tree's private .tmp area, for the app to import."""
        root = self._snap(snapshot)
        rel = clean_rel(path)
        src = os.path.join(root, rel)
        if os.path.islink(src) or not os.path.exists(src):
            raise Refused("That didn't exist at that time")
        key = uuid.uuid4().hex
        dst = os.path.join(self.tree, ".tmp", "restore-" + key, os.path.basename(rel))
        os.makedirs(os.path.dirname(dst))
        if os.path.isdir(src):
            shutil.copytree(src, dst, symlinks=True)
        else:
            shutil.copy2(src, dst)
        return {"staged": f".tmp/restore-{key}/{os.path.basename(rel)}", "folder": os.path.isdir(src)}

    def op_add_disks(self, disks: list[str], layout: str = "mirror") -> dict[str, Any]:
        with self.lock:
            self.z.add(disks, layout)
        self.s.event("disk", f"Added {', '.join(os.path.basename(d) for d in disks)} ({layout})")
        return self.z.status()

    def op_mirror_disk(self, existing: str, new: str) -> dict[str, Any]:
        with self.lock:
            self.z.attach(existing, new)
        self.s.event("disk", f"{os.path.basename(new)} now mirrors {os.path.basename(existing)}")
        return self.z.status()

    def op_replace_disk(self, old: str, new: str) -> dict[str, Any]:
        with self.lock:
            self.z.replace(old, new)
        self.s.event("disk", f"Replacing {os.path.basename(old)} with {os.path.basename(new)}")
        return self.z.status()

    def op_grow(self, disk: str, gigabytes: int) -> dict[str, Any]:
        if not isinstance(gigabytes, int) or not 1 <= gigabytes <= 1024:
            raise Refused("Grow by 1 to 1024 GB")
        with self.lock:
            self.z.grow_file(disk, gigabytes * 1024 ** 3)
        self.s.event("disk", f"Grew {os.path.basename(disk)} by {gigabytes} GB")
        return self.z.status()

    def op_scrub(self) -> dict[str, Any]:
        self.z.scrub()
        self.s.data["last_scrub"] = now().isoformat()
        self.s.event("scrub", "Health check started")
        return {"started": True}

    def op_set_keep(self, hourly: int, daily: int, weekly: int, monthly: int) -> dict[str, Any]:
        keep = {"hourly": hourly, "daily": daily, "weekly": weekly, "monthly": monthly}
        for k, v in keep.items():
            if not isinstance(v, int) or not 0 <= v <= 400:
                raise Refused(f"{k} must be 0 to 400")
        with self.s.lock:
            self.s.data["keep"] = keep
            self.s.save()
        self.s.event("schedule", "Restore point schedule changed")
        self.prune()
        return keep

    def op_set_backup(self, kind: str | None = None, host: str | None = None, dataset: str | None = None) -> Any:
        if kind is None:
            with self.s.lock:
                self.s.data["backup"] = None
                self.s.save()
            self.s.event("backup", "Backups turned off")
            return None
        if kind not in ("ssh", "local") or not dataset or not re.match(r"^[A-Za-z0-9_.:/-]+$", dataset):
            raise Refused("Give a backup pool/dataset, e.g. backup/buckets")
        if kind == "ssh" and (not host or not re.match(r"^[A-Za-z0-9_.@-]+$", host)):
            raise Refused("Give the backup host, e.g. root@nas.lab.local")
        b = {"kind": kind, "host": host if kind == "ssh" else None, "dataset": dataset, "last": None, "last_ok": None,
             "last_error": None, "last_run": None}
        with self.s.lock:
            self.s.data["backup"] = b
            self.s.save()
        self.s.event("backup", f"Backups go to {(host + ':') if host else ''}{dataset}")
        return b

    def op_backup_now(self) -> dict[str, Any]:
        return self.backup()

    def op_fault(self, disk: str) -> dict[str, Any]:  # fake mode only, for tests and demos
        if not isinstance(self.z, FakeZfs):
            raise Refused("unknown operation 'fault'")
        self.z.fault(disk)
        return self.z.status()

    def _snap(self, name: str) -> str:
        if not isinstance(name, str) or not SNAP_RE.match(name) or name not in {s["name"] for s in self.z.snapshots()}:
            raise Refused("No such restore point")
        return self.z.snapshot_root(name)

    # schedule
    def tick(self, t: dt.datetime | None = None) -> None:
        t = t or now()
        snaps = self.z.snapshots()
        have = {s["name"] for s in snaps}
        keep = self.s.data["keep"]
        due = []
        if keep["hourly"]:
            due.append(f"ab-hourly.{t:%Y%m%d-%H}00")
        if keep["daily"] and t.hour >= 1:
            due.append(f"ab-daily.{t:%Y%m%d}-0100")
        if keep["weekly"] and t.weekday() == 6 and t.hour >= 2:
            due.append(f"ab-weekly.{t:%Y%m%d}-0200")
        if keep["monthly"] and t.day == 1 and t.hour >= 3:
            due.append(f"ab-monthly.{t:%Y%m%d}-0300")
        for name in due:
            if name not in have:
                with self.lock:
                    self.z.snapshot(name)
        self.prune()
        # Monthly health check (scrub) on the first Sunday, and the nightly backup.
        last = self.s.data.get("last_scrub")
        if t.weekday() == 6 and t.day <= 7 and t.hour >= 4 and (not last or (t - dt.datetime.fromisoformat(last)).days > 20):
            try:
                self.op_scrub()
            except Refused as e:
                self.s.event("scrub", f"Health check didn't start: {e}", ok=False)
        b = self.s.data.get("backup")
        if b and t.hour >= 1 and (not b.get("last_run") or dt.datetime.fromisoformat(b["last_run"]).date() < t.date()):
            self.backup()

    def prune(self) -> None:
        keep = self.s.data["keep"]
        by_kind: dict[str, list[str]] = {}
        for s in self.z.snapshots():
            by_kind.setdefault(s["kind"], []).append(s["name"])
        b = self.s.data.get("backup") or {}
        for kind, names in by_kind.items():
            if kind == "manual":
                n = 20  # hand-made ones: keep the latest 20
            else:
                n = keep.get(kind, 0)
            for name in names[:-n] if n else names:
                if name == b.get("last"):
                    continue  # the next backup builds on this one
                with self.lock:
                    self.z.destroy_snapshot(name)

    def backup(self) -> dict[str, Any]:
        b = self.s.data.get("backup")
        if not b:
            raise Refused("No backup target is set up")
        snaps = [s["name"] for s in self.z.snapshots() if s["kind"] != "manual"]
        if not snaps:
            self.op_snapshot_now()
            snaps = [s["name"] for s in self.z.snapshots()]
        snap = snaps[-1]
        last = b.get("last") if b.get("last") in {s["name"] for s in self.z.snapshots()} else None
        b["last_run"] = now().isoformat()
        try:
            if snap != last:
                self.z.send_backup(b, last, snap)
            b.update(last=snap, last_ok=now().isoformat(), last_error=None)
            self.s.event("backup", f"Backed up to {snap}" + (" (changes only)" if last else " (full copy)"))
        except (Refused, subprocess.TimeoutExpired, OSError) as e:
            b["last_error"] = str(e)[:300]
            self.s.event("backup", f"Backup failed: {b['last_error']}", ok=False)
        with self.s.lock:
            self.s.save()
        return b


def serve(helper: Helper, sock_path: str, group: str | None) -> None:
    class H(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            for line in self.rfile:
                try:
                    req = json.loads(line)
                    if not isinstance(req, dict) or not isinstance(req.get("args", {}), dict):
                        raise Refused("bad request")
                    res = {"ok": True, "result": helper.handle(str(req.get("op")), req.get("args") or {})}
                except (Refused, TypeError, ValueError, OSError, subprocess.TimeoutExpired) as e:
                    res = {"ok": False, "error": str(e) or e.__class__.__name__}
                self.wfile.write((json.dumps(res) + "\n").encode())
                self.wfile.flush()

    class S(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True

    if os.path.exists(sock_path):
        os.unlink(sock_path)
    os.makedirs(os.path.dirname(sock_path), exist_ok=True)
    srv = S(sock_path, H)
    os.chmod(sock_path, 0o660)
    if group:
        os.chown(sock_path, -1, grp.getgrnam(group).gr_gid if not group.isdigit() else int(group))
    srv.serve_forever()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pool", default="buckets")
    ap.add_argument("--dataset", default="buckets/tree")
    ap.add_argument("--files-dir", default="/var/lib/buckets/vdevs", help="where file-backed pool space lives")
    ap.add_argument("--socket", default="/run/buckets/zfs.sock")
    ap.add_argument("--group", default=None, help="group (name or gid) allowed to use the socket")
    ap.add_argument("--state", default="/var/lib/buckets/bucketsd.json")
    ap.add_argument("--fake", default=None, help="run a pretend ZFS in this folder (development only)")
    ap.add_argument("--tree", default=None, help="fake mode: the app's tree root")
    ap.add_argument("--every", type=int, default=60, help="seconds between schedule checks")
    a = ap.parse_args()
    if a.fake:
        os.makedirs(a.fake, exist_ok=True)
        z: Zfs = FakeZfs(a.fake, a.tree or os.path.join(a.fake, "tree"))
        state = State(os.path.join(a.fake, "state.json"))
        tree = z.mountpoint()
    else:
        z = Zfs(a.pool, a.dataset, a.files_dir)
        os.makedirs(os.path.dirname(a.state), exist_ok=True)
        state = State(a.state)
        tree = z.mountpoint()
    helper = Helper(z, state, tree)

    def loop() -> None:
        while True:
            try:
                helper.tick()
            except Exception as e:  # keep going; say what happened
                state.event("schedule", f"Scheduled work failed: {e}", ok=False)
            time.sleep(a.every)

    threading.Thread(target=loop, daemon=True).start()
    print(f"bucketsd: {'fake ' if a.fake else ''}pool {z.pool}, tree {tree}, socket {a.socket}", file=sys.stderr, flush=True)
    serve(helper, a.socket, a.group)


if __name__ == "__main__":
    main()
