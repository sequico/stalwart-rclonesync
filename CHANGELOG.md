# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Documentation generalized: the tool is described as a two-way mirror
  between **any two rclone remotes**; Stalwart WebDAV and pCloud are the
  primary examples, not the only supported endpoints.
- Repository description and topics updated accordingly (all content is in
  English).

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

[0.3.0]: https://github.com/sequico/stalwart-rclonesync/releases/tag/v0.3.0
