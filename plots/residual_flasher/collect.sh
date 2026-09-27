#!/usr/bin/env bash
# Read three checked P21A/EH3 mini-files; emit histogram data, never modify inputs.
set -eo pipefail
source /cvmfs/sft.cern.ch/lcg/views/LCG_110/x86_64-el9-gcc13-opt/setup.sh
set -u
python3 -u - <<'PY'
import datetime
import json
import os
import socket
import time
import ROOT

ROOT.PyConfig.DisableRootLogon = True
ROOT.gROOT.SetBatch(True)
started = time.monotonic()
paths = [
    '/lustrefs/juno26/dyw/rec/All-mini/runs_0081000/runs_0081700/run_0081743/minirecon.Neutrino.0081743.Physics.EH3-Merged.P21A-I._0234.root',
    '/lustrefs/juno26/dyw/rec/All-mini/runs_0082000/runs_0082300/run_0082379/minirecon.Neutrino.0082379.Physics.EH3-Merged.P21A-I._0092.root',
    '/lustrefs/juno26/dyw/rec/All-mini/runs_0082000/runs_0082900/run_0082989/minirecon.Neutrino.0082989.Physics.EH3-Merged.P21A-I._0128.root',
]
sources = []
ad = ROOT.TChain('Event/Rec/AdSimpleNL')
stats = ROOT.TChain('Event/Data/CalibStats')
for path in paths:
    with ROOT.TFile.Open(path, 'READ') as file:
        rec, calib = file.Get('Event/Rec/AdSimpleNL'), file.Get('Event/Data/CalibStats')
        assert rec and calib and rec.GetEntries() == calib.GetEntries() > 0
        sources.append(dict(path=path, entries=rec.GetEntries(), bytes=os.stat(path).st_size))
    assert ad.Add(path) == stats.Add(path) == 1
stats.AddFriend(ad, 'ad')
frame = ROOT.RDataFrame(stats).Filter('ad.detector >= 1 && ad.detector <= 4')
definitions = {
    'q_ratio': 'double(Q1) / double(Q2)',
    'radius_mm': 'std::hypot(double(ad.x), double(ad.y))',
    'radius_m': 'radius_mm / 1000.0',
    'z_m': 'double(ad.z) / 1000.0',
    'ellipse_KR': 'std::pow((q_ratio - 1.75) / 0.5, 2) + std::pow((double(Kurtosis) - 1.5) / 2.5, 2)',
    'group_A': '(ad.z > 2200) && (double(MaxQ) + 0.025 * double(Kurtosis) - 0.05 > 0)',
    'group_Aa': 'time_PSD_local_RMS > 14',
    'group_Ab': '(time_PSD_local_RMS < 14) && (ellipse_KR > 1)',
    'group_B': '(ad.z > 2400) && (radius_mm > 1000)',
    'group_C': '(std::pow((double(Kurtosis) + 1.5) / 1.5, 2) + std::pow((double(MaxQ) - 0.35) / 0.15, 2)) < 1',
    'residual_flag': '(group_A && group_Aa) || (group_A && group_Ab) || group_B || group_C',
    'analysis_preselection': '(ad.energy > 0.7) && (std::sqrt(double(Quadrant)*Quadrant + std::pow(double(MaxQ)/0.45, 2)) < 1) && (MaxQ_2inchPMT < 100)',
}
for name, expression in definitions.items():
    frame = frame.Define(name, expression)
preselected = frame.Filter('analysis_preselection')
populations = {'all_ad': frame, 'preselected': preselected,
               'residual_flag': preselected.Filter('residual_flag')}
counts = {name: data.Count() for name, data in populations.items()}
raw_flag_count = frame.Filter('residual_flag').Count()
cut_counts = {name: preselected.Filter(expression).Count() for name, expression in {
    'A_and_Aa': 'group_A && group_Aa', 'A_and_Ab': 'group_A && group_Ab',
    'B': 'group_B', 'C': 'group_C',
}.items()}
flagged_above_2200 = populations['residual_flag'].Filter('ad.z > 2200').Count()
variables = [
    ('q_ratio', 100, 0., 5.),
    ('Kurtosis', 150, -5., 25.),
    ('time_PSD_local_RMS', 100, 0., 100.),
    ('MaxQ', 100, 0., 1.),
    ('z_m', 120, -3., 3.),
    ('radius_m', 120, 0., 3.),
    ('Quadrant', 100, 0., 2.),
    ('MaxQ_2inchPMT', 100, 0., 1.),
]
booked = {}
for variable, bins, lower, upper in variables:
    for population, data in populations.items():
        finite = data.Filter('std::isfinite(double(' + variable + '))')
        booked[variable, population] = (
            finite.Histo1D((variable + '_' + population, '', bins, lower, upper), variable),
            finite.Count(), finite.Min(variable), finite.Max(variable),
        )
totals = {name: int(action.GetValue()) for name, action in counts.items()}
histograms = []
for variable, bins, lower, upper in variables:
    record = dict(variable=variable, edges=[lower + (upper-lower)*i/bins for i in range(bins+1)])
    for population in populations:
        hist, finite_count, minimum, maximum = booked[variable, population]
        hist = hist.GetValue()
        values = dict(counts=[int(hist.GetBinContent(i)) for i in range(1, bins+1)],
                      underflow=int(hist.GetBinContent(0)), overflow=int(hist.GetBinContent(bins+1)),
                      nonfinite=totals[population]-int(finite_count.GetValue()))
        values.update(minimum=float(minimum.GetValue()) if finite_count.GetValue() else None,
                      maximum=float(maximum.GetValue()) if finite_count.GetValue() else None)
        assert sum(values['counts']) + values['underflow'] + values['overflow'] + values['nonfinite'] == totals[population]
        record[population] = values
    assert all(a >= b for a,b in zip(record['all_ad']['counts'], record['residual_flag']['counts']))
    histograms.append(record)
print(json.dumps(dict(
    utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), host=socket.gethostname(),
    root_version=ROOT.gROOT.GetVersion(), sources=sources, totals=totals, histograms=histograms,
    definitions=definitions, base_selection='detector >= 1 && detector <= 4',
    raw_residual_flag_count=int(raw_flag_count.GetValue()),
    residual_component_counts={name: int(action.GetValue()) for name, action in cut_counts.items()},
    residual_flag_above_z_2200mm=int(flagged_above_2200.GetValue()),
    note='Residual flag is a subset after E > 0.7 MeV, ellipse < 1 and MaxQ_2inchPMT < 100. No muon identification/veto or coincidence selection; not truth labels.',
    bin_convention='lower <= x < upper; underflow, overflow and nonfinite recorded separately',
    elapsed_seconds=time.monotonic()-started,
), indent=2, sort_keys=True))
PY
