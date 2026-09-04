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
```

Tests exercise the engine end-to-end with local folders: add, delete,
same-size edit on an untrusted-mtime side, conflicts (including the `.conflict`
copies on both sides), `--ignore-prefix` and `--dry-run`.

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

1. Bump the version in `pyproject.toml` and `stalwart_rclonesync.py`.
2. Move CHANGELOG entries from "Unreleased" to the new version.
3. Tag and release via GitHub (`gh release create vX.Y.Z --generate-notes`).
4. The CI badge and release badges in the README update automatically.
