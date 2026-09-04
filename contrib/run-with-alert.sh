#!/usr/bin/env bash
# Example wrapper: run the sync and email an alert on failure.
# Adapt the mail command / credentials to your environment.
set -u
LOG=/var/log/stalwart-rclonesync.log
if /usr/local/bin/stalwart-rclonesync \
      --left-remote  pcloud:StalwartSync \
      --right-remote freight-dav: \
      --right-untrusted-mtime \
      --ignore-prefix 1 \
      --state-dir /var/lib/stalwart-rclonesync \
      --log "$LOG"; then
    exit 0
fi
rc=$?
echo "stalwart-rclonesync failed (rc=$rc) at $(date -Is), tail of $LOG:" \
    | mail -s "[ALERT] file sync failed" ops@example.com
tail -n 20 "$LOG" | mail -s "[ALERT] file sync failed - log" ops@example.com
exit "$rc"
