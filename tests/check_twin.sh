#!/usr/bin/env bash
# Private, temporary IHEP regression. See TWIN_COMPATIBILITY.md for its payload.
set -eo pipefail
unset PYTHONHOME PYTHONPATH
source /cvmfs/sft.cern.ch/lcg/views/LCG_110/x86_64-el9-gcc13-opt/setup.sh
set -u
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
python3 -c '
import pathlib, subprocess, sys, tarfile, tempfile
with tempfile.TemporaryDirectory(prefix="dyb-twin-test-") as temporary:
    work = pathlib.Path(temporary)
    allowed = {"src/slim_root.cpp", "tests/check_twin.py",
               "estimates/noenergy-2026-09-27/benchmark.py", "estimates/noenergy-2026-09-27/plan.json",
               "catalog_backups/P17B/EH1/run48734.list", "TWin/Common/Cuts.h"}
    seen = set()
    with tarfile.open(fileobj=sys.stdin.buffer, mode="r|gz") as archive:
        for member in archive:
            path = pathlib.PurePosixPath(member.name)
            twin = str(path.parent) == "TWin/Retw_lijj/src_FD" and (
                path.suffix in (".C", ".h") or path.name in ("Makefile", "CaliParameters_withoutaverage.txt"))
            if member.name in seen or not member.isfile() or (member.name not in allowed and not twin):
                raise ValueError("unexpected payload member: " + member.name)
            archive.extract(member, work, filter="data")
            seen.add(member.name)
    assert allowed <= seen
    subprocess.run([sys.executable, str(work / "tests/check_twin.py")], check=True)
'
