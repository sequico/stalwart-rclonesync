# Minimal image: python + rclone + the sync CLI.
# Runs as non-root user "rclonesync" (uid 1000).
# Mount your rclone config and a state dir; pass remotes as args.
FROM python:3.12-slim

# rclone is pinned to a specific release for reproducible builds
# (checksums: https://github.com/rclone/rclone/releases/tag/v1.75.0)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl passwd \
    && curl -o /tmp/rclone.deb -L \
        https://github.com/rclone/rclone/releases/download/v1.75.0/rclone-v1.75.0-linux-amd64.deb \
    && dpkg -i /tmp/rclone.deb \
    && rm /tmp/rclone.deb \
    && apt-get purge -y curl && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Non-root runtime user; /state and the rclone config dir belong to it.
# Host mounts must be readable/writable by uid 1000 (see README).
RUN useradd --create-home --shell /usr/sbin/nologin --uid 1000 rclonesync \
    && install -d -o rclonesync -g rclonesync /home/rclonesync/.config/rclone /state

COPY stalwart_rclonesync.py /usr/local/bin/stalwart_rclonesync.py
RUN chmod +x /usr/local/bin/stalwart_rclonesync.py

ENV HOME=/home/rclonesync
USER rclonesync

# rclone config (optional mount point) and sync state
VOLUME ["/home/rclonesync/.config/rclone", "/state"]

ENTRYPOINT ["python3", "/usr/local/bin/stalwart_rclonesync.py"]
