#!/usr/bin/env python3
"""stalwart-rclonesync — two-way, state-driven file mirror between two sides.

Each side is a "transport":
  * rclone  (default): any rclone remote — local disk, pCloud, S3, SFTP,
    WebDAV (Stalwart Files area at /dav/file/<account>), etc.
  * jmap    (--*-type jmap): the native Stalwart JMAP FileNode API
    (JSON over HTTPS). Use this instead of WebDAV when file/folder names
    must be stored cleanly (Stalwart's WebDAV binding stores URL-encoded
    names in the JMAP FileNode, so JMAP clients see "%20" in names).

The typical use case is keeping a group/account "Files" area of a Stalwart
mail server in sync with a pCloud folder. The engine talks to rclone sides
through the rclone binary and to jmap sides through a small dependency-free
HTTP client (Python standard library only).

Why not plain `rclone bisync`?
    Generic WebDAV servers (including Stalwart) cannot set file modification
    times: they ignore X-OC-Mtime / Last-Modified and stamp the receive time.
    bisync compares mtimes, so every run would see one side as "newer" and
    copy the file back and forth forever. This engine instead keeps its own
    state file (size + sha1 + observed timestamps) and only copies when
    content actually differs. A side whose mtime cannot be preserved is
    treated as "touched" by any mtime newer than our last write (grace
    period --touch-grace), and same-size edits are confirmed by sha1 before
    copying. (A jmap side is always treated as untrusted: the server owns
    the 'modified' timestamp.)

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
import base64
import fcntl
import hashlib
import json
import logging
import mimetypes
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

VERSION = "0.4.0"


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
        r"(?:\.(\d{1,9}))?(?:([+-])(\d{2}):(\d{2}))?$",
        t,
    )
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


# ---------------------------------------------------------------------------
# JMAP (Stalwart FileNode) transport — Python standard library only.
# ---------------------------------------------------------------------------
class JmapError(RuntimeError):
    pass


class JmapSide:
    """Minimal JMAP client for Stalwart's FileNode storage.

    Talks to the JMAP API with Basic auth (account or app password).
    The JMAP session is fetched lazily; the target account is selected by
    principal name (e.g. 'freight@example.com') among the accounts that
    advertise the 'urn:ietf:params:jmap:filenode' capability.
    """

    FILENODE = "urn:ietf:params:jmap:filenode"

    def __init__(self, name, url, user, password, account_name, timeout=120):
        self.name = name
        self.base = url.rstrip("/")
        self.user = user
        self.password = password
        self.account_name = account_name
        self.timeout = timeout
        self.auth_header = (
            "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
        )
        self.session = None
        self.api_url = None
        self.upload_url = None
        self.download_url = None
        self.account_id = None
        self.nodes = {}  # path -> node dict (refreshed by listing())

    # -- low-level HTTP -----------------------------------------------------
    def _http(self, method, url, payload=None, headers=None, timeout=None):
        hdrs = {"Authorization": self.auth_header}
        if headers:
            hdrs.update(headers)
        data = None
        if payload is not None:
            if isinstance(payload, (bytes, bytearray)):
                data = bytes(payload)
            else:
                data = json.dumps(payload).encode()
                hdrs.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:400]
            raise JmapError(f"jmap HTTP {e.code} on {method} {url}: {body}") from e
        except urllib.error.URLError as e:
            raise JmapError(f"jmap network error on {method} {url}: {e}") from e

    def _json(self, method, url, payload=None, headers=None):
        status, body = self._http(method, url, payload, headers)
        try:
            return json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise JmapError(f"jmap invalid JSON from {url}: {e}") from e

    # -- session & account selection ----------------------------------------
    def _ensure_session(self):
        if self.session is not None:
            return
        sess = self._json("GET", self.base + "/jmap/session")
        accounts = sess.get("accounts") or {}
        pick = None
        for aid, acc in accounts.items():
            caps = acc.get("accountCapabilities") or {}
            has_file = self.FILENODE in caps
            if self.account_name and acc.get("name") == self.account_name:
                pick = (aid, acc, has_file)
                break
            if pick is None and has_file:
                pick = (aid, acc, has_file)
        if pick is None:
            raise JmapError(
                f"jmap: no account with {self.FILENODE} capability"
                + (f" named {self.account_name!r}" if self.account_name else "")
            )
        aid, acc, has_file = pick
        if self.account_name and not has_file:
            raise JmapError(
                f"jmap: account {self.account_name!r} has no {self.FILENODE} capability"
            )
        self.account_id = aid
        self.api_url = sess.get("apiUrl") or (self.base + "/jmap")
        self.upload_url = (sess.get("uploadUrl") or "").replace(
            "{accountId}", self.account_id
        )
        self.download_url = (sess.get("downloadUrl") or "").replace(
            "{accountId}", self.account_id
        )
        if not self.upload_url or not self.download_url:
            raise JmapError("jmap: session did not advertise upload/download URLs")
        self.session = sess

    def _call(self, method_calls):
        self._ensure_session()
        data = {
            "using": ["urn:ietf:params:jmap:core", self.FILENODE],
            "methodCalls": method_calls,
        }
        resp = self._json("POST", self.api_url, data)
        if "error" in resp:
            raise JmapError(f"jmap session-level error: {resp['error']}")
        return resp.get("methodResponses", [])

    @staticmethod
    def _find_response(responses, method, tag):
        for m in responses:
            if len(m) >= 3 and m[0] == method and m[2] == tag:
                return m[1]
        # fallback: first response with that method name
        for m in responses:
            if m and m[0] == method:
                return m[1]
        raise JmapError(f"jmap: no {method} response (tag {tag}) in {responses}")

    # -- listing ------------------------------------------------------------
    def listing(self):
        """Return (files, dirs) where paths are '/'-joined relative paths."""
        self._ensure_session()
        ids = []
        position = 0
        while True:
            data = self._find_response(
                self._call(
                    [
                        [
                            "FileNode/query",
                            {
                                "accountId": self.account_id,
                                "limit": 500,
                                "position": position,
                            },
                            "q",
                        ]
                    ]
                ),
                "FileNode/query",
                "q",
            )
            batch = data.get("ids") or []
            ids.extend(batch)
            if len(batch) < 500:
                break
            position += 500
            if position > 100000:
                raise JmapError("jmap: FileNode/query pagination runaway")
        by_id = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i : i + 500]
            data = self._find_response(
                self._call(
                    [
                        [
                            "FileNode/get",
                            {
                                "accountId": self.account_id,
                                "ids": chunk,
                                "properties": [
                                    "id",
                                    "name",
                                    "parentId",
                                    "nodeType",
                                    "blobId",
                                    "size",
                                    "modified",
                                ],
                            },
                            "g",
                        ]
                    ]
                ),
                "FileNode/get",
                "g",
            )
            for n in data.get("list", []):
                by_id[n["id"]] = n
        # build paths from parentId chains
        memo = {}
        lookup = {}

        def path_of(nid):
            if nid in memo:
                return memo[nid]
            n = by_id.get(nid)
            if n is None:
                return None
            if nid in lookup:  # cycle guard
                return None
            lookup[nid] = True
            parent = None
            pid = n.get("parentId")
            if pid:
                parent = path_of(pid)
            lookup.pop(nid, None)
            p = (parent + "/" if parent else "") + (n.get("name") or "")
            memo[nid] = p
            return p

        files, dirs = {}, set()
        self.nodes = {}
        for nid, n in by_id.items():
            p = path_of(nid)
            if p is None or p == "":
                continue
            self.nodes[p] = n
            if n.get("nodeType") == "directory":
                dirs.add(p)
            else:
                files[p] = {
                    "size": int(n.get("size") or 0),
                    "mtime": epoch(n.get("modified")),
                }
        return files, dirs

    # -- helpers ------------------------------------------------------------
    def _parent_id(self, path):
        if "/" not in path:
            return None
        parent = path.rsplit("/", 1)[0]
        node = self.nodes.get(parent)
        if node is None:
            self.mkdir(parent)  # ensure ancestors exist
            node = self.nodes.get(parent)
        return node["id"] if node else None

    def _basename(self, path):
        return path.rsplit("/", 1)[-1]

    def _upload(self, local_path):
        ctype = mimetypes.guess_type(local_path)[0] or "application/octet-stream"
        with open(local_path, "rb") as f:
            data = f.read()
        resp = self._json(
            "POST", self.upload_url, payload=data, headers={"Content-Type": ctype}
        )
        blob = resp.get("blobId")
        if not blob:
            raise JmapError(f"jmap upload returned no blobId: {resp}")
        return blob, resp.get("type") or ctype

    def _set(self, create=None, update=None, destroy=None):
        payload = {"accountId": self.account_id}
        if create:
            payload["create"] = create
        if update:
            payload["update"] = update
        if destroy:
            payload["destroy"] = destroy
        return self._find_response(
            self._call([["FileNode/set", payload, "s"]]), "FileNode/set", "s"
        )

    # -- operations used by the engine --------------------------------------
    def mkdir(self, path):
        """Ensure a directory exists at `path` (creating ancestors too)."""
        if path in self.nodes:
            return
        parent = None
        acc = ""
        for part in path.split("/"):
            acc = acc + "/" + part if acc else part
            if acc in self.nodes:
                parent = self.nodes[acc]["id"]
                continue
            create = {"name": part, "nodeType": "directory"}
            if parent is not None:
                create["parentId"] = parent
            resp = self._set(create={"k": create})
            created = resp.get("created") or {}
            nid = created.get("k", {}).get("id") if created else None
            if not nid:
                raise JmapError(
                    f"jmap mkdir {acc!r} failed: {resp.get('notCreated') or resp}"
                )
            node = {
                "id": nid,
                "name": part,
                "parentId": parent,
                "nodeType": "directory",
            }
            self.nodes[acc] = node
            parent = nid

    def write_file(self, path, local_path):
        """Create or overwrite the file at `path` with the local content."""
        parent = path.rsplit("/", 1)[0] if "/" in path else None
        if parent:
            self.mkdir(parent)
        blob, ctype = self._upload(local_path)
        node = self.nodes.get(path)
        if node is not None and node.get("nodeType") == "file":
            resp = self._set(update={node["id"]: {"blobId": blob, "type": ctype}})
            if not (resp.get("updated") or {}).get(node["id"]):
                # update refused (e.g. node vanished): recreate
                self._set(destroy=[node["id"]])
                node = None
        if node is None:
            create = {
                "name": self._basename(path),
                "nodeType": "file",
                "blobId": blob,
                "type": ctype,
            }
            pid = self._parent_id(path) if "/" in path else None
            if pid is not None:
                create["parentId"] = pid
            resp = self._set(create={"k": create})
            created = (resp.get("created") or {}).get("k")
            if not created or not created.get("id"):
                raise JmapError(
                    f"jmap write {path!r} failed: {resp.get('notCreated') or resp}"
                )
            nid = created["id"]
        else:
            nid = node["id"]
        with open(local_path, "rb") as f:
            size = os.fstat(f.fileno()).st_size
        self.nodes[path] = {
            "id": nid,
            "name": self._basename(path),
            "parentId": self.nodes.get(parent, {}).get("id") if parent else None,
            "nodeType": "file",
            "blobId": blob,
            "size": size,
        }

    def delete_file(self, path):
        node = self.nodes.get(path)
        if node is None:
            return
        self._set(destroy=[node["id"]])
        self.nodes.pop(path, None)

    def destroy_dir(self, path):
        """Remove an empty directory (best effort, like rclone rmdirs)."""
        node = self.nodes.get(path)
        if node is None:
            return
        resp = self._set(destroy=[node["id"]])
        if (resp.get("destroyed")) and node["id"] in resp.get("destroyed", []):
            self.nodes.pop(path, None)
        # notDestroyed (non-empty or busy): leave it, next run retries

    def download(self, path, dest):
        node = self.nodes.get(path)
        if node is None:
            raise JmapError(f"jmap download: unknown path {path!r}")
        blob = node.get("blobId")
        if not blob:
            open(dest, "wb").close()  # empty file (no blob)
            return
        name = urllib.parse.quote(node.get("name") or "file", safe="")
        url = self.download_url.replace("{blobId}", blob).replace("{name}", name)
        url = url.replace(
            "{type}", urllib.parse.quote("application/octet-stream", safe="")
        )
        status, body = self._http("GET", url)
        with open(dest, "wb") as f:
            f.write(body)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args():
    ap = argparse.ArgumentParser(
        prog="stalwart-rclonesync",
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--version", action="version", version=VERSION)
    ap.add_argument(
        "--left-remote",
        default=None,
        help="rclone remote of side A, e.g. 'pcloud:MailSync/freight' "
        "or a local dir '/srv/sync' (required when --left-type is rclone)",
    )
    ap.add_argument(
        "--right-remote",
        default=None,
        help="rclone remote of side B, e.g. 'freight-dav:' "
        "(required when --right-type is rclone)",
    )
    ap.add_argument(
        "--left-type",
        choices=("rclone", "jmap"),
        default="rclone",
        help="transport of side A: rclone (any remote) or jmap (Stalwart FileNode API)",
    )
    ap.add_argument(
        "--right-type",
        choices=("rclone", "jmap"),
        default="rclone",
        help="transport of side B: rclone (any remote) or jmap (Stalwart FileNode API)",
    )
    for side in ("left", "right"):
        ap.add_argument(
            f"--{side}-jmap-url",
            default=None,
            help=f"JMAP base URL for the {side} side, e.g. "
            "https://mail.example.com (session at /jmap/session)",
        )
        ap.add_argument(
            f"--{side}-jmap-user",
            default=None,
            help=f"JMAP username for the {side} side",
        )
        ap.add_argument(
            f"--{side}-jmap-password",
            default=None,
            help=f"JMAP password/app-password for the {side} side",
        )
        ap.add_argument(
            f"--{side}-jmap-account",
            default=None,
            help=f"JMAP principal name for the {side} side, e.g. "
            "freight@example.com (default: first account with files)",
        )
    ap.add_argument(
        "--state-dir",
        default=".sync-state",
        help="directory for state.json and the lock file",
    )
    ap.add_argument(
        "--ignore-prefix",
        action="append",
        default=[],
        metavar="PATH",
        help="relative path prefix excluded on both sides (repeatable), e.g. '1'",
    )
    ap.add_argument(
        "--left-untrusted-mtime",
        action="store_true",
        help="side A stamps its own mtime and cannot preserve "
        "client mtimes (generic WebDAV); jmap sides are always untrusted",
    )
    ap.add_argument(
        "--right-untrusted-mtime",
        action="store_true",
        help="side B stamps its own mtime and cannot preserve "
        "client mtimes (generic WebDAV, e.g. Stalwart); jmap sides are "
        "always untrusted",
    )
    ap.add_argument(
        "--touch-grace",
        type=float,
        default=5.0,
        help="seconds after our own write during which an mtime "
        "change on an untrusted side is ignored",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="list planned actions without changing anything",
    )
    ap.add_argument(
        "--log",
        metavar="FILE",
        default=None,
        help="append log lines to FILE instead of stderr",
    )
    ap.add_argument("--verbose", action="store_true", help="log every action")
    return ap.parse_args()


def make_side(args, name):
    typ = getattr(args, f"{name}_type")
    untrusted = getattr(args, f"{name}_untrusted_mtime")
    params = {}
    if typ == "jmap":
        for key in ("url", "user", "password", "account"):
            params[key] = getattr(args, f"{name}_jmap_{key}")
        missing = [k for k, v in params.items() if not v]
        if missing:
            raise SystemExit(
                f"error: --{name}-type jmap requires "
                f"--{name}-jmap-{', --'.join(missing)}"
            )
        return {
            "name": name,
            "type": "jmap",
            "untrusted": True,  # the server owns the mtime
            "jmap": JmapSide(
                name,
                params["url"],
                params["user"],
                params["password"],
                params["account"],
            ),
        }
    remote = getattr(args, f"{name}_remote")
    if not remote:
        raise SystemExit(f"error: --{name}-type rclone requires --{name}-remote")
    return {
        "name": name,
        "type": "rclone",
        "untrusted": untrusted,
        "remote": remote,
    }


def main():
    args = parse_args()
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        filename=args.log,
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    log = logging.getLogger("sync")
    info = log.info

    left = make_side(args, "left")
    right = make_side(args, "right")

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

    def is_ignored(path):
        if path in ignored_exact:
            return True
        return any(path.startswith(p) for p in ignored)

    # -- rclone primitives (rclone-type sides) ------------------------------
    def rp(remote, rel):
        return remote.rstrip("/") + "/" + rel

    def run_rclone(cmd_args, check=True):
        p = subprocess.run(
            ["rclone"] + cmd_args, capture_output=True, text=True, timeout=900
        )
        if check and p.returncode != 0:
            raise RuntimeError(
                "rclone {} failed: {}".format(
                    " ".join(cmd_args[:4]), p.stderr.strip()[:400]
                )
            )
        return p.returncode, p.stdout

    def rclone_listing(side):
        rc, out = run_rclone(["lsjson", "-R", side["remote"]])
        files, dirs = {}, set()
        for e in json.loads(out or "[]"):
            path = e["Path"]
            if path == "" or is_ignored(path):
                continue
            if e.get("IsDir"):
                dirs.add(path)
            else:
                files[path] = {"size": int(e["Size"]), "mtime": epoch(e.get("ModTime"))}
        return files, dirs

    # -- transport dispatch --------------------------------------------------
    def listing(side):
        if side["type"] == "jmap":
            files, dirs = side["jmap"].listing()
            files = {p: v for p, v in files.items() if not is_ignored(p)}
            dirs = {d for d in dirs if not is_ignored(d)}
            return files, dirs
        return rclone_listing(side)

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
        if side["type"] == "jmap":
            side["jmap"].download(path, dest)
        else:
            run_rclone(["copyto", rp(side["remote"], path), dest])
        return dest, (os.path.getsize(dest), sha1_of(dest))

    def do_write(side, path, local):
        """Write local file content at `path` on `side` (create or overwrite)."""
        if args.dry_run:
            info("[dry-run] would write %s on %s", path, side["name"])
            return
        if side["type"] == "jmap":
            side["jmap"].write_file(path, local)
        else:
            run_rclone(["copyto", "--ignore-times", local, rp(side["remote"], path)])

    def do_mkdir(side, path):
        if args.dry_run:
            info("[dry-run] would mkdir %s on %s", path, side["name"])
            return
        if side["type"] == "jmap":
            side["jmap"].mkdir(path)
        else:
            run_rclone(["mkdir", rp(side["remote"], path)])

    def do_delete(side, path):
        if args.dry_run:
            info("[dry-run] would delete %s on %s", path, side["name"])
            return
        if side["type"] == "jmap":
            side["jmap"].delete_file(path)
        else:
            run_rclone(["deletefile", rp(side["remote"], path)])

    def do_prune(side, path):
        """Remove an empty directory on `side` (best effort)."""
        if args.dry_run:
            info("[dry-run] would prune dir %s on %s", path, side["name"])
            return
        if side["type"] == "jmap":
            side["jmap"].destroy_dir(path)
        else:
            run_rclone(["rmdirs", rp(side["remote"], path)], check=False)

    def conflict_name(path, ts):
        stem, dot, ext = path.rpartition(".")
        base = stem if dot and stem else path
        suffix = (dot + ext) if dot and stem else ""
        return f"{base}.conflict-{ts}{suffix}"

    def changed(side, entry, st):
        """True when the side's file differs from what we last synced."""
        if st is None:
            return True
        if entry["size"] != st.get("size"):
            return True
        if side["untrusted"]:
            return entry["mtime"] > st.get("mtime", 0) + args.touch_grace
        return abs(entry["mtime"] - st.get("mtime", 0)) > 1

    info(
        "listing %s (%s) and %s (%s)",
        left["name"],
        left["type"],
        right["name"],
        right["type"],
    )
    pf, pd = listing(left)
    df, dd = listing(right)
    info(
        "left files=%d dirs=%d | right files=%d dirs=%d",
        len(pf),
        len(pd),
        len(df),
        len(dd),
    )

    state = {"version": 1, "files": {}}
    if os.path.exists(state_path):
        with open(state_path) as f:
            state = json.load(f)
    files = state.setdefault("files", {})
    counters = {"added": 0, "updated": 0, "deleted": 0, "conflicts": 0}

    # ensure every directory known on either side exists on both
    for d in sorted(pd | dd):
        if d not in pd:
            do_mkdir(left, d)
        if d not in dd:
            do_mkdir(right, d)

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
                        files[path] = {
                            "size": content[0],
                            "sha1": content[1],
                            "mtime": ep["mtime"],
                        }
                        info("noop %s (left touch only)", path)
                        continue
                    do_write(right, path, src)
                    files[path] = {
                        "size": content[0],
                        "sha1": content[1],
                        "mtime": epoch(now_iso())
                        if right["untrusted"]
                        else ep["mtime"],
                    }
                    counters["updated"] += 1
                    info("push %s left->right", path)
                    continue
                if d_changed and not p_changed:
                    if args.dry_run:
                        info("would push %s right->left", path)
                        continue
                    src, content = fetch(right, path, tmpdir)
                    if content[1] == st.get("sha1"):
                        files[path] = {
                            "size": content[0],
                            "sha1": content[1],
                            "mtime": ed["mtime"],
                        }
                        info("noop %s (right touch only)", path)
                        continue
                    do_write(left, path, src)
                    files[path] = {
                        "size": content[0],
                        "sha1": content[1],
                        "mtime": os.stat(src).st_mtime
                        if not left["untrusted"]
                        else epoch(now_iso()),
                    }
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
                    files[path] = {"size": ca[0], "sha1": ca[1], "mtime": ep["mtime"]}
                    info("noop %s (same content on both sides)", path)
                    continue
                l_newer = ep["mtime"] > ed["mtime"] + 2 or (
                    abs(ep["mtime"] - ed["mtime"]) <= 2
                )
                winner, loser = (left, right) if l_newer else (right, left)
                w_local = pa if l_newer else db
                l_local = db if l_newer else pa
                ts = datetime.now().strftime("%Y%m%d-%H%M%S")
                cname = conflict_name(path, ts)
                do_write(left, cname, l_local)
                do_write(right, cname, l_local)
                do_write(left, path, w_local)
                do_write(right, path, w_local)
                now = epoch(now_iso())
                files[path] = {
                    "size": os.path.getsize(w_local),
                    "sha1": sha1_of(w_local),
                    "mtime": now,
                }
                files[cname] = {
                    "size": os.path.getsize(l_local),
                    "sha1": sha1_of(l_local),
                    "mtime": now,
                }
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
                    do_write(right, path, src)
                    files[path] = {
                        "size": content[0],
                        "sha1": content[1],
                        "mtime": epoch(now_iso())
                        if right["untrusted"]
                        else ep["mtime"],
                    }
                    counters["added"] += 1
                    info("add %s left->right", path)
                elif ed:
                    if args.dry_run:
                        info("would add %s right->left", path)
                        continue
                    src, content = fetch(right, path, tmpdir)
                    do_write(left, path, src)
                    files[path] = {
                        "size": content[0],
                        "sha1": content[1],
                        "mtime": os.stat(src).st_mtime
                        if not left["untrusted"]
                        else epoch(now_iso()),
                    }
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
                    do_write(right, path, src)
                    files[path] = {
                        "size": content[0],
                        "sha1": content[1],
                        "mtime": epoch(now_iso())
                        if right["untrusted"]
                        else ep["mtime"],
                    }
                    counters["updated"] += 1
                    info("keep %s left changed while deleted on right (restored)", path)
                else:
                    if args.dry_run:
                        info("would delete %s left (removed on right)", path)
                        continue
                    do_delete(left, path)
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
                    do_write(left, path, src)
                    files[path] = {
                        "size": content[0],
                        "sha1": content[1],
                        "mtime": os.stat(src).st_mtime
                        if not left["untrusted"]
                        else epoch(now_iso()),
                    }
                    counters["updated"] += 1
                    info("keep %s right changed while deleted on left (restored)", path)
                else:
                    if args.dry_run:
                        info("would delete %s right (removed on left)", path)
                        continue
                    do_delete(right, path)
                    files.pop(path, None)
                    counters["deleted"] += 1
                    info("delete %s right (removed on left)", path)
                continue
    finally:
        for fn in os.listdir(tmpdir):
            os.unlink(os.path.join(tmpdir, fn))
        os.rmdir(tmpdir)

    # prune dirs that vanished on one side (rmdirs/destroy only remove empty)
    for d in sorted(dd - pd, key=lambda x: -x.count("/")):
        do_prune(right, d)
    for d in sorted(pd - dd, key=lambda x: -x.count("/")):
        do_prune(left, d)

    if not args.dry_run:
        tmp_state = state_path + ".tmp"
        with open(tmp_state, "w") as f:
            json.dump(state, f, indent=1, sort_keys=True)
        os.replace(tmp_state, state_path)

    detail = (
        f"added {counters['added']}, updated {counters['updated']}, "
        f"deleted {counters['deleted']}, conflicts {counters['conflicts']}"
    )
    info("done: %s%s", detail, " (dry-run)" if args.dry_run else "")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
