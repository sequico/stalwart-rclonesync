# stalwart-rclonesync

**Two-way, state-driven file mirror between two sides — each one reachable
through [rclone](https://rclone.org) or, for a Stalwart mail server, through
its native JMAP API.**

`stalwart-rclonesync` keeps two folder trees in sync in both directions. Its
original use case is a **Stalwart mail server** group/account "Files" area
mirrored with a **pCloud** folder. Each side has a selectable **transport**:

- `rclone` (default) — any rclone-supported backend: local disk, pCloud, S3,
  SFTP, WebDAV (Stalwart `/dav/file/<account>`), ...
- `jmap` (`--left-type jmap` / `--right-type jmap`) — the **native Stalwart
  FileNode API** (JSON over HTTPS), implemented with the Python standard
  library only. Recommended for a Stalwart side when file/folder names must
  be stored cleanly: Stalwart's WebDAV binding stores URL-encoded names in
  the JMAP FileNode (JMAP clients then display `%20` inside names), while the
  JMAP transport stores names as-is (spaces, unicode, ...).

[![Release](https://img.shields.io/github/v/release/sequico/stalwart-rclonesync)](https://github.com/sequico/stalwart-rclonesync/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](pyproject.toml)
[![CI](https://github.com/sequico/stalwart-rclonesync/actions/workflows/ci.yml/badge.svg)](https://github.com/sequico/stalwart-rclonesync/actions/workflows/ci.yml)
[![Security](https://github.com/sequico/stalwart-rclonesync/actions/workflows/security.yml/badge.svg)](https://github.com/sequico/stalwart-rclonesync/actions/workflows/security.yml)

---

## Why this tool exists

`rclone bisync` compares file modification times, and **generic WebDAV
servers cannot set them**: Stalwart included — they ignore
`X-OC-Mtime`/`Last-Modified` and stamp the receive time instead. Pointing
`bisync` at such a server makes it copy every file back and forth **on every
run, forever**.

`stalwart-rclonesync` solves this by keeping its own state file
(`state.json`: size + sha1 + observed timestamps) and copying a file **only
when its content actually differs**. A side that cannot preserve mtimes is
treated as "touched" by any mtime newer than our last write, and same-size
edits are confirmed by sha1 before anything is copied.

## Features

- **True two-way mirror** — creates, edits and deletions propagate in both
  directions; directory trees are mirrored including empty directories.
- **Two transports per side** — `rclone` (pCloud, local dir, S3, SFTP,
  WebDAV, ...) or `jmap` for a Stalwart Files area via the native JSON API
  (clean names, no `%20` artifacts). Selected per side with
  `--left-type`/`--right-type`; the default (`rclone`) keeps every existing
  invocation unchanged.
- **Safe by default**:
  - deletions propagate **only** when the other side is unchanged since the
    last sync; a concurrent edit wins and the deleted file is restored;
  - both-sides change → **conflict**: the newer version wins on both sides
    and the losing version is kept as `<name>.conflict-<ts><ext>` on **both**
    sides (no silent data loss);
  - `--dry-run` previews every action.
- **No server-side agent** — the Stalwart side is reached through its public
  WebDAV endpoint or its public JMAP API (same origin as your webmail); the
  other side uses what rclone already supports.
- **Runs anywhere** — Linux/macOS/BSD, as a systemd timer, cron job or
  container; no root required; no Python dependencies.
- **Operational** — flock-based mutual exclusion, atomic state updates,
  meaningful exit codes, plain logs.

## How it works

```
  side A  ◀───►  ┌──────────────────────┐  ◀───►  side B
 (rclone remote, │       engine         │        (rclone remote, or
  e.g. pCloud)   │  state compare /     │         Stalwart via JMAP)
                 │  transport layer     │
                 └──────────┬───────────┘
                            │
                  state.json (size+sha1+times)   ← our source of truth
```

Each run: list both sides → compare against the state file → copy only real
differences → update state atomically. Because the state file is the only
source of truth about "what did we already mirror?", both sides stay
consistent even when one of them cannot report reliable mtimes.

## Requirements

- Python **3.9+** (standard library only)
- [rclone](https://rclone.org/downloads/) **1.60+** in `$PATH` (any recent
  version is fine)
- rclone remotes configured for both endpoints (`rclone config`)
- Network access to both endpoints

## Installation

```bash
# from this repository
pip install .                # or: pipx install .
# or without pip — the file is self-contained (attached to every release):
curl -LO https://github.com/sequico/stalwart-rclonesync/releases/latest/download/stalwart_rclonesync.py
curl -LO https://github.com/sequico/stalwart-rclonesync/releases/latest/download/SHA256SUMS.txt
sha256sum -c SHA256SUMS.txt   # verify before running
chmod +x stalwart_rclonesync.py
```

Verify:

```bash
stalwart-rclonesync --version
```

## Quick start (Stalwart Files ↔ pCloud)

This is the original use case; adapt the remote names for any other pair of
remotes.

### 1. Prepare the remotes

**pCloud remote (first time only).** If you do not have a `pcloud:` remote
yet, create one with rclone's OAuth flow:

```bash
# on a machine with a browser:
rclone config            # n)ew remote -> name: pcloud -> type: pcloud
                         #   -> client_id/secret: leave empty (use rclone's)
                         #   -> use web browser to authenticate
# on a headless server: run the authorize step on a machine WITH a browser,
# then paste the token into the config on the server:
rclone authorize "pcloud"
```

Verify with `rclone lsd pcloud:`. Full reference:
[rclone.org/pcloud](https://rclone.org/pcloud/). The token is stored in
`rclone.conf` and can be revoked any time from the pCloud account settings.

### 2. Prepare the Stalwart side (WebDAV transport, rclone)


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

4. Create the pCloud target folder (e.g. `StalwartSync`), then run:

   ```bash
   stalwart-rclonesync \
     --left-remote  'pcloud:StalwartSync' \
     --right-remote 'freight-dav:' \
     --right-untrusted-mtime \
     --state-dir     /var/lib/stalwart-rclonesync \
     --dry-run                       # preview first!
   ```

   Remove `--dry-run` when the plan looks right. The first real run mirrors
   the union of both sides (nothing is deleted on a first run with an empty
   state).

### 3. Stalwart via JMAP (recommended)

If the Stalwart Files area is used from JMAP clients (e.g. the Waxwing
webmail Files view), prefer the native JMAP transport: names written through
the WebDAV binding are stored URL-encoded by Stalwart (clients then show
`%20` in names), while the JMAP transport stores them cleanly.

Only the pCloud remote is needed — no rclone WebDAV remote. Point the engine
at the JMAP API of the account **member** (same dedicated account as above):

```bash
stalwart-rclonesync   --left-remote  'pcloud:StalwartSync'   --right-type   jmap   --right-jmap-url        https://mail.example.com   --right-jmap-user       freight-sync@example.com   --right-jmap-password   'the-app-password'   --right-jmap-account    freight@example.com   --state-dir     /var/lib/stalwart-rclonesync   --dry-run                       # preview first!
```

The `--right-jmap-account` is the principal whose Files area you sync (the
group, e.g. `freight@example.com`); the user authenticates as a member. A
`jmap` side is always treated as untrusted-mtime (the server owns the
`modified` timestamp), so `--*-untrusted-mtime` is implied.

### 4. General case (any two remotes)

```bash
stalwart-rclonesync \
  --left-remote  '/srv/team-files' \      # local folder
  --right-remote 's3:my-bucket/team' \    # or any rclone remote
  --state-dir    /var/lib/stalwart-rclonesync
```

Only add `--*-untrusted-mtime` for a side whose server stamps its own mtimes
(generic WebDAV, e.g. Stalwart, Nextcloud/ownCloud behind plain WebDAV, …).

## CLI reference

| Flag | Description |
|---|---|
| `--left-remote REMOTE` | rclone remote of side A (required when `--left-type` is `rclone`) |
| `--right-remote REMOTE` | rclone remote of side B (required when `--right-type` is `rclone`) |
| `--left-type TYPE` / `--right-type TYPE` | transport per side: `rclone` (default) or `jmap` (Stalwart FileNode API) |
| `--left-jmap-url/--right-jmap-url URL` | JMAP base URL, e.g. `https://mail.example.com` |
| `--left-jmap-user/--right-jmap-user USER` | JMAP username (account member / app password) |
| `--left-jmap-password/--right-jmap-password PASS` | JMAP password or app password |
| `--left-jmap-account/--right-jmap-account NAME` | JMAP principal whose Files area is synced, e.g. `freight@example.com` |
| `--left-untrusted-mtime` | side A stamps its own mtime (generic WebDAV); implied for `jmap` |
| `--right-untrusted-mtime` | side B stamps its own mtime (generic WebDAV); implied for `jmap` |
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

Pre-built images are published to **GHCR** for every release:

```bash
docker pull ghcr.io/sequico/stalwart-rclonesync:0.3.1   # or :latest
```

To build from source instead, a minimal Dockerfile is provided: `rclone`
pinned to a specific version for reproducible builds, running as non-root
user `rclonesync` (uid **1000**).

```bash
docker build -t stalwart-rclonesync .
mkdir -p state && sudo chown 1000:1000 state   # writable by the container user
docker run --rm \
  -v "$PWD/state:/state" \
  -v ~/.config/rclone:/home/rclonesync/.config/rclone:ro \
  stalwart-rclonesync \
  --left-remote ... --right-remote ... --state-dir /state
```

The image runs as non-root uid **1000** (`rclonesync`); the rclone config is mounted
read-only and only needs to be readable by that uid. If your host user is not
uid 1000, add `--user $(id -u):$(id -g)` and make `state` writable by your own
uid instead. If the remotes are baked into the image instead (not
recommended), the config mount can be dropped.

### Failure alerting

`contrib/run-with-alert.sh` shows a wrapper that emails on failure; adapt it
to your mail setup.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, testing and the PR process.

```bash
pip install -e '.[dev]'
pytest
ruff check . && ruff format --check .
```

The test-suite runs the real engine against local directories (no network
needed) — add/remove/edit/conflict/dry-run scenarios. `ruff` keeps the code
linted and consistently formatted; CI enforces both.

## Known limitations

- Per-file upload size is capped by the server when one endpoint is a mail
  server (Stalwart FileStorage `maxSize`, default **25 MB**). Raise it
  server-side for larger documents.
- rclone-type sides need the `rclone` binary; `jmap` sides need none (Python
  standard library only).
- Not a real-time sync: it runs on a schedule (15 min in the examples).
- Conflict resolution is **newest-wins**, not a three-way merge.

## Roadmap (ideas, PRs welcome)

- `--checksum` mode for backends that expose hashes
- Setup wizard (`--setup` that creates the Stalwart account + rclone remotes)
- Windows support notes / installer

## License

[MIT](LICENSE) © 2026 [sequico](https://github.com/sequico)

## Security

Found a problem? See [SECURITY.md](SECURITY.md) for how to report it.
