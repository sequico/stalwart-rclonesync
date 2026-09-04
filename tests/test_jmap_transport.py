"""End-to-end tests of the JMAP transport against a local mock JMAP server.

The mock implements the minimal Stalwart FileNode surface used by the
engine: /jmap/session, /jmap (FileNode/query|get|set), blob upload and
download. No network access needed, no real Stalwart required.
"""

import json
import os
import subprocess
import sys
import threading
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ENGINE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "stalwart_rclonesync.py"
)

CAP = "urn:ietf:params:jmap:filenode"


class MockJmapServer:
    """In-memory FileNode store with a JMAP-ish HTTP surface."""

    def __init__(self):
        self.nodes = {}  # id -> node
        self.blobs = {}  # blobId -> bytes
        self._nid = 0
        self._bid = 0

    def _new_id(self, prefix):
        self._nid += 1
        return f"{prefix}{self._nid}"

    def _new_blob(self):
        self._bid += 1
        return f"blob{self._bid}"

    # -- helpers for the tests to simulate remote-side users ---------------
    def mkdir(self, path, parent_id=None):
        name = path.rsplit("/", 1)[-1]
        node = {
            "id": self._new_id("n"),
            "name": name,
            "parentId": parent_id,
            "nodeType": "directory",
            "blobId": None,
            "type": None,
            "size": None,
            "modified": datetime.now(timezone.utc).isoformat(),
        }
        self.nodes[node["id"]] = node
        return node["id"]

    def put_file(self, path, content, parent_id=None, ctype="text/plain"):
        name = path.rsplit("/", 1)[-1]
        blob = self._new_blob()
        self.blobs[blob] = content if isinstance(content, bytes) else content.encode()
        node = {
            "id": self._new_id("n"),
            "name": name,
            "parentId": parent_id,
            "nodeType": "file",
            "blobId": blob,
            "type": ctype,
            "size": len(self.blobs[blob]),
            "modified": datetime.now(timezone.utc).isoformat(),
        }
        self.nodes[node["id"]] = node
        return node["id"]

    def by_name(self, name):
        return [n for n in self.nodes.values() if n["name"] == name]

    # -- JMAP handling ------------------------------------------------------
    def session(self, base):
        return {
            "apiUrl": base + "/jmap",
            "uploadUrl": base + "/jmap/upload/{accountId}/",
            "downloadUrl": base + "/jmap/download/{accountId}/{blobId}/{name}"
            "?accept={type}",
            "accounts": {
                "a1": {
                    "name": "freight@example.com",
                    "accountCapabilities": {CAP: {}, "urn:ietf:params:jmap:core": {}},
                },
                "a2": {"name": "other@example.com", "accountCapabilities": {}},
            },
            "primaryAccounts": {"urn:ietf:params:jmap:mail": "a2"},
        }

    def jmap(self, payload):
        out = []
        for call in payload.get("methodCalls", []):
            method, args = call[0], call[1]
            tag = call[2] if len(call) > 2 else ""
            if method == "FileNode/query":
                out.append(
                    [
                        "FileNode/query",
                        {
                            "accountId": args.get("accountId"),
                            "queryState": "mock",
                            "ids": list(self.nodes),
                        },
                        tag,
                    ]
                )
            elif method == "FileNode/get":
                wanted = args.get("ids") or list(self.nodes)
                present = {i: self.nodes[i] for i in wanted if i in self.nodes}
                out.append(
                    [
                        "FileNode/get",
                        {
                            "accountId": args.get("accountId"),
                            "list": [
                                self._project(n, args.get("properties"))
                                for n in present.values()
                            ],
                            "notFound": [i for i in wanted if i not in self.nodes],
                        },
                        tag,
                    ]
                )
            elif method == "FileNode/set":
                out.append(
                    [
                        "FileNode/set",
                        {"accountId": args.get("accountId"), **self._set(args)},
                        tag,
                    ]
                )
            else:
                out.append([method, {"type": "unknownMethod"}, tag])
        return {"methodResponses": out, "sessionState": "mock"}

    @staticmethod
    def _project(node, props):
        if props:
            return {k: node.get(k) for k in props}
        return dict(node)

    def _set(self, args):
        res = {
            "oldState": "mock",
            "newState": "mock",
            "created": {},
            "updated": {},
            "destroyed": [],
        }
        creates = args.get("create") or {}
        for key, want in creates.items():
            parent = (
                self.nodes.get(want.get("parentId")) if want.get("parentId") else None
            )
            if want.get("parentId") and parent is None:
                res.setdefault("notCreated", {})[key] = {"type": "invalidProperties"}
                continue
            node = {
                "id": self._new_id("n"),
                "name": want.get("name"),
                "parentId": want.get("parentId"),
                "nodeType": want.get("nodeType", "file"),
                "blobId": want.get("blobId"),
                "type": want.get("type"),
                "size": len(self.blobs[want["blobId"]])
                if want.get("blobId") in self.blobs
                else None,
                "modified": datetime.now(timezone.utc).isoformat(),
            }
            self.nodes[node["id"]] = node
            res["created"][key] = {"id": node["id"]}
        updates = args.get("update") or {}
        for nid, patch in updates.items():
            node = self.nodes.get(nid)
            if node is None:
                res.setdefault("notUpdated", {})[nid] = {"type": "notFound"}
                continue
            if "blobId" in patch:
                node["blobId"] = patch["blobId"]
                node["size"] = len(self.blobs.get(patch["blobId"], b""))
            if "type" in patch:
                node["type"] = patch["type"]
            node["modified"] = datetime.now(timezone.utc).isoformat()
            res["updated"][nid] = node
        destroys = args.get("destroy") or []
        for nid in destroys:
            node = self.nodes.get(nid)
            if node is None:
                continue
            children = [n for n in self.nodes.values() if n.get("parentId") == nid]
            if node["nodeType"] == "directory" and children:
                res.setdefault("notDestroyed", {})[nid] = {"type": "stillHasChildren"}
                continue
            del self.nodes[nid]
            res["destroyed"].append(nid)
        return res


def make_handler(server):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silence
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _base(self):
            host = self.headers.get("Host", "127.0.0.1")
            return f"http://{host}"

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/jmap/session":
                return self._send(200, server.session(self._base()))
            parts = parsed.path.split("/")
            # /jmap/download/<aid>/<blob>/<name>
            if len(parts) >= 6 and parts[1] == "jmap" and parts[2] == "download":
                blob = parts[4]
                if blob in server.blobs:
                    return self._send(
                        200, server.blobs[blob], "application/octet-stream"
                    )
                return self._send(404, {"type": "notFound"})
            return self._send(404, {"type": "notFound"})

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path.startswith("/jmap/upload/"):
                ctype = self.headers.get("Content-Type", "application/octet-stream")
                blob = server._new_blob()
                server.blobs[blob] = body
                return self._send(
                    200,
                    {
                        "accountId": parsed.path.split("/")[3],
                        "blobId": blob,
                        "type": ctype,
                        "size": len(body),
                    },
                )
            if parsed.path == "/jmap":
                return self._send(200, server.jmap(json.loads(body)))
            return self._send(404, {"type": "notFound"})

    return Handler


@pytest.fixture()
def jmap():
    srv = MockJmapServer()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(srv))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield srv, base
    httpd.shutdown()


def run_engine(left, right_type, state, *extra, jmap_base=None):
    cmd = [
        sys.executable,
        ENGINE,
        "--left-remote",
        left,
        "--right-remote",
        "unused:",  # overridden by --right-type jmap
        "--right-type",
        right_type,
        "--state-dir",
        state,
        "--touch-grace",
        "1",
    ]
    if right_type == "jmap":
        cmd += [
            "--right-jmap-url",
            jmap_base,
            "--right-jmap-user",
            "u",
            "--right-jmap-password",
            "p",
            "--right-jmap-account",
            "freight@example.com",
        ]
    cmd += list(extra)
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr
    return p.stdout + p.stderr


def tree(root):
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            full = os.path.join(dirpath, f)
            out[os.path.relpath(full, root)] = open(full, "rb").read()
    return out


def write(root, rel, content):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(content)
    return path


def test_jmap_sync_keeps_names_clean(jmap, tmp_path):
    srv, base = jmap
    left = str(tmp_path / "l")
    right_dir = str(tmp_path / "r")
    state = str(tmp_path / "s")
    os.makedirs(left)
    os.makedirs(right_dir)
    os.makedirs(state)

    # files whose names need encoding on the wire
    write(left, "folder one/file two.txt", b"hello\n")
    write(left, "folder one/a b c.txt", b"abc\n")

    run_engine(left, "jmap", state, jmap_base=base)

    # names must be stored clean (no percent-encoding)
    names = sorted(n["name"] for n in srv.nodes.values())
    assert "folder one" in names
    assert "file two.txt" in names
    assert "a b c.txt" in names
    assert not any("%" in n for n in names)

    # second run: no-op
    out = run_engine(left, "jmap", state, jmap_base=base)
    assert "added 0" in out and "done: added 0" in out

    # deletion propagates left -> jmap
    os.unlink(os.path.join(left, "folder one", "a b c.txt"))
    run_engine(left, "jmap", state, jmap_base=base)
    names = [n["name"] for n in srv.nodes.values()]
    assert "a b c.txt" not in names
    assert "file two.txt" in names


def test_jmap_remote_change_comes_back(jmap, tmp_path):
    srv, base = jmap
    left = str(tmp_path / "l")
    state = str(tmp_path / "s")
    os.makedirs(left)
    os.makedirs(state)
    write(left, "seed.txt", b"seed\n")
    run_engine(left, "jmap", state, jmap_base=base)

    # a "remote user" creates a file directly through the JMAP API
    root_file = srv.by_name("seed.txt")[0]
    srv.put_file(
        "from remote side.txt", b"remote content\n", parent_id=root_file["parentId"]
    )

    run_engine(left, "jmap", state, jmap_base=base)
    assert tree(left)["from remote side.txt"] == b"remote content\n"
    # and the node still exists on the jmap side (not duplicated/renamed)
    assert "from remote side.txt" in [n["name"] for n in srv.nodes.values()]


def test_jmap_dry_run_changes_nothing(jmap, tmp_path):
    srv, base = jmap
    left = str(tmp_path / "l")
    state = str(tmp_path / "s")
    os.makedirs(left)
    os.makedirs(state)
    write(left, "x.txt", b"x\n")
    run_engine(left, "jmap", state, "--dry-run", jmap_base=base)
    assert srv.nodes == {}


def test_jmap_side_treated_as_untrusted(jmap, tmp_path):
    """Same-size edit on the jmap side must be detected via modified+sha1."""
    srv, base = jmap
    left = str(tmp_path / "l")
    state = str(tmp_path / "s")
    os.makedirs(left)
    os.makedirs(state)
    write(left, "same.txt", b"0123456789\n")  # 11 bytes
    run_engine(left, "jmap", state, jmap_base=base)
    # remote user rewrites the same-size file with different content
    node = srv.by_name("same.txt")[0]
    srv.blobs[node["blobId"]] = b"abcdefghij\n"  # 11 bytes
    node["size"] = 11
    # well past the 1 s touch-grace so the engine sees a real edit
    from datetime import timedelta

    node["modified"] = (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat()
    run_engine(left, "jmap", state, jmap_base=base)
    assert tree(left)["same.txt"] == b"abcdefghij\n"
