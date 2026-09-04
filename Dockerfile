# Minimal image: python + rclone + the sync CLI.
# Mount your rclone config and a state dir; pass remotes as args.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && curl -o /tmp/rclone.deb -L https://downloads.rclone.org/rclone-current-linux-amd64.deb \
    && dpkg -i /tmp/rclone.deb \
    && rm /tmp/rclone.deb \
    && apt-get purge -y curl && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY stalwart_rclonesync.py /usr/local/bin/stalwart_rclonesync.py
RUN chmod +x /usr/local/bin/stalwart_rclonesync.py

# rclone config (optional mount point) and sync state
VOLUME ["/root/.config/rclone", "/state"]

ENTRYPOINT ["python3", "/usr/local/bin/stalwart_rclonesync.py"]
