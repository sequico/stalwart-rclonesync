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


def run_one_way(left, right, state, direction, *extra):
    """Run the engine in one-way mode (local dirs: both sides trusted)."""
    return run_engine(
        left, right, state, "--direction", direction, *extra, untrusted=None
    )


def test_one_way_left_to_right_mirrors_source_only(tmp_path):
    left, right, state = setup(tmp_path)
    write(left, "a.txt", b"alpha\n")
    write(left, "sub/b.txt", b"beta\n")
    write(right, "dest-only.txt", b"keep me\n")  # pre-existing on the destination
    out = run_one_way(left, right, state, "left-to-right")
    t_left, t_right = tree(left), tree(right)
    assert t_right["a.txt"] == b"alpha\n"
    assert t_right["sub/b.txt"] == b"beta\n"
    assert t_right["dest-only.txt"] == b"keep me\n"  # never deleted, never pushed back
    assert t_left == {"a.txt": b"alpha\n", "sub/b.txt": b"beta\n"}
    assert "extra 1" in out, out
    # a second run re-copies nothing (no mtime ping-pong)
    out = run_one_way(left, right, state, "left-to-right")
    assert "done: added 0, updated 0, deleted 0" in out, out


def test_one_way_is_idempotent_with_untrusted_destination(tmp_path):
    """An untrusted destination (server-stamped mtime) must not ping-pong."""
    left, right, state = setup(tmp_path)
    write(left, "a.txt", b"alpha\n")
    run_engine(left, right, state, "--direction", "left-to-right")
    out = run_engine(left, right, state, "--direction", "left-to-right")
    assert "done: added 0, updated 0, deleted 0" in out, out
    assert tree(left) == tree(right)


def test_one_way_destination_edits_never_come_back(tmp_path):
    left, right, state = setup(tmp_path)
    write(left, "a.txt", b"v1\n")
    run_one_way(left, right, state, "left-to-right")
    now = time.time_ns()
    # a user edits the file on the destination and adds another one
    write(right, "a.txt", b"dest edit\n", mtime_ns=now + 5_000_000_000)
    write(right, "dest-new.txt", b"n\n", mtime_ns=now + 5_000_000_000)
    run_one_way(left, right, state, "left-to-right")
    assert tree(left) == {"a.txt": b"v1\n"}
    # the source stays authoritative: its next edit wins on the destination
    write(left, "a.txt", b"v2\n", mtime_ns=now + 10_000_000_000)
    run_one_way(left, right, state, "left-to-right")
    assert tree(right)["a.txt"] == b"v2\n"


def test_one_way_restores_file_lost_on_destination(tmp_path):
    left, right, state = setup(tmp_path)
    write(left, "a.txt", b"alpha\n")
    run_one_way(left, right, state, "left-to-right")
    os.unlink(os.path.join(right, "a.txt"))  # e.g. lost server-side
    out = run_one_way(left, right, state, "left-to-right")
    assert tree(right)["a.txt"] == b"alpha\n"
    assert "restore a.txt left->right (missing there)" in out, out


def test_one_way_without_delete_dest_keeps_source_deletion(tmp_path):
    left, right, state = setup(tmp_path)
    write(left, "bye.txt", b"bye\n")
    run_one_way(left, right, state, "left-to-right")
    os.unlink(os.path.join(left, "bye.txt"))
    out = run_one_way(left, right, state, "left-to-right")
    assert tree(right)["bye.txt"] == b"bye\n"
    assert "kept 1" in out, out
    # the file stays tracked, so enabling deletion later still cleans it up
    out = run_one_way(left, right, state, "left-to-right", "--delete-dest")
    assert "delete bye.txt on right (deleted on left)" in out, out
    assert tree(right) == {}


def test_one_way_delete_dest_propagates_source_deletion(tmp_path):
    left, right, state = setup(tmp_path)
    write(left, "gone.txt", b"x\n")
    write(left, "stay.txt", b"y\n")
    run_one_way(left, right, state, "left-to-right", "--delete-dest")
    os.unlink(os.path.join(left, "gone.txt"))
    run_one_way(left, right, state, "left-to-right", "--delete-dest")
    assert tree(right) == {"stay.txt": b"y\n"}


def test_one_way_delete_extra_replicates_the_source(tmp_path):
    left, right, state = setup(tmp_path)
    write(left, "a.txt", b"alpha\n")
    write(right, "dest-only.txt", b"keep me\n")
    # --delete-dest alone leaves a file that was never on the source alone
    run_one_way(left, right, state, "left-to-right", "--delete-dest")
    assert tree(right)["dest-only.txt"] == b"keep me\n"
    # --delete-extra (which implies --delete-dest) makes an exact replica
    run_one_way(left, right, state, "left-to-right", "--delete-extra")
    assert tree(right) == {"a.txt": b"alpha\n"}


def test_one_way_right_to_left(tmp_path):
    left, right, state = setup(tmp_path)
    write(right, "from-right.txt", b"one\n")
    write(left, "from-left.txt", b"unsynced\n")
    run_one_way(left, right, state, "right-to-left")
    assert tree(left)["from-right.txt"] == b"one\n"
    assert tree(left)["from-left.txt"] == b"unsynced\n"
    assert tree(right) == {"from-right.txt": b"one\n"}  # never copied back
    os.unlink(os.path.join(right, "from-right.txt"))
    run_one_way(left, right, state, "right-to-left", "--delete-dest")
    assert "from-right.txt" not in tree(left)


def test_one_way_dry_run_changes_nothing(tmp_path):
    left, right, state = setup(tmp_path)
    write(left, "a.txt", b"alpha\n")
    write(right, "dest-only.txt", b"keep me\n")
    run_one_way(left, right, state, "left-to-right", "--delete-extra", "--dry-run")
    assert tree(right) == {"dest-only.txt": b"keep me\n"}
    assert tree(left) == {"a.txt": b"alpha\n"}


def test_delete_dest_requires_one_way_direction(tmp_path):
    left, right, state = setup(tmp_path)
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
            "--delete-dest",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert p.returncode == 2, p.stdout + p.stderr
    assert "--delete-dest" in p.stderr and "--direction" in p.stderr


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
