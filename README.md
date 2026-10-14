# Daya Bay ROOT converter

`slim-root` copies selected reconstruction and calibration fields into smaller
ROOT files using Zstandard (ZSTD) compression at level 9. It preserves the stored numerical
values and writes source-file provenance. It runs on one CPU and does not need
a GPU, a batch scheduler, or the NuWa runtime for the retained scalar branches.

Every conversion requires and stores three aligned event trees:
`AdSimpleNL`, `CalibStats`, and `ARMuon`. There is no profile option.
All entries are retained by default. Optional AD energy > 0.6 MeV and pool
nHit > 5 cuts keep or remove the same original rows in all three trees.

AD energy, position, and event metadata all come from `AdSimpleNL`, as in the
base converter. `AdSimple`, `AdScaled`, and `AdScaledNL` are neither required
nor stored. The `ARMuon` fields are described in the
[muon-analysis guide](docs/muon.md). A mini file without `ARMuon` cannot supply
that information; use an input file containing all three trees. Conversion
stops if a required tree or field is absent.

## Build and test

To check out the standalone converter branch:

```sh
git clone --single-branch --branch converter-share https://github.com/Josiah1/DYB_DATA_PRESERVATION.git dyb-converter
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
multiple input files, shared selection of aligned trees, rejection of mismatched
event rows with cuts on or off, failure cleanup, and
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
  --ad-selection on

bin/slim-root --verify output/run21221.root
```

`--run-number` is supplied by the caller and is written into the output. The
converter does not infer it from filenames or check it against a run header.
Check the input list before starting.

`--ad-selection on` enables these detector-specific cuts:

- ADs (detector 1–4): keep raw `AdSimpleNL.energy > 0.6 MeV`; discard values
  at or below the threshold and NaN.
- Water pools (detector 5–6): keep `CalibStats.nHit > 5` (`NPmt > 5`);
  discard values at or below 5, regardless of energy.
- Other detectors, including RPC (7): keep every entry.

The converter always requires equal input row counts and matching AD/ARMuon
event identifiers at every row, then applies one mask to all three trees.
The decision uses `AdSimpleNL.energy` and `CalibStats.nHit`, never
`ARMuon.energy`. The default `--ad-selection off` retains all entries.
The selected sample includes all AD events above 20 MeV and all pool events
with `nHit > 5`.

These cuts preserve the retained values and row alignment; they do not
establish unchanged veto timing, trigger grouping, or live time in a downstream
analysis such as TWin. Retaining the complete trigger stream requires
`--ad-selection off`.

Run one conversion per output path. Both output names must be new and their
parent directories must exist. Output filesystems must support hard links.
Inputs are opened read-only. Missing or broken inputs cause a nonzero exit;
the converter does not silently skip files or remove originals.

## Output layout

```text
Event/
  Rec/
    AdSimpleNL
    ARMuon
  Data/
    CalibStats
Preservation/
  Sources
  selection
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

All three event trees use the same retained row order. Input entry counts
must agree, and the six AD/ARMuon event identifiers must match at every row,
including rows that will be discarded. These checks also apply with selection
off. CalibStats has no event identifiers, so the input format must already
pair its rows with AdSimpleNL; conversion preserves that pairing. See the
guide for the event keys and provenance fields.

## Provenance and verification

`Preservation/Sources` has one row per listed input, in list order:

| Field | Meaning |
| --- | --- |
| `path` | Input path as listed |
| `firstEntry`, `entries`, `keptEntries` | Starting output row in all three trees, input row count, retained row count |
| `inputBytes` | Input file size |
| `firstTriggerTimeSec`, `firstTriggerTimeNanoSec`, `lastTriggerTimeSec`, `lastTriggerTimeNanoSec` | Minimum and maximum source trigger times, including entries removed by the optional cut |
| `dataDurationSeconds` | Difference between those two trigger times |
| `processingWallSeconds`, `processingCpuSeconds` | Time spent converting that source |
| `adBelow0p3MeV[4]` | Counts per AD with raw `AdSimpleNL.energy` at or below 0.3 MeV, including NaN |
| `adFrom0p3To0p6MeV[4]` | Counts per AD with raw `AdSimpleNL.energy` above 0.3 and at or below 0.6 MeV |
| `muonEntries` | Number of input `ARMuon` entries (`Long64_t`), required to equal `entries` |
| `poolAtMost5Hits[2]` | Input counts with `nHit <= 5`, in IWS (5), OWS (6) order; removed when selection is on |

The tab-separated (TSV) report contains the shared row counts, timing, byte size, and per-AD
energy-bin counts. The extra `muonEntries` counts are stored in the ROOT
provenance tree. The bin counts are written even when selection is off.
The TSV columns `iwsAtMost5Hits` and `owsAtMost5Hits` contain the pool counts.
For selected output, `entries = keptEntries + sum(adBelow0p3MeV) +
sum(adFrom0p3To0p6MeV) + sum(poolAtMost5Hits)` for each source.
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

`--verify` checks the schema, source accounting, timing, selected AD energies and pool hit counts,
and row provenance. It always checks equal tree counts and matching AD/ARMuon
event identities. It does not compare every output
value against the original files, verify a good-run list, or prove the input
catalog complete. Keep the original input list and source data until your
analysis validation is complete. Older converter versions have a different
metadata contract and should be verified with their corresponding executable.

## Common errors

| Error | Action |
| --- | --- |
| `missing required tree` or `scalar numeric branch` | Inspect the input with ROOT; use a file containing all three trees and the named field |
| `tree entry mismatch` | Inspect the source file for incomplete or mismatched reconstruction/calibration trees |
| `conversion requires aligned` | Check the AD/ARMuon counts and event identifiers; all three trees must describe the same rows, even with cuts off |
| `unknown or incomplete argument: --profile` | Remove the old option; all three trees are always included |
| `refusing to overwrite` | Choose new output names or inspect and remove only an unwanted previous result |
| `cannot atomically publish` | Check permissions, free space, hard-link support, and whether another process created the destination |

Output size depends on the input data and selection. Start with a small input
list before allocating storage for a full run. Each listed file is processed
in sequence; no intermediate copy of the input data is made.
