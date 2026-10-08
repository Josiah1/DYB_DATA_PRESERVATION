#!/usr/bin/env bash
# One-shot recovery after the NTFS3 kernel crash. Never run against the old boot.
set -euo pipefail
if [[ $# != 4 ]]; then
  echo 'usage: recover_campaign_disk.sh OLD_BOOT_ID BASELINE_RUNS LAST_VERIFIED_RUN MOUNT_ROOT' >&2
  exit 2
fi
if [[ $(cat /proc/sys/kernel/random/boot_id) == "$1" ]]; then
  echo 'Refusing recovery before reboot: the old kernel still owns the disk.' >&2
  exit 2
fi
if [[ $4 != /* || $4 == / ]]; then
  echo 'MOUNT_ROOT must be an explicitly inspected absolute mount path.' >&2
  exit 2
fi
[[ $EUID == 0 ]]
PROJECT_ROOT=$(cd "$(dirname "$0")/.." && pwd)
RECOVERY_USER=$(stat -c %U "$PROJECT_ROOT")
RECOVERY_UID=$(id -u "$RECOVERY_USER")
RECOVERY_GID=$(id -g "$RECOVERY_USER")
DISK_UUID=2A4F48F8BDFEE355
MOUNT_ROOT=$4
DEVICE="/dev/disk/by-uuid/$DISK_UUID"
PYTHON="$(getent passwd "$RECOVERY_USER" | cut -d: -f6)/TOOLs/install/python-current/bin/python3"
cd "$PROJECT_ROOT"
as_user() { runuser -u "$RECOVERY_USER" -- env XDG_RUNTIME_DIR="/run/user/$RECOVERY_UID" "$@"; }
if findmnt -rn -S "UUID=$DISK_UUID" || mountpoint -q "$MOUNT_ROOT"; then
  echo 'Refusing to replace an existing mount; inspect its identity and driver.' >&2
  exit 1
fi
[[ $(blkid -s UUID -o value "$DEVICE") == "$DISK_UUID" ]]
timeout 60 ntfsfix --no-action "$DEVICE"
mkdir -p "$MOUNT_ROOT"
# Keep the FUSE daemon outside this one-shot service's cgroup, including on failure.
systemd-mount --collect --type=ntfs-3g \
  --options="rw,norecover,nosuid,nodev,uid=$RECOVERY_UID,gid=$RECOVERY_GID,umask=0022" "$DEVICE" "$MOUNT_ROOT"
as_user "$PYTHON" - "$MOUNT_ROOT" "$2" "$3" <<'PY'
import json
from pathlib import Path
import subprocess
import sys
import time
import preserve

mount, baseline, last = Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
root = mount / 'DYB_DATA_PRESERVATION/data'
preserve.validate_local_mount(root, mount, '2A4F48F8BDFEE355')
before, records = preserve.archive_progress(root)
assert before['completed_runs'] == baseline, 'saved-run baseline changed; inspect before restarting'
assert last in {'/'.join(r[k] for k in ('period', 'hall', 'stem')) for r in records}
print('Reverified last saved run:', preserve.verify_local_run(root / last), flush=True)
preserve.atomic_json(Path('build/reboot-recovery-before.json'), dict(
    archive=before, runs=['/'.join(r[k] for k in ('period', 'hall', 'stem')) for r in records]))
PY
systemctl start "user@$RECOVERY_UID.service"
as_user systemctl --user daemon-reload
as_user systemctl --user start dyb-preservation.service dyb-preservation-check.timer
# The local Codex process may end at reboot; leave an actual verification result, not just a submitted job.
as_user "$PYTHON" - "$MOUNT_ROOT" "$2" <<'PY'
import json
from pathlib import Path
import subprocess
import sys
import time
import preserve

root = Path(sys.argv[1]) / 'DYB_DATA_PRESERVATION/data'
baseline = int(sys.argv[2])
before = set(preserve.load_json(Path('build/reboot-recovery-before.json'))['runs'])
for _ in range(120):
    subprocess.run(['systemctl', '--user', 'is-active', '--quiet', 'dyb-preservation.service'], check=True)
    progress, records = preserve.archive_progress(root)
    ledger = preserve.load_json(root.parent / 'provenance/input-issues.json')
    controller = preserve.load_json(root.parent / 'provenance/controller.json')
    if progress['completed_runs'] > baseline and ledger['summary'] == progress and not controller.get('error'):
        newest = next(r for r in records if '/'.join(r[k] for k in ('period', 'hall', 'stem')) not in before)
        directory = root / newest['period'] / newest['hall'] / newest['stem']
        verified = preserve.verify_local_run(directory)
        pool, _ = preserve.remote_json('dayabay1', '/scratchfs2/dyw/lijj16/DYB_DATA_PRESERVATION/code',
                                       ['_pool', '--stage-root', '/scratchfs2/dyw/lijj16/DYB_DATA_PRESERVATION/staging'])
        assert pool['queue']['outstanding'] <= 300 and not pool['queue']['held']
        assert pool['reserved_bytes'] <= 400 * preserve.GIB
        result = dict(checked_at=preserve.utc_now(), archive=progress, verified=verified,
                      controller=controller, scheduler=pool['queue'],
                      reserved_bytes=pool['reserved_bytes'], output_bytes=pool['output_bytes'])
        preserve.atomic_json(Path('build/reboot-recovery-result.json'), result)
        print(json.dumps(result, sort_keys=True), flush=True)
        break
    time.sleep(10)
else:
    raise RuntimeError('No new verified local save within 20 minutes; inspect the active campaign')
PY
