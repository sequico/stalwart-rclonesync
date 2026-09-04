# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
