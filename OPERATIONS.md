# Operations runbook

This runbook covers deployment, a one-run production pilot, restart, transfer,
cleanup, and final completeness verification. Nothing in the preparation or
benchmark performed on 2026-08-28 submitted a HepJob task or deleted cluster
data.

## Concurrent campaign, 2026-10-01 UTC

The Ubuntu launcher targets up to **300 outstanding single-core compression
jobs**, spanning multiple runs. `--max-jobs` cannot exceed 300; each HepJob
submission explicitly requests `-cpu 1`. Actual running/idle/held counts come
from `condor_q -global` across the IHEP schedulers. Query failures stop admission.
Existing arrays are adopted from their frozen markers and submission receipts.
An interrupted submission leaves an intent record: a uniquely matching queued
cluster can be adopted; an ambiguous result stops for inspection, never blind resubmission.
Retries wait until the old array has left the scheduler. Held or failed jobs
stop new work and remain available for diagnosis.

An incomplete array can outlive its scheduler jobs without any worker failure
marker: on October 8, task `81949511.15` of run43037 was killed at the short
class's 30-minute limit before creating worker logs/status. Seventeen other
chunks were verified. After the existing two-hour wait limit, an incomplete
array with no jobs in a successful global scheduler query uses the existing
bounded retry (two submissions maximum), rather than stopping forever on the
old submission timestamp. The submission helper independently rechecks that
no jobs remain before retrying; workers reopen and verify completed chunks,
then reuse them. Exhausted attempts and held jobs still stop safely; the known
scheduler transport outages described below pause and retry automatically.
This is job recovery, never a missing-mini skip;
catalogs, gaps and verified outputs stay intact. The scheduler class and its
30-minute limit are unchanged.

Since the October 11 run54964 recovery, an expired wait deadline with live jobs
leaves that run pending and its writers untouched, while other verified transfers
and bounded submissions continue. `controller.json.overdue_runs` records its exact
run/catalog hash, submission time, elapsed seconds and live cluster/process IDs.
The five-minute checker issues a visible overdue warning; it does not classify
the input as missing or broken, reset the submission clock, or retry a live writer.
The warning clears after the jobs leave the queue. Other scheduler, source,
checksum and storage failures retain their existing error handling.

Job `82081707.23` had remained claimed on `slot211@bws1005` for roughly seven
hours. Initial live queries reported zero CPU, empty scheduler stdout/stderr, and
no chunk23 worker logs/status/output; the other 26 chunks were complete. Its final
history subsequently reported 34,896 user CPU seconds and 939 system CPU seconds,
so the earlier zero-CPU snapshot did not establish an idle process. Direct node
access and a bounded Condor job-shell probe timed out; the precise execute-node
fault remains unknown. The controller's former fatal two-hour submission-age
check unnecessarily stopped the entire transfer backlog.
Any targeted retry must first establish that the old job and writer are gone;
ordinary [Condor removal](https://htcondor.readthedocs.io/en/latest/man-pages/condor_rm.html)
must complete, without force-removing a still-live job. Keep good chunks intact.

On October 11 at 12:07 UTC, targeted ordinary removal of `82081707.23` completed:
it left the queue, history recorded removal/SIGTERM, and its former slot became
idle. Only after no old jobs remained did the controller retry as `82084513`.
Worker `82084513.23` finished at 12:13:12 UTC with exit 0, creating and verifying
a 213,846,933-byte output for its 16 minis (301.385 wall / 291.744 converter CPU
seconds). Run54964 reached 27/27 good chunks, awaiting its normal verified
transfer. All 26 pre-existing chunk-list/report/OK hashes and ROOT sizes/mtimes
were unchanged. Independently of that retry, run51990 was verified on the portable
disk and its staging cleaned, advancing the archive from 602 to 603 runs.
These are dated recovery observations; live controller/progress records supersede
the counts. Small diagnostic evidence is in ignored `build/oct11-timeout-*.json`.

The staging admission budget is **400 GiB**, reserving a conservative 1 GiB per
chunk while an array is unfinished. Once all chunks are complete and every job
has exited the scheduler, that run reserves its actual output size plus 1 MiB
per chunk for metadata until verified cleanup. This is a reservation estimate,
not a truncation limit; larger observed outputs count at their actual size.
Live Lustre byte/file quota checks retain 30 GiB of extra
headroom, and destination free space also gates new runs. Completed runs count
against the budget, so a slow disk or network applies backpressure. One background
transfer performs ROOT validation, checksum generation, resumable copying and
local checksum verification while the controller fills available job slots.
Only the controller deletes verified staging, under the existing single-controller lock.

The live capacity check found a 500 GiB soft / about 507.8 GiB hard scratch quota
and 200,000 files. Schedd per-owner limits were 100,000–500,000 jobs and per-submit
limits 30,000. The unchanged short-job class has a 30-minute walltime and maps
to `COMMON.short.dyw`, whose configured share was 100 slots (parent
`COMMON.short`: 120). A 300-job queue does not guarantee 300 running jobs;
IHEP allocation/fair-share and staging/transfer backpressure remain authoritative.
No scheduler priority, group or walltime limit is bypassed.

The five-minute monitor includes live controller queue counts, staging reservations,
quota, transfer activity and backpressure from `provenance/controller.json`.
It alerts on a heartbeat older than five minutes, held jobs, controller errors,
cap/budget violations, stopped service, or an hour without new verified output.
Prior published runs are checked against archive metadata on resume; full artifact
rehashing remains available through `verify-all`. Any staged copy left after a
restart must still pass local artifact hashes before cleanup.

Read-only `_pool` snapshot failures retry twice, after 1 and 2 seconds, logging
each failure with its timestamp. Since the October 13 recovery, a recognized
`SECMAN:2007:Read failure during security negotiation` diagnostic (without other
unknown stderr), a bounded `condor_q` timeout, or an explicitly recognized
read-only SSH transport disconnect leaves the controller alive and retries
after 60 seconds. `scheduler_status: unavailable`, `scheduler_error`, retry
time/count and a fresh local heartbeat remain visible. Queue, quota, staged
counts and byte reservations become `null`, never zero or stale live counts.
New admission, submissions, content mutation and staging cleanup pause until a
fresh successful global query. The checker warns about the unknown queue and
automatic retry; stopped heartbeats, fatal errors and prolonged lack of verified
output still alert normally.

`_submit` and `_skip_broken` independently query before intent creation or
reconciliation, broken-input journaling, or output changes. Only a structured
exit-75 response naming that exact command and `before_mutation` phase permits
the same safe deferral. Each mutable helper still gets one SSH attempt; SSH loss
during mutation, ambiguous intents and unrecognized failures stop for inspection.
The next controller cycle always obtains fresh global writer state before retrying.
An already authorized transfer may finish during the outage, but its staging
cannot be deleted until a fresh successful query confirms no jobs, worker
markers or unresolved intent remain. A concurrent checksum/copy failure remains
fatal. No queue failure is classified as a missing or broken input.

The October 13 regression exercises repeated failed query cycles before work
and after a verified transfer publishes, deferred cleanup, eventual admission
and transfer recovery, both pre-mutation helpers, preserved intents/good chunks,
rejected ambiguous SSH failures, and monitor warning/recovery. All 21 focused
pipeline, monitor, existing broken-run and content-hold checks passed. The
incident baseline was 817 verified runs; compact deployment and live recovery
evidence is kept in ignored `build/oct13-scheduler-recovery-result.json`.

On October 12, submission of run66506 stopped during HepJob's line-26 import of
`slurm/slurm_sub.py`: CVMFS returned errno 5 before the submission backend was
imported. No cluster receipt or worker artifacts existed, but the prewritten
submission intent correctly prevented an uncertain replay. Recovery confirmed
successful empty global queue queries, no writers, and a readable site module
with working `hep_sub -h`. Under the existing submission lock, only this proved
pre-submission failure's intent was cleared; its full original intent/error and
all 18 prepared chunk-list hashes were retained in `submission-recovery.json`
inside staging and in portable `provenance/submission-recovery-run66506.json`.
The full 283-input catalog and its two already recorded missing minis were
unchanged; the site I/O error added no missing/broken input. The existing service
then resumed. Never clear an unknown submission intent merely because the queue
is empty: retain the existing ambiguity guard unless the failure is established
or the exact scheduler cluster can be adopted. No shared CVMFS package or
submission code was changed for this recovery.

Mini-only input handling, the immutable catalog, truthful legacy fallback
metadata, the 0.6-MeV AD cut, all non-AD events, ROOT compression and disk UUID
remain unchanged. The archive remains in progress with explicitly recorded gaps.

## NTFS3 crash and transfer recovery, 2026-10-02

The run37945 rsync exit 12 on October 1 at 08:59 EDT was caused by a **local
NTFS3 kernel BUG**, not established evidence of an IHEP network fault. The
08:58:49 kernel trace names `rsync`, `iomap_write_end`, and `ntfs_file_write_iter`
on kernel `7.0.0-31-generic`. The signature matches
[upstream inline-data alignment bug 221446](https://github.com/torvalds/linux/commit/70d3855594cf6e8791970714b65cac3202d6160e).
Normal unmount blocked in `ntfs_evict_inode` waiting on a folio lock. Do not
force/lazy-unmount, unplug, remount the still-busy block device, or retry writes.
Stop campaign/checker writes and reboot Ubuntu normally before changing drivers.
This dual-boot machine defaults to Windows: select Ubuntu for the next boot
and verify the remote-access user service and user lingering first.

Use the installed **ntfs-3g** driver for this archive (reported by `findmnt` as
`fuseblk`), retaining UUID `2A4F48F8BDFEE355` and the existing mount path. Check
the unmounted volume with `ntfsfix --no-action`; this is a limited read-only
check, not Windows `chkdsk`. Refuse a dirty/inconsistent volume that cannot be
mounted without recovery; never clear flags or force a mount to resume. The
controller and checker now reject NTFS3 and read-only destinations before
creating archive files. Existing verified output and `.incoming` data stay intact.

For transport failures on a validated destination, rsync retains `--partial`,
has a 120-second inactivity timeout, and retries at most twice after 5 and 15
seconds. Socket/timeouts and explicit disconnect diagnostics are eligible;
permission, file I/O, space, vanished-source, and host-key failures stop.
Mount UUID, driver, writable mode and free space are rechecked before every
attempt. Every successful transfer still passes full checksums/accounting before
publication and remote cleanup. Retries never resubmit compression jobs or add
missing-mini ledger entries.

`scripts/dyb-preservation.service` provides the same single controller as a
persistent user unit (no automatic restart on errors). The one-shot
`scripts/recover_campaign_disk.sh OLD_BOOT_ID BASELINE_RUNS LAST_VERIFIED_RUN MOUNT_ROOT`
is for a root service after the required reboot. It refuses the old boot or
an existing mount, performs the read-only filesystem check, mounts with
ntfs-3g and `norecover` in a separate systemd mount unit, rechecks the saved-run baseline and hashes the last
verified run, then starts the user controller and timer. It waits up to 20
minutes for a new verified save and reconciled ledger, rechecks that save's
hashes and the live scheduler/budget, and writes
`build/reboot-recovery-result.json`. A failure leaves an explicit journal
error; it never forces the filesystem, clears flags or starts another controller.

The October 7 boot exposed two startup problems: login1 denied the lingering
user's sleep inhibitor before a desktop login, and the failed recovery service
killed its own ntfs-3g daemon. The desktop then mounted the disk with NTFS3.
The recovery helper now requires the freshly inspected mount path and gives
the mount its own systemd unit, so a controller/recovery error cannot kill it.
Its verification query explicitly uses the campaign's scratchfs2 roots.
Install `scripts/50-dyb-preservation-inhibit.rules` as root, mode 0644, under
`/etc/polkit-1/rules.d/`. It permits only user `josiah` to inhibit sleep;
the controller still fails if it cannot obtain that inhibitor.

On October 8 the inspected mount path was `/media/josiah/LAYJ萤捷`, without
the former trailing `1`. After stopping checker/controller writes, a normal
unmount and successful `ntfsfix --no-action` check, pin this UUID to ntfs-3g
in `/etc/fstab` so desktop automount cannot select NTFS3 again:

```text
UUID=2A4F48F8BDFEE355 /media/josiah/LAYJ萤捷 ntfs-3g rw,norecover,nosuid,nodev,uid=1000,gid=1000,umask=0022,nofail,x-systemd.device-timeout=15s 0 0
```

Reload systemd and start the corresponding mount unit. Verify the exact UUID,
`fuseblk`, writable mode and last saved run's hashes before starting one user
controller. Disable the obsolete one-shot reboot recovery service after the
recovery; it must not run on every boot against an already mounted volume.

## Single-disk campaign, 2026-09-29

The threshold grid uses **0.1-MeV steps**. The completed 0.3–0.7-MeV
sweep selects 0.6 MeV.
Verified changes must be committed and pushed before starting the full campaign.

The user authorized threshold testing and production onto the mounted empty
portable disk. The chosen raw AD cut is **energy > 0.6 MeV**, with all WP/RPC
events retained. The [48-file worker study](estimates/energy-threshold-2026-09-29/README.md)
projects 11.84 TB, or 12.35 TB using within-stratum maxima, within a 12.60-TB
budget that reserves 10% of the disk's 14.00 TB free space.

- Disk UUID: `2A4F48F8BDFEE355`, currently `/dev/sdb3` at `/media/josiah/LAYJ萤捷1`.
- Data: `/media/josiah/LAYJ萤捷1/DYB_DATA_PRESERVATION/data`.
- Code and staging: `/scratchfs2/dyw/lijj16/DYB_DATA_PRESERVATION/{code,staging}` on `dayabay1`.
- Format: `dyb-slim-v5`, metadata schema 4, `adFrom0p3To0p6MeV` count band.
- Checks: `make test`; all 15 tests passed on IHEP, including both ROOT tests and gap reconciliation.
- Catalog inventory: 4,009 catalogs, 831,334 source files, no catalog failures.

The one-run pilot **P17B/EH1/run21221** (HepJob **81751470**) passed: 217 mini
inputs, 14 ROOT chunks, 164,525,202 source entries, 131,036,010 kept entries,
and 2,683,398,113 bytes including the duration table. All 15 artifact SHA-256
hashes passed on the destination disk before the generated remote staging
was removed. This verified run remains in the archive and is skipped on resume.
See [pilot.json](estimates/energy-threshold-2026-09-29/pilot.json).

The earlier run P17B/EH1/run21228 had an unavailable mini for file 0200.
Its exact original passed a full read of all 19 required branches across
755,835 entries; [fallback-input-check.json](estimates/energy-threshold-2026-09-29/fallback-input-check.json)
records the branch signatures. That historical original-file fallback stays
truthfully recorded in the verified output. It is not the policy for new work.

The user's September 29 correction requires **minis only** for all new work.
The launcher now passes `--input-source mini --skip-missing-minis --skip-broken-minis`. It reads
no originals. Confirmed missing minis are recorded and skipped individually;
I/O errors are retried twice (1 and 2 seconds), while unresolved I/O, permission,
invalid-header or suspected storage-outage errors stop processing. Missing-file
confirmation requires two consecutive ENOENT results, 0.2 seconds apart.
Three consecutive missing files or more than `max(3, input_count // 10)` gaps
stops preparation for inspection, without freezing a run full of suspected gaps.

On **2026-10-11**, the user authorized recording confirmed existing unusable
minis as **BROKEN**, separately from ENOENT **MISSING**, and processing the other
inputs. The narrow supported broken signature and recovery/verification contract
are in [BROKEN_MINIS.md](BROKEN_MINIS.md). Existing seven-run holds are released
only after fresh independent ROOT proof and a no-writer check. Full catalogs,
verified chunks and historical provenance stay unchanged. Other operational or
content failures still stop; the missing-only outage protections above remain.

At the correction, 11 runs / 2,763 sources / 34.24 GB had been verified.
Run21540 has 126 expected minis: files 0103 and 0159 are missing; 124 minis
remain usable in the live check. Existing outputs are reverified and retained.
The full expected catalog remains 4,009 runs / 831,334 inputs.

Each gapped run has a checksummed `input-gaps.json`; processed and skipped
counts must reconcile to its full catalog. The controller updates
`provenance/input-issues.json` and `INPUT_ISSUES.md` before submission and after
verified transfer. Historical fallback observations retain their actual source.
Broken filenames and proof are exported beside them as `BROKEN_INPUTS.md`,
`broken_files.list`, `broken_files.csv`, and `broken-input-issues.json`.
The expected count equals processed + missing-skipped + broken-skipped;
unfinalized runs stay pending in archive totals.
Final accounting distinguishes `finished_with_gaps` from `complete`; `verify-all`
exits 2 for a valid archive with recorded gaps. `.complete` and the run's
`complete` field describe finalization, not absence of catalog gaps.

`scripts/run_campaign.sh` locates this UUID, explicitly enables the selected
cut, and supplies the already-authorized execution and verified-staging
cleanup flags. Every transfer checks the UUID before and after copying; all
SHA-256 checks must pass before this run's generated scratch data is removed.
Original mini/recon inputs are read-only throughout.

```bash
# One-run pilot; the same output is retained for the full campaign.
bash scripts/run_campaign.sh --limit-runs 1
# Resume or run all catalogs; do not launch a second controller concurrently.
bash scripts/run_campaign.sh
```

Start the full campaign in a local `dyb-preservation.service` user unit:

```bash
systemd-run --user --unit=dyb-preservation \
  --description='Daya Bay preservation to portable disk' \
  --property="WorkingDirectory=$PWD" \
  --property="StandardOutput=append:$PWD/build/campaign.log" \
  --property="StandardError=append:$PWD/build/campaign.log" \
  /usr/bin/systemd-inhibit --what=sleep --mode=block \
  --why='Daya Bay data preservation' /usr/bin/bash "$PWD/scripts/run_campaign.sh"
```

Read progress with `systemctl --user status dyb-preservation.service`
and `tail -n 30 build/campaign.log`. The command inhibits sleep while running.
After a failure, inspect the log and fix its cause before restarting. An
unmounted/replaced disk, unreadable input, failed chunk, or checksum mismatch
stops the controller except for explicitly recorded missing minis and proved
empty-`AdEnergy`-only broken inputs. A disk-held
advisory lock rejects a second controller. This sample-based capacity estimate does not establish
archive-wide input availability or lossless TWin physics equivalence.

Install the persistent five-minute progress/error check after committing and
pushing the verified code:

```bash
mkdir -p ~/.config/systemd/user
cp scripts/dyb-preservation-check.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now dyb-preservation-check.timer
systemctl --user start dyb-preservation-check.service
```

It checks the disk UUID, controller state and finalized local metadata; it does
not repeat multi-terabyte hashes. `build/campaign-status.json` and the disk's
`provenance/progress.json` show the latest observation. GNOME desktop notices
report hourly progress, stopped controllers, failed checks, and an hour without
new verified output; critical alerts repeat every 30 minutes while unresolved.
Desktop notification delivery is bounded to ten seconds. Delivery failure is
recorded separately as `notification_error` in both reports and the journal;
the original campaign alert is retained, and the next timer check retries delivery.
The checker exits unsuccessfully for campaign alerts. Optional desktop delivery
failure alone does not fail a healthy check; headless sessions retain reports and
the journal but cannot display desktop notices until a notification service returns.
The monitor never starts a second controller or changes data. Inspect failures
with `journalctl --user -u dyb-preservation-check.service` and `build/campaign.log`.

Schema-3 / 0.7-MeV archives require the previous toolchain at `5479aef`;
start new staging and a new destination for schema 4. Do not relabel old counts.

## Safety gates

Production requires all of the following:

- an explicit user order to start;
- the destination disk mounted at the path passed as `--mount-root`;
- the complete 4,009-catalog repository snapshot passing checksum and identity
  validation;
- `--execute --confirm-start START-DYB-PRESERVATION`;
- for automatic cluster cleanup,
  `--delete-remote-after-sync --confirm-remote-delete DELETE-VERIFIED-RUN`.

The program rechecks the mount immediately before and after each transfer.
Remote cleanup is impossible until local SHA-256 verification succeeds and the
local catalog/schema match the remote finalized run.

## Preflight

From this repository on the local machine:

```bash
make test
python3 preserve.py verify-catalog-backups
python3 preserve.py deploy
```

The `deploy` command is a dry run. After the explicit start order, deploy and
compile under the pinned IHEP LCG 110 environment:

```bash
python3 preserve.py deploy \
  --alias dayabay1 \
  --remote-code-root /dybfs2/users/lijj16/DYB_DATA_PRESERVATION/code \
  --execute
```

Then require a clean fixed-catalog inventory:

```bash
python3 preserve.py inventory --alias dayabay1
```

The inventory must report `catalogs=4009 readable=4009 unreadable=0
backups=4009 inputs=831334`. It exits nonzero if the snapshot is incomplete or
any checksum, period, hall, run identity, or source path fails validation. Do
not bypass this gate.

`inventory` validates catalogs, not current ROOT-file availability. Conversion
defaults to `--input-source mini`, using `/lustrefs/juno26/dyw/rec/All-mini`
with the exact run/file/version identities from those catalogs. New-run
preparation opens every mapped source and checks its four-byte ROOT signature
before staging. Unavailable inputs stop strict `mini` mode. The campaign's
explicit `--skip-missing-minis` flag permits only confirmed missing minis to
be skipped, with recorded gaps and the full expected catalog retained.
The separately authorized `--skip-broken-minis` policy requires strict independent
ROOT proof; generic ROOT errors are not skip observations.
The September 2 mini index leaves 4,087 required paths unlisted;
see `estimates/mini-branches-2026-09-27/`. A dry run does not open source files.

The archive snapshot and run metadata pin the choice. The current explicit
transition from `mini-or-recon` to `mini` retains only already finalized runs
as named legacy exceptions; later original-file fallbacks are refused.
Older schema-3
archives require the previous toolchain at `5479aef`. Deploy the updated
`preserve.py` before using the new default on the cluster.

## Staging without a writable `/dybfs2`

On 2026-09-03 `/dybfs2` was mounted read-only on every login node and on the
compute nodes, and it was 97% full. Original `recon` inputs use `/dybfs2`
(`/dybfs` is a symlink into it); the new default reads `/lustrefs/juno26/dyw`.
The code root and the
staging root can live on the scratch Lustre pool instead. A worker probe job
that day (cluster 81006673 on node cws048) confirmed that compute nodes write
to `/scratchfs2/dyw/lijj16`, read the `/dybfs` sources, load the LCG 110 view
with ROOT 6.40.02, and run the deployed converter. The user quota there is
500 GB and 200,000 files against a peak of about 30 GB and a few hundred
files per run, and the pool had 1 PB free. Scratch is not backed up, which is
acceptable: the code is in Git and every verified run is deleted from staging
after its local copy is checked.

Pass both roots to every command:

```bash
CODE=/scratchfs2/dyw/lijj16/DYB_DATA_PRESERVATION/code
STAGE=/scratchfs2/dyw/lijj16/DYB_DATA_PRESERVATION/staging
python3 preserve.py deploy --alias dayabay1 --remote-code-root $CODE --execute
python3 preserve.py inventory --alias dayabay1 --remote-code-root $CODE
python3 preserve.py run --remote-code-root $CODE --remote-stage-root $STAGE \
  --local-root /Volumes/DYB_DISK/data --mount-root /Volumes/DYB_DISK \
  --period P17B --hall EH1 --limit-runs 1
```

The last observed converter under `/dybfs2` was `dyb-slim-v2`; the current
format requires deploying `dyb-slim-v5` to the new code root first, together
with the updated `preserve.py`. Confirm `bin/slim-root --version` there before
starting. The September 28 format change did not deploy production code or
start production. Its bounded TWin test used disposable `/tmp` files. The earlier
worker probe did not test compute-node readability of `/lustrefs/juno26/dyw`;
verify it in the authorized pilot. A one-file local mini conversion passed
reopening and all retained-value comparisons; see
`estimates/mini-branches-2026-09-27/conversion-check.json`. This does not replace
the compute-node check. Inputs becoming unreadable after preparation
make their chunk fail; the run stays in staging until storage is repaired.

## Dry run and first one-run pilot

Replace both `/Volumes/DYB_DISK` examples with the real mounted disk paths.
This selection command only reports the planned work:

```bash
python3 preserve.py run \
  --local-root /Volumes/DYB_DISK/data \
  --mount-root /Volumes/DYB_DISK \
  --period P17B --hall EH1 --limit-runs 1
```

After reviewing the selection and receiving the start order, the same one-run
pilot becomes executable only with both confirmation phrases:

```bash
python3 preserve.py run \
  --local-root /Volumes/DYB_DISK/data \
  --mount-root /Volumes/DYB_DISK \
  --period P17B --hall EH1 --limit-runs 1 \
  --execute --confirm-start START-DYB-PRESERVATION \
  --delete-remote-after-sync \
  --confirm-remote-delete DELETE-VERIFIED-RUN
```

Inspect that local run with `verify-local` before expanding the selection.

## Full run

Omitting period, hall, pattern, and limit selects all fixed catalogs. This is
the command to use only after the one-run pilot is accepted:

```bash
python3 preserve.py run \
  --local-root /Volumes/DYB_DISK/data \
  --mount-root /Volumes/DYB_DISK \
  --execute --confirm-start START-DYB-PRESERVATION \
  --delete-remote-after-sync \
  --confirm-remote-delete DELETE-VERIFIED-RUN
```

Defaults are mini inputs, ROOT ZSTD 9 / 256 KiB baskets, 16 source files per
task, the AD energy selection off (`--ad-selection off`, every entry kept),
HepJob group `dyw`, `short`
walltime, 1,024 MB memory, two submission attempts, and a two-hour per-run
wait. Override only after measured evidence from the production pilot. Use one
selection mode for the whole archive: `verify-all` rejects a mixed archive and
`run` refuses to continue a local run made with the other mode.

## Restart and recovery

- Re-run the same command after an SSH interruption or failed task. The run
  marker fixes the catalog and chunking; valid outputs and timing reports are
  reopened, verified, and skipped.
- A catalog checksum, period, hall, `--files-per-job`, `--input-source`, or `--ad-selection`
  mismatch is refused.
- New staging uses `preserved.NNNNNNN.slim.CCCCCC.root` filenames for TWin.
  Legacy staging without this recorded output prefix cannot resume under the
  new preparation command; use a new empty staging root. Existing completed
  archives retain their names and remain verifiable. Do not rename files
  independently of their checksums and run metadata.
- All 4,009 catalogs are rehashed and checked for exact period, hall, run,
  source-path identity, and duplicate paths before they can be staged.
- A failed transfer remains under `.RUN_STEM.incoming` and never authorizes
  remote deletion. Re-running resumes `rsync` and rechecks all hashes.
- If the external disk disappears, remount it at the same `--mount-root` and
  rerun. Do not manually move `.incoming` into its final name.
- If a run exhausts both attempts, inspect its remote `logs/*.err` and
  `status/*.fail`; no cleanup occurs.
- Never manually remove a remote run until the corresponding local
  `verify-local` command succeeds.

## Per-run timing file

`durations.tsv` is tab-separated and follows the fixed source catalog order:

| Column | Meaning |
| --- | --- |
| `fileIndex` | zero-based index in the run catalog |
| `chunkIndex`, `sourceIndex` | output chunk and index within that chunk |
| `path` | actual absolute input path: minirecon by default, recon in explicit original mode |
| `entries`, `inputBytes` | actual input event count before our selection and input byte count |
| `firstTriggerTimeSec/NanoSec` | earliest trigger timestamp in the source |
| `lastTriggerTimeSec/NanoSec` | latest trigger timestamp in the source |
| `dataDurationSeconds` | `(latest_ns - earliest_ns) / 1e9` as a double |
| `processingWallSeconds` | source read plus compression-flush wall time |
| `processingCpuSeconds` | source read plus compression-flush CPU time |
| `keptEntries` | entries written to the ROOT chunk from this source |
| `ad1Below0p3MeV` … `ad4Below0p3MeV` | per-AD entries with `energy <= 0.3 MeV` or NaN |
| `ad1From0p3To0p6MeV` … `ad4From0p3To0p6MeV` | per-AD entries with `0.3 < energy <= 0.6 MeV` |

The seconds/nanoseconds columns make the acquisition duration independently
recomputable without subtracting large floating-point epoch values; they cover
every trigger in the source, including dropped AD entries. With the selection
on, `entries = keptEntries + sum of the eight AD counts` for every row; with it
off, `keptEntries = entries` and the counts are informational.

`catalog.list` retains the original fixed recon paths. Verification maps those
paths using the recorded input source before comparing the duration rows.
Mini input byte counts and durations describe the actual mini files, not an
unmeasured original file. This tool cannot undo an upstream mini-file selection.

## Completeness verification

Per run:

```bash
python3 preserve.py verify-local /Volumes/DYB_DISK/data/P17B/EH1/RUN_STEM
```

After the full campaign:

```bash
python3 preserve.py verify-all /Volumes/DYB_DISK/data
```

`verify-all` requires exactly every run in `catalog-inventory.json`, rejects
unexpected run directories, matches catalog hashes and source counts, checks
one duration row per catalog source in exact order with valid selection
accounting, matches the row, entry, and kept-entry totals against `run.json`,
checks the exact ROOT chunk set, recomputes every artifact SHA-256 and byte
total, and reports the single selection mode of the archive. It exits nonzero
if runs were made with different modes.

Where a compatible ROOT installation is available, add structural reopening:

```bash
make converter
python3 preserve.py verify-all /Volumes/DYB_DISK/data \
  --root-verifier ./bin/slim-root
```

The converter then checks required trees and branches, the selection tag,
equal event-tree entry counts, per-source selection accounting, kept-entry
totals, finite nonnegative timing values, exact agreement between each double
data duration and its integer timestamps, and that no kept AD entry is at or
below 0.6 MeV; `verify-all` compares each chunk's reported kept entries, source
entries, source count, and mode with the corresponding timing rows.

## 2026-10-12 scheduler-query recovery

The controller stopped after three read-only `_pool` queries failed with
`SECMAN:2007:Read failure during security negotiation` against the IHEP
schedd. Although `condor_q` returned zero, its diagnostic stderr correctly
prevented treating this as an empty queue. The deeper site-side cause remains
unconfirmed; no scheduler, credential, source, or compression settings changed.

At 16:09 UTC a successful global query found zero jobs, 127 complete staged
runs, and no unresolved submission intents. With the archive UUID/rw mount
validated and the controller lock free, the existing service was restarted
once. No job or intent was removed or replayed. Both focused read-only retry
and scheduler-fail-closed tests passed; no pipeline code change was necessary.

By 16:24 UTC, run61335 was checksum-verified and published before its remote
staging was deleted: 797 finalized runs, 189,546 processed inputs, 919 missing
and 1,730 broken skips, and 2,440,681,231,170 artifact bytes. Run61360 was
transferring; 18 single-core jobs were running, including the retained final
run66638 worker (15/16 chunks verified). Admission remained enabled, bounded
by the unchanged 300-job/400-GiB limits. Controller PID 722583 held the single
controller lock, and the checker/timer were healthy. These are dated recovery
observations, not live status; current evidence is in portable provenance and
the small ignored `build/oct12-secman-recovery-result.json` checkpoint.

## 2026-10-13 run71188 worker recovery

The controller stopped on an unrecognized worker failure in run71188. Chunks
9 and 10 of cluster 82189434 failed at `worker.sh` line 54 while sourcing the
CVMFS LCG setup: bws0990 reported `Transport endpoint is not connected`, and
bws0993 reported `No such file or directory` for that same software setup
script. Neither converter had started reading minis. The strict content-error
classifier correctly refused to interpret these as missing or broken inputs.

A fresh successful global query found zero jobs, no unresolved intents or
worker markers, ten good chunks and two failed chunks. Original stderr,
stdout, failure markers, receipts, exact catalog/source identities and good
chunk fingerprints were archived before invoking the existing locked retry.
Cluster 82189500 was attempt two of the existing two-attempt limit; no job was
cancelled or uncertain intent removed. The ten successful chunks were reused.
Both new chunks passed ROOT verification on aws003 at 10:16 UTC, producing
207,824,348 and 207,656,467 bytes. All 50 recorded hashes/sizes/mtimes of the
ten old chunks remained unchanged. The full catalog still contains 184 inputs:
182 processed and the same two previously confirmed missing minis, with no
new gaps from this environment failure. No pipeline/site configuration changed;
the scheduler retry fix 6962387 remains deployed. Two focused retry/reuse tests
passed.

The in-flight run63819 finished verified publication while the failed controller
unwound, advancing the portable archive from 850 to 851 runs. These are dated
recovery observations. Evidence is in portable
`provenance/worker-recovery-run71188.json`; the compact current recovery result
is ignored `build/oct13-worker-recovery-result.json`.

## 2026-10-13 run71820 and bounded LCG startup recovery

Run71820 had two different failures in cluster 82195805: chunk 9 could not
source the CVMFS LCG setup on lhws199, while chunk 8 entered the converter and
received SIGBUS on dws133. That mixed failure is deliberately ineligible for
automatic startup recovery. The SIGBUS cause remains unconfirmed. A fresh
global query found no jobs, worker markers or unresolved intents; read-only
headers of the 32 affected minis and staging quota checks showed no contrary
input/storage evidence. Original logs, failure markers, receipts, exact
catalog/source identities and 70 good-file fingerprints were preserved before
the explicitly authorized diagnostic second attempt, cluster 82198463.
Both retried chunks passed ROOT verification on lhws283 at 13:53 UTC. All 14
previously good chunks retained their 70 recorded hashes/sizes/mtimes. The run
remains 247 expected inputs, 246 processed and one earlier missing mini, with
no new missing/broken classifications. Portable evidence is
`provenance/worker-recovery-run71820.json`.

The pool now identifies only exclusive, exact `worker.sh` line-54 setup-source
failures: the default LCG view reports `No such file or directory`,
`Transport endpoint is not connected`, or the separately observed exact setup
`Input/output error`. Proof requires the pinned worker
SHA256, rc=1, an exact current receipt/chunk identity and timezone-bearing
timestamp, empty stdout and no nonempty converter report, and unchanged
failure snapshots. Catalog hashes and fixed mini chunk membership must match.
SIGBUS, mixed failures, other messages, source errors, permissions, other I/O,
checksums, mounts and quota faults retain their existing fatal handling.
Changing snapshots defer for at most eight polls; they never authorize a retry.

Known startup-only failures wait visibly in `startup_recovery_pending` while
live writers finish, allowing unrelated verified transfers and admissions.
The existing submission lock and a fresh successful global query must prove
no old writers, running markers or unresolved intent before the retry helper
independently repeats the strict proof. Before removing failure markers or
overwriting logs, it saves `startup-recovery.json` with original failure proof,
source/catalog identities, receipts, existing gaps and good-file fingerprints.
The controller exports that journal to portable
`provenance/worker-startup-recovery/<period>/<hall>/<run>.json`; it also travels
with the run. Retry exhaustion remains visible and fatal; this path cannot
extend the existing two-attempt allowance or replay an uncertain submission.
The five-minute checker warns while startup recovery is pending and clears
that warning after recovery. Scheduler-query pause/retry fix 6962387 remains
unchanged. No worker, converter, schema, source or physics policy changed.

Validation: 53 focused/existing startup, pipeline, preservation, broken-mini,
content, worker/converter and monitor tests ran: 49 passed and four skipped
for their unavailable supported ROOT/LCG runtimes on Ubuntu. Tests cover mixed
and unknown rejection, evidence-before-overwrite, good-chunk preservation,
writer/intent/query guards, safe second attempt and exhaustion, verified
unrelated transfers/admissions during deferral, portable journal export and
monitor warning clearance. The compact runtime/deployment checkpoint is
ignored `build/oct13-startup-recovery-result.json`.

The first reload exposed one additional setup-source message in run71980:
chunks 5, 17, 23 and 24 of cluster 82199672 on lhws270 reported the exact
line-54 default-LCG `setup.sh: Input/output error`. No converter report existed;
stdout was empty, rc was 1, pinned worker/catalog/chunk/receipt identities
matched, and read-only headers of the 64 affected minis were usable. This is
an observed software-setup read failure, without any new input gap. The
whitelist was extended only to that full setup-path signature under identical
proof, writer/intent and attempt guards; mini/source I/O still cannot qualify.
Original proof is in portable `provenance/worker-startup-eio-run71980.json`.
The run remains 419 expected inputs, 416 usable minis and three earlier
confirmed missing skips. Current retry/disposition belongs to the dated
checkpoint rather than this static report.

## 2026-10-14 run72099 and hourly recovery checks

Run72099's original cluster 82201118 had converter SIGBUS failures in chunks
7 and 16, on bws1006 and bws1008. These are not startup-only faults or evidence
of missing/broken minis; the deeper SIGBUS cause remains unconfirmed. A fresh
successful global query proved zero old jobs, worker markers and unresolved
intents. Read-only source-header/runtime checks and quota checks found no
contradictory input/storage evidence. Original logs, failure markers, receipts,
catalog/source identities and good-chunk fingerprints were preserved before
the authorized diagnostic second attempt, cluster 82235073. It completed all
17 chunks: chunk 7 was converted and ROOT-verified on cws049; chunk 16 reused
its existing verifiable output on lhcb001. All 75 recorded fingerprints of the
15 previously good chunks remained unchanged. The expected catalog still has
272 inputs, 270 processed and the same two confirmed missing minis, with no
new gaps. Evidence is portable `provenance/worker-recovery-run72099.json` and
the small ignored `build/oct14-recovery-result.json`. The existing production
converter, worker, selection, schema and automatic failure classifier did not
change for this recovery.

The user authorized hourly checks and safe recovery through this same native
Codex conversation. `scripts/hourly_recovery.py` starts the existing five-minute
metadata checker, requires its fresh report, and records mount UUID, rw/driver,
portable/internal free space, verified counters and controller queue/storage
freshness. Healthy checks do not contact Codex or rehash the archive. Alerts or
warnings request diagnosis/resumption using only the existing loaded owner and
`gpt-6.1-sol` with max reasoning. The helper never restarts the campaign itself,
creates a conversation/daemon, changes credentials or replays submissions.

The private binding is `build/hourly-recovery/config.json` (mode 0600 in a 0700
directory): `thread_id`, a unique `monitor_id`, this project's `cwd`,
`model: "gpt-6.1-sol"` and `reasoning_effort: "max"`. It must identify the live
authorized conversation; do not create a replacement owner. The current socket
is resolved with `codex app-server daemon version` on each request. Native
conversation reads/deletion use the installed ssh-ops `codex-rpc.py`; requests
use installed `codex queue` with the explicit model/effort. No socket hash or
thread ID belongs in tracked source, converter-share or public reports.

Install the reviewed units after committing locally:

```bash
cp scripts/dyb-preservation-hourly.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now dyb-preservation-hourly.timer
systemctl --user start dyb-preservation-hourly.service
systemctl --user list-timers dyb-preservation-hourly.timer
```

`OnCalendar=hourly` checks at the top of each hour; `Persistent=true` catches a
missed check after normal restart. User lingering is enabled on this Ubuntu
host. An advisory lock and systemd's oneshot unit prevent overlap. Native queue
reads deduplicate the exact monitor marker, remove only duplicate/cancelled
requests owned by this monitor, and preserve other messages. An active recovery
or uncertain delivery defers further requests; an idle owner is rechecked before
another request. Intent is saved before queue mutation. No active turn is
interrupted. Completion is based on `finished`, including an archive with
recorded gaps; subsequent checks are cached no-ops, without claiming `complete`.

State and the latest concise observation overwrite ignored
`build/hourly-recovery/state.json` and `status.json`; failures are also visible
in `journalctl --user -u dyb-preservation-hourly.service`. The native delivery
probe was acknowledged in the existing conversation once, its redundant queued
copy removed, and zero probe requests remained. Focused tests cover stopped and
healthy cases, uncertain/active delivery, overlap, category-preserving completion,
mount failures, private state, model binding and dynamic socket resolution.
The dated private setup/runtime checkpoint is
`build/oct14-hourly-recovery-result.json`.

The timer survives reboot; the native backend/loaded owner must also be available
for diagnosis. If unavailable, it records a visible blocker and retries next hour
without starting a replacement session. An external site or integrity failure
may still require diagnosis rather than an immediate safe restart. To disable:

```bash
systemctl --user disable --now dyb-preservation-hourly.timer
```

This leaves the campaign, its five-minute checker and existing CLI untouched.

### First scheduled recovery: EH2/run21511

The 13:00 UTC hourly check on October 14 detected the failed controller and
delivered one native recovery request to this existing owner. The controller
had stopped at 12:49 UTC on chunk 0 of P17B/EH2/run21511, cluster 82236049.
On aws006 the converter produced its output and source report, then ROOT
library autoload warnings and a missing `GLIBCXX_3.4.31` symbol in system
`libstdc++.so.6` prevented the verification process from running. Why that
worker's library environment became inconsistent remains unconfirmed. This
post-conversion failure deliberately remains outside startup-only and
missing/broken-input classifiers.

A fresh global query found one unrelated running job, no run21511 writers,
worker markers or unresolved intent. Catalog/source identities and quota
guards passed; the 16 affected mini headers were readable. The existing
200,641,088-byte output passed independent ROOT verification under the
configured LCG runtime, matching its source-report counts and exact chunk
membership. Original stderr/stdout/report/failure marker, source list and
receipt were preserved before the authorized second attempt, cluster
82236093 at 13:03:17 UTC. Worker bwsjnws024 verified and reused that output at
13:03:39 UTC, leaving its SHA256, size and mtime unchanged. All 12 chunks
completed, with all 55 recorded fingerprints of the 11 previously good
chunks unchanged. The fixed catalog remains 179 expected inputs, 178
processed and one earlier confirmed missing mini, with zero new gaps.

The single controller was restarted using the existing lock/mount guards;
no worker, pipeline, converter, schema, physics, site setup or automatic
failure classifier changed. Original proof is portable
`provenance/worker-recovery-run21511-EH2.json`. Current resumed service,
transfer, scheduler and checker evidence is in the small ignored
`build/oct14-eh2-recovery-result.json`; the hourly schedule remains enabled.

### Second scheduled recovery: EH2/run21543

The 14:00 UTC check detected another failed controller. Chunk 2 of
P17B/EH2/run21543, cluster 82236228, had finished conversion on aws006 before
the verification process received SIGBUS at 13:15:27 UTC. Its stderr also
reported the missing CVMFS ROOT backtrace script. This is the same node as
the preceding library-loader failure, but the deeper fault remains
unconfirmed. Neither failure qualifies as startup-only, missing or broken
input; the strict automatic classifiers remain unchanged.

Fresh global queries found one unrelated running job, zero writers/markers
for run21543 and no unresolved intent. All 16 affected mini headers and exact
catalog/source-report identities checked successfully. Under the configured
LCG runtime its existing 197,269,082-byte output independently verified with
12,397,032 retained events from 14,016,544 events across 16 sources. Original
stderr/stdout/report/failure marker, receipts, sources and good fingerprints
were preserved before the authorized second attempt, cluster 82236991 at
14:07:57 UTC. Worker gecam006 verified/reused the output at 14:08:15 UTC.
All five chunks completed; the recovered output's SHA256/size/mtime and all
20 fingerprints of the four previously good chunks remained unchanged.
The fixed catalog remains 74 expected and processed minis, with zero gaps.

Installed HepJob help/source exposed dedicated-node requests and an `-extra`
string attribute (`HepJob_ExtraAttr`), without establishing a supported
negative-host constraint. No custom requirements or site configuration were
applied, and no scheduler restriction was bypassed. Future aws006 failures
remain a concrete operational risk monitored by the hourly owner. Only
the existing guarded retry/restart was used; pipeline/worker/source/selection
and attempt limits did not change. Portable proof is
`provenance/worker-recovery-run21543-EH2.json`; the small ignored current
checkpoint is `build/oct14-21543-recovery-result.json`.

### Third scheduled recovery: EH2/run21952

The 15:00 UTC check detected a converter SIGBUS in chunk 2 of
P17B/EH2/run21952, cluster 82237417. This crash was on lhws303 at 14:33:46 UTC
inside ROOT `TStreamerInfoActions::WriteBasicType<float>` while writing ROOT
metadata. The converter report was empty; its 195,176,303-byte partial output
and empty partial source report were retained. This is a different node and
execution phase from the preceding aws006 verification faults. The deeper
cause remains unconfirmed; no infrastructure failure was reclassified as an
input omission or admitted by the startup-only classifier.

Successful fresh global queries found zero jobs, 175 staged runs (174 ready)
and no unresolved intents. The affected 16 mini headers, fixed catalog/chunk
mapping, selection, worker/source hashes and quota guards passed. The
configured LCG runtime independently verified a previously good chunk with
12,212,268 retained entries from 13,844,493 source entries. Original logs,
failure marker, receipts, exact sources, partial-file metadata and all 15
fingerprints of the three good chunks were saved before the authorized
second attempt, cluster 82239558 at 15:02:55 UTC. Its worker on lhws232 began
at 15:03:17 UTC; good chunks and original partials were unchanged in the
15:07:38 UTC check. The fixed catalog remains 55 expected minis, 54 processed
and one earlier confirmed missing mini, with zero new gaps.

The single controller was restarted with the existing code and guards.
Portable original proof is `provenance/worker-recovery-run21952-EH2.json`;
current retry outcome, fingerprints, controller, transfer and monitoring
evidence is the small ignored `build/oct14-21952-recovery-result.json`.
No source, worker, converter, schema, selection, scheduler configuration or
attempt-limit change was made.

### Fourth scheduled recovery: EH2/run22131

The 15:26 UTC check detected three converter SIGBUS failures in chunks 5–7
of P17B/EH2/run22131, original cluster 82241372. All three were on lhws304
at 15:24 UTC inside ROOT `TStreamerInfoActions::WriteBasicType<float>` while
writing metadata. Their stdout and converter reports were empty. This is
not a proved missing or broken mini, and the strict startup classifier
continues to reject it. The underlying cause remains unconfirmed.

The existing run65305 transfer was allowed to finish and publish before the
failed controller exited; no transfer was interrupted. Fresh successful
global queries established that all five remaining original writers had
exited, leaving seven good chunks, three failures and no unresolved intents.
The fixed 150-input catalog still accounts for 149 processed minis and one
previously confirmed missing mini, with zero new input gaps. All 48 affected
mini headers, exact chunk identities, deployed source/worker hashes and
quota guards passed. The configured LCG runtime independently verified a
good output with 12,287,812 retained entries from 13,903,610 source entries.

Before retry, the original logs, failure markers, receipts, catalog/source
identities, six partial-file records and all 35 fingerprints covering the
seven good chunks were saved in the remote run's `worker-recovery.json` and
portable `provenance/worker-recovery-run22131-EH2.json`. Under the submission
lock, another fresh writer/intent check allowed the authorized second and
last attempt, cluster 82242347 at 15:35:43 UTC. No third attempt is authorized
by this recovery. The single existing service was restarted with unchanged
production code; live retry, preservation, transfer, controller and monitor
results are in the small ignored `build/oct14-22131-recovery-result.json`.
No source, worker, converter, schema, selection, shared site configuration,
staging budget or attempt-limit change was made.

run22131 reached 10/10 verified chunks at 15:42:43 UTC. All 35 old-good
fingerprints and six original partial-file records were unchanged. Two
replacement conversions succeeded on the original lhws304 node; this does
not establish a persistent node fault or explain the earlier SIGBUS.

### Follow-up recovery: EH2/run22262

The final health check caught a new fatal worker error at 15:44:12 UTC in
P17B/EH2/run22262, cluster 82243368. Chunks 7 and 8 had exited with SIGBUS on
lhws182 shortly after starting the converter, with empty stdout and reports.
The different execution phase was retained explicitly; neither failure was
classified as missing/broken input or an eligible LCG setup failure.

The run65318 transfer was allowed to verify and publish, bringing the archive
to 891 runs, before the failed controller exited. The retry guard first
refused while an original scheduler job still existed despite no `.running`
markers. A later successful global query proved that run's writers were
gone. Under the submission lock, both original failures, exact sources and
all 45 fingerprints covering nine good chunks were saved before the single
authorized second attempt, cluster 82244010 at 15:53:27 UTC. Other runs'
unrelated jobs were retained. All 32 affected mini headers and the configured
LCG verification of a good chunk passed. The full catalog remains 170 inputs
with zero missing/broken omissions and zero new gaps. Portable original proof
is `provenance/worker-recovery-run22262-EH2.json`; the small ignored current
checkpoint is `build/oct14-22262-recovery-result.json`. Production code and
all existing guards remain unchanged; the deeper SIGBUS cause is unconfirmed.

run22262 reached 11/11 verified chunks at 15:59:21 UTC. All 45 old-good
fingerprints were unchanged, and both recoveries recorded zero new input
gaps. The resumed controller subsequently reverified run65318 before staging
cleanup, admitted run22269 and continued the run65333 portable transfer.

## 2026-10-14 run22540 C++ runtime-loader recovery

The 17:00 UTC hourly check found 896 verified portable runs and a failed
controller. In P17B/EH2/run22540, chunk 4 of cluster 82248333 exited rc=1 on
lhws161 at 16:53:40 UTC. Its entire stderr was the dynamic loader's exact
`/lib64/libstdc++.so.6: version 'GLIBCXX_3.4.31' not found` diagnostic naming
the configured campaign converter as both executable and requiring object.
stdout and the report were empty. This runtime failure is separate from
missing/broken mini evidence and must not create a catalog omission.

A successful fresh global query found two jobs, 183 staged runs (181 ready),
416,673,886,440 reserved bytes and no unresolved intents. The original run's
chunk 5 still had a live scheduler job and `.running` marker on lhws263; its
55,795,013-byte partial had not advanced since 16:55:15 UTC. A bounded SSH
probe of that execute node timed out. These observations do not prove the
writer dead, so no cancellation, timeout reset or manual replay was used.

The existing startup proof now admits only this additional exact loader
diagnostic, with the pinned worker, current receipt/chunk/time, fixed mini
catalog, empty stdout and unchanged failure snapshots. This case additionally
requires an existing empty report, no chunk ROOT/source-report artifacts
(including empty files and partials), and no OK marker. Artifact-directory
I/O/permission errors propagate; they are never treated as absence. Proof
records `phase=cxx_loader` and the converter SHA256. Other versions, paths,
diagnostics, mixed/SIGBUS, post-conversion, source, checksum and storage
failures remain ineligible. The worker, converter and LCG configuration are
unchanged.

This uses the existing visible `startup_recovery_pending` deferral so verified
transfers and unaffected admissions can continue while writers remain. A
fresh successful global query, no run writers/markers or unresolved intent,
exclusive proof, preserved failure/good-chunk journal and the existing
two-attempt cap are still required before automatic retry. The checker now
describes both setup and loader cases as known worker startup faults.

Validation: eight focused checks passed, then 67 relevant startup, pipeline,
preservation, broken-mini, content, converter, monitor and hourly tests ran:
63 passed and four skipped for unavailable supported ROOT/LCG runtimes.
Checks cover exact/mixed signatures, zero-sized and partial artifacts, I/O
and permission errors, proof preservation, unchanged good chunks, writer and
intent guards, continued verified transfer/admission, second-attempt
exhaustion and monitor clearance. Python/shell syntax and Git whitespace
checks passed. Current deployment, retained-writer and runtime evidence is
the small ignored `build/oct14-22540-recovery-result.json`; original portable
proof is `provenance/worker-startup-loader-run22540.json`.

## 2026-10-14 21:00 UTC run24134 verification-runtime recovery

The hourly owner found the controller stopped at P17B/EH2/run24134. Chunk 7
of cluster 82262020 finished conversion and its initial verification, then
the separate verification process exited 127 on lhws301 because its loader
could not open `libtbb.so.12`. Its nonempty conversion report and existing
186,601,609-byte ROOT output correctly exclude it from automatic startup
recovery. This is not missing/broken-mini evidence; the deeper runtime fault
remains unconfirmed.

Fresh global queries under the submission lock proved no run writers,
worker markers or unresolved intent. Catalog/chunk/source-report identities,
all 16 affected mini headers, quota and the 300-job/400-GiB guards passed.
The existing output independently verified under the configured LCG runtime:
12,335,363 retained events from 13,886,913 events in 16 sources. Original
stderr/stdout/report/failure marker, source list, receipt and 75 fingerprints
of the fifteen good chunks were preserved before the authorized second
attempt, cluster 82265639 at 21:04:20 UTC.

Worker lhws306 verified/reused chunk 7 at 21:04:45 UTC. All sixteen chunks
completed; the recovered output's SHA256, size and mtime and all 75 earlier
good fingerprints are unchanged. The fixed catalog remains 244 expected
inputs, 242 processed and two previously confirmed missing minis, with no
broken inputs or new gaps. The existing controller was restarted after its
local lock and process checks, preserving the other retained scheduler work.

No pipeline, worker, converter, classifier, physics, schema, source policy or
site setup changed or was deployed. The original proof and guarded retry
result are portable `provenance/worker-recovery-run24134-EH2.json`; current
controller/transfer/checker evidence is the small ignored
`build/oct14-24134-recovery-result.json`. Hourly and five-minute monitoring
remain enabled.

## 2026-10-15 00:00 UTC run26089 guarded SIGBUS recovery

The hourly check found the controller stopped at P17B/EH2/run26089. Chunks
7, 9 and 10 of cluster 82271310 exited 135 on lhws286 while ROOT streamed
float data during object writing. Their stdout and conversion reports were
empty; three ROOT partials and three source-report partials remained. This
is not startup-only or missing/broken-mini evidence. The deeper SIGBUS
cause remains unconfirmed.

A successful fresh global query under the submission lock proved an empty
campaign queue, no running worker markers or unresolved intent, and one previous
attempt. Fixed catalog/chunk identities, all 48 affected mini headers,
pinned code/worker/converter, source rows, quota and the 300-job/400-GiB
guards passed. A good chunk independently verified under the configured LCG
runtime, with its ROOT counts matching its source report. Original failures,
receipts, sources, six partial-file fingerprints and 45 fingerprints of the
nine good chunks were preserved before the authorized diagnostic second
attempt, cluster 82271436 at 00:04:21 UTC.

The three retry chunks completed on aws006, lhws090 and lhws201 by
00:11:32 UTC, with blank stderr and both conversion and subsequent ROOT
verification successful. At 00:13:10 UTC all 12 chunks were verified and
the global queue was empty. All 45 earlier good fingerprints and six
original partial fingerprints were unchanged. The fixed catalog remains 181
expected inputs, 180 processed and one earlier confirmed missing mini, with
no broken inputs or new gaps. A successful retry does not establish the
original crash's cause.

Only the existing guarded retry and single-controller restart were used;
production code, classifiers, physics, schema, runtime and site setup were
unchanged. Original proof is portable
`provenance/worker-recovery-run26089-EH2.json`; current controller, transfer,
queue and monitoring evidence is the small ignored
`build/oct15-26089-recovery-result.json`. Hourly and five-minute monitoring
remain enabled.
