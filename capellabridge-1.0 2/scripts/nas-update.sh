#!/bin/sh
# Copy to the NAS or schedule these commands using Synology Task Scheduler.
# For a default installation use: /bin/sh /volume1/docker/capellabridge/scripts/nas-update.sh
set -eu
cd /volume1/docker/capellabridge
docker compose pull
docker compose up -d --remove-orphans
docker image prune -f
