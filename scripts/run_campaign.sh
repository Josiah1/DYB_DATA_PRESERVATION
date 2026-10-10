#!/usr/bin/env bash
# Authorized 2026-09-29 single-disk campaign; resumable with the same command.
set -euo pipefail
PROJECT_ROOT=$(cd "$(dirname "$0")/.." && pwd)
DISK_UUID=2A4F48F8BDFEE355
PYTHON="$HOME/TOOLs/install/python-current/bin/python3"
MOUNT_ROOT=$(findmnt -J -S "UUID=$DISK_UUID" -o TARGET | "$PYTHON" -c \
  'import json, sys; mounts = json.load(sys.stdin)["filesystems"]; assert len(mounts) == 1; print(mounts[0]["target"])')
exec "$PYTHON" -u "$PROJECT_ROOT/preserve.py" run \
  --alias dayabay1 \
  --remote-code-root /scratchfs2/dyw/lijj16/DYB_DATA_PRESERVATION/code \
  --remote-stage-root /scratchfs2/dyw/lijj16/DYB_DATA_PRESERVATION/staging \
  --local-root "$MOUNT_ROOT/DYB_DATA_PRESERVATION/data" \
  --mount-root "$MOUNT_ROOT" --mount-uuid "$DISK_UUID" \
  --input-source mini --skip-missing-minis --ad-selection on \
  --max-jobs 300 --max-staged-gib 400 \
  --hold-run P17B/EH1/run51237 \
  --hold-run P17B/EH1/run51243 --hold-run P17B/EH1/run51250 \
  --drain-staging \
  --execute --confirm-start START-DYB-PRESERVATION \
  --delete-remote-after-sync --confirm-remote-delete DELETE-VERIFIED-RUN "$@"
