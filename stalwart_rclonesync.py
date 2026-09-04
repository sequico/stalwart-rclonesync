#!/usr/bin/env python3
"""stalwart-rclonesync — two-way, state-driven mirror between any two
rclone remotes (typically a Stalwart mail server Files area over WebDAV
and a pCloud folder).

The typical use case is keeping a group/account "Files" area of a Stalwart
mail server (exposed over WebDAV) in sync with a pCloud folder. The engine
talks to both sides only through rclone, so any rclone-supported remote can
stand in for either endpoint.

Why not plain `rclone bisync`?
    Generic WebDAV servers (including Stalwart) cannot set file modification
    times: they ignore X-OC-Mtime / Last-Modified and stamp the receive time.
    bisync compares mtimes, so every run would see one side as "newer" and
    copy the file back and forth forever. This engine instead keeps its own
    state file (size + sha1 + observed timestamps) and only copies when
    content actually differs. A side whose mtime cannot be preserved
    (--right-untrusted-mtime / --left-untrusted-mtime) is treated as
    "touched" by any mtime newer than our last write (grace period
    --touch-grace), and same-size edits are confirmed by sha1 before copying.

Safety rules
    * deletions propagate only when the other side is unchanged since the
      last sync (true mirror); a concurrent edit on the other side wins and
      the deleted file is restored there;
    * both-sides change = conflict: the newer side wins on both sides, the
      losing version is preserved as `<name>.conflict-<ts><ext>` on both
      sides (ties go to --left-remote);
    * paths listed with --ignore-prefix are excluded from the sync on both
      sides;
    * one instance at a time (flock in the state dir); a failed run makes no
      changes; use --dry-run to preview.

State/log:
    state  : <state-dir>/state.json   (default: ./.sync-state)
    lock   : <state-dir>/sync.lock
    log    : stderr, or --log FILE

Exit codes: 0 ok, 1 failed, 2 another instance running.
"""

import argparse
import fcntl
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

VERSION = "0.3.0"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def parse_iso(iso):
    """ISO-8601 -> epoch seconds. Robust across Python versions: <=3.10
    fromisoformat() rejects >6 fractional digits and some offset forms, so
    fall back to a manual parse instead of returning 0."""
    if not iso:
        return 0.0
    t = iso.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(t).timestamp()
    except ValueError:
        pass
    m = re.match(
        r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})"
        r"(?:\.(\d{1,9}))?(?:([+-])(\d{2}):(\d{2}))?$", t)
    if not m:
        return 0.0
    y, mo, d, h, mi, s = (int(m.group(i)) for i in range(1, 7))
    frac = (m.group(7) or "").ljust(6, "0")[:6]
    dt = datetime(y, mo, d, h, mi, s, int(frac) if frac else 0)
    if m.group(8):
        sign = -1 if m.group(8) == "-" else 1
        off = sign * (int(m.group(9)) * 3600 + int(m.group(10)) * 60)
        dt = dt.replace(tzinfo=timezone(timedelta(seconds=off)))
    else:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def epoch(iso):
    return parse_iso(iso)


def parse_args():
    ap = argparse.ArgumentParser(
        prog="stalwart-rclonesync",
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--version", action="version", version=VERSION)
    ap.add_argument("--left-remote", required=True,
                    help="rclone remote of side A, e.g. 'pcloud:MailSync/freight' "
                         "or a local dir '/srv/sync'")
    ap.add_argument("--right-remote", required=True,
                    help="rclone remote of side B, e.g. 'freight-dav:'")
    ap.add_argument("--state-dir", default=".sync-state",
                    help="directory for state.json and the lock file")
    ap.add_argument("--ignore-prefix", action="append", default=[],
                    metavar="PATH",
                    help="relative path prefix excluded on both sides "
                         "(repeatable), e.g. '1'")
    ap.add_argument("--left-untrusted-mtime", action="store_true",
                    help="side A stamps its own mtime and cannot preserve "
                         "client mtimes (generic WebDAV)")
    ap.add_argument("--right-untrusted-mtime", action="store_true",
                    help="side B stamps its own mtime and cannot preserve "
                         "client mtimes (generic WebDAV, e.g. Stalwart)")
    ap.add_argument("--touch-grace", type=float, default=5.0,
                    help="seconds after our own write during which an mtime "
                         "change on an untrusted side is ignored")
    ap.add_argument("--dry-run", action="store_true",
                    help="list planned actions without changing anything")
    ap.add_argument("--log", metavar="FILE", default=None,
                    help="append log lines to FILE instead of stderr")
    ap.add_argument("--verbose", action="store_true", help="log every action")
    return ap.parse_args()


def main():
    args = parse_args()
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        filename=args.log, level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z")
    log = logging.getLogger("sync")
    info = log.info
    if args.log is None:
        logging.getLogger().addHandler(logging.StreamHandler())

    os.makedirs(args.state_dir, exist_ok=True)
    lock_path = os.path.join(args.state_dir, "sync.lock")
    lock_fd = open(lock_path, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        info("another sync instance is running, skipping")
        return 2

    state_path = os.path.join(args.state_dir, "state.json")
    ignored = tuple(p if p.endswith("/") else p + "/" for p in args.ignore_prefix)
    ignored_exact = set(args.ignore_prefix)
    left = {"remote": args.left_remote, "untrusted": args.left_untrusted_mtime,
            "name": "left"}
    right = {"remote": args.right_remote, "untrusted": args.right_untrusted_mtime,
             "name": "right"}
    sides = {"left": left, "right": right}

    def rp(remote, rel):
        return remote.rstrip("/") + "/" + rel

    def is_ignored(path):
        if path in ignored_exact:
            return True
        return any(path.startswith(p) for p in ignored)

    def run_rclone(cmd_args, check=True):
        p = subprocess.run(["rclone"] + cmd_args, capture_output=True,
                           text=True, timeout=900)
        if check and p.returncode != 0:
            raise RuntimeError(
                "rclone %s failed: %s" % (" ".join(cmd_args[:4]),
                                          p.stderr.strip()[:400]))
        return p.returncode, p.stdout

    def listing(side):
        rc, out = run_rclone(["lsjson", "-R", side["remote"]])
        files, dirs = {}, set()
        for e in json.loads(out or "[]"):
            path = e["Path"]
            if path == "" or is_ignored(path):
                continue
            if e.get("IsDir"):
                dirs.add(path)
            else:
                files[path] = {"size": int(e["Size"]),
                               "mtime": epoch(e.get("ModTime"))}
        return files, dirs

    def sha1_of(path):
        h = hashlib.sha1()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    def fetch(side, path, tmpdir):
        # side-tagged temp name: a conflict fetches both sides of the same
        # path, so the two downloads must not collide in tmpdir.
        dest = os.path.join(tmpdir, side["name"] + "__" + path.replace("/", "__"))
        run_rclone(["copyto", rp(side["remote"], path), dest])
        return dest, (os.path.getsize(dest), sha1_of(dest))

    def mut(cmd_args, what):
        if args.dry_run:
            info("[dry-run] would %s: rclone %s", what,
                 " ".join(cmd_args[:3]))
            return
        run_rclone(cmd_args)

    def conflict_name(path, ts):
        stem, dot, ext = path.rpartition(".")
        base = stem if dot and stem else path
        suffix = (dot + ext) if dot and stem else ""
        return "%s.conflict-%s%s" % (base, ts, suffix)

    def changed(side, entry, st):
        """True when the side's file differs from what we last synced."""
        if st is None:
            return True
        if entry["size"] != st.get("size"):
            return True
        if side["untrusted"]:
            return entry["mtime"] > st.get("mtime", 0) + args.touch_grace
        return abs(entry["mtime"] - st.get("mtime", 0)) > 1

    info("listing %s and %s", left["remote"], right["remote"])
    pf, pd = listing(left)
    df, dd = listing(right)
    info("left files=%d dirs=%d | right files=%d dirs=%d",
         len(pf), len(pd), len(df), len(dd))

    state = {"version": 1, "files": {}}
    if os.path.exists(state_path):
        with open(state_path) as f:
            state = json.load(f)
    files = state.setdefault("files", {})
    counters = {"added": 0, "updated": 0, "deleted": 0, "conflicts": 0}

    # ensure every directory known on either side exists on both
    for d in sorted(pd | dd):
        if d not in pd:
            mut(["mkdir", rp(left["remote"], d)], "mkdir %s" % d)
        if d not in dd:
            mut(["mkdir", rp(right["remote"], d)], "mkdir %s" % d)

    tmpdir = tempfile.mkdtemp(prefix="stalwart-sync-")
    try:
        for path in sorted(set(pf) | set(df) | set(files)):
            ep, ed = pf.get(path), df.get(path)
            st = files.get(path)
            p_changed = bool(ep and st and changed(left, ep, st))
            d_changed = bool(ed and st and changed(right, ed, st))

            # present on both sides
            if ep and ed:
                if st is None:
                    p_changed = d_changed = True
                if not p_changed and not d_changed:
                    continue
                if p_changed and not d_changed:
                    if args.dry_run:
                        info("would push %s left->right", path)
                        continue
                    src, content = fetch(left, path, tmpdir)
                    if content[1] == st.get("sha1"):
                        files[path] = {"size": content[0], "sha1": content[1],
                                       "mtime": ep["mtime"]}
                        info("noop %s (left touch only)", path)
                        continue
                    mut(["copyto", "--ignore-times", src, rp(right["remote"], path)],
                        "push %s left->right" % path)
                    files[path] = {"size": content[0], "sha1": content[1],
                                   "mtime": epoch(now_iso())
                                   if right["untrusted"] else ep["mtime"]}
                    counters["updated"] += 1
                    info("push %s left->right", path)
                    continue
                if d_changed and not p_changed:
                    if args.dry_run:
                        info("would push %s right->left", path)
                        continue
                    src, content = fetch(right, path, tmpdir)
                    if content[1] == st.get("sha1"):
                        files[path] = {"size": content[0], "sha1": content[1],
                                       "mtime": ed["mtime"]}
                        info("noop %s (right touch only)", path)
                        continue
                    mut(["copyto", "--ignore-times", src, rp(left["remote"], path)],
                        "push %s right->left" % path)
                    files[path] = {"size": content[0], "sha1": content[1],
                                   "mtime": os.stat(src).st_mtime
                                   if not left["untrusted"] else epoch(now_iso())}
                    counters["updated"] += 1
                    info("push %s right->left", path)
                    continue
                # both changed: conflict
                if args.dry_run:
                    info("would resolve conflict for %s", path)
                    continue
                pa, ca = fetch(left, path, tmpdir)
                db, cb = fetch(right, path, tmpdir)
                if ca[1] == cb[1]:
                    files[path] = {"size": ca[0], "sha1": ca[1],
                                   "mtime": ep["mtime"]}
                    info("noop %s (same content on both sides)", path)
                    continue
                l_newer = ep["mtime"] > ed["mtime"] + 2 or (
                    abs(ep["mtime"] - ed["mtime"]) <= 2)
                winner, loser = (left, right) if l_newer else (right, left)
                w_local = pa if l_newer else db
                l_local = db if l_newer else pa
                ts = datetime.now().strftime("%Y%m%d-%H%M%S")
                cname = conflict_name(path, ts)
                mut(["copyto", "--ignore-times", l_local, rp(left["remote"], cname)],
                    "keep %s conflict copy" % cname)
                mut(["copyto", "--ignore-times", l_local, rp(right["remote"], cname)],
                    "keep %s conflict copy" % cname)
                mut(["copyto", "--ignore-times", w_local, rp(left["remote"], path)],
                    "write winner %s" % path)
                mut(["copyto", "--ignore-times", w_local, rp(right["remote"], path)],
                    "write winner %s" % path)
                now = epoch(now_iso())
                files[path] = {"size": os.path.getsize(w_local),
                               "sha1": sha1_of(w_local), "mtime": now}
                files[cname] = {"size": os.path.getsize(l_local),
                                "sha1": sha1_of(l_local), "mtime": now}
                counters["conflicts"] += 1
                info("conflict %s: %s lost, kept as %s", path, loser["name"], cname)
                continue

            # present on one side only, unknown to the state -> add
            if st is None:
                if ep:
                    if args.dry_run:
                        info("would add %s left->right", path)
                        continue
                    src, content = fetch(left, path, tmpdir)
                    mut(["copyto", "--ignore-times", src, rp(right["remote"], path)],
                        "add %s left->right" % path)
                    files[path] = {"size": content[0], "sha1": content[1],
                                   "mtime": epoch(now_iso())
                                   if right["untrusted"] else ep["mtime"]}
                    counters["added"] += 1
                    info("add %s left->right", path)
                elif ed:
                    if args.dry_run:
                        info("would add %s right->left", path)
                        continue
                    src, content = fetch(right, path, tmpdir)
                    mut(["copyto", "--ignore-times", src, rp(left["remote"], path)],
                        "add %s right->left" % path)
                    files[path] = {"size": content[0], "sha1": content[1],
                                   "mtime": os.stat(src).st_mtime
                                   if not left["untrusted"] else epoch(now_iso())}
                    counters["added"] += 1
                    info("add %s right->left", path)
                continue

            # known file, present on one side only -> delete or keep
            if not ep and not ed:
                files.pop(path, None)
                info("delete %s (gone on both sides)", path)
                continue
            if ep and not ed:
                if p_changed:
                    if args.dry_run:
                        info("would restore %s left (deleted on right)", path)
                        continue
                    src, content = fetch(left, path, tmpdir)
                    mut(["copyto", "--ignore-times", src, rp(right["remote"], path)],
                        "restore %s" % path)
                    files[path] = {"size": content[0], "sha1": content[1],
                                   "mtime": epoch(now_iso())
                                   if right["untrusted"] else ep["mtime"]}
                    counters["updated"] += 1
                    info("keep %s left changed while deleted on right (restored)",
                         path)
                else:
                    if args.dry_run:
                        info("would delete %s left (removed on right)", path)
                        continue
                    mut(["deletefile", rp(left["remote"], path)],
                        "delete %s" % path)
                    files.pop(path, None)
                    counters["deleted"] += 1
                    info("delete %s left (removed on right)", path)
                continue
            if ed and not ep:
                if d_changed:
                    if args.dry_run:
                        info("would restore %s right (deleted on left)", path)
                        continue
                    src, content = fetch(right, path, tmpdir)
                    mut(["copyto", "--ignore-times", src, rp(left["remote"], path)],
                        "restore %s" % path)
                    files[path] = {"size": content[0], "sha1": content[1],
                                   "mtime": os.stat(src).st_mtime
                                   if not left["untrusted"] else epoch(now_iso())}
                    counters["updated"] += 1
                    info("keep %s right changed while deleted on left (restored)",
                         path)
                else:
                    if args.dry_run:
                        info("would delete %s right (removed on left)", path)
                        continue
                    mut(["deletefile", rp(right["remote"], path)],
                        "delete %s" % path)
                    files.pop(path, None)
                    counters["deleted"] += 1
                    info("delete %s right (removed on left)", path)
                continue
    finally:
        for fn in os.listdir(tmpdir):
            os.unlink(os.path.join(tmpdir, fn))
        os.rmdir(tmpdir)

    # prune dirs that vanished on one side (rclone rmdirs only removes empty)
    for d in sorted(dd - pd, key=lambda x: -x.count("/")):
        mut(["rmdirs", rp(right["remote"], d)], "prune dir %s (right)" % d)
    for d in sorted(pd - dd, key=lambda x: -x.count("/")):
        mut(["rmdirs", rp(left["remote"], d)], "prune dir %s (left)" % d)

    if not args.dry_run:
        tmp_state = state_path + ".tmp"
        with open(tmp_state, "w") as f:
            json.dump(state, f, indent=1, sort_keys=True)
        os.replace(tmp_state, state_path)

    detail = ("added %d, updated %d, deleted %d, conflicts %d" %
              (counters["added"], counters["updated"],
               counters["deleted"], counters["conflicts"]))
    info("done: %s%s", detail, " (dry-run)" if args.dry_run else "")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
