# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0] - 2026-09-12

### Added

- **One-way sync**: `--direction left-to-right` / `--direction right-to-left`
  (the default stays `both`, i.e. the existing two-way mirror). The source
  side is authoritative and is never written to or deleted from; the
  destination side is only written to. Creates and edits are mirrored over,
  and a destination file that went missing there is restored from the source.
- **Deletion control on the destination** (one-way only):
  - `--delete-dest` — also delete on the destination the files the source no
    longer has. Without it such files are kept, logged as `kept`, and stay
    tracked, so enabling the flag later still cleans them up.
  - `--delete-extra` — additionally delete destination files that were never
    on the source, i.e. make the destination an exact replica (implies
    `--delete-dest`). Without either flag a destination folder that already
    held content is never emptied.
  - Both flags are refused with `--direction both` (exit code 2): in two-way
    mode deletions already propagate on their own.
- One-way runs report their own summary counters, e.g.
  `done: added 2, updated 0, deleted 1, kept 0, extra 3, one-way left-to-right with delete-dest`.
- Tests: one-way scenarios in `tests/test_engine.py` (both directions,
  idempotence with an untrusted destination, restore after a destination-side
  loss, keep/delete semantics, extras, dry-run, flag validation) and one in
  `tests/test_jmap_transport.py` (deletion propagation through the JMAP
  transport against the mock server).

### Changed

- `--help`, the README and this changelog document the new mode; the CLI
  reference and the sync-semantics section now separate two-way from one-way
  rules.
- A README note on the state file: switching between `both` and a one-way
  direction on the same `--state-dir` triggers one re-examination pass (use a
  separate state dir per mode if you want clean counters).
- Nothing changes for existing invocations: `--direction both` is the default
  and the two-way code path is untouched.

[0.5.0]: https://github.com/sequico/stalwart-rclonesync/releases/tag/v0.5.0

## [0.4.0] - 2026-09-04

### Added

- **JMAP transport for a Stalwart side**: `--left-type jmap` /
  `--right-type jmap` with `--*-jmap-url`, `--*-jmap-user`,
  `--*-jmap-password` and `--*-jmap-account`. The engine talks to the
  native Stalwart FileNode API (JSON over HTTPS) with a dependency-free
  Python standard-library client: recursive listing, create/update/delete
  of files and folders, blob upload/download.
- **Clean file/folder names on the JMAP side**: names are stored as-is
  (spaces, unicode, brackets ...). This avoids the Stalwart WebDAV quirk
  where names written through the WebDAV binding are stored URL-encoded
  (JMAP clients then show `%20` in names); the rclone/WebDAV transport is
  unchanged for setups that do not hit that issue.
- Test suite for the JMAP transport against a local **mock JMAP server**
  (stdlib only): clean names round-trip, both directions, dry-run, and
  same-size edits on an untrusted (jmap) side.

### Changed

- Transports are now selectable **per side** (`--left-type`/`--right-type`,
  default `rclone`); existing invocations keep working unchanged.
- A `jmap` side is always treated as untrusted-mtime (the server owns the
  `modified` timestamp), like a generic WebDAV side.
- README, CLI help and this changelog updated; `--left-remote` /
  `--right-remote` are only required for rclone-type sides.

### Notes

- The WebDAV name-encoding behaviour was reproduced against Stalwart
  `0.16.20` (folder `test dir` created via MKCOL is stored as FileNode
  `test%20dir`); the JMAP transport is the recommended way to sync a
  Stalwart Files area until that is addressed server-side.

[0.4.0]: https://github.com/sequico/stalwart-rclonesync/releases/tag/v0.4.0

## [0.3.1] - 2026-09-04

### Fixed

- Log lines were printed **twice** on stderr when `--log` was not set (two
  handlers attached to the root logger); the extra handler was removed and a
  regression test added.
- The example systemd service referenced an `EnvironmentFile` whose absence
  prevented the unit from starting; the file was never used because all flags
  are literal, so it was removed.

### Changed

- **Single source of truth for the version**: `VERSION` in
  `stalwart_rclonesync.py`; `pyproject.toml` reads it dynamically. Bumping a
  release now touches exactly one file.
- Packaging metadata modernized (PEP 639): `license = "MIT"` SPDX expression,
  `setuptools>=77`.
- Linting/formatting with **ruff** added (config in `pyproject.toml`, dev
  extra, CI job `Lint (ruff)`); the code base was linted and reformatted.
- Releases now carry the standalone script and its checksums: the new
  `Release` workflow creates the GitHub release on tag push and attaches
  `stalwart_rclonesync.py` + `SHA256SUMS.txt`, and the new `Docker` workflow
  publishes a pre-built image to GHCR (`:<version>` + `:latest`). The README
  install/verify instructions were updated accordingly (the previous
  "download from the latest release" link pointed at a release without
  assets → 404).
- Dockerfile: rclone **pinned to v1.75.0** for reproducible builds; the image
  now runs as non-root user `rclonesync` (uid 1000); systemd timer gained
  `RandomizedDelaySec=60`. CI also builds the Docker image on every push/PR to
  catch Dockerfile regressions.
- Repository housekeeping: issue forms (bug report / feature request), pull
  request template, Code of Conduct, Dependabot for GitHub Actions, and
  branch protection on `main` requiring the CI checks to pass.
- Documentation generalized: the tool is described as a two-way mirror
  between **any two rclone remotes**; Stalwart WebDAV and pCloud are the
  primary examples, not the only supported endpoints.
- README quick start now documents creating the **pCloud (OAuth) rclone
  remote** from scratch (interactive and headless flows) and uses
  `pcloud:StalwartSync` as the example folder, matching the reference
  deployment.

### Added

- Security workflow: CodeQL analysis + `pip-audit` on every push, PR and
  weekly schedule.

## [0.3.0] - 2026-09-04

### Added

- Initial public release.
- Two-way mirror between two rclone remotes (typical: Stalwart WebDAV Files
  area ↔ pCloud), driven by a persistent state file (size + sha1 +
  timestamps).
- `--*-untrusted-mtime` support for sides that cannot preserve file
  modification times (generic WebDAV, e.g. Stalwart), with a configurable
  `--touch-grace` window and sha1 confirmation of same-size edits.
- Conflict handling: newest version wins on both sides; losing versions kept
  as `<name>.conflict-<ts><ext>` on both sides.
- Deletion policy: deletions propagate only when the other side is unchanged;
  concurrent edits win and are restored.
- `--ignore-prefix` (repeatable), `--dry-run`, `--log`, `--verbose`,
  `--state-dir`, flock mutual exclusion, atomic state updates.
- Packaging: `pyproject.toml`, console script `stalwart-rclonesync`.
- `contrib/`: systemd service + timer, failure-alert wrapper.
- Test suite running the real engine against local directories.
- CI: GitHub Actions (Python 3.9–3.13, rclone from apt).

[0.3.1]: https://github.com/sequico/stalwart-rclonesync/releases/tag/v0.3.1
[0.3.0]: https://github.com/sequico/stalwart-rclonesync/releases/tag/v0.3.0
