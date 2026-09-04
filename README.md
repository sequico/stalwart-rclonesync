# stalwart-rclonesync

**Two-way mirror between a Stalwart mail server "Files" area and pCloud — or
between any two [rclone](https://rclone.org) remotes.**

`stalwart-rclonesync` keeps a folder tree on a Stalwart server (group or
account file storage, exposed over WebDAV at `/dav/file/<account>`) in sync
with a pCloud folder, in both directions. It is a single dependency-free
Python CLI that drives **rclone** for every transfer, so each endpoint can be
replaced by any rclone-supported backend (local disk, S3, SFTP, WebDAV, …).

[![Release](https://img.shields.io/github/v/release/sequico/stalwart-rclonesync)](https://github.com/sequico/stalwart-rclonesync/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](pyproject.toml)
[![CI](https://github.com/sequico/stalwart-rclonesync/actions/workflows/ci.yml/badge.svg)](https://github.com/sequico/stalwart-rclonesync/actions/workflows/ci.yml)

---

## Why this tool exists

Generic WebDAV servers — Stalwart included — **cannot set file modification
times**: they ignore `X-OC-Mtime`/`Last-Modified` and stamp the receive time
instead. Because `rclone bisync` compares mtimes, pointing it at such a server
makes it copy every file back and forth **on every run, forever**.

`stalwart-rclonesync` solves this by keeping its own state file
(`state.json`: size + sha1 + observed timestamps) and copying a file **only
when its content actually differs**. A side that cannot preserve mtimes is
treated as "touched" by any mtime newer than our last write, and same-size
edits are confirmed by sha1 before anything is copied.

## Features

- **True two-way mirror** — creates, edits and deletes propagate in both
  directions; directory trees are mirrored including empty directories.
- **Safe by default**:
  - deletions propagate **only** when the other side is unchanged since the
    last sync; a concurrent edit wins and the deleted file is restored;
  - both-sides change → **conflict**: the newer version wins on both sides
    and the losing version is kept as `<name>.conflict-<ts><ext>` on **both**
    sides (no silent data loss);
  - `--dry-run` previews every action.
- **No server-side agent** — talks to Stalwart only through its public WebDAV
  endpoint (same origin as your webmail) and to pCloud through rclone.
- **Runs anywhere** — Linux/macOS/BSD, as a systemd timer, cron job or
  container; no root required; no Python dependencies.
- **Operational** — flock-based mutual exclusion, atomic state updates,
  meaningful exit codes, plain logs.

## How it works

```
                 ┌────────────────────┐        ┌─────────────────────┐
   pCloud  ◀───► │                    │  HTTP  │                     │
   (or any       │      rclone        │ WebDAV │  Stalwart Files     │
   rclone        │   (lsjson/copyto/  ├────────►  /dav/file/<group>  │
   remote)       │    deletefile)     │        │  (group Files area) │
                 │                    │        │                     │
                 └─────────┬──────────┘        └─────────────────────┘
                           │
                 state.json (size+sha1+times)   ← our source of truth
```

Each run: list both sides → compare against the state file → copy only real
differences → update state atomically.

## Requirements

- Python **3.9+** (standard library only)
- [rclone](https://rclone.org/downloads/) **1.60+** in `$PATH` (any recent
  version is fine)
- Network access to both endpoints

## Installation

```bash
# from this repository
pip install .                # or: pipx install .
# or without pip — the file is self-contained:
curl -LO https://github.com/sequico/stalwart-rclonesync/releases/latest/download/stalwart_rclonesync.py
chmod +x stalwart_rclonesync.py
```

Verify:

```bash
stalwart-rclonesync --version
```

## Quick start (Stalwart + pCloud)

### 1. Prepare the Stalwart side

1. Create a **dedicated account** that is a member of the group whose Files
   area you want to sync. (Stalwart groups cannot hold credentials, so the
   sync authenticates as a group member — this also keeps your personal
   account out of automation.)
2. Give the account an app password (Stalwart WebUI → Account → App
   Passwords), or a strong dedicated password.
3. Register the rclone remote:

   ```bash
   rclone config create freight-dav webdav \
     url   https://mail.example.com/dav/file/freight@example.com \
     vendor other \
     user  freight-sync@example.com \
     pass  "$(rclone obscure 'the-app-password')"
   ```

4. Create the pCloud target folder (e.g. `MailSync/freight`), then run:

   ```bash
   stalwart-rclonesync \
     --left-remote  'pcloud:MailSync/freight' \
     --right-remote 'freight-dav:' \
     --right-untrusted-mtime \
     --state-dir     /var/lib/stalwart-rclonesync \
     --dry-run                       # preview first!
   ```

   Remove `--dry-run` when the plan looks right. The first real run mirrors
   the union of both sides (nothing is deleted on a first run with an empty
   state).

## CLI reference

| Flag | Description |
|---|---|
| `--left-remote REMOTE` | rclone remote of side A (e.g. `pcloud:MailSync/freight` or `/srv/sync`) |
| `--right-remote REMOTE` | rclone remote of side B (e.g. `freight-dav:`) |
| `--left-untrusted-mtime` | side A cannot preserve client mtimes (generic WebDAV) |
| `--right-untrusted-mtime` | side B cannot preserve client mtimes (**set for Stalwart**) |
| `--ignore-prefix PATH` | path prefix excluded on **both** sides (repeatable) |
| `--state-dir DIR` | state file + lock location (default `./.sync-state`) |
| `--touch-grace SECONDS` | ignore mtime "touches" within N seconds of our own write (default 5) |
| `--dry-run` | print the plan, change nothing |
| `--log FILE` | append logs to FILE (default: stderr) |
| `--verbose` | debug-level logging |
| `--version` | show version |

Exit codes: `0` = ok · `1` = failed (nothing changed) · `2` = another
instance already running.

## Sync semantics (read this)

| Situation | What happens |
|---|---|
| New file on one side | Copied to the other side |
| Edited file (size or content differs) | Newer content copied to the other side |
| Deleted on one side, other side unchanged | Delete propagated (true mirror) |
| Deleted on one side, other side **edited** | Edited file wins and is restored on the deleted side |
| Edited on **both** sides, same content | No-op |
| Edited on **both** sides, different content | **Conflict**: newer side wins on both sides; loser kept as `<name>.conflict-<ts><ext>` on both sides (ties → left side) |
| Path listed in `--ignore-prefix` | Ignored on both sides, never touched |

Notes:

- On a side flagged `--*-untrusted-mtime`, an edit is only noticed when its
  mtime is newer than our own last write **plus** `--touch-grace`. Edits that
  happen within that grace window with identical size are still caught on the
  next run **if** their content hash differs; treat the window as a few
  seconds of best-effort latency, not a data-loss risk.
- The state file is the source of truth for "what did we already mirror?".
  Back it up with the rest of your config; losing it only means the next run
  treats everything as new on both sides (no deletions happen without state).

## Scheduling

### systemd (recommended on servers)

Copy `contrib/stalwart-rclonesync.service` and
`contrib/stalwart-rclonesync.timer` to `/etc/systemd/system/`, adjust the
`ExecStart` flags and:

```bash
systemctl daemon-reload
systemctl enable --now stalwart-rclonesync.timer
```

### cron

```cron
*/15 * * * *  /usr/local/bin/stalwart-rclonesync --left-remote ... --right-remote ...
```

### Docker

A minimal image is provided:

```bash
docker build -t stalwart-rclonesync .
docker run --rm -v "$PWD/state:/state" stalwart-rclonesync \
  --left-remote ... --right-remote ... --state-dir /state
```

### Failure alerting

`contrib/run-with-alert.sh` shows a wrapper that emails on failure; adapt it
to your mail setup.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, testing and the PR process.

```bash
pip install -e '.[dev]'
pytest
```

The test-suite runs the real engine against local directories (no network
needed) — add/remove/edit/conflict/dry-run scenarios.

## Known limitations

- Per-file upload size is capped by the server (Stalwart FileStorage
  `maxSize`, default **25 MB**). Raise it server-side for larger documents.
- The Stalwart side must be reachable over WebDAV; the sync does not use the
  JMAP API.
- Not a real-time sync: it runs on a schedule (15 min in the examples).
- Conflict resolution is **newest-wins**, not a three-way merge.

## Roadmap (ideas, PRs welcome)

- JMAP backend option (no WebDAV required)
- Checksum-only mode for mtime-preserving remotes
- Setup wizard (`--setup` that creates the Stalwart account + rclone remote)
- Windows support notes / installer

## License

[MIT](LICENSE) © 2026 [sequico](https://github.com/sequico)

## Security

Found a problem? See [SECURITY.md](SECURITY.md) for how to report it.
