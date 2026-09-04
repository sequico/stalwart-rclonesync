# Contributing to stalwart-rclonesync

Thanks for your interest! This project is small on purpose: a single,
dependency-free Python file that drives rclone. Keep that spirit.

## Ground rules

- **No new runtime dependencies.** The CLI must stay runnable with the Python
  standard library plus an external `rclone` binary.
- **Safety first.** The tool mirrors deletions and resolves conflicts; any
  change to those semantics must keep the guarantees in the README:
  no silent data loss, no destructive first run, one instance at a time.
- **Backwards-compatible state.** `state.json` is user data; don't break
  existing state files without a migration path and a changelog entry.

## Setup

```bash
git clone https://github.com/sequico/stalwart-rclonesync.git
cd stalwart-rclonesync
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
```

You also need [rclone](https://rclone.org/downloads/) in `$PATH` — the test
suite runs the real engine against local directories (no network access).

## Running the tests

```bash
pytest
ruff check .
ruff format --check .
```

Tests exercise the engine end-to-end with local folders: add, delete,
same-size edit on an untrusted-mtime side, conflicts (including the `.conflict`
copies on both sides), `--ignore-prefix`, `--dry-run` and a stderr-logging
regression test. Ruff keeps the code linted and consistently formatted; CI
enforces both on every push and PR.

**Test on the lowest supported Python too.** The CI matrix covers 3.9-3.13;
before Python 3.11, `datetime.fromisoformat()` rejects timestamps with more
than 6 fractional digits (rclone emits nanoseconds), which is why the engine
has a manual ISO-8601 fallback. If you touch timestamp handling, run the
suite with Python 3.9/3.10 as well (e.g. via a
[python-build-standalone](https://github.com/astral-sh/python-build-standalone)
build) — a green result on 3.12+ alone is not enough.

## Making changes

1. Create a branch: `git switch -c feat/your-change`.
2. Make the change, add tests that cover it.
3. Run `pytest` locally until green.
4. Update the README if user-facing behaviour changed, and add a
   `CHANGELOG.md` entry under "Unreleased".
5. Open a pull request with a clear description; mention what you tested.

## Commit style

- Imperative subject line, ≤ 72 characters (`Fix conflict copy naming`).
- One logical change per commit.
- No generated files (`.pyc`, `__pycache__/`, `.sync-state/`).

## Release process (maintainers)

1. Bump `VERSION` in `stalwart_rclonesync.py` — that is the **only** place;
   `pyproject.toml` reads the version from it dynamically.
2. Move CHANGELOG entries from "Unreleased" to the new version.
3. Commit, tag and push:

   ```bash
   git commit -m "stalwart-rclonesync X.Y.Z"
   git tag vX.Y.Z
   git push origin main --tags
   ```

4. The `Release` workflow creates the GitHub release (generated notes) and
   attaches `stalwart_rclonesync.py` + `SHA256SUMS.txt`; the `Docker`
   workflow publishes the image to GHCR (`:<version>` and `:latest`). The
   README badges update automatically. Edit the release body afterwards if
   you want hand-written notes.
