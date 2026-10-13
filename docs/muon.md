# Muon-analysis fields and event matching

Use `--profile muon` with original reconstruction files. This profile preserves
the field requirements in `dyb.pdf`, pages 2 and 3 (13 October 2026). It keeps
all entries, so the requested pool sample with `NPmt > 5` and AD sample with
`AdSimple.energy > 20 MeV` remain available for analysis cuts.

The mini profile supplies the existing `AdSimpleNL` and `CalibStats` fields.
It cannot recover `ARMuon` or a different AD reconstruction from a mini file
that omitted those trees. `AdSimple.energy` and `AdSimpleNL.energy` are separate
stored quantities; the converter does not substitute one for the other or
apply a new calibration.

## ARMuon fields

All fields below retain their original names under `Event/Rec/ARMuon`:

| Requirement | Branches |
| --- | --- |
| Detector and time | `detector`, `triggerTimeSec`, `triggerTimeNanoSec` |
| Reconstructed energy | `energy`, `rawEvis` |
| Position and time | `x`, `y`, `z`, `t` |
| Direction and time component | `dx`, `dy`, `dz`, `dt` |
| Reconstruction status | `energyStatus`, `positionStatus`, `directionStatus` |
| Reconstruction quality | `energyQuality`, `positionQuality`, `directionQuality` |

`site`, `triggerNumber`, and `triggerType` are also kept for event matching.
The converter adds source identity and the supplied run number. Status and
quality values are copied without interpretation. Apply the reconstruction
version's validity criteria before using a direction or fitted position;
zero-valued fields alone do not establish a successful reconstruction.

## AD and calibration fields

The stream variables requested on page 3 map to the output as follows:

| Stream variable | Output branch |
| --- | --- |
| `Run` | `Event/Rec/AdSimple.runNumber`, supplied by `--run-number` |
| `Site`, `Det` | `Event/Rec/AdScaled.site`, `.detector` |
| `TrigNum`, `TrigType` | `Event/Rec/AdScaled.triggerNumber`, `.triggerType` |
| `TrigSec`, `TrigNano` | `Event/Rec/AdScaled.triggerTimeSec`, `.triggerTimeNanoSec` |
| `NPmt` | `Event/Data/CalibStats.nHit` |
| `TRms` | `Event/Data/CalibStats.MiddleTimeRMS` |
| `Quadrant`, `Q1`, `Q2` | Same-named branches in `Event/Data/CalibStats` |
| `QMax2Sum` | `Event/Data/CalibStats.MaxQ` |
| `MaxQ2Inch` | `Event/Data/CalibStats.MaxQ_2inchPMT` |
| `E`, `X`, `Y`, `Z` | `Event/Rec/AdSimple.energy`, `.x`, `.y`, `.z` |

The original instruction marks the run index, `nHit`, and `MaxQ` mappings with
question marks. The converter preserves these named quantities and records
the run number provided by the caller. It does not redefine their physics
meaning. Check those conventions in the analysis reader before using them as
derived observables.

## Reading the AD and pool samples

The AD trees and `CalibStats` have aligned output entries in muon mode. A ROOT
session can inspect the requested selections directly:

```cpp
TFile f("output/run21221.root");
auto ad = f.Get<TTree>("Event/Rec/AdSimple");
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

Do not assume that `ARMuon` row `i` describes AD row `i`. Its source may contain
a separate or differently ordered stream. Use this event identity within a
converted file:

```text
sourceIndex, site, detector, triggerNumber, triggerTimeSec, triggerTimeNanoSec
```

`AdSimple`, `AdScaled`, and `ARMuon` retain these fields. After finding the
matching AD row, read `CalibStats` at that AD row to obtain `nHit`. Require one
matching row; report absent or ambiguous matches instead of taking a nearby
timestamp. `sourceEntry` records a row's original position in its own tree
and is not an event key across different trees.

`sourceIndex` starts at zero for each output file. Across several converted
files, include the output-file identity or resolve it through
`Preservation/Sources.path`, together with the run number. Trigger numbers
alone are insufficient because different detectors can share a number.

Compute time differences with integer nanoseconds:

```cpp
std::int64_t stamp = std::int64_t(triggerTimeSec) * 1000000000LL
                   + triggerTimeNanoSec;
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
