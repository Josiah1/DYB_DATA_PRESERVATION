#!/usr/bin/env bash
# Input: tar.gz containing benchmark.py, plan.json and src/slim_root.cpp.
# Only temporary private files are written; all ROOT inputs are opened read-only.
set -eo pipefail
unset PYTHONHOME PYTHONPATH
source /cvmfs/sft.cern.ch/lcg/views/LCG_110/x86_64-el9-gcc13-opt/setup.sh
set -u
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
python3 -c '
import hashlib, json, pathlib, shlex, subprocess, sys, tarfile, tempfile
with tempfile.TemporaryDirectory() as temporary:
    work = pathlib.Path(temporary)
    expected = {"src/slim_root.cpp", "estimates/noenergy-2026-09-27/benchmark.py", "estimates/noenergy-2026-09-27/plan.json"}
    with tarfile.open(fileobj=sys.stdin.buffer, mode="r|gz") as archive:
        seen = set()
        for member in archive:
            if member.name not in expected or not member.isfile() or member.name in seen:
                raise ValueError("unexpected payload member: " + member.name)
            archive.extract(member, work, filter="data")
            seen.add(member.name)
        assert seen == expected
    base = work / "estimates/noenergy-2026-09-27"
    plan = json.loads((base / "plan.json").read_text())
    assert hashlib.sha256((work / "src/slim_root.cpp").read_bytes()).hexdigest() == plan["converter_sha256"]
    flags = shlex.split(subprocess.check_output(["root-config", "--cflags", "--libs"], text=True))
    converter = work / "slim-root"
    subprocess.run(["g++", "-O2", str(work / "src/slim_root.cpp"), "-o", str(converter), *flags], check=True)
    result = subprocess.run([sys.executable, str(base / "benchmark.py"), "run", str(base / "plan.json"), str(converter), str(work), "--limit", sys.argv[1]])
    raise SystemExit(result.returncode)
' "${1:-48}"
