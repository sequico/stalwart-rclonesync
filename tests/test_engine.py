"""End-to-end tests: run the real engine against local directories.

Requires rclone in $PATH (any recent version). No network access needed.

Timing-sensitive scenarios use explicit nanosecond mtimes (os.utime) instead
of sleeps: this is deterministic on any runner AND exercises high-precision
timestamps (more than 6 fractional digits), which Python <3.11 cannot parse
with datetime.fromisoformat().
"""

import os
import subprocess
import sys
import time
from shutil import which

import pytest

pytestmark = pytest.mark.skipif(which("rclone") is None, reason="rclone not installed")

ENGINE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "stalwart_rclonesync.py"
)

GRACE = "1"
RUN_LOGS = []


def run_engine(left, right, state, *extra, untrusted="--right-untrusted-mtime"):
    cmd = [
        sys.executable,
        ENGINE,
        "--left-remote",
        left,
        "--right-remote",
        right,
        "--state-dir",
        state,
        "--touch-grace",
        GRACE,
        "--verbose",
    ]
    if untrusted:
        cmd.append(untrusted)
    cmd += list(extra)
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    RUN_LOGS.append(p.stdout + p.stderr)
    assert p.returncode == 0, p.stdout + p.stderr
    return p.stdout + p.stderr


def tree(root):
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            full = os.path.join(dirpath, f)
            rel = os.path.relpath(full, root)
            with open(full, "rb") as fh:
                out[rel] = fh.read()
    return out


def write(root, rel, content, mtime_ns=None):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(content)
    if mtime_ns is not None:
        os.utime(path, ns=(mtime_ns, mtime_ns))
    return path


def setup(tmp_path):
    left = str(tmp_path / "l")
    right = str(tmp_path / "r")
    state = str(tmp_path / "s")
    os.makedirs(left)
    os.makedirs(right)
    os.makedirs(state)
    return left, right, state


def test_add_mirrors_both_directions(tmp_path):
    left, right, state = setup(tmp_path)
    write(left, "a.txt", b"alpha\n")
    write(left, "sub/b.txt", b"beta\n")
    run_engine(left, right, state)
    assert tree(left) == tree(right)
    write(right, "from-right.txt", b"right\n")
    run_engine(left, right, state)
    assert tree(left) == tree(right)


def test_delete_propagates_when_other_side_unchanged(tmp_path):
    left, right, state = setup(tmp_path)
    write(left, "a.txt", b"alpha\n")
    run_engine(left, right, state)
    os.unlink(os.path.join(left, "a.txt"))
    run_engine(left, right, state)
    assert tree(left) == tree(right) == {}


def test_delete_vs_concurrent_edit_keeps_file(tmp_path):
    left, right, state = setup(tmp_path)
    write(left, "keep.txt", b"v1\n")
    run_engine(left, right, state)
    now = time.time_ns()
    os.unlink(os.path.join(left, "keep.txt"))
    write(right, "keep.txt", b"edited after deletion\n", mtime_ns=now + 10_000_000_000)
    run_engine(left, right, state)
    assert tree(left) == tree(right)
    assert tree(left)["keep.txt"] == b"edited after deletion\n"


def test_same_size_edit_on_untrusted_side_pushes_back(tmp_path):
    left, right, state = setup(tmp_path)
    write(left, "a.txt", b"same-size line\n")  # 14 bytes
    run_engine(left, right, state)
    # same size, different content, mtime far in the future (ns precision)
    write(
        right, "a.txt", b"same-size LINES\n", mtime_ns=time.time_ns() + 10_000_000_000
    )
    run_engine(left, right, state)
    logs = "\n".join(RUN_LOGS[-2:])
    assert tree(left) == tree(right), logs
    assert tree(left)["a.txt"] == b"same-size LINES\n", logs


def test_conflict_keeps_winner_and_loser_on_both_sides(tmp_path):
    left, right, state = setup(tmp_path)
    write(left, "c.txt", b"v1\n")
    run_engine(left, right, state)
    now = time.time_ns()
    # left edited at +3 s, right edited at +8 s -> right clearly newer
    write(left, "c.txt", b"left edit\n", mtime_ns=now + 3_000_000_000)
    write(right, "c.txt", b"right edit newer\n", mtime_ns=now + 8_000_000_000)
    run_engine(left, right, state)
    logs = "\n".join(RUN_LOGS[-2:])
    t = tree(left)
    assert t == tree(right), logs  # both sides identical
    assert t["c.txt"] == b"right edit newer\n", logs  # newest wins
    conflicts = [k for k in t if ".conflict-" in k]
    assert len(conflicts) == 1  # loser preserved
    assert t[conflicts[0]] == b"left edit\n"
    # convergence: a second run changes nothing
    run_engine(left, right, state)
    assert tree(left) == t


def test_ignore_prefix_excluded_on_both_sides(tmp_path):
    left, right, state = setup(tmp_path)
    write(left, "1/artifact.txt", b"x\n")
    write(left, "real.txt", b"y\n")
    run_engine(left, right, state, "--ignore-prefix", "1")
    assert "1/artifact.txt" not in tree(right)
    assert tree(right)["real.txt"] == b"y\n"


def test_dry_run_changes_nothing(tmp_path):
    left, right, state = setup(tmp_path)
    write(left, "a.txt", b"alpha\n")
    run_engine(left, right, state, "--dry-run")
    assert tree(right) == {}


def test_log_lines_not_duplicated_on_stderr(tmp_path):
    """Regression: without --log every line used to be printed twice."""
    left, right, state = setup(tmp_path)
    write(left, "a.txt", b"alpha\n")
    p = subprocess.run(
        [
            sys.executable,
            ENGINE,
            "--left-remote",
            left,
            "--right-remote",
            right,
            "--state-dir",
            state,
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert p.returncode == 0, p.stderr
    assert p.stderr.count("INFO listing ") == 1
    assert p.stderr.count("INFO done:") == 1
