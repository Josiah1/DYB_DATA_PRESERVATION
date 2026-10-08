#!/usr/bin/env python3
"""Export file-level campaign omissions from the copied input-issue ledger."""

import csv
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import preserve


def missing_rows(ledger):
    digests = preserve.catalog_backup_digests()
    catalogs = {}
    rows = []
    for issue in ledger["issues"]:
        if issue["status"] in ("preserved_from_exact_recon", "prepared_from_exact_recon"):
            continue
        if issue["status"] not in ("skipped", "skip_recorded"):
            raise ValueError("unclassified input issue: {}".format(issue["status"]))
        relative = str(Path(issue["period"]) / issue["hall"] / (issue["run"] + ".list"))
        expected_sha = digests[relative]
        if relative not in catalogs:
            data = (preserve.CATALOG_BACKUP_ROOT / relative).read_bytes()
            if hashlib.sha256(data).hexdigest() != expected_sha:
                raise ValueError("fixed catalog checksum mismatch: " + relative)
            catalogs[relative] = preserve.parse_catalog_inputs(data, relative)
        index = issue["catalog_index"]
        paths = catalogs[relative]
        if type(index) is not int or not 0 <= index < len(paths):
            raise ValueError("invalid catalog index: " + relative)
        original = paths[index]
        if (issue["catalog_sha256"] != expected_sha or
                issue["catalog_entry"] != original or issue["catalog_path"] != original):
            raise ValueError("issue does not match its fixed catalog: " + relative)
        check = issue["mini_check"]
        mini = preserve.resolve_inputs([original], "mini")[0]
        attempts = check["attempts"]
        if (check["path"] != mini or check["status"] != "missing" or not attempts or
                attempts[-1]["status"] != "missing" or attempts[-1]["errno"] != 2):
            raise ValueError("omission lacks confirmed ENOENT evidence: " + mini)
        dt.datetime.fromisoformat(issue["observed_at"])
        rows.append(dict(mini_path=mini, filename=Path(mini).name,
                         period=issue["period"], hall=issue["hall"], run=issue["run"],
                         catalog_index=index, disposition=issue["status"],
                         observed_at=issue["observed_at"], errno=2, catalog_path=original,
                         catalog_sha256=issue["catalog_sha256"], evidence=issue["evidence"]))
    rows.sort(key=lambda row: row["mini_path"])
    if len({row["mini_path"] for row in rows}) != len(rows):
        raise ValueError("duplicate missing mini paths")
    if sum(row["disposition"] == "skipped" for row in rows) != ledger["summary"]["skipped_inputs"]:
        raise ValueError("finalized omission count differs from campaign summary")
    return rows


def main():
    ledger = json.loads((ROOT / "input-issues.json").read_text())
    rows = missing_rows(ledger)
    summary = ledger["summary"]
    finalized = summary["skipped_inputs"]
    recovered = sum(i["status"] == "preserved_from_exact_recon" for i in ledger["issues"])
    columns = ("mini_path", "filename", "period", "hall", "run", "catalog_index",
               "disposition", "observed_at", "errno", "catalog_path", "catalog_sha256", "evidence")
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    preserve.atomic_text(ROOT / "missing-input-files.csv", output.getvalue())
    preserve.atomic_text(ROOT / "missing-input-files.list", "".join(row["mini_path"] + "\n" for row in rows))
    preserve.atomic_text(ROOT / "INPUT_ISSUES.md", f"""# Recorded missing input files

Campaign ledger snapshot: **{ledger['updated_at']}**. The campaign is ongoing.

- **{len(rows)} individual mini files** recorded missing with `ENOENT` (errno 2).
- **{finalized}** omitted from verified local archives (`skipped`).
- **{len(rows) - finalized}** recorded for omission during run preparation (`skip_recorded`).
- **{recovered}** earlier mini failures were preserved from their exact originals;
  their provenance remains in the ledger and they are excluded from this lost-input list.

[Plain path list](missing-input-files.list) has one full All-mini input path per line.
[CSV](missing-input-files.csv) adds filename, period, hall, run, zero-based catalog index,
disposition, observation time, original catalog path, checksum, and evidence.
Each row identifies a file. Scheduler timeouts, bad-run classifications, I/O
errors, invalid ROOT headers, and unassessed inputs are excluded.

Coverage is finalized archives plus recorded preparation observations. At this
snapshot, {summary['completed_runs']} of {summary['expected_runs']} runs were finalized,
with {summary['processed_inputs']:,} preserved inputs and {finalized} finalized omissions
out of {summary['expected_inputs']:,} catalog inputs. Further missing files may be
recorded as the campaign processes the remaining catalogs. Absence is established
at each recorded observation time; this export does not probe source storage again.

Source: Ubuntu's `provenance/input-issues.json` on archive disk UUID
`{ledger['disk_uuid']}`, copied to [input-issues.json](input-issues.json).
The ledger retains retry evidence, historical recoveries, and prior observations.

After copying an updated ledger, regenerate and validate the lists with:

```sh
~/TOOLs/install/python-current/bin/python3 scripts/export_missing_files.py
```

The exporter checks each catalog checksum and file index, exact mini-path mapping,
missing-file evidence, unique paths, and the finalized omission total before writing.
""")
    print(json.dumps(dict(snapshot=ledger["updated_at"], files=len(rows),
                          finalized=finalized, prepared=len(rows) - finalized,
                          recovered_excluded=recovered), sort_keys=True))


if __name__ == "__main__":
    main()
