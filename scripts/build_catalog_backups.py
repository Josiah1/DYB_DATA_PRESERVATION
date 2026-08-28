#!/usr/bin/env python3
"""Split the four fixed authoritative sync masters into per-run catalogs."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile


MASTERS = {
    "P17B": (
        "paths_p17b_sync_v4.txt",
        "aef1107d8d082fd87d3a82a88bdd5bcde6c162c9e1558cefdebcdfceba39ca7d",
        3150,
    ),
    "P19A": (
        "paths_p19a_sync_v5.txt",
        "73e26e62c36484854b61e5389033c34a26c1930e3a87083a79314adae7edc6d5",
        507,
    ),
    "P20A": (
        "paths_p20a_sync_v3.txt",
        "71b18d6595690b5a3b4717fb5546fc56961086f2cf63f80f6e0264dd8b91183d",
        125,
    ),
    "P21A": (
        "paths_p21a_sync_v1.txt",
        "73df980bd1e5295c56bab8f8c83f8e208fe17ed745adf2be9fbc7215112c10c0",
        227,
    ),
}
EXPECTED_CATALOGS = 4009
EXPECTED_INPUTS = 831334
EXPECTED_MANIFEST_SHA256 = "f7e87d4884118170f27c6dff1b64a34cd783d88f5490bc952334430058067728"
SOURCE_RE = re.compile(
    rb"^/dybfs/rec/(?P<period>P17B|P19A|P20A|P21A)/rec/"
    rb"runs_\d{7}/runs_\d{7}/run_(?P<run>\d{7})/"
    rb"recon\.(?:Neutrino|NoTag)\.(?P=run)\.Physics\."
    rb"(?P<hall>EH[123])-Merged\.(?P=period)-I\._\d{4}\.root\n$"
)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def build(masters_dir, output):
    masters_dir = Path(masters_dir).resolve()
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("output already exists: {}".format(output))

    catalogs = {}
    input_count = 0
    for period, (name, expected_sha, expected_runs) in MASTERS.items():
        data = (masters_dir / name).read_bytes()
        if sha256(data) != expected_sha:
            raise ValueError("master checksum mismatch: {}".format(name))
        period_runs = set()
        for line_number, line in enumerate(data.splitlines(keepends=True), 1):
            match = SOURCE_RE.fullmatch(line)
            if not match or match.group("period").decode() != period:
                raise ValueError("invalid source at {}:{}".format(name, line_number))
            hall = match.group("hall").decode()
            run = int(match.group("run"))
            key = (period, hall, run)
            catalogs.setdefault(key, bytearray()).extend(line)
            period_runs.add(key)
            input_count += 1
        if len(period_runs) != expected_runs:
            raise ValueError("{} has {} runs, expected {}".format(
                name, len(period_runs), expected_runs))
    if len(catalogs) != EXPECTED_CATALOGS or input_count != EXPECTED_INPUTS:
        raise ValueError("unexpected fixed-catalog totals")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".catalog-backups-", dir=str(output.parent)))
    try:
        checksum_lines = []
        byte_count = 0
        for (period, hall, run), contents in sorted(catalogs.items()):
            relative = Path(period) / hall / "run{}.list".format(run)
            destination = temporary / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            data = bytes(contents)
            destination.write_bytes(data)
            byte_count += len(data)
            checksum_lines.append("{}  {}".format(sha256(data), relative.as_posix()))
        manifest = ("\n".join(checksum_lines) + "\n").encode("utf-8")
        if sha256(manifest) != EXPECTED_MANIFEST_SHA256:
            raise ValueError("generated checksum manifest differs from the fixed snapshot")
        (temporary / "SHA256SUMS").write_bytes(manifest)
        os.replace(str(temporary), str(output))
    except Exception:
        shutil.rmtree(str(temporary), ignore_errors=True)
        raise
    return {
        "catalogs": len(catalogs),
        "inputs": input_count,
        "bytes": byte_count,
        "output": str(output),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--masters-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.masters_dir, args.output), sort_keys=True))


if __name__ == "__main__":
    main()
