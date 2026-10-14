# Muon-analysis fields and event matching

Use `--profile muon` with files containing `AdSimpleNL`, `CalibStats`, and
`ARMuon`. By default this profile keeps all entries in those trees, so the pool sample
with `NPmt > 5` and the AD sample with `AdSimpleNL.energy > 20 MeV` remain
available for analysis cuts.

Use `--profile muon --ad-selection on` to keep AD events with energy > 0.6 MeV
and pool events with `NPmt > 5`. Pool detectors are 5 and 6, and `NPmt` is read
from `CalibStats.nHit`. The converter checks AD/ARMuon row alignment and
applies one combined mask to all three trees. Other detectors, including RPC,
are retained. These same AD/pool cuts also apply in selected mini mode.

All AD energy, position, and event metadata are read from `AdSimpleNL`.
The shared branch names in `AdSimple`, `AdScaled`, and `AdScaledNL` are not
read or duplicated. The converter applies no new energy calibration.

The `ARMuon` fields follow `dyb.pdf`, page 2, and the AD/calibration mapping
below uses the stream variables on page 3 (13 October 2026), with `AdSimpleNL`
as the AD reconstruction source. Muon mode requires the original reconstruction
files when the minis omit `ARMuon`.

## ARMuon fields

Under `Event/Rec/ARMuon`, each original field gains the prefix `arm_` in the
output. For example, input `energy` becomes output `arm_energy`. Values and
types are unchanged.

| Requirement | Branches |
| --- | --- |
| Detector and time | `arm_detector`, `arm_triggerTimeSec`, `arm_triggerTimeNanoSec` |
| Reconstructed energy | `arm_energy`, `arm_rawEvis` |
| Position and time | `arm_x`, `arm_y`, `arm_z`, `arm_t` |
| Direction and time component | `arm_dx`, `arm_dy`, `arm_dz`, `arm_dt` |
| Reconstruction status | `arm_energyStatus`, `arm_positionStatus`, `arm_directionStatus` |
| Reconstruction quality | `arm_energyQuality`, `arm_positionQuality`, `arm_directionQuality` |

`arm_site`, `arm_triggerNumber`, and `arm_triggerType` are also kept for event
matching. The added `runNumber`, `sourceIndex`, and `sourceEntry` provenance
fields keep those unprefixed names. Status and
quality values are copied without interpretation. Apply the reconstruction
version's validity criteria before using a direction or fitted position;
zero-valued fields alone do not establish a successful reconstruction.

Converter v10 adds the pool `nHit > 5` cut to selected output in both profiles
and records per-pool rejection counts. Version v9 added shared AD selection
for aligned three-tree inputs. Like v8, muon mode stores `AdSimpleNL`,
`CalibStats`, and `ARMuon`. Earlier v6/v7
muon output also contained `AdSimple` and `AdScaled`; v6 used unprefixed
`ARMuon` names. For new output, point AD readers at `AdSimpleNL` and use
the prefixed muon names. Existing files retain their original schema.

## AD and calibration fields

The stream variables requested on page 3 map to the output as follows:

| Stream variable | Output branch |
| --- | --- |
| `Run` | `Event/Rec/AdSimpleNL.runNumber`, supplied by `--run-number` |
| `Site`, `Det` | `Event/Rec/AdSimpleNL.site`, `.detector` |
| `TrigNum`, `TrigType` | `Event/Rec/AdSimpleNL.triggerNumber`, `.triggerType` |
| `TrigSec`, `TrigNano` | `Event/Rec/AdSimpleNL.triggerTimeSec`, `.triggerTimeNanoSec` |
| `NPmt` | `Event/Data/CalibStats.nHit` |
| `TRms` | `Event/Data/CalibStats.MiddleTimeRMS` |
| `Quadrant`, `Q1`, `Q2` | Same-named branches in `Event/Data/CalibStats` |
| `QMax2Sum` | `Event/Data/CalibStats.MaxQ` |
| `MaxQ2Inch` | `Event/Data/CalibStats.MaxQ_2inchPMT` |
| `E`, `X`, `Y`, `Z` | `Event/Rec/AdSimpleNL.energy`, `.x`, `.y`, `.z` |

The original instruction marks the run index, `nHit`, and `MaxQ` mappings with
question marks. The converter preserves these named quantities and records
the run number provided by the caller. It does not redefine their physics
meaning. Check those conventions in the analysis reader before using them as
derived observables.

## Reading the AD and pool samples

`AdSimpleNL` and `CalibStats` have aligned output entries in muon mode. A ROOT
session can inspect the requested selections directly:

```cpp
TFile f("output/run21221.root");
auto ad = f.Get<TTree>("Event/Rec/AdSimpleNL");
auto stats = f.Get<TTree>("Event/Data/CalibStats");
ad->AddFriend(stats, "calib");

// AD candidates according to the supplied energy requirement.
ad->Scan("runNumber:detector:triggerTimeSec:triggerTimeNanoSec:energy",
         "detector >= 1 && detector <= 4 && energy > 20");

// Pool readouts satisfying the supplied NPmt requirement.
ad->Scan("runNumber:detector:triggerTimeSec:triggerTimeNanoSec:calib.nHit",
         "(detector == 5 || detector == 6) && calib.nHit > 5");
```

Detector codes 5 and 6 identify the inner and outer water pools in this input
format. These cuts produce candidate readouts. Coincidence building, veto
windows, flasher cuts, detector quality, and run selection belong in the
analysis.

## Matching ARMuon to other trees

In the original reconstruction format, `AdSimpleNL`, `CalibStats`, and
`ARMuon` are expected to describe the same events in the same row order.
With selection off, muon mode retains every row of all three trees, preserving
that alignment when it is present in each input file. With selection on, it
requires equal row counts and checks the six AD/ARMuon event fields below at
every input row. A mismatch stops conversion without publishing output.
It applies the combined AD-energy and pool-hit mask to all three trees,
preserving row correspondence. CalibStats has no event identity fields in
these minis; its original row pairing with AdSimpleNL is an input requirement.

Selection off also supports separate or differently ordered ARMuon streams
without assuming alignment. For an unfamiliar or modified input, verify
alignment by comparing these `AdSimpleNL` fields row by row
within the same source file:

```text
site, detector, triggerNumber, triggerTimeSec, triggerTimeNanoSec, triggerType
```

to these fields in `ARMuon`, in the same order:

```text
arm_site, arm_detector, arm_triggerNumber, arm_triggerTimeSec, arm_triggerTimeNanoSec, arm_triggerType
```

For `ARMuon`, `sourceIndex` identifies the source directly. For `AdSimpleNL`,
find the row of `Preservation/Sources` whose output range contains the AD
entry: `firstEntry <= entry < firstEntry + keptEntries`. That provenance
row's zero-based index is the corresponding source index.

After finding the matching AD row, read `CalibStats` at that AD row to obtain `nHit`. Require one
matching row; report absent or ambiguous matches instead of taking a nearby
timestamp. `sourceEntry` records a row's original position in its own tree
and is a shared row index only when the input trees are aligned. If the event
keys differ at the same row, use the keys to join the trees instead.
With selection on, `sourceEntry` retains the original input row number;
gaps mark removed rows and are expected. `Preservation/Sources.muonEntries`
records the input count, while `keptEntries` records the retained count in
each of the three trees.
`poolAtMost5Hits[2]` counts the original IWS and OWS rows with `nHit <= 5`.

`sourceIndex` starts at zero for each output file. Across several converted
files, include the output-file identity or resolve it through
`Preservation/Sources.path`, together with the run number. Trigger numbers
alone are insufficient because different detectors can share a number.

Compute time differences with integer nanoseconds:

```cpp
std::int64_t stamp = std::int64_t(triggerTimeSec) * 1000000000LL
                   + triggerTimeNanoSec;

// The corresponding timestamp in ARMuon uses the prefixed fields.
std::int64_t arm_stamp = std::int64_t(arm_triggerTimeSec) * 1000000000LL
                       + arm_triggerTimeNanoSec;
```

Converting an epoch timestamp to floating-point seconds before subtraction
can lose nanosecond precision. Input-list order is preserved; events are not
globally sorted or deduplicated. Use the time fields when building a time
ordered analysis stream.

## Scope of the retained data

This profile covers the named scalar fields in the supplied instruction.
It does not preserve all contents of the original ROOT file. RPC tracks,
per-PMT waveforms or calibrated hit arrays, run headers, detector conditions,
and live-time counters are outside this schema. Keep the corresponding
external inputs if the analysis needs them. In particular, the timestamp
span in `Preservation/Sources` does not correct for DAQ dead time, muon vetoes,
overlapping files, or missing data.
