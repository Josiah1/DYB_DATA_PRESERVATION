# TWin input compatibility

Target: private GitHub repository `Josiah1/DayaBay-TWin`,
[`Retw_lijj/src_FD` at `d6b1b5ec89e158a353a77c528d2f8511dced76a8`](https://github.com/Josiah1/DayaBay-TWin/tree/d6b1b5ec89e158a353a77c528d2f8511dced76a8/Retw_lijj/src_FD).
This is the current-reader target, not the older cluster source audit.
No TWin source or cluster analysis worktree is modified by this change.

Validated on September 28 (local date), ROOT 6.40.02 at IHEP: **2 files /
1,509,445 readouts**, with zero retained-value or reader differences. With the
test-only initialization clock described below, all **23 Event branches**
and RunInfo match exactly: H 6,079 events, Gd 1,189, H/fastn 78,273, H/acpo
30,137. The output chunk is 40,919,903 bytes. This tests merged source-file
boundaries; it is not a full-run or archive-wide physics validation.

## Contract

The current `InputReader` reads only `Event/Rec/AdSimpleNL` and
`Event/Data/CalibStats`; it does not need AdScaledNL or a synthetic copy of it.
The converter keeps their entry alignment and input order, with scalar leaves:

| Tree | Type | Fields used by this TWin reader |
| --- | --- | --- |
| AdSimpleNL | Short_t | site, detector |
| AdSimpleNL | UInt_t | triggerType, triggerNumber |
| AdSimpleNL | Int_t | triggerTimeSec, triggerTimeNanoSec |
| AdSimpleNL | Float_t | energy, x, y, z |
| CalibStats | Int_t | nHit |
| CalibStats | Float_t | Quadrant, Q1, Q2, MaxQ, MaxQ_2inchPMT, Kurtosis, time_PSD_local_RMS |

`MiddleTimeRMS` is also preserved for other analyses, plus the provenance
`runNumber` and `Preservation/Sources` tree. Timing fields remain `Double_t`.
The reader takes the run number from the **filename**, not `runNumber`.
New chunks therefore use `preserved.0048734.slim.000000.root`: a dot-delimited
seven-digit run followed by a six-digit, zero-based chunk index. Avoid a
different `.NNNNNNN.` field in any parent directory: TWin scans the full path.

ROOT compression is ZSTD 9 (setting 509); all branch basket targets are
262,144 bytes. The reader's non-zlib path delegates decompression to ROOT.
Event branches remain fixed-width, with zero entry-offset-table length and
fully written baskets. Flushing at source boundaries preserves per-file
compression timing; a final basket may be smaller than the target size.

No energy selection is applied by default. TWin corrects AD energies itself;
its normal threshold is 0.7 MeV, while `--acpo` uses 0.3 MeV. An opt-in raw
0.7 MeV preservation cut is not a lossless substitute for these selections.

## Run the analysis

After preservation and verification of a complete run, build an ordered list
of that run's chunks and run Retw once, from its calibration-file directory:

```bash
python3 preserve.py verify-local /mounted/archive/P17B/EH1/run48734
printf '%s\n' /mounted/archive/P17B/EH1/run48734/outputs/preserved.0048734.slim.*.root \
  | LC_ALL=C sort > /analysis/run48734.list
cd /path/to/DayaBay-TWin/Retw_lijj/src_FD
./Retw /analysis/run48734.list /analysis/run48734.TWin.root /analysis/run48734.RunInfo.txt H
```

Use a fresh RunInfo path: TWin appends to it. Do not analyze each chunk
independently; coincidence and muon state must continue across chunk boundaries.
Older cluster readers and separate background executables require their own
compatibility check. Deployment and a worker-node pilot remain separate steps.

## Reproduce the bounded regression

`tests/check_twin.py` converts exactly the first two adjacent files in fixed
catalog `P17B/EH1/run48734.list` into one chunk. It checks all 19 retained
source branches bitwise, their order, generated run numbers, ROOT compression
and basket settings, and double-precision timing round trips. It then runs
the unmodified GitHub Retw on the original two-file list and on the preserved
chunk. The actual `InputReader` is also checked readout-by-readout, without
changing its behavior. Physics comparisons check every output Event branch
bitwise (including variable-length arrays), entry counts and byte-identical
RunInfo in H, Gd, H/fastn and H/acpo modes. A zero-entry physics output is a
failure, not a vacuous pass.

**Existing TWin clock dependence:** `BuildEvent.C` initializes previous pool
and IWS muon timestamps using the job-start wall clock. Before the first
relevant muon, the corresponding output time is therefore not reproducible
across processes. A native H run and an identical-input repeat both produced
6,079 events; only `T2PrevIwsMu` differed. Original versus preserved input had
the same single-field difference, with all other branches and RunInfo equal.
The regression retains this native control and, for strict full-field physics
comparison, wraps only TWin's directly linked `gettimeofday` calls at link
time to a fixed 1,800,000,000 seconds. No columns are ignored or rounded.
This is a test-only initialization clock, not a data change or a production
TWin fix. Acquisition timestamps and preservation CPU/wall timing use their
normal, unmodified clocks.

The pinned LCG 110 EL9/GCC13 runtime supplies ROOT and CLHEP. The test uses
the C++ driver and explicitly links the installed system `libdeflate`:
the upstream Makefile's `gcc` link omits libm in this environment, and the
libdeflate pkg-config file is absent. No dependency is installed and no
upstream source is patched. All remote outputs live in a private temporary
directory, automatically removed on exit. No scheduler jobs are submitted.

From this preservation repository on the local machine, with GitHub and IHEP
access already configured:

```bash
test_work=$(mktemp -d)
git clone --filter=blob:none --sparse https://github.com/Josiah1/DayaBay-TWin.git "$test_work/TWin"
git -C "$test_work/TWin" sparse-checkout set Retw_lijj/src_FD Common
git -C "$test_work/TWin" checkout d6b1b5ec89e158a353a77c528d2f8511dced76a8
set -o pipefail
git -C "$test_work/TWin" ls-files 'Retw_lijj/src_FD/*.C' 'Retw_lijj/src_FD/*.h' \
  Retw_lijj/src_FD/CaliParameters_withoutaverage.txt Common/Cuts.h \
  | sed 's|^|TWin/|' \
  | COPYFILE_DISABLE=1 tar -czf - src/slim_root.cpp tests/check_twin.py \
      estimates/noenergy-2026-09-27/benchmark.py estimates/noenergy-2026-09-27/plan.json \
      catalog_backups/P17B/EH1/run48734.list -C "$test_work" -T - \
  | bash ~/.agents/skills/ssh-ops/scripts/remote-run.sh dayabay1 tests/check_twin.sh --pubkey-auth \
  | tee estimates/twin-compatibility.json
```

The [small result](estimates/twin-compatibility.json) records source identities,
code hashes, retained-value hashes, the native clock control, per-source timing
rows and physics-output hashes; the data files themselves are not committed.
This is a bounded compatibility test, not archive-wide
validation or a representative throughput measurement. Production still
requires the explicit start order and mounted destination disk.
