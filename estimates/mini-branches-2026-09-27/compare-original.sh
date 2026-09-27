#!/usr/bin/env bash
# Read-only, six-pair original-recon versus mini check. No production jobs or data writes.
set -eo pipefail
source /cvmfs/sft.cern.ch/lcg/views/LCG_110/x86_64-el9-gcc13-opt/setup.sh
set -u
timeout 240s python3 -u - <<'PY'
import datetime
import json
import os
import socket
import time

import numpy as np

FIELDS = {
    'Event/Rec/AdSimpleNL': {
        'site': 'Short_t', 'detector': 'Short_t', 'triggerType': 'UInt_t',
        'triggerNumber': 'UInt_t', 'triggerTimeSec': 'Int_t',
        'triggerTimeNanoSec': 'Int_t', 'energy': 'Float_t',
        'x': 'Float_t', 'y': 'Float_t', 'z': 'Float_t',
    },
    'Event/Data/CalibStats': {
        'nHit': 'Int_t', 'MiddleTimeRMS': 'Float_t', 'Quadrant': 'Float_t',
        'Q1': 'Float_t', 'Q2': 'Float_t', 'MaxQ': 'Float_t',
        'MaxQ_2inchPMT': 'Float_t', 'Kurtosis': 'Float_t',
        'time_PSD_local_RMS': 'Float_t',
    },
}
KEYS = ('site', 'detector', 'triggerNumber', 'triggerTimeSec', 'triggerTimeNanoSec')
# First three are the canvas samples; last three are the original August pilot.
SOURCES = [
    '/dybfs/rec/P21A/rec/runs_0081000/runs_0081700/run_0081743/recon.Neutrino.0081743.Physics.EH3-Merged.P21A-I._0234.root',
    '/dybfs/rec/P21A/rec/runs_0082000/runs_0082300/run_0082379/recon.Neutrino.0082379.Physics.EH3-Merged.P21A-I._0092.root',
    '/dybfs/rec/P21A/rec/runs_0082000/runs_0082900/run_0082989/recon.Neutrino.0082989.Physics.EH3-Merged.P21A-I._0128.root',
    '/dybfs/rec/P17B/rec/runs_0048000/runs_0048700/run_0048731/recon.Neutrino.0048731.Physics.EH1-Merged.P17B-I._0071.root',
    '/dybfs/rec/P17B/rec/runs_0048000/runs_0048600/run_0048610/recon.Neutrino.0048610.Physics.EH2-Merged.P17B-I._0157.root',
    '/dybfs/rec/P17B/rec/runs_0048000/runs_0048200/run_0048236/recon.Neutrino.0048236.Physics.EH3-Merged.P17B-I._0071.root',
]


def emit(kind, **values):
    print(json.dumps(dict(kind=kind, **values), sort_keys=True, allow_nan=False), flush=True)


def compare_values(a, b):
    if a.dtype != b.dtype or a.shape != b.shape:
        raise ValueError('different comparison dtype or shape')
    both_nan = np.isnan(a) & np.isnan(b)
    unequal = ~((a == b) | both_nan)
    bits = np.dtype('u' + str(a.dtype.itemsize))
    bit_unequal = a.view(bits) != b.view(bits)
    result = dict(dtype=str(a.dtype), compared=len(a), numeric_mismatches=int(unequal.sum()),
                  bitwise_mismatches=int(bit_unequal.sum()), both_nan=int(both_nan.sum()),
                  original_nonfinite=int((~np.isfinite(a)).sum()),
                  mini_nonfinite=int((~np.isfinite(b)).sum()))
    if np.any(unequal):
        indexes = np.flatnonzero(unequal)[:3]
        result['examples'] = [dict(matched_index=int(i), original=repr(a[i].item()),
                                   mini=repr(b[i].item())) for i in indexes]
        finite = unequal & np.isfinite(a) & np.isfinite(b)
        result['max_finite_abs_difference'] = (
            float(np.max(np.abs(a[finite].astype('f8') - b[finite].astype('f8'))))
            if np.any(finite) else None)
    return result


def align_events(original, mini):
    left = np.rec.fromarrays([original[key] for key in KEYS], names=KEYS)
    right = np.rec.fromarrays([mini[key] for key in KEYS], names=KEYS)
    if np.array_equal(left, right):
        return slice(None), slice(None), dict(same_order=True, matched=len(left),
                                             only_original=0, only_mini=0)
    # Do not silently many-to-many join nonunique event keys.
    if len(np.unique(left)) != len(left) or len(np.unique(right)) != len(right):
        raise ValueError('event sequences differ and identity keys are not unique')
    common, first, second = np.intersect1d(left, right, return_indices=True)
    return first, second, dict(same_order=False, matched=len(common),
                               only_original=len(left)-len(common), only_mini=len(right)-len(common))


def derived(a):
    # Promote before arithmetic to match the explicit doubles in the canvas collector.
    # Canvas diagnostic only: raw energy, no TWin CorrEnergy or muon/coincidence logic.
    with np.errstate(divide='ignore', invalid='ignore', over='ignore'):
        q_ratio = a['Q1'].astype('f8') / a['Q2'].astype('f8')
        radius_m = np.hypot(a['x'].astype('f8'), a['y'].astype('f8')) / 1000.
        z_m = a['z'].astype('f8') / 1000.
        kurtosis, max_q = a['Kurtosis'].astype('f8'), a['MaxQ'].astype('f8')
        standard = np.sqrt(a['Quadrant'].astype('f8')**2 + (max_q/.45)**2)
        ellipse_kr = ((q_ratio-1.75)/.5)**2 + ((kurtosis-1.5)/2.5)**2
        group_a = (a['z'] > 2200) & (max_q + .025*kurtosis - .05 > 0)
        group_aa = a['time_PSD_local_RMS'] > 14
        group_ab = (a['time_PSD_local_RMS'] < 14) & (ellipse_kr > 1)
        group_b = (a['z'] > 2400) & (radius_m > 1)
        group_c = ((kurtosis+1.5)/1.5)**2 + ((max_q-.35)/.15)**2 < 1
        ad = (a['detector'] >= 1) & (a['detector'] <= 4)
        pre = ad & (a['energy'].astype('f8') > .7) & (standard < 1) & (a['MaxQ_2inchPMT'] < 100)
        flag = (group_a & group_aa) | (group_a & group_ab) | group_b | group_c
    return dict(q_ratio=q_ratio, radius_m=radius_m, z_m=z_m, standard_ellipse=standard,
                ellipse_KR=ellipse_kr, all_ad=ad, preselected=pre, residual_flag=pre & flag)


def self_check():
    a = np.array([0., 1., np.nan, np.inf, -np.inf], dtype='f4')
    assert compare_values(a, a.copy())['bitwise_mismatches'] == 0
    b = a.copy()
    b[0] = -0.
    assert compare_values(a, b)['numeric_mismatches'] == 0
    assert compare_values(a, b)['bitwise_mismatches'] == 1
    b[1] = 2.
    assert compare_values(a, b)['numeric_mismatches'] == 1
    keys = {key: np.array([1, 2, 3], dtype='i4') for key in KEYS}
    reordered = {key: value[[2, 0, 1]] for key, value in keys.items()}
    left, right, info = align_events(keys, reordered)
    assert info == dict(same_order=False, matched=3, only_original=0, only_mini=0)
    assert np.array_equal(keys['triggerNumber'][left], reordered['triggerNumber'][right])
    missing = {key: value[:2] for key, value in keys.items()}
    assert align_events(keys, missing)[2]['only_original'] == 1


self_check()
import ROOT
ROOT.PyConfig.DisableRootLogon = True
ROOT.gROOT.SetBatch(True)


def read_file(path):
    started = time.monotonic()
    initial = os.stat(path)
    arrays, schema = {}, {}
    counts = []
    with ROOT.TFile.Open(path, 'READ') as file:
        if not file or file.IsZombie():
            raise ValueError('cannot open ROOT input')
        for tree_path, fields in FIELDS.items():
            tree = file.Get(tree_path)
            if not tree:
                raise ValueError('missing tree ' + tree_path)
            counts.append(int(tree.GetEntries()))
            for name, expected in fields.items():
                leaf = tree.GetLeaf(name)
                if not leaf or leaf.GetTypeName() != expected or leaf.GetLen() != 1:
                    raise ValueError('missing/nonscalar/wrongly typed leaf ' + tree_path + '/' + name)
            schema[tree_path] = dict(fields)
            arrays.update(ROOT.RDataFrame(tree).AsNumpy(list(fields)))
    final = os.stat(path)
    if len(set(counts)) != 1 or counts[0] <= 0 or any(len(a) != counts[0] for a in arrays.values()):
        raise ValueError('inconsistent or empty tree counts')
    if (initial.st_size, initial.st_mtime_ns) != (final.st_size, final.st_mtime_ns):
        raise ValueError('input changed while reading')
    return arrays, dict(entries=counts[0], bytes=initial.st_size, mtime_ns=initial.st_mtime_ns,
                        schema=schema, read_wall_seconds=time.monotonic()-started)


started = time.monotonic()
emit('environment', utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
     host=socket.gethostname(), root_version=ROOT.gROOT.GetVersion(), numpy_version=np.__version__,
     event_key=KEYS, self_check='passed', expected_pairs=len(SOURCES),
     scope='All entries in six named pairs; original recon versus upstream mini, no event selection.')
passed = 0
for index, original in enumerate(SOURCES):
    period = original.split('/')[3]
    tail = original.removeprefix('/dybfs/rec/' + period + '/rec/')
    mini = '/lustrefs/juno26/dyw/rec/All-mini/' + tail.replace('/recon.', '/minirecon.')
    emit('start', index=index, original=original, mini=mini)
    record = dict(index=index, original=original, mini=mini,
                  sample='canvas' if index < 3 else 'earlier_pilot', passed=False)
    try:
        raw, record['original_info'] = read_file(original)
        small, record['mini_info'] = read_file(mini)
        left, right, record['alignment'] = align_events(raw, small)
        record['branches'] = {name: compare_values(raw[name][left], small[name][right]) for name in raw}
        first, second = derived(raw), derived(small)
        record['derived'] = {name: compare_values(first[name][left], second[name][right]) for name in first}
        record['populations'] = {name: dict(original=int(first[name].sum()), mini=int(second[name].sum()))
                                 for name in ('all_ad', 'preselected', 'residual_flag')}
        alignment = record['alignment']
        record['passed'] = (alignment['only_original'] == alignment['only_mini'] == 0 and
                            all(v['bitwise_mismatches'] == 0 for v in record['branches'].values()) and
                            all(v['numeric_mismatches'] == 0 for v in record['derived'].values()))
        del raw, small, first, second
    except Exception as error:
        record['error'] = str(error)
    passed += record['passed']
    emit('comparison', **record)
emit('summary', pairs=len(SOURCES), passed=passed, failed=len(SOURCES)-passed,
     elapsed_seconds=time.monotonic()-started)
raise SystemExit(0 if passed == len(SOURCES) else 1)
PY
