#!/usr/bin/env bash
set -eo pipefail
unset PYTHONHOME PYTHONPATH
source "${LCG_VIEW:-/cvmfs/sft.cern.ch/lcg/views/LCG_110/x86_64-el9-gcc13-opt}/setup.sh"
set -u
python3 - "$@" <<'PY'
import datetime
import hashlib
import json
from pathlib import Path
import sys
import ROOT

ROOT.PyConfig.DisableRootLogon = True
ROOT.PyConfig.StartGUIThread = False
ROOT.gROOT.SetBatch(True)
issues = []
for name in sys.argv[1:]:
    path = Path(name)
    before = path.stat()
    file = ROOT.TFile.Open(name, 'READ')
    if not file or file.IsZombie() or file.TestBit(ROOT.TFile.kRecovered):
        raise RuntimeError('unusable ROOT input, not a confirmed empty tuple: ' + name)
    keys = [(key.GetName(), key.GetClassName()) for key in file.GetListOfKeys()]
    if keys != [('AdEnergy', 'TNtuple')] or file.Get('AdEnergy').GetEntries() != 0:
        raise RuntimeError('input is not an AdEnergy-only empty tuple: ' + name)
    file.Close()
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError('input changed during content inspection: ' + name)
    issues.append(dict(path=name, bytes=after.st_size, sha256=digest.hexdigest(),
                       keys=[dict(name='AdEnergy', classname='TNtuple', entries=0)],
                       required={'Event/Rec/AdSimpleNL': False, 'Event/Data/CalibStats': False},
                       observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')))
print(json.dumps(issues))
PY
