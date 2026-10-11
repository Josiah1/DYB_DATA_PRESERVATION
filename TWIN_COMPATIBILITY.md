# TWin input compatibility

## Live cluster src_FD recheck, October 11

The live `/dybfs2/users/lijj16/TWin/Retw_lijj/src_FD` source was checked
again on `dayabay1` (`lxlogin001.ihep.ac.cn`). **All 19 source variables
copied into the active analysis are stored for retained readouts.** This
includes every input to the active residual-flasher cuts. The unchanged
cluster reader still requires a tree-layout migration before reading these
compressed files.

The cluster checkout is at `2c6f7ac5e8f36c09a263f813e6da09d3881ff1c0`,
with existing changes to `Common/Cuts.h` and `src_FD/BuildEvent.C`. The check
used those live files, without modifying them. `Retw.C:13` enables
`Validate_AdSimple`; the AdScaled/AdTime energy-position alternatives are
disabled. Source hashes, branch mappings, sample identities and results are
recorded in [src-fd-compatibility.json](estimates/src-fd-compatibility.json).

The active input contract follows `Retw.C:239-278` through
`SubEvt.C:53-78`, `CachedQueueInsert.C` and `BuildEvent.C`:

| Compressed tree | ROOT scalar type | Preserved source variables |
| --- | --- | --- |
| `Event/Rec/AdSimpleNL` | `Short_t` | `site`, `detector` |
| `Event/Rec/AdSimpleNL` | `UInt_t` | `triggerType`, `triggerNumber` |
| `Event/Rec/AdSimpleNL` | `Int_t` | `triggerTimeSec`, `triggerTimeNanoSec` |
| `Event/Rec/AdSimpleNL` | `Float_t` | `energy`, `x`, `y`, `z` |
| `Event/Data/CalibStats` | `Int_t` | `nHit` |
| `Event/Data/CalibStats` | `Float_t` | `MiddleTimeRMS`, `Quadrant`, `Q1`, `Q2`, `MaxQ`, `MaxQ_2inchPMT`, `Kurtosis`, `time_PSD_local_RMS` |

`MaxQ` supplies `QMax2Sum`, `MaxQ_2inchPMT` supplies `MaxQ2Inch`, and
`nHit` supplies `NPmt`. Ratios, radii, flasher ellipses, muon labels and
coincidence history are derived from these inputs. `Run` is parsed from
the filename; the converter also stores an `Int_t runNumber` branch.

### Actual portable-disk sample

One recent completed chunk was read from Ubuntu's portable-disk archive:

```text
/media/josiah/LAYJ萤捷/DYB_DATA_PRESERVATION/data/P17B/EH1/run51263/outputs/preserved.0051263.slim.000000.root
```

Its processed source is mini file `run_0051263/..._0016.root`. Applying
the production selection to its **749,788** source readouts gives
**608,677** retained readouts: 180,447 AD and 428,230 non-AD. All 19 branch
types, lengths and ordered byte hashes match the saved chunk, covering
**11,564,863 scalar values**, with zero differing branch hashes. Both
event trees have 608,677 entries, and every stored `runNumber` is 51263.
The file uses ROOT compression setting 509.

For this same file, the six metadata fields used by the old reader were
also compared directly between the original reconstruction's `AdScaledNL`
and the mini's `AdSimpleNL`: all **749,788** entries agree bitwise, with
zero mismatches. This checks the metadata relocation in this sample.

Only the named 13,032,343-byte compressed chunk was copied temporarily to
the Mac for its ROOT read. Its SHA-256 matched the Ubuntu file, and the
temporary copy and SSH connection were removed afterward. Original data
remained on IHEP. No dependencies were installed or jobs submitted.

### Compatibility limits

- **Old reader layout:** `Retw.C:390-395` constructs an `AdScaledNL` chain;
  lines 124-163 require its entry alignment, and lines 240-245 read the six
  metadata fields there. The saved chunk has only `AdSimpleNL` and
  `CalibStats`. Route those six reads to `AdSimpleNL`, remove the AdScaled
  chain dependencies, and remove unused legacy branch bindings when
  migrating this reader. No event variable needs adding for its active
  `Validate_AdSimple` mode. The disabled reconstruction modes are outside
  this conclusion.
- **Selected readouts:** production keeps raw AD `energy > 0.6 MeV` and
  every non-AD entry; lower-energy/NaN AD readouts are absent. This sample
  drops 141,111 AD readouts under that policy. The live H/Gd code corrects
  energy before its default 0.7 MeV cut. Full physics equivalence, including
  corrections, trigger grouping and live time, was not checked. The newer
  GitHub reader's 0.3 MeV `--acpo` analysis cannot recover these discarded
  readouts; that option is not present in this older cluster executable.
- **External analysis inputs:** `CaliParameters_withoutaverage.txt`,
  `Fixer.C` and `Common/Cuts.h` must accompany the analysis. Their live
  hashes are recorded; they are not event variables inside the archive.
- **Bounded verification:** this is one full chunk comparison, not an
  archive-wide scan or a TWin physics rerun. `CalibStatsReader.C` was
  unreadable with `PermissionError`; its readable header contains the
  `Init`/`GetEntry` definitions and bindings used in the trace. No rebuild
  was attempted. The sampled run has 20 separately recorded broken-mini
  gaps and one processed input; its completed transaction does not imply
  a complete source catalog.

## GitHub reader regression, September 28

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

### Contract

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
0.6 MeV preservation cut is not a lossless substitute for these selections.

### Run the analysis

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

### Reproduce the bounded regression

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
