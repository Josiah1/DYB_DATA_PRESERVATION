# Daya Bay ROOT converter

`slim-root` copies selected reconstruction and calibration fields into smaller
ROOT files using Zstandard (ZSTD) compression at level 9. It preserves the stored numerical
values and writes source-file provenance. It runs on one CPU and does not need
a GPU, a batch scheduler, or the NuWa runtime for the retained scalar branches.

Two profiles are available for antineutrino detector (AD) and muon analyses:

| Profile | Required input | Event retention |
| --- | --- | --- |
| `mini` (default) | `AdSimpleNL` and `CalibStats` | All entries by default; optional raw `AdSimpleNL.energy > 0.6 MeV` cut for ADs |
| `muon` | `AdSimpleNL`, `CalibStats`, and `ARMuon` | All entries by default; optional AD cut applied to all three aligned trees |

AD energy, position, and event metadata all come from `AdSimpleNL`, as in the
base converter. `AdSimple`, `AdScaled`, and `AdScaledNL` are neither required
nor stored. Muon mode adds the `ARMuon` fields described in the
[muon-analysis guide](docs/muon.md). A mini file without `ARMuon` cannot supply
that information; use an original reconstruction file containing it. Muon
mode stops if a required tree or field is absent.

## Build and test

To check out this branch, replace `REPOSITORY_URL` with its Git repository URL:

```sh
git clone --single-branch --branch converter-share REPOSITORY_URL dyb-converter
cd dyb-converter
```

Use Linux or macOS with a C++17 compiler, GNU Make, and ROOT 6 built with ZSTD
support. Python 3 is needed only for the tests. Load your ROOT environment
before building; `root` and `root-config` must be on `PATH`.

```sh
root-config --version
make
bin/slim-root --version
make test
```

You can specify a compiler or Python executable:

```sh
make CXX=g++ ROOT_CONFIG=/path/to/root/bin/root-config
make test PYTHON=/path/to/python3
```

The tests generate small files in a temporary directory and remove them after
use. They check event selection, values and types in all added muon fields,
multiple input files, independent `ARMuon` ordering with selection off,
shared selection of aligned trees, rejection of mismatched event rows, failure cleanup, and
refusal to overwrite existing outputs. Conflicting values in unused AD
reconstruction trees are checked to have no effect on the output.

## Convert one run

Create a text file with one absolute input path per line. Use files from one
run and one hall, in the order you want them written. Blank lines are ignored;
comments, relative paths, and paths containing tabs are not supported. Duplicate path strings are
rejected. Avoid listing the same file through two different symlinks.

```text
/data/reconstruction/run21221/recon.part0001.root
/data/reconstruction/run21221/recon.part0002.root
```

Save this as `inputs.list`, create the output directory, and run:

```sh
mkdir -p output
bin/slim-root \
  --input-list inputs.list \
  --output output/run21221.root \
  --source-report output/run21221.sources.tsv \
  --run-number 21221 \
  --profile muon

bin/slim-root --verify output/run21221.root
```

`--run-number` is supplied by the caller and is written into the output. The
converter does not infer it from filenames or check it against a run header.
Check the input list before starting.

For mini files, use `--profile mini`. Add `--ad-selection on` only if the
analysis accepts discarding AD entries with raw `AdSimpleNL.energy <= 0.6 MeV`
or NaN. The cut applies to detector codes 1 through 4. Every other detector
entry is retained, regardless of energy or `nHit`. The default is `off`.
Muon mode also accepts `--ad-selection on`. It requires equal input row counts
and matching AD/ARMuon event identifiers at every row, then applies the same
AD energy mask to all three trees. The decision uses `AdSimpleNL.energy`,
never `ARMuon.energy`. With the default `off`, all entries are retained.
Both settings preserve the `AdSimpleNL.energy > 20 MeV` and pool `nHit > 5`
samples for later analysis cuts.

Run one conversion per output path. Both output names must be new and their
parent directories must exist. Output filesystems must support hard links.
Inputs are opened read-only. Missing or broken inputs cause a nonzero exit;
the converter does not silently skip files or remove originals.

## Output layout

```text
Event/
  Rec/
    AdSimpleNL       # both profiles
    ARMuon           # muon profile
  Data/
    CalibStats       # both profiles
Preservation/
  Sources
  selection
  profile
  converterVersion
```

The event trees contain these branches:

| Tree | Retained fields |
| --- | --- |
| `AdSimpleNL` | `site`, `detector`, `triggerType`, `triggerNumber`, `triggerTimeSec`, `triggerTimeNanoSec`, `energy`, `x`, `y`, `z`; added `runNumber` |
| `CalibStats` | `nHit`, `MiddleTimeRMS`, `Quadrant`, `Q1`, `Q2`, `MaxQ`, `MaxQ_2inchPMT`, `Kurtosis`, `time_PSD_local_RMS` |
| `ARMuon` | The six event metadata fields above, plus all 16 reconstruction, status, and quality fields in the [muon schema](docs/muon.md#armuon-fields), each with the prefix `arm_` |

`ARMuon` also has `runNumber` (`Int_t`), `sourceIndex`
(`Long64_t`, zero based), and `sourceEntry` (`Long64_t`, zero based). These
identify the original source row. These three added provenance names have no
prefix. Original
`ARMuon` fields are written as `arm_site`, `arm_energy`, and so on; input
branch names remain unchanged. The expected scalar types in `ARMuon`,
using original field names below, are `Short_t`
for `site` and `detector`, `UInt_t` for trigger number and type, `Int_t` for
trigger timestamps and reconstruction status, and `Float_t` for reconstruction
values and quality. Conversion rejects other types rather than casting them.

`AdSimpleNL` and `CalibStats` use the same retained row order. Their input
entry counts must agree; the input format must already pair calibration and
reconstruction entries.
Original reconstruction files are expected to align `ARMuon` with those
trees. With selection off, each tree is copied in its original order without
enforcing equal counts or matching event identifiers for `ARMuon`. With
selection on, muon mode checks AD/ARMuon alignment, including rows that will
be discarded, and retains the same rows in all three trees. See the guide
for the event keys and provenance fields.

## Provenance and verification

`Preservation/Sources` has one row per listed input, in list order:

| Field | Meaning |
| --- | --- |
| `path` | Input path as listed |
| `firstEntry`, `entries`, `keptEntries` | Starting output row in the base trees, input row count, retained row count |
| `inputBytes` | Input file size |
| `firstTriggerTimeSec`, `firstTriggerTimeNanoSec`, `lastTriggerTimeSec`, `lastTriggerTimeNanoSec` | Minimum and maximum source trigger times, including entries removed by the optional cut |
| `dataDurationSeconds` | Difference between those two trigger times |
| `processingWallSeconds`, `processingCpuSeconds` | Time spent converting that source |
| `adBelow0p3MeV[4]` | Counts per AD with raw `AdSimpleNL.energy` at or below 0.3 MeV, including NaN |
| `adFrom0p3To0p6MeV[4]` | Counts per AD with raw `AdSimpleNL.energy` above 0.3 and at or below 0.6 MeV |
| `muonEntries` | Muon mode only: number of `ARMuon` entries in this source (`Long64_t`) |

The tab-separated (TSV) report contains the base-tree counts, timing, byte size, and per-AD
energy-bin counts. The extra `muonEntries` counts are stored in the ROOT
provenance tree. The bin counts are written even when selection is off.
With selection on, `muonEntries` remains the original input count; the retained
ARMuon count is `keptEntries`. Its `sourceEntry` values keep their original
input row numbers and can therefore have gaps.
`dataDurationSeconds` is a timestamp span, not a dead-time-corrected live time.

Conversion writes temporary files beside the requested outputs, verifies the
ROOT result, then publishes the TSV followed by the ROOT file. Existing
outputs are never replaced. A caught error removes the temporary files; a
forced process kill can leave `.partial.<pid>` files or a TSV without a final
ROOT file. Remove leftovers only after checking that no process is writing
them. A report alone does not establish successful conversion.

`--verify` checks the schema, source accounting, timing, selected AD energies,
and muon stream counts and row provenance. For selected muon output it also
checks equal tree counts and matching AD/ARMuon event identities. It does not compare every output
value against the original files, verify a good-run list, or prove the input
catalog complete. Keep the original input list and source data until your
analysis validation is complete. Older converter versions have a different
metadata contract and should be verified with their corresponding executable.

## Common errors

| Error | Action |
| --- | --- |
| `muon mode requires tree` or `scalar numeric branch` | Inspect the input with ROOT; use an original reconstruction file containing the named tree and field |
| `tree entry mismatch` | Inspect the source file for incomplete or mismatched reconstruction/calibration trees |
| `muon selection requires aligned` | Check the AD/ARMuon counts and event identifiers; a shared row cut cannot be applied to different event streams |
| `refusing to overwrite` | Choose new output names or inspect and remove only an unwanted previous result |
| `cannot atomically publish` | Check permissions, free space, hard-link support, and whether another process created the destination |

Output size depends on the profile and input data. Start with a small input
list before allocating storage for a full run. Each listed file is processed
in sequence; no intermediate copy of the input data is made.
