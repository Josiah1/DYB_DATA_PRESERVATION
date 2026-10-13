#!/usr/bin/env python3
"""Bounded preservation pipeline for the fixed Daya Bay catalogs."""

import argparse
import concurrent.futures
import csv
import datetime as dt
import errno
import fcntl
import functools
import hashlib
import io
import json
import math
import os
import pwd
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time


SCHEMA_VERSION = 4
AD_SELECTION_MODES = ("on", "off")
INPUT_SOURCES = ("mini", "recon", "mini-or-recon")
DEFAULT_INPUT_SOURCE = "mini"
MINI_ROOT = "/lustrefs/juno26/dyw/rec/All-mini"
AD_COUNT_COLUMNS = tuple(
    "ad{}{}".format(detector, kind)
    for detector in (1, 2, 3, 4) for kind in ("Below0p3MeV", "From0p3To0p6MeV"))
MARKER_NAME = ".dyb-preserve-run.json"
START_CONFIRMATION = "START-DYB-PRESERVATION"
DELETE_CONFIRMATION = "DELETE-VERIFIED-RUN"
DEFAULT_ALIAS = "dayabay1"
DEFAULT_CODE_ROOT = "/dybfs2/users/lijj16/DYB_DATA_PRESERVATION/code"
DEFAULT_STAGE_ROOT = "/dybfs2/users/lijj16/DYB_DATA_PRESERVATION/staging"
DEFAULT_LCG_VIEW = "/cvmfs/sft.cern.ch/lcg/views/LCG_110/x86_64-el9-gcc13-opt"
# The startup proof is tied to this worker's source-before-converter ordering.
LCG_STARTUP_WORKER_SHA256 = "1800190b34ce101681b9dd72eb6bd2feaa449acc369b06e1fe7bb4efbda6e3ea"
GIB = 1024 ** 3
# Conservative admission reservation, not an output-size prediction or truncation limit.
CHUNK_RESERVE_BYTES = GIB
SCRATCH_HEADROOM_BYTES = 30 * GIB
CATALOG_BACKUP_ROOT = Path(__file__).resolve().parent / "catalog_backups"
CATALOG_BACKUP_SUMS = CATALOG_BACKUP_ROOT / "SHA256SUMS"
EXPECTED_CATALOG_BACKUPS = 4009
EXPECTED_CATALOG_INPUTS = 831334
EXPECTED_CATALOG_BYTES = 98092666
EXPECTED_CATALOG_SUMS_SHA256 = "f7e87d4884118170f27c6dff1b64a34cd783d88f5490bc952334430058067728"
CATALOGS = (
    ("P17B", "/dybfs2/rec/P17B/goodrunlist_p17b_v4/good_v4_sync.ihep"),
    ("P19A", "/dybfs2/rec/P19A/goodrunlist_p19a_v5/good_v5_sync.ihep"),
    ("P20A", "/dybfs2/rec/P20A/goodrunlist_p20a_v3/good_v3_sync.ihep"),
    ("P21A", "/dybfs2/rec/P21A/goodrunlist_p21a_v1/good_v1_sync.ihep"),
)
SOURCE_REPORT_COLUMNS = (
    "sourceIndex", "path", "entries", "inputBytes", "firstTriggerTimeSec",
    "firstTriggerTimeNanoSec", "lastTriggerTimeSec", "lastTriggerTimeNanoSec",
    "dataDurationSeconds", "processingWallSeconds", "processingCpuSeconds", "keptEntries",
) + AD_COUNT_COLUMNS
DURATION_COLUMNS = ("fileIndex", "chunkIndex") + SOURCE_REPORT_COLUMNS
SSH_OPTIONS = (
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=15",
    "-o", "ServerAliveInterval=15",
    "-o", "ServerAliveCountMax=4",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ControlMaster=auto",
    "-o", "ControlPersist=600",
    "-o", "ControlPath=~/.ssh/dyb-preserve-%C",
)


class SchedulerQueryUnavailable(RuntimeError):
    """A known read-only scheduler transport failure; no queue snapshot exists."""


# These helpers query before creating/reconciling an intent or changing broken-input state.
SCHEDULER_QUERY_PHASES = {"_pool": "read_only", "_submit": "before_mutation", "_skip_broken": "before_mutation"}


def selection_record(mode):
    if mode not in AD_SELECTION_MODES:
        raise ValueError("AD selection must be 'on' or 'off'")
    return {
        "mode": mode,
        "ad_detectors": [1, 2, 3, 4],
        "ad_min_energy_mev": 0.6,
        "counted_band_mev": [0.3, 0.6],
        "keep_non_ad": True,
    }


def selection_mode(record):
    mode = (record or {}).get("mode") if isinstance(record, dict) else None
    if mode not in AD_SELECTION_MODES or record != selection_record(mode):
        raise ValueError("run selection record does not match this toolchain")
    return mode


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def atomic_text(path, text):
    path = Path(path)
    temporary = path.with_name(".{}.tmp-{}".format(path.name, os.getpid()))
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(temporary), str(path))


def atomic_json(path, value):
    atomic_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def load_json(path):
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_bytes_retry(path, attempts=3):
    error = None
    for attempt in range(attempts):
        try:
            return Path(path).read_bytes()
        except OSError as caught:
            error = caught
            if attempt + 1 < attempts:
                time.sleep(0.1 * (attempt + 1))
    raise error


def validate_token(value, label):
    if not value or not re.fullmatch(r"[A-Za-z0-9._-]+", value):
        raise ValueError("{} must contain only letters, digits, dot, underscore, or dash".format(label))
    return value


def validate_remote_path(value, label):
    value = str(value)
    if not value.startswith("/") or any(character.isspace() for character in value):
        raise ValueError("{} must be an absolute path without whitespace".format(label))
    normalized = os.path.normpath(value)
    if normalized == "/" or len(Path(normalized).parts) < 4:
        raise ValueError("{} is too broad: {}".format(label, normalized))
    return normalized


def ensure_child(path, root):
    path = Path(path).resolve()
    root = Path(root).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise ValueError("{} is outside {}".format(path, root))
    if path == root:
        raise ValueError("refusing to operate on the staging root itself")
    return path


def registered_catalog(period, hall, catalog):
    roots = dict(CATALOGS)
    if period not in roots or hall not in ("EH1", "EH2", "EH3"):
        return False
    return os.path.dirname(os.path.normpath(catalog)) == os.path.join(roots[period], hall)


def parse_run_number(stem):
    match = re.search(r"run[_-]?(\d{4,8})", stem, flags=re.IGNORECASE)
    if not match:
        raise ValueError("cannot extract run number from {}".format(stem))
    return int(match.group(1))


def output_prefix(record):
    # Permit the catalog-stem naming convention when no prefix is recorded.
    prefix = validate_token(record.get("output_prefix", record["stem"]), "output prefix")
    if "output_prefix" in record:
        run = record.get("run_number")
        if (not isinstance(run, int) or not 0 <= run <= 9999999 or
                prefix != "preserved.{:07d}".format(run)):
            raise ValueError("output prefix does not match the TWin run-number convention")
    return prefix


def parse_catalog_inputs(catalog_data, catalog):
    inputs = []
    seen = set()
    for raw_line in catalog_data.splitlines():
        path = raw_line.decode("utf-8").strip()
        if not path:
            continue
        if not path.startswith("/") or not path.endswith(".root") or any(c.isspace() for c in path):
            raise ValueError("invalid ROOT input path: {!r}".format(path))
        if path in seen:
            raise ValueError("duplicate ROOT input path: {}".format(path))
        seen.add(path)
        inputs.append(path)
    if not inputs:
        raise ValueError("catalog is empty: {}".format(catalog))
    return inputs


def input_source_mode(record):
    # Schema-3 archives made before mini support used the original recon paths.
    source = record.get("input_source", "recon")
    if source not in INPUT_SOURCES:
        raise ValueError("invalid input source: {}".format(source))
    return source


def resolve_inputs(paths, source=DEFAULT_INPUT_SOURCE):
    input_source_mode({"input_source": source})
    if source == "mini-or-recon":
        raise ValueError("mini-or-recon requires the run's frozen resolved_inputs")
    if source == "recon":
        return list(paths)
    pattern = re.compile(
        r"/dybfs/rec/(P17B|P19A|P20A|P21A)/rec/"
        r"(runs_\d{7}/runs_\d{7}/run_(\d{7}))/"
        r"(recon\.(?:Neutrino|NoTag)\.\3\.Physics\.EH[123]-Merged\.\1-I\._\d{4}\.root)")
    resolved = []
    for path in paths:
        match = pattern.fullmatch(path)
        if not match:
            raise ValueError("cannot map fixed source to mini input: {}".format(path))
        resolved.append(str(Path(MINI_ROOT) / match.group(2) / ("mini" + match.group(4))))
    return resolved


def recorded_inputs(paths, record):
    source = input_source_mode(record)
    if ((record.get("skip_missing_minis") and source != "mini") or
            (record.get("skip_broken_minis") and (source != "mini" or not record.get("skip_missing_minis"))) or
            (record.get("skipped_inputs") and not record.get("skip_missing_minis"))):
        raise ValueError("gaps require the recorded mini-only skip policy")
    if source != "mini-or-recon" and not record.get("skip_missing_minis"):
        return resolve_inputs(paths, source)
    inputs = record.get("resolved_inputs")
    minis = resolve_inputs(paths, "mini")
    skipped = record.get("skipped_inputs", [])
    gaps = {}
    for issue in skipped:
        index = issue["catalog_index"]
        if (type(index) is not int or not 0 <= index < len(paths) or index in gaps or
                issue["catalog_entry"] != paths[index]):
            raise ValueError("invalid skipped catalog identity")
        dt.datetime.fromisoformat(issue["observed_at"])
        check = issue["mini_check"]
        category = issue.get("category", "missing")
        if source != "mini" or check["path"] != minis[index]:
            raise ValueError("skipped input is not the fixed catalog's exact mini")
        if category == "missing":
            if (check["status"] != "missing" or not check["attempts"] or
                    check["attempts"][-1]["status"] != "missing" or
                    check["attempts"][-1]["errno"] != errno.ENOENT):
                raise ValueError("skipped input is not a confirmed missing mini")
        elif category == "broken":
            if (not record.get("skip_broken_minis") or check["status"] != "broken" or
                    not valid_empty_mini_proof(check) or check["observed_at"] != issue["observed_at"]):
                raise ValueError("skipped input is not a confirmed broken mini")
        else:
            raise ValueError("unknown skipped-input category")
        gaps[index] = issue
    if (not isinstance(inputs, list) or len(inputs) != len(paths) or
            any(actual is not None if index in gaps else
                actual not in ((mini,) if source == "mini" else (original, mini))
                for index, (actual, original, mini) in enumerate(zip(inputs, paths, minis)))):
        raise ValueError("resolved inputs do not match the fixed catalog's exact mini/recon identities")
    usable = [path for path in inputs if path is not None]
    if record.get("skip_missing_minis") and (
            record.get("processed_input_count") != len(usable) or
            record.get("skipped_input_count") != len(skipped)):
        raise ValueError("processed and skipped input accounting does not match the catalog")
    if not skipped and record.get("skipped_input_count", 0):
        raise ValueError("skipped input count has no gap records")
    if record.get("skip_broken_minis") and (
            source != "mini" or not record.get("skip_missing_minis") or
            record.get("missing_skipped_input_count") != sum(i.get("category", "missing") == "missing" for i in skipped) or
            record.get("broken_skipped_input_count") != sum(i.get("category") == "broken" for i in skipped)):
        raise ValueError("missing/broken input accounting does not match the catalog")
    return usable


def valid_empty_mini_proof(issue):
    return (type(issue.get("bytes")) is int and issue["bytes"] > 0 and
            issue.get("readable") is True and issue.get("zombie") is False and issue.get("recovered") is False and
            isinstance(issue.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", issue["sha256"]) and
            issue.get("keys") == [dict(name="AdEnergy", classname="TNtuple", entries=0)] and
            issue.get("required") == {"Event/Rec/AdSimpleNL": False, "Event/Data/CalibStats": False})


def recorded_chunks(paths, record):
    usable = recorded_inputs(paths, record)
    if not record.get("skip_broken_minis"):
        return split_chunks(usable, record["files_per_job"])
    # Missing files were compacted at preparation; broken files must never shift those chunk boundaries.
    missing = {i["catalog_index"] for i in record["skipped_inputs"] if i.get("category", "missing") == "missing"}
    baseline = split_chunks([i for i in range(len(paths)) if i not in missing], record["files_per_job"])
    return [[record["resolved_inputs"][i] for i in chunk if record["resolved_inputs"][i] is not None]
            for chunk in baseline]


def probe_input(path):
    """Read-only header check; retry transient storage errors twice, never permissions."""
    attempts = []
    for attempt in range(3):
        code, reason = None, None
        try:
            with open(path, "rb") as stream:
                status = "ok" if stream.read(4) == b"root" else "invalid_root"
            if status == "invalid_root":
                reason = "not a ROOT file header"
        except OSError as error:
            code, reason = error.errno, str(error)
            if code == errno.ENOENT:
                status = "missing"
            elif code in (errno.EIO, errno.ESTALE, errno.ETIMEDOUT):
                status = "io_error"
            else:
                raise  # Permission, descriptor, device and other systemic errors stop the run.
        attempts.append(dict(timestamp=utc_now(), status=status, errno=code, reason=reason))
        if status not in ("missing", "io_error"):
            break
        if status == "missing" and len(attempts) >= 2 and attempts[-2]["status"] == "missing":
            break
        if attempt == 2:
            if status == "missing":
                status = "unstable"  # An ENOENT after I/O errors is not confirmed absence.
            break
        time.sleep(0.2 if status == "missing" else attempt + 1)
    return dict(path=path, status=status, attempts=attempts)


def check_input_storage():
    with os.scandir(MINI_ROOT) as entries:
        if next(entries, None) is None:
            raise ValueError("mini storage is empty: {}".format(MINI_ROOT))


def available_inputs(paths):
    check_input_storage()
    resolved, skipped = [], []
    consecutive = 0
    for index, (original, mini) in enumerate(zip(paths, resolve_inputs(paths))):
        mini_check = probe_input(mini)
        if mini_check["status"] == "ok":
            resolved.append(mini)
            consecutive = 0
            continue
        check_input_storage()
        issue = dict(catalog_index=index, catalog_entry=original, observed_at=utc_now(),
                     mini_check=mini_check)
        if mini_check["status"] != "missing":
            raise ValueError("mini input is not confirmed missing; no run staged: {}".format(
                json.dumps(issue, sort_keys=True)))
        resolved.append(None)
        skipped.append(issue)
        consecutive += 1
        # Stop a suspected outage before recording a run full of false gaps.
        if consecutive >= 3 or len(skipped) > max(3, len(paths) // 10):
            raise ValueError("possible mini storage outage; no run staged: {}".format(
                json.dumps(issue, sort_keys=True)))
    if not any(resolved):
        raise ValueError("no usable inputs; refusing to classify the entire run as gaps")
    check_input_storage()
    return resolved, skipped


def gap_report(record):
    result = {key: record[key] for key in (
        "catalog_sha256", "input_count", "processed_input_count", "skipped_input_count", "skipped_inputs")}
    if record.get("skip_broken_minis"):
        result.update({key: record[key] for key in (
            "missing_skipped_input_count", "broken_skipped_input_count", "broken_failure_evidence")})
    return result


def check_input_files(paths):
    unavailable = []
    for path in paths:
        try:
            with open(path, "rb") as stream:
                if stream.read(4) != b"root":
                    raise ValueError("not a ROOT file header")
        except (OSError, ValueError) as error:
            unavailable.append("{}: {}".format(path, error))
    if unavailable:
        raise ValueError("{} unavailable inputs; no files skipped or substituted. Examples: {}".format(
            len(unavailable), unavailable[:5]))


@functools.lru_cache(maxsize=1)
def catalog_backup_digests():
    manifest = read_bytes_retry(CATALOG_BACKUP_SUMS)
    if hashlib.sha256(manifest).hexdigest() != EXPECTED_CATALOG_SUMS_SHA256:
        raise ValueError("catalog-backup checksum manifest mismatch")
    digests = {}
    for line in manifest.decode("utf-8").splitlines():
        try:
            digest, relative = line.split(None, 1)
        except ValueError:
            raise ValueError("malformed catalog-backup checksum line")
        relative = relative.strip()
        parts = Path(relative).parts
        if (not re.fullmatch(r"[0-9a-f]{64}", digest) or relative in digests or
                len(parts) != 3 or parts[0] not in dict(CATALOGS) or parts[1] not in (
                    "EH1", "EH2", "EH3") or not re.fullmatch(r"run\d{4,8}\.list", parts[2])):
            raise ValueError("invalid catalog-backup checksum: {}".format(relative))
        digests[relative] = digest
    if len(digests) != EXPECTED_CATALOG_BACKUPS:
        raise ValueError("catalog-backup checksum manifest is incomplete")
    return digests


@functools.lru_cache(maxsize=1)
def catalog_backup_records():
    root = CATALOG_BACKUP_ROOT.resolve()
    records = {}
    for relative, digest in catalog_backup_digests().items():
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            raise ValueError("catalog-backup path escapes its root: {}".format(relative))
        if path.is_symlink() or not path.is_file():
            raise ValueError("missing catalog backup: {}".format(relative))
        data = read_bytes_retry(path)
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("catalog-backup checksum mismatch: {}".format(relative))
        period, hall, name = Path(relative).parts
        run_number = parse_run_number(Path(name).stem)
        inputs = parse_catalog_inputs(data, relative)
        run_fragment = "/run_{:07d}/".format(run_number)
        name_pattern = re.compile(
            r"recon\.(?:Neutrino|NoTag)\.{:07d}\.Physics\.{}-Merged\.{}-I\._\d{{4}}\.root".format(
                run_number, hall, period))
        for source in inputs:
            if (not source.startswith("/dybfs/rec/{}/rec/".format(period)) or
                    run_fragment not in source or
                    not name_pattern.fullmatch(Path(source).name)):
                raise ValueError("catalog-backup source identity mismatch: {}".format(relative))
        records[relative] = {
            "path": path,
            "sha256": digest,
            "bytes": len(data),
            "input_count": len(inputs),
        }
    actual = {
        str(path.relative_to(root)) for path in root.rglob("*.list")
    }
    input_count = sum(record["input_count"] for record in records.values())
    byte_count = sum(record["bytes"] for record in records.values())
    if (actual != set(records) or input_count != EXPECTED_CATALOG_INPUTS or
            byte_count != EXPECTED_CATALOG_BYTES):
        raise ValueError("catalog-backup set is incomplete or contains unexpected files")
    return records


def verify_catalog_backups():
    records = catalog_backup_records()
    return {
        "complete": True,
        "catalogs": len(records),
        "inputs": sum(record["input_count"] for record in records.values()),
        "bytes": sum(record["bytes"] for record in records.values()),
        "checksum_manifest": str(CATALOG_BACKUP_SUMS),
    }


def read_catalog_data(period, hall, catalog):
    catalog = os.path.normpath(catalog)
    if registered_catalog(period, hall, catalog):
        relative = str(Path(period) / hall / Path(catalog).name)
        backup = CATALOG_BACKUP_ROOT / relative
        digest = catalog_backup_digests().get(relative)
        if digest is None:
            raise ValueError("catalog backup is not registered: {}".format(relative))
        data = read_bytes_retry(backup)
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("catalog-backup checksum mismatch: {}".format(relative))
        return data, "repository-backup", relative
    return read_bytes_retry(catalog), "primary", None


def split_chunks(items, size):
    if size <= 0:
        raise ValueError("files per job must be positive")
    return [items[index:index + size] for index in range(0, len(items), size)]


def inventory_records():
    roots = dict(CATALOGS)
    records = []
    for relative, backup in sorted(catalog_backup_records().items()):
        period, hall, name = Path(relative).parts
        catalog = str(Path(roots[period]) / hall / name)
        records.append({
            "period": period,
            "hall": hall,
            "catalog": catalog,
            "stem": Path(name).stem,
            "catalog_bytes": backup["bytes"],
            "catalog_sha256": backup["sha256"],
            "input_count": backup["input_count"],
            "catalog_source": "repository-backup",
            "catalog_backup": relative,
        })
    return records


def prepare_run(catalog, period, hall, stage_root, code_root, files_per_job,
                require_registered=True, ad_selection="off", input_source=DEFAULT_INPUT_SOURCE,
                skip_missing_minis=False):
    selection = selection_record(ad_selection)
    catalog = os.path.normpath(catalog)
    if require_registered and not registered_catalog(period, hall, catalog):
        raise ValueError("catalog is not one of the fixed IBD submission-script catalogs")
    stage_root = validate_remote_path(stage_root, "stage root")
    code_root = validate_remote_path(code_root, "code root")
    if stage_root.startswith("/afs/") or stage_root.startswith("/workfs2/"):
        raise ValueError("IHEP compute nodes cannot write to {}".format(stage_root))
    stem = validate_token(Path(catalog).stem, "catalog stem")
    run_number = parse_run_number(stem)
    prefix = output_prefix({"stem": stem, "run_number": run_number,
                            "output_prefix": "preserved.{:07d}".format(run_number)})
    catalog_data, catalog_source, catalog_backup = read_catalog_data(period, hall, catalog)
    catalog_sha = hashlib.sha256(catalog_data).hexdigest()
    catalog_paths = parse_catalog_inputs(catalog_data, catalog)
    if skip_missing_minis and input_source != "mini":
        raise ValueError("skipping missing minis requires mini-only inputs")

    run_dir = (Path(stage_root) / period / hall / stem).resolve()
    if run_dir.exists():
        marker = load_json(run_dir / MARKER_NAME)
        expected = (catalog_sha, files_per_job, period, hall, selection, input_source, prefix)
        actual = (marker.get("catalog_sha256"), marker.get("files_per_job"),
                  marker.get("period"), marker.get("hall"), marker.get("selection"),
                  input_source_mode(marker), marker.get("output_prefix"))
        if actual != expected:
            raise ValueError("existing run staging does not match requested catalog")
        if marker.get("skip_missing_minis", False) != skip_missing_minis:
            raise ValueError("existing run staging has a different gap policy")
        recorded_inputs(catalog_paths, marker)
        return marker

    fallbacks, skipped = [], []
    if skip_missing_minis:
        resolved, skipped = available_inputs(catalog_paths)
        inputs = [path for path in resolved if path is not None]
    elif input_source == "mini-or-recon":
        inputs = resolve_inputs(catalog_paths, "mini")
        for index, path in enumerate(inputs):
            try:
                check_input_files([path])
            except ValueError as error:
                original = catalog_paths[index]
                check_input_files([original])
                inputs[index] = original
                fallbacks.append({"mini": path, "recon": original, "reason": str(error)})
    else:
        inputs = resolve_inputs(catalog_paths, input_source)
        check_input_files(inputs)
    run_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".{}.prepare-".format(stem), dir=str(run_dir.parent)))
    try:
        for name in ("chunks", "outputs", "reports", "status", "logs"):
            (temporary / name).mkdir()
        (temporary / "catalog.list").write_bytes(catalog_data)
        chunks = split_chunks(inputs, files_per_job)
        for index, paths in enumerate(chunks):
            atomic_text(temporary / "chunks" / "{:06d}.list".format(index),
                        "\n".join(paths) + "\n")
        atomic_text(temporary / "output-prefix", prefix + "\n")
        atomic_text(temporary / "run-number", str(run_number) + "\n")
        atomic_text(temporary / "ad-selection", ad_selection + "\n")

        quoted_run = shlex.quote(str(run_dir))
        quoted_code = shlex.quote(code_root)
        job_script = """#!/usr/bin/env bash
set -euo pipefail
[ \"$#\" -eq 1 ] || exit 2
printf -v task_id '%06d' \"$1\"
exec >{run}/logs/$task_id.out 2>{run}/logs/$task_id.err
exec bash {code}/scripts/worker.sh {run} \"$1\" {code}
""".format(run=quoted_run, code=quoted_code)
        atomic_text(temporary / "array-job.sh", job_script)
        os.chmod(str(temporary / "array-job.sh"), 0o700)
        marker = {
            "kind": "dyb-preserve-run",
            "schema_version": SCHEMA_VERSION,
            "created_at": utc_now(),
            "period": period,
            "hall": hall,
            "stem": stem,
            "run_number": run_number,
            "output_prefix": prefix,
            "catalog": catalog,
            "catalog_source": catalog_source,
            "catalog_sha256": catalog_sha,
            "input_source": input_source,
            "input_count": len(catalog_paths),
            "files_per_job": files_per_job,
            "chunk_count": len(chunks),
            "run_dir": str(run_dir),
            "code_root": code_root,
            "selection": selection,
        }
        if catalog_backup:
            marker["catalog_backup"] = catalog_backup
        if input_source == "mini-or-recon":
            marker["resolved_inputs"] = inputs
            marker["reconstruction_fallbacks"] = fallbacks
        if skip_missing_minis:
            marker.update(skip_missing_minis=True, resolved_inputs=resolved, processed_input_count=len(inputs),
                          skipped_input_count=len(skipped), skipped_inputs=skipped)
        if skipped:
            atomic_json(temporary / "input-gaps.json", gap_report(marker))
        recorded_inputs(catalog_paths, marker)
        atomic_json(temporary / "manifest.json", marker)
        atomic_json(temporary / MARKER_NAME, marker)
        os.rename(str(temporary), str(run_dir))
        return marker
    except Exception:
        shutil.rmtree(str(temporary), ignore_errors=True)
        raise


def load_run(run_dir, stage_root):
    stage_root = validate_remote_path(stage_root, "stage root")
    run_dir = ensure_child(run_dir, stage_root)
    marker_path = run_dir / MARKER_NAME
    if not marker_path.is_file():
        raise ValueError("missing run marker: {}".format(marker_path))
    marker = load_json(marker_path)
    if marker.get("kind") != "dyb-preserve-run" or marker.get("run_dir") != str(run_dir):
        raise ValueError("invalid run marker")
    return run_dir, marker


def scheduler_jobs(stage_root):
    try:
        result = subprocess.run([
            "condor_q", "-global", pwd.getpwuid(os.getuid()).pw_name, "-json", "-attributes",
            "ClusterId,ProcId,JobStatus,RequestCpus,Cmd,Iwd,HoldReason",
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
    except subprocess.TimeoutExpired as error:
        raise SchedulerQueryUnavailable("read-only condor_q query timed out after 60 seconds") from error
    if result.returncode or result.stderr.strip():
        diagnostics = [line.strip() for line in result.stderr.splitlines() if line.strip()]
        read_failure = "SECMAN:2007:Read failure during security negotiation."
        transient = read_failure in diagnostics and all(
            line == read_failure or line.startswith("-- Failed to fetch ads from:") for line in diagnostics)
        error_type = SchedulerQueryUnavailable if transient else RuntimeError
        raise error_type("scheduler query failed ({}):\nstdout:\n{}\nstderr:\n{}".format(
            result.returncode, result.stdout, result.stderr))
    # HTCondor emits nothing for an empty global queue, and may emit one array per schedd.
    pending, jobs = result.stdout.lstrip(), []
    while pending:
        rows, end = json.JSONDecoder().raw_decode(pending)
        if not isinstance(rows, list):
            raise ValueError("invalid scheduler response")
        jobs.extend(rows)
        pending = pending[end:].lstrip()
    prefix = str(Path(stage_root)) + "/"
    jobs = [job for job in jobs if job.get("Cmd", "").startswith(prefix)]
    for job in jobs:
        if job["RequestCpus"] != 1 or job["JobStatus"] not in range(1, 8):
            raise ValueError("unexpected campaign CPU request or scheduler state: {}".format(job))
    return jobs


def queue_counts(jobs):
    return dict(outstanding=len(jobs), running=sum(j["JobStatus"] == 2 for j in jobs),
                idle=sum(j["JobStatus"] == 1 for j in jobs),
                held=sum(j["JobStatus"] == 5 for j in jobs),
                running_cpus=sum(j["RequestCpus"] for j in jobs if j["JobStatus"] == 2))


def scratch_quota(stage_root):
    result = run_command(["/usr/bin/lfs", "quota", "-u", str(os.getuid()), stage_root])
    rows = []
    for line in result.stdout.splitlines():
        if line.strip().startswith("/"):
            rows.append(line.split())
        elif rows and len(rows[-1]) == 1 and line.strip():
            rows[-1].extend(line.split())  # lfs wraps long paths onto a separate line.
    if len(rows) != 1 or len(rows[0]) != 9:
        raise ValueError("cannot read scratch quota: " + result.stdout)
    row = rows[0]
    used, soft, hard = [int(value.rstrip("*")) * 1024 for value in row[1:4]]
    files, file_soft, file_hard = [int(value.rstrip("*")) for value in row[5:8]]
    limits = [n for n in (soft, hard) if n]
    file_limits = [n for n in (file_soft, file_hard) if n]
    if not limits or not file_limits:
        raise ValueError("scratch quota must have explicit byte and file limits")
    return dict(used_bytes=used, limit_bytes=min(limits), used_files=files,
                limit_files=min(file_limits),
                free_bytes=min(min(limits) - used, shutil.disk_usage(stage_root).free))


def pool_status(stage_root):
    stage_root = validate_remote_path(stage_root, "stage root")
    jobs = scheduler_jobs(stage_root)
    runs = []
    for path in sorted(Path(stage_root).glob("*/*/*/" + MARKER_NAME)):
        directory, marker = load_run(path.parent, stage_root)
        submissions = directory / "submissions.json"
        # Workers rename their partial output atomically; disappearance during this size scan is normal.
        output_bytes = 0
        for output in (directory / "outputs").iterdir():
            try:
                output_bytes += output.stat().st_size
            except FileNotFoundError:
                pass
        status = run_status(directory, stage_root)
        drained = (status["state"] == "COMPLETE" and not status["running"] and
                   not any(j["Iwd"] == str(directory) for j in jobs))
        reserve = (output_bytes + marker["chunk_count"] * 1024 ** 2 if drained else
                   max(output_bytes, marker["chunk_count"] * CHUNK_RESERVE_BYTES))
        runs.append(dict(marker=marker, status=status,
                         submissions=load_json(submissions) if submissions.exists() else [],
                         intent=(directory / "submission-intent.json").exists(),
                         broken_recovery_pending=(directory / "broken-recovery.json").exists(),
                         startup_failure=lcg_startup_errors(directory, stage_root) if status.get("failed") else None,
                         startup_recovery=load_json(directory / "startup-recovery.json")
                         if (directory / "startup-recovery.json").exists() else None,
                         output_bytes=output_bytes,
                         reserved_bytes=reserve))
    return dict(checked_at=utc_now(), jobs=jobs, queue=queue_counts(jobs), runs=runs,
                reserved_bytes=sum(r["reserved_bytes"] for r in runs),
                output_bytes=sum(r["output_bytes"] for r in runs), quota=scratch_quota(stage_root))


def admission_reason(pool, chunks, max_jobs, stage_bytes, local_free):
    reserve = chunks * CHUNK_RESERVE_BYTES
    if pool["queue"]["outstanding"] + chunks > max_jobs:
        return "job cap"
    if pool["reserved_bytes"] + reserve > stage_bytes:
        return "staging reservation budget"
    future_bytes = max(0, pool["reserved_bytes"] - pool["output_bytes"]) + reserve
    if future_bytes + SCRATCH_HEADROOM_BYTES > pool["quota"]["free_bytes"]:
        return "scratch quota headroom"
    if pool["quota"]["used_files"] + 16 * chunks + 1000 >= pool["quota"]["limit_files"]:
        return "scratch file quota"
    if (pool["reserved_bytes"] + reserve) * 1.05 > local_free:
        return "destination free space"
    return None


def submit_run(run_dir, stage_root, group, walltime, memory_mb, resubmit=False, max_jobs=300,
               startup_retry=False, max_attempts=2):
    run_dir, marker = load_run(run_dir, stage_root)
    # Remote lock also protects against two simultaneous submission helpers after an SSH interruption.
    with (Path(stage_root) / ".submission.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return submit_locked(run_dir, marker, stage_root, group, walltime, memory_mb, resubmit, max_jobs,
                             startup_retry, max_attempts)


def submit_locked(run_dir, marker, stage_root, group, walltime, memory_mb, resubmit, max_jobs,
                  startup_retry=False, max_attempts=2):
    submissions_path = run_dir / "submissions.json"
    submissions = load_json(submissions_path) if submissions_path.exists() else []
    intent_path = run_dir / "submission-intent.json"
    jobs = scheduler_jobs(stage_root)
    own = [j for j in jobs if j["Cmd"] == str(run_dir / "array-job.sh")]
    if startup_retry and (not resubmit or intent_path.exists() or
                          any(j["Iwd"] == str(run_dir) for j in jobs) or
                          run_status(run_dir, stage_root)["running"]):
        raise RuntimeError("startup retry has possible writers or an unresolved intent; retain it")
    if intent_path.exists():
        intent = load_json(intent_path)
        clusters = {j["ClusterId"] for j in own}
        if len(clusters) != 1:
            raise RuntimeError("ambiguous submission; inspect scheduler before retrying: {}".format(intent_path))
        submission = dict(intent, cluster_id=clusters.pop(), recovered_at=utc_now())
        if not any(s["cluster_id"] == submission["cluster_id"] for s in submissions):
            submissions.append(submission)
            atomic_json(submissions_path, submissions)
        intent_path.unlink()
        return submission
    if submissions and not resubmit:
        return submissions[-1]
    if own:
        raise RuntimeError("refusing duplicate submission while this run has queued jobs")
    if not 1 <= max_jobs <= 300 or len(jobs) + marker["chunk_count"] > max_jobs:
        raise RuntimeError("submission would exceed the campaign job cap")
    if startup_retry:
        if not submissions or len(submissions) - marker.get("broken_recovery_submissions", 0) >= min(2, max_attempts):
            raise RuntimeError("run exhausted startup submission attempts; retain it")
        proof = lcg_startup_errors(run_dir, stage_root)
        if proof.get("recheck") or not proof.get("startup"):
            raise RuntimeError("startup failures no longer have a stable exclusive proof; retain it")
        good = {}
        for ok in sorted((run_dir / "status").glob("*.ok")):
            chunk = ok.stem
            files = [ok, run_dir / "status" / (chunk + ".report"), run_dir / "chunks" / (chunk + ".list")]
            if files[-1].read_text().strip():
                files.append(run_dir / "reports" / ("{}.slim.{}.sources.tsv".format(output_prefix(marker), chunk)))
                output = run_dir / "outputs" / ("{}.slim.{}.root".format(output_prefix(marker), chunk))
                good[str(output.relative_to(run_dir))] = dict(bytes=output.stat().st_size, mtime_ns=output.stat().st_mtime_ns)
            for path in files:
                good[str(path.relative_to(run_dir))] = dict(bytes=path.stat().st_size, sha256=sha256_file(path))
        journal = run_dir / "startup-recovery.json"
        evidence = dict(kind="dyb-worker-startup-recovery", run_dir=str(run_dir),
                        catalog_sha256=marker["catalog_sha256"], selection=marker["selection"],
                        input_count=marker["input_count"], processed_input_count=marker.get("processed_input_count", marker["input_count"]),
                        skipped_inputs=marker.get("skipped_inputs", []), previous_submissions=submissions,
                        attempt=len(submissions) + 1, max_attempts=min(2, max_attempts), proof=proof, good_files=good)
        history = load_json(journal) if journal.exists() else []
        if any(item["catalog_sha256"] != marker["catalog_sha256"] or item["run_dir"] != str(run_dir) for item in history):
            raise ValueError("startup recovery journal identity differs from the staged run")
        same_attempt = [item for item in history if item["attempt"] == evidence["attempt"]]
        if same_attempt and same_attempt != [evidence]:
            raise RuntimeError("startup recovery evidence differs; retain the original journal")
        if not same_attempt:
            atomic_json(journal, history + [evidence])  # Preserve evidence before markers/logs are overwritten.
    if resubmit:
        for path in (run_dir / "status").glob("*.fail"):
            path.unlink()
        for path in (run_dir / "status").glob("*.running"):
            path.unlink()
    hepjob_bin = "/afs/ihep.ac.cn/soft/common/sysgroup/hep_job/bin"
    command = [
        os.path.join(hepjob_bin, "hep_sub"), "-g", group, "-wt", walltime,
        "-os", "AlmaLinux9", "-cpu", "1",
        "-mem", str(memory_mb), str(run_dir / "array-job.sh"),
        "-argu", "%{ProcId}", "-n", str(marker["chunk_count"]),
    ]
    intent = dict(submitted_at=utc_now(), chunk_count=marker["chunk_count"], command=command)
    atomic_json(intent_path, intent)
    result = subprocess.run(command, cwd=str(run_dir), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, universal_newlines=True)
    if result.returncode != 0:
        raise RuntimeError("hep_sub failed:\n{}".format(result.stdout))
    match = re.search(r"submitted to cluster\s+(\d+)", result.stdout)
    if not match:
        raise RuntimeError("could not parse HepJob cluster id:\n{}".format(result.stdout))
    submission = {
        "cluster_id": int(match.group(1)),
        "submitted_at": utc_now(),
        "chunk_count": marker["chunk_count"],
        "command": command,
        "output": result.stdout.strip(),
    }
    submissions.append(submission)
    atomic_json(submissions_path, submissions)
    intent_path.unlink()
    return submission


def run_status(run_dir, stage_root):
    run_dir, marker = load_run(run_dir, stage_root)
    ok = sorted((run_dir / "status").glob("*.ok"))
    failed = sorted((run_dir / "status").glob("*.fail"))
    running = sorted((run_dir / "status").glob("*.running"))
    if len(ok) == marker["chunk_count"] and not failed:
        state = "COMPLETE"
    elif failed:
        state = "FAILED"
    elif running:
        state = "RUNNING"
    else:
        state = "PENDING"
    result = {
        "state": state,
        "ok": len(ok),
        "failed": len(failed),
        "running": len(running),
        "chunks": marker["chunk_count"],
        "run_dir": str(run_dir),
    }
    if failed:
        result["failure_updates"] = {
            path.stem: [path.stat().st_mtime_ns,
                        (run_dir / "logs" / (path.stem + ".err")).stat().st_mtime_ns
                        if (run_dir / "logs" / (path.stem + ".err")).is_file() else None]
            for path in failed}
    return result


def lcg_startup_errors(run_dir, stage_root):
    """Prove only the two observed setup-source failures, never converter/input faults or mixed failures."""
    run_dir, marker = load_run(run_dir, stage_root)
    before = run_status(run_dir, stage_root)
    signature = before.get("failure_updates")
    failures = []
    worker = Path(marker["code_root"]) / "scripts/worker.sh"
    prefix = "{}: line 54: {}/setup.sh: ".format(worker, DEFAULT_LCG_VIEW)
    for chunk in sorted(signature or {}):
        paths = [run_dir / "logs" / (chunk + ".err"), run_dir / "logs" / (chunk + ".out"),
                 run_dir / "status" / (chunk + ".fail")]
        if any(path.stat().st_size > 4096 for path in paths):
            break
        error, output, failure = [path.read_text() for path in paths]
        if error not in (prefix + "No such file or directory\n", prefix + "Transport endpoint is not connected\n"):
            break
        status = re.fullmatch(r"rc=1\nhost=([^\s]+)\njob=(\d+)\.(\d+)\nfinished=([^\s]+)\n", failure)
        report = run_dir / "status" / (chunk + ".report")
        if not status or output or (report.exists() and report.stat().st_size):
            break
        host, cluster, proc, finished = status.groups()
        receipts = load_json(run_dir / "submissions.json")
        if (not receipts or int(cluster) != receipts[-1]["cluster_id"] or int(proc) != int(chunk) or
                not 0 <= int(chunk) < marker["chunk_count"]):
            raise ValueError("startup failure does not match its current submission/chunk")
        if dt.datetime.fromisoformat(finished.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("startup failure timestamp has no timezone")
        failures.append(dict(chunk=chunk, failure=failure, stderr=error, stdout=output,
                             stderr_sha256=sha256_file(paths[0]), failure_sha256=sha256_file(paths[2])))
    after = run_status(run_dir, stage_root)
    if after.get("failure_updates") != signature:
        return dict(recheck=True, failure_updates=after.get("failure_updates"))
    if not failures or len(failures) != before["failed"]:
        return dict(startup=False, failure_updates=signature)
    if input_source_mode(marker) != "mini" or sha256_file(worker) != LCG_STARTUP_WORKER_SHA256:
        raise ValueError("startup proof requires the known mini worker")
    catalog = run_dir / "catalog.list"
    if sha256_file(catalog) != marker["catalog_sha256"]:
        raise ValueError("startup proof catalog checksum mismatch")
    originals = parse_catalog_inputs(catalog.read_bytes(), str(catalog))
    minis = resolve_inputs(originals, "mini")
    chunks = recorded_chunks(originals, marker)
    for failure in failures:
        chunk = failure["chunk"]
        inputs = run_dir / "chunks" / (chunk + ".list")
        if inputs.read_text().splitlines() != chunks[int(chunk)] or not chunks[int(chunk)]:
            raise ValueError("startup failure chunk differs from fixed mini catalog")
        failure.update(chunk_sha256=sha256_file(inputs), sources=[
            dict(path=path, catalog_index=minis.index(path), catalog_entry=originals[minis.index(path)])
            for path in chunks[int(chunk)]])
    after = run_status(run_dir, stage_root)
    if after.get("failure_updates") != signature:
        return dict(recheck=True, failure_updates=after.get("failure_updates"))
    return dict(startup=True, failure_updates=signature, failures=failures, worker_sha256=LCG_STARTUP_WORKER_SHA256)


def confirmed_empty_mini_errors(run_dir, stage_root):
    run_dir, marker = load_run(run_dir, stage_root)
    if input_source_mode(marker) != "mini":
        raise ValueError("empty-mini holds require mini-only inputs")
    before = run_status(run_dir, stage_root)
    if not before["failed"]:
        raise ValueError("run has no failed chunks to inspect")
    catalog = run_dir / "catalog.list"
    if sha256_file(catalog) != marker["catalog_sha256"]:
        raise ValueError("content-error catalog checksum mismatch")
    paths = parse_catalog_inputs(catalog.read_bytes(), str(catalog))
    usable = recorded_inputs(paths, marker)
    minis = resolve_inputs(paths, "mini")
    candidates = []
    for chunk in sorted(before["failure_updates"]):
        failure = (run_dir / "status" / (chunk + ".fail")).read_text()
        error = run_dir / "logs" / (chunk + ".err")
        if error.stat().st_size > 4096:
            raise RuntimeError("unrecognized worker failure; not holding " + str(error))
        match = re.fullmatch(r"ERROR missing required tree: (/[^\s]+\.root)\s*", error.read_text())
        if not match or "rc=1" not in failure.splitlines():
            raise RuntimeError("unrecognized worker failure; not holding " + str(error))
        mini = match.group(1)
        inputs = (run_dir / "chunks" / (chunk + ".list")).read_text().splitlines()
        if mini not in inputs or mini not in usable or mini not in minis:
            raise ValueError("content-error path does not match this fixed mini chunk")
        candidates.append((chunk, minis.index(mini)))
    probe = run_command(["/usr/bin/bash", str(Path(marker["code_root"]) / "scripts/check_empty_mini.sh")] +
                        [minis[index] for _, index in candidates])
    if probe.stderr.strip():
        raise RuntimeError("ROOT inspection reported errors; not holding: " + probe.stderr)
    issues = json.loads(probe.stdout)
    if len(issues) != len(candidates):
        raise ValueError("incomplete content-error inspection")
    for issue, (chunk, index) in zip(issues, candidates):
        if issue["path"] != minis[index] or not valid_empty_mini_proof(issue):
            raise ValueError("input was not confirmed as an empty AdEnergy-only mini")
        issue.update(period=marker["period"], hall=marker["hall"], run=marker["stem"],
                     catalog=marker["catalog"], catalog_sha256=marker["catalog_sha256"],
                     catalog_index=index, catalog_entry=paths[index], chunk=chunk,
                     category="content_error", status="pending", reason="existing mini contains only empty AdEnergy tuple")
    after = run_status(run_dir, stage_root)
    if after.get("failure_updates") != before["failure_updates"]:
        return dict(recheck=True, failure_updates=after.get("failure_updates"))
    return dict(issues=issues, failure_updates=before["failure_updates"])


def skip_broken_minis(run_dir, stage_root):
    """Controller-owned, restartable removal of independently proved broken inputs; never move good chunks."""
    with (Path(stage_root) / ".submission.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run_dir, marker = load_run(run_dir, stage_root)
        if input_source_mode(marker) != "mini" or not marker.get("skip_missing_minis"):
            raise ValueError("broken skips require the mini-only missing-file policy")
        if (run_dir / "run.json").exists() or (run_dir / ".complete").exists():
            raise ValueError("never change a finalized run")
        if (any(j["Iwd"] == str(run_dir) for j in scheduler_jobs(stage_root)) or
                run_status(run_dir, stage_root)["running"] or (run_dir / "submission-intent.json").exists()):
            raise RuntimeError("run still has possible writers or an unresolved submission; retain it")
        paths = parse_catalog_inputs((run_dir / "catalog.list").read_bytes(), str(run_dir))
        if sha256_file(run_dir / "catalog.list") != marker["catalog_sha256"]:
            raise ValueError("broken recovery catalog checksum mismatch")
        journal_path = run_dir / "broken-recovery.json"
        if journal_path.exists():
            journal = load_json(journal_path)
            if marker not in (journal["before"], journal["after"]):
                raise ValueError("broken recovery journal does not match the staged run")
            mutable = {"skip_broken_minis", "resolved_inputs", "skipped_inputs", "processed_input_count",
                       "skipped_input_count", "missing_skipped_input_count", "broken_skipped_input_count",
                       "broken_failure_evidence", "broken_recovery_submissions"}
            if ({k: v for k, v in journal["before"].items() if k not in mutable} !=
                    {k: v for k, v in journal["after"].items() if k not in mutable} or
                    any(i not in journal["after"]["skipped_inputs"] for i in journal["before"]["skipped_inputs"])):
                raise ValueError("broken recovery journal would change fixed metadata or earlier gaps")
            additions = [i for i in journal["after"]["skipped_inputs"]
                         if i not in journal["before"]["skipped_inputs"]]
            if not additions or any(i.get("category") != "broken" for i in additions):
                raise ValueError("broken recovery must add only independently proved broken gaps")
            proof = run_command(["bash", str(Path(marker["code_root"]) / "scripts/check_empty_mini.sh")] +
                                [i["mini_check"]["path"] for i in additions])
            inspected = json.loads(proof.stdout)
            if proof.stderr.strip() or len(inspected) != len(additions) or any(
                    not valid_empty_mini_proof(p) or (p["path"], p["sha256"]) !=
                    (i["mini_check"]["path"], i["mini_check"]["sha256"]) for p, i in zip(inspected, additions)):
                raise ValueError("broken input changed during recovery; retain journal and data")
        else:
            proof = confirmed_empty_mini_errors(run_dir, stage_root)
            if proof.get("recheck"):
                return proof
            before = run_status(run_dir, stage_root)
            if before.get("failure_updates") != proof["failure_updates"]:
                return dict(recheck=True, failure_updates=before.get("failure_updates"))
            chunks = recorded_chunks(paths, marker)
            candidates = [path for chunk in sorted(proof["failure_updates"])
                          for path in chunks[int(chunk)]]
            scan = run_command(["bash", str(Path(marker["code_root"]) / "scripts/check_empty_mini.sh"), "--scan"] + candidates)
            inspected = json.loads(scan.stdout)
            if scan.stderr.strip() or not inspected or len({p["path"] for p in inspected}) != len(inspected) or any(
                    p["path"] not in candidates or not valid_empty_mini_proof(p) for p in inspected):
                raise ValueError("incomplete or invalid broken-input inspection")
            if not {i["path"] for i in proof["issues"]} <= {p["path"] for p in inspected}:
                raise ValueError("failed input changed during inspection; nothing skipped")
            after = run_status(run_dir, stage_root)
            if after.get("failure_updates") != proof["failure_updates"]:
                return dict(recheck=True, failure_updates=after.get("failure_updates"))
            updated = dict(marker, skip_broken_minis=True, resolved_inputs=list(marker["resolved_inputs"]),
                           skipped_inputs=list(marker["skipped_inputs"]),
                           broken_failure_evidence=list(marker.get("broken_failure_evidence", [])))
            minis = resolve_inputs(paths, "mini")
            for p in inspected:
                index = minis.index(p["path"])
                updated["resolved_inputs"][index] = None
                updated["skipped_inputs"].append(dict(catalog_index=index, catalog_entry=paths[index],
                    observed_at=p["observed_at"], category="broken",
                    reason="existing readable ROOT file contains only an empty AdEnergy TNtuple; required event trees absent",
                    mini_check=dict(p, status="broken")))
            updated["skipped_inputs"].sort(key=lambda i: i["catalog_index"])
            updated.update(processed_input_count=sum(p is not None for p in updated["resolved_inputs"]),
                           skipped_input_count=len(updated["skipped_inputs"]),
                           missing_skipped_input_count=sum(i.get("category", "missing") == "missing" for i in updated["skipped_inputs"]),
                           broken_skipped_input_count=sum(i.get("category") == "broken" for i in updated["skipped_inputs"]))
            for chunk in sorted(proof["failure_updates"]):
                error = run_dir / "logs" / (chunk + ".err")
                updated["broken_failure_evidence"].append(dict(chunk=chunk, observed_at=utc_now(),
                    failure=(run_dir / "status" / (chunk + ".fail")).read_text(), stderr=error.read_text(),
                    stderr_sha256=sha256_file(error), failure_updates=proof["failure_updates"][chunk]))
            submissions = run_dir / "submissions.json"
            updated["broken_recovery_submissions"] = len(load_json(submissions)) if submissions.exists() else 0
            journal = dict(before=marker, after=updated)
            atomic_json(journal_path, journal)
        original, updated = journal["before"], journal["after"]
        old_chunks, new_chunks = recorded_chunks(paths, original), recorded_chunks(paths, updated)
        if len(old_chunks) != len(new_chunks) or len(new_chunks) != marker["chunk_count"]:
            raise ValueError("broken recovery would change chunk numbering")
        prefix = output_prefix(marker)
        for index, (old, new) in enumerate(zip(old_chunks, new_chunks)):
            chunk = run_dir / "chunks" / "{:06d}.list".format(index)
            if chunk.read_text().splitlines() not in (old, new):
                raise ValueError("chunk list changed outside guarded recovery")
            if old == new:
                continue
            ok = run_dir / "status" / "{:06d}.ok".format(index)
            output = run_dir / "outputs" / "{}.slim.{:06d}.root".format(prefix, index)
            report = run_dir / "reports" / "{}.slim.{:06d}.sources.tsv".format(prefix, index)
            if output.exists() or (ok.exists() and (new or not ok.read_text().startswith("EMPTY confirmed_broken_inputs\n"))):
                raise ValueError("refusing to change a chunk with saved output or a verified OK marker")
            if report.exists() and (new or report.read_text() != "\t".join(SOURCE_REPORT_COLUMNS) + "\n"):
                raise ValueError("refusing to reuse an old report for a changed chunk list")
        for index, (old, new) in enumerate(zip(old_chunks, new_chunks)):
            if old != new:
                atomic_text(run_dir / "chunks" / "{:06d}.list".format(index), "\n".join(new) + ("\n" if new else ""))
        atomic_json(run_dir / "input-gaps.json", gap_report(updated))
        atomic_json(run_dir / "manifest.json", updated)
        atomic_json(run_dir / MARKER_NAME, updated)
        for index, (old, new) in enumerate(zip(old_chunks, new_chunks)):
            if old == new:
                continue
            if not new:
                atomic_text(run_dir / "reports" / "{}.slim.{:06d}.sources.tsv".format(prefix, index),
                            "\t".join(SOURCE_REPORT_COLUMNS) + "\n")
                atomic_text(run_dir / "status" / "{:06d}.ok".format(index), "EMPTY confirmed_broken_inputs\n")
            failure = run_dir / "status" / "{:06d}.fail".format(index)
            if failure.exists():
                failure.unlink()
        journal_path.unlink()
        return dict(marker=updated, status=run_status(run_dir, stage_root))


def parse_result_file(path):
    values = {}
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            if not line.startswith("RESULT "):
                continue
            for token in line.strip().split()[1:]:
                if "=" in token:
                    key, value = token.split("=", 1)
                    values[key] = value
    return values


def parse_verify_output(text):
    for line in text.splitlines():
        if line.startswith("VERIFY "):
            values = dict(token.split("=", 1) for token in line.split()[1:] if "=" in token)
            try:
                parsed = {key: int(values[key]) for key in ("entries", "source_entries", "sources")}
                parsed["selection"] = values["selection"]
                return parsed
            except (KeyError, ValueError):
                break
    raise ValueError("ROOT verifier did not report entries, source_entries, sources, and selection")


def check_verified_chunk(text, chunk_totals, mode):
    expected = {"entries": chunk_totals[0], "source_entries": chunk_totals[1],
                "sources": chunk_totals[2], "selection": mode}
    values = parse_verify_output(text)
    if values != expected:
        raise ValueError("ROOT chunk content does not match its timing rows: verifier={} table={}".format(
            values, expected))
    return values


def verify_remote_root(verifier, path, chunk_totals, mode):
    script = 'set +u; source "$1/setup.sh"; set -u; exec "$2" --verify "$3"'
    result = run_command([
        "bash", "-c", script, "verify-root", DEFAULT_LCG_VIEW, str(verifier), str(path),
    ])
    check_verified_chunk(result.stdout, chunk_totals, mode)
    return result


def validate_source_fields(fields, expected_path, expected_source_index, mode="on"):
    if len(fields) != len(SOURCE_REPORT_COLUMNS):
        raise ValueError("source timing row has {} fields, expected {}".format(
            len(fields), len(SOURCE_REPORT_COLUMNS)))
    try:
        source_index = int(fields[0])
        entries, input_bytes = int(fields[2]), int(fields[3])
        first_sec, first_nano = int(fields[4]), int(fields[5])
        last_sec, last_nano = int(fields[6]), int(fields[7])
        data_seconds, wall_seconds, cpu_seconds = map(float, fields[8:11])
        kept = int(fields[11])
        ad_counts = [int(value) for value in fields[12:20]]
    except ValueError as error:
        raise ValueError("invalid source timing number: {}".format(error))
    if source_index != expected_source_index or fields[1] != expected_path:
        raise ValueError("source timing order does not match the fixed catalog")
    if entries < 0 or input_bytes < 0 or not (0 <= first_nano < 1000000000) or not (
            0 <= last_nano < 1000000000):
        raise ValueError("invalid source timing count, size, or nanoseconds")
    counted = sum(ad_counts)
    if kept < 0 or min(ad_counts) < 0 or (
            kept + counted != entries if mode == "on" else kept != entries or counted > entries):
        raise ValueError("source selection accounting does not match its entries")
    if not all(math.isfinite(value) and value >= 0
               for value in (data_seconds, wall_seconds, cpu_seconds)):
        raise ValueError("invalid source duration")
    first_ns = first_sec * 1000000000 + first_nano
    last_ns = last_sec * 1000000000 + last_nano
    if last_ns < first_ns or data_seconds != (last_ns - first_ns) / 1e9:
        raise ValueError("source duration does not match its timestamps")
    return entries, input_bytes, kept


def read_source_report(path, expected_paths):
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    if not lines or tuple(lines[0].split("\t")) != SOURCE_REPORT_COLUMNS:
        raise ValueError("invalid source timing header: {}".format(path))
    rows = [line.split("\t") for line in lines[1:]]
    if len(rows) != len(expected_paths):
        raise ValueError("source timing row count does not match chunk: {}".format(path))
    return rows


def check_chunk_sources(run_dir, marker, index, chunks=None):
    if chunks is None:
        catalog = run_dir / "catalog.list"
        if sha256_file(catalog) != marker["catalog_sha256"]:
            raise ValueError("chunk catalog checksum mismatch")
        chunks = recorded_chunks(parse_catalog_inputs(catalog.read_bytes(), str(catalog)), marker)
    if len(chunks) != marker["chunk_count"] or not 0 <= index < len(chunks):
        raise ValueError("chunk index/count differs from recorded catalog layout")
    expected_paths = chunks[index]
    if (run_dir / "chunks" / "{:06d}.list".format(index)).read_text().splitlines() != expected_paths:
        raise ValueError("staged chunk paths do not match fixed catalog/input source")
    if not expected_paths and (not marker.get("skip_broken_minis") or
            (run_dir / "outputs" / "{}.slim.{:06d}.root".format(output_prefix(marker), index)).exists()):
        raise ValueError("empty chunk must represent confirmed broken gaps without ROOT output")
    report = run_dir / "reports" / "{}.slim.{:06d}.sources.tsv".format(output_prefix(marker), index)
    rows = read_source_report(report, expected_paths)
    for source_index, fields in enumerate(rows):
        validate_source_fields(fields, expected_paths[source_index], source_index, selection_mode(marker["selection"]))
    return rows


def aggregate_duration_reports(run_dir, marker, prefix):
    mode = selection_mode(marker.get("selection"))
    catalog = run_dir / "catalog.list"
    if sha256_file(catalog) != marker["catalog_sha256"]:
        raise ValueError("staged catalog checksum does not match run marker")
    chunks = recorded_chunks(parse_catalog_inputs(catalog.read_bytes(), str(catalog)), marker)
    if len(chunks) != marker["chunk_count"]:
        raise ValueError("staged chunk count does not match fixed catalog")
    rows = []
    total_entries = 0
    total_input_bytes = 0
    total_kept = 0
    chunk_totals = []
    for chunk_index in range(marker["chunk_count"]):
        expected_paths = chunks[chunk_index]
        chunk_kept = chunk_entries = 0
        for source_index, fields in enumerate(check_chunk_sources(run_dir, marker, chunk_index, chunks)):
            entries, input_bytes, kept = validate_source_fields(
                fields, expected_paths[source_index], source_index, mode)
            rows.append([str(len(rows)), str(chunk_index)] + fields)
            total_entries += entries
            total_input_bytes += input_bytes
            total_kept += kept
            chunk_kept += kept
            chunk_entries += entries
        chunk_totals.append((chunk_kept, chunk_entries, len(expected_paths)))
    if len(rows) != marker.get("processed_input_count", marker["input_count"]):
        raise ValueError("source timing total does not match run input count")
    destination = run_dir / "durations.tsv"
    atomic_text(destination, "\t".join(DURATION_COLUMNS) + "\n" +
                "".join("\t".join(row) + "\n" for row in rows))
    return {
        "path": destination, "rows": len(rows), "entries": total_entries,
        "input_bytes": total_input_bytes, "kept": total_kept, "chunk_totals": chunk_totals,
    }


def validate_duration_table(path, catalog_paths, files_per_job, mode="on", chunks=None):
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    if not lines or tuple(lines[0].split("\t")) != DURATION_COLUMNS:
        raise ValueError("invalid run duration header: {}".format(path))
    rows = [line.split("\t") for line in lines[1:]]
    if len(rows) != len(catalog_paths):
        raise ValueError("run duration row count does not match catalog")
    total_entries = total_kept = 0
    chunks = split_chunks(catalog_paths, files_per_job) if chunks is None else chunks
    if [p for chunk in chunks for p in chunk] != catalog_paths:
        raise ValueError("duration chunk layout does not match processed catalog inputs")
    chunk_totals = [[0, 0, len(chunk)] for chunk in chunks]
    indices = [(c, s) for c, chunk in enumerate(chunks) for s in range(len(chunk))]
    for file_index, (fields, expected_path) in enumerate(zip(rows, catalog_paths)):
        if len(fields) != len(DURATION_COLUMNS):
            raise ValueError("invalid run duration row width")
        chunk_index, source_index = indices[file_index]
        try:
            row_file_index, row_chunk_index = int(fields[0]), int(fields[1])
        except ValueError as error:
            raise ValueError("invalid run duration index: {}".format(error))
        if row_file_index != file_index or row_chunk_index != chunk_index:
            raise ValueError("run duration indices do not match the recorded chunk layout")
        entries, _, kept = validate_source_fields(fields[2:], expected_path, source_index, mode)
        total_entries += entries
        total_kept += kept
        chunk_totals[chunk_index][0] += kept
        chunk_totals[chunk_index][1] += entries
    return {
        "rows": len(rows), "entries": total_entries, "kept": total_kept,
        "chunk_totals": [tuple(chunk) for chunk in chunk_totals],
    }


def finalize_run(run_dir, stage_root):
    run_dir, marker = load_run(run_dir, stage_root)
    status = run_status(run_dir, stage_root)
    if status["state"] != "COMPLETE":
        raise ValueError("run is not complete: {}".format(status))
    prefix = (run_dir / "output-prefix").read_text(encoding="utf-8").strip()
    if prefix != output_prefix(marker):
        raise ValueError("staged output prefix does not match run metadata")
    paths = parse_catalog_inputs((run_dir / "catalog.list").read_bytes(), str(run_dir))
    chunks = recorded_chunks(paths, marker)
    output_indices = [index for index, chunk in enumerate(chunks) if chunk]
    outputs = [run_dir / "outputs" / "{}.slim.{:06d}.root".format(prefix, index) for index in output_indices]
    if set((run_dir / "outputs").glob("*.root")) != set(outputs):
        raise ValueError("staged ROOT outputs differ from nonempty recorded chunks")
    missing = [str(path) for path in outputs if not path.is_file()]
    if missing:
        raise ValueError("missing outputs: {}".format(missing[:3]))

    verifier = Path(marker["code_root"]) / "bin" / "slim-root"
    if not verifier.is_file() or not os.access(str(verifier), os.X_OK):
        raise ValueError("missing ROOT verifier: {}".format(verifier))
    durations = aggregate_duration_reports(run_dir, marker, prefix)
    duration_file = durations["path"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(4, len(outputs)))) as executor:
        list(executor.map(
            lambda item: verify_remote_root(verifier, item[0], item[1], marker["selection"]["mode"]),
            zip(outputs, [durations["chunk_totals"][i] for i in output_indices])))

    artifacts = outputs + [duration_file]
    if marker.get("skipped_inputs"):
        gaps = run_dir / "input-gaps.json"
        if load_json(gaps) != gap_report(marker):
            raise ValueError("gap report differs from the frozen run marker")
        artifacts.append(gaps)
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(artifacts))) as executor:
        hashes = list(executor.map(sha256_file, artifacts))
    checksum_lines = ["{}  outputs/{}".format(digest, path.name)
                      for digest, path in zip(hashes, outputs)]
    checksum_lines.extend("{}  {}".format(digest, path.name)
                          for digest, path in zip(hashes[len(outputs):], artifacts[len(outputs):]))
    atomic_text(run_dir / "SHA256SUMS", "\n".join(checksum_lines) + "\n")

    reports = [parse_result_file(run_dir / "status" / "{:06d}.ok".format(index))
               for index in range(marker["chunk_count"])]
    numeric_totals = {}
    for key in ("sources", "entries", "kept_entries", "input_bytes", "output_bytes",
                "wall_seconds", "cpu_seconds"):
        values = [report.get(key) for report in reports if report.get(key) is not None]
        numeric_totals[key] = sum(float(value) for value in values)
    result = dict(marker)
    result.update({
        "complete": True,
        "catalog_complete": not marker.get("skipped_inputs"),
        "completed_at": utc_now(),
        "output_count": len(outputs),
        "output_bytes": sum(path.stat().st_size for path in outputs),
        "artifact_count": len(artifacts),
        "artifact_bytes": sum(path.stat().st_size for path in artifacts),
        "duration_file": duration_file.name,
        "duration_rows": durations["rows"],
        "source_entries": durations["entries"],
        "source_kept_entries": durations["kept"],
        "source_input_bytes": durations["input_bytes"],
        "checksums": "SHA256SUMS",
        "metrics": numeric_totals,
        "reports_with_metrics": sum(bool(report) for report in reports),
    })
    atomic_json(run_dir / "run.json", result)
    atomic_text(run_dir / ".complete", marker["catalog_sha256"] + "\n")
    return result


def cleanup_run(run_dir, stage_root, expected_catalog_sha):
    run_dir, marker = load_run(run_dir, stage_root)
    if marker.get("catalog_sha256") != expected_catalog_sha:
        raise ValueError("catalog checksum does not match cleanup request")
    result_path = run_dir / "run.json"
    if not result_path.is_file():
        raise ValueError("run has not been finalized")
    result = load_json(result_path)
    if (not result.get("complete") or result.get("catalog_sha256") != expected_catalog_sha or
            result.get("schema_version") != SCHEMA_VERSION or
            input_source_mode(result) != input_source_mode(marker)):
        raise ValueError("finalized run does not match the cleanup request")
    if list((run_dir / "status").glob("*.running")) or list((run_dir / "status").glob("*.fail")):
        raise ValueError("run still has running or failed tasks")
    complete = run_dir / ".complete"
    checksums = run_dir / "SHA256SUMS"
    if (not complete.is_file() or complete.read_text(encoding="utf-8").strip() !=
            expected_catalog_sha or not checksums.is_file() or checksums.stat().st_size == 0):
        raise ValueError("completion sentinels are missing")
    shutil.rmtree(str(run_dir))
    return {"deleted": str(run_dir), "catalog_sha256": expected_catalog_sha}


def ssh_command(alias, remote_arguments):
    validate_token(alias, "SSH alias")
    return ["ssh"] + list(SSH_OPTIONS) + [alias] + list(remote_arguments)


def run_command(command, check=True, cwd=None):
    result = subprocess.run(command, cwd=cwd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, universal_newlines=True)
    if check and result.returncode != 0:
        raise RuntimeError("command failed ({}):\n{}\nstdout:\n{}\nstderr:\n{}".format(
            result.returncode, " ".join(command), result.stdout, result.stderr))
    return result


def remote_call(alias, code_root, arguments, check=True):
    code_root = validate_remote_path(code_root, "code root")
    remote_script = os.path.join(code_root, "preserve.py")
    command = ssh_command(alias, [
        "/usr/bin/env", "-u", "PYTHONHOME", "-u", "PYTHONPATH",
        "/usr/bin/python3", remote_script,
    ] + list(arguments))
    return run_command(command, check=check)


def remote_json(alias, code_root, arguments, check=True):
    # Only this read-only snapshot is safe to replay; never retry a submission or cleanup here.
    attempts = 3 if arguments[0] == "_pool" and check else 1
    for attempt in range(1, attempts + 1):
        try:
            result = remote_call(alias, code_root, arguments, check=check and arguments[0] not in SCHEDULER_QUERY_PHASES)
            if check and arguments[0] in SCHEDULER_QUERY_PHASES and result.returncode:
                if (arguments[0] == "_pool" and result.returncode == 255 and not result.stdout.strip() and
                        len(result.stderr.strip().splitlines()) == 1 and re.search(
                        r"Connection (?:reset by peer|timed out)|Broken pipe|"
                        r"Connection to .+ closed by remote host|Timeout, server .+ not responding",
                        result.stderr)):
                    raise SchedulerQueryUnavailable("read-only scheduler SSH transport failed: " + result.stderr.strip())
                if result.returncode == 75:
                    failure = json.loads(result.stdout)
                    if (isinstance(failure, dict) and set(failure) == {"scheduler_unavailable", "command", "phase"} and
                            failure["command"] == arguments[0] and
                            failure["phase"] == SCHEDULER_QUERY_PHASES[arguments[0]] and not result.stderr.strip() and
                            isinstance(failure["scheduler_unavailable"], str) and failure["scheduler_unavailable"]):
                        raise SchedulerQueryUnavailable("{} scheduler query unavailable ({}): {}".format(
                            arguments[0], failure["phase"], failure["scheduler_unavailable"]))
                raise RuntimeError("remote command {} failed ({}):\nstdout:\n{}\nstderr:\n{}".format(
                    arguments[0], result.returncode, result.stdout, result.stderr))
            lines = [line for line in result.stdout.splitlines() if line.strip()]
            if not lines:
                raise RuntimeError("remote command returned no JSON: {}".format(result.stderr))
            return json.loads(lines[-1]), result.returncode
        except (RuntimeError, json.JSONDecodeError) as error:
            if attempts > 1:
                print("{} _pool attempt {}/{} failed: {}".format(
                    utc_now(), attempt, attempts, error), file=sys.stderr, flush=True)
            if attempt == attempts:
                raise
            time.sleep(attempt)


def deployed_inventory(alias, code_root):
    records, _ = remote_json(alias, code_root, ["_inventory"])
    return records


def verify_local_run(directory, root_verifier=None):
    directory = Path(directory).resolve()
    result = load_json(directory / "run.json")
    if not result.get("complete"):
        raise ValueError("local run.json is not complete")
    if result.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("local run schema does not match this toolchain")
    mode = selection_mode(result.get("selection"))
    input_source = input_source_mode(result)
    if result.get("duration_file") != "durations.tsv" or result.get("checksums") != "SHA256SUMS":
        raise ValueError("run.json names unexpected integrity artifacts")
    validate_token(result.get("stem"), "run stem")
    prefix = output_prefix(result)
    if result.get("chunk_count", 0) <= 0 or result.get("files_per_job", 0) <= 0:
        raise ValueError("run.json has invalid chunk sizing")
    catalog = directory / "catalog.list"
    if sha256_file(catalog) != result["catalog_sha256"]:
        raise ValueError("local catalog checksum does not match run.json")
    catalog_paths = [line for line in catalog.read_text(encoding="utf-8").splitlines() if line]
    if len(catalog_paths) != result["input_count"]:
        raise ValueError("local catalog count does not match run.json")
    complete = directory / ".complete"
    if complete.read_text(encoding="utf-8").strip() != result["catalog_sha256"]:
        raise ValueError("local completion marker does not match the catalog")
    durations = validate_duration_table(
        directory / result["duration_file"], recorded_inputs(catalog_paths, result),
        result["files_per_job"], mode, chunks=recorded_chunks(catalog_paths, result))
    duration_rows = durations["rows"]
    if (duration_rows, durations["entries"], durations["kept"]) != (
            result.get("duration_rows"), result.get("source_entries"),
            result.get("source_kept_entries")):
        raise ValueError("duration rows or entry totals do not match run.json")
    if len(durations["chunk_totals"]) != result["chunk_count"]:
        raise ValueError("duration chunk count does not match run.json")

    expected_outputs = {
        "outputs/{}.slim.{:06d}.root".format(prefix, index)
        for index, totals in enumerate(durations["chunk_totals"]) if totals[2]
    }
    actual_outputs = {
        str(path.relative_to(directory)) for path in (directory / "outputs").glob("*.root")
    }
    if actual_outputs != expected_outputs:
        raise ValueError("local ROOT output set is incomplete or contains unexpected files")
    if result.get("output_count", len(expected_outputs)) != len(expected_outputs):
        raise ValueError("ROOT output count differs from nonempty recorded chunks")
    expected_artifacts = expected_outputs | {result["duration_file"]}
    if result.get("skipped_inputs"):
        if result.get("catalog_complete") is not False or load_json(
                directory / "input-gaps.json") != gap_report(result):
            raise ValueError("gap report or catalog completeness differs from run.json")
        expected_artifacts.add("input-gaps.json")
    checksums = directory / result["checksums"]
    checked_bytes = 0
    checked_files = 0
    checked_paths = set()
    with checksums.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                digest, relative = line.rstrip("\n").split(None, 1)
            except ValueError:
                raise ValueError("malformed checksum line")
            relative = relative.strip()
            if relative in checked_paths:
                raise ValueError("duplicate checksum path: {}".format(relative))
            path = (directory / relative).resolve()
            try:
                path.relative_to(directory)
            except ValueError:
                raise ValueError("checksum path escapes local run: {}".format(relative))
            if sha256_file(path) != digest:
                raise ValueError("checksum mismatch: {}".format(path))
            checked_paths.add(relative)
            checked_files += 1
            checked_bytes += path.stat().st_size
    if checked_paths != expected_artifacts:
        raise ValueError("checksum manifest does not contain the exact expected artifacts")
    if checked_files != result["artifact_count"] or checked_bytes != result["artifact_bytes"]:
        raise ValueError("local artifact count or byte total does not match run.json")

    if root_verifier:
        verifier = Path(root_verifier).resolve()
        if not verifier.is_file() or not os.access(str(verifier), os.X_OK):
            raise ValueError("ROOT verifier is not executable: {}".format(verifier))
        for index, expected in enumerate(durations["chunk_totals"]):
            if not expected[2]:
                continue
            relative = "outputs/{}.slim.{:06d}.root".format(prefix, index)
            output = run_command([str(verifier), "--verify", str(directory / relative)])
            check_verified_chunk(output.stdout, expected, mode)
    return {
        "files": checked_files,
        "bytes": checked_bytes,
        "outputs": len(expected_outputs),
        "duration_rows": duration_rows,
        "source_entries": durations["entries"],
        "kept_entries": durations["kept"],
        "ad_selection": mode,
        "input_source": input_source,
        "catalog_sha256": result["catalog_sha256"],
        "schema_version": result["schema_version"],
        "directory": str(directory),
        "expected_inputs": len(catalog_paths),
        "processed_inputs": duration_rows,
        "skipped_inputs": len(result.get("skipped_inputs", [])),
        "missing_skipped_inputs": sum(i.get("category", "missing") == "missing" for i in result.get("skipped_inputs", [])),
        "broken_skipped_inputs": sum(i.get("category") == "broken" for i in result.get("skipped_inputs", [])),
        "catalog_complete": not result.get("skipped_inputs"),
    }


def catalog_signature(records):
    fields = ("period", "hall", "catalog", "stem", "catalog_sha256", "input_count")
    try:
        return sorted(tuple(record[field] for field in fields) for record in records)
    except (KeyError, TypeError) as error:
        raise ValueError("invalid catalog inventory record: {}".format(error))


def save_inventory_snapshot(local_root, records, input_source=DEFAULT_INPUT_SOURCE, skip_missing_minis=False):
    input_source_mode({"input_source": input_source})
    bad = [record for record in records if "error" in record]
    if bad:
        raise ValueError("cannot save an inventory with unreadable catalogs")
    path = Path(local_root) / "catalog-inventory.json"
    if path.exists():
        previous = load_json(path)
        if (previous.get("kind") != "dyb-preserve-catalog-inventory" or
                previous.get("schema_version") != SCHEMA_VERSION):
            raise ValueError("existing catalog inventory has the wrong kind or schema")
        if catalog_signature(previous.get("records", [])) != catalog_signature(records):
            raise ValueError("fixed catalog inventory differs from the existing local snapshot")
        if previous.get("skip_missing_minis") and input_source != "mini":
            raise ValueError("archive policy requires mini-only inputs for new work")
        if input_source_mode(previous) != input_source:
            if (skip_missing_minis and input_source == "mini" and
                    input_source_mode(previous) == "mini-or-recon"):
                legacy = {}
                for record in records:
                    relative = str(Path(record["period"]) / record["hall"] / record["stem"])
                    result_path = Path(local_root) / relative / "run.json"
                    if result_path.exists():
                        result = load_json(result_path)
                        if not result.get("complete") or result["catalog_sha256"] != record["catalog_sha256"]:
                            raise ValueError("cannot retain an unfinalized or mismatched legacy run")
                        if input_source_mode(result) not in ("mini", "mini-or-recon"):
                            raise ValueError("unexpected legacy input source")
                        legacy[relative] = input_source_mode(result)
                previous["legacy_input_sources"] = legacy
            elif input_source_mode(previous) != "mini" or input_source != "mini-or-recon":
                raise ValueError("input source differs from the existing local snapshot")
            previous.update(previous_input_source=input_source_mode(previous), input_source=input_source,
                            source_policy_updated_at=utc_now())
            if skip_missing_minis:
                previous["skip_missing_minis"] = True
            atomic_json(path, previous)
        return path
    atomic_json(path, {
        "kind": "dyb-preserve-catalog-inventory",
        "schema_version": SCHEMA_VERSION,
        "input_source": input_source,
        "skip_missing_minis": skip_missing_minis,
        "created_at": utc_now(),
        "records": records,
    })
    return path


def archive_input_sources(snapshot, relative):
    legacy = snapshot.get("legacy_input_sources", {}).get(relative)
    if legacy:
        return {legacy}
    policy = input_source_mode(snapshot)
    return {"mini", "mini-or-recon"} if policy == "mini-or-recon" else {policy}


def verify_all(local_root, root_verifier=None):
    local_root = Path(local_root).resolve()
    snapshot = load_json(local_root / "catalog-inventory.json")
    if (snapshot.get("kind") != "dyb-preserve-catalog-inventory" or
            snapshot.get("schema_version") != SCHEMA_VERSION):
        raise ValueError("invalid catalog inventory snapshot")
    records = snapshot.get("records", [])
    if not records or any("error" in record for record in records):
        raise ValueError("catalog inventory snapshot is incomplete")
    expected = {
        str(Path(record["period"]) / record["hall"] / record["stem"]): record
        for record in records
    }
    if len(expected) != len(records):
        raise ValueError("catalog inventory contains duplicate run identities")
    actual = {
        str(path.parent.relative_to(local_root)): path.parent
        for path in local_root.glob("*/*/*/run.json")
    }
    missing = sorted(set(expected) - set(actual))
    unexpected = sorted(set(actual) - set(expected))
    if missing or unexpected:
        raise ValueError("preservation set is incomplete: missing={} unexpected={} examples={}".format(
            len(missing), len(unexpected), (missing + unexpected)[:5]))

    total_artifacts = total_bytes = total_inputs = total_entries = total_kept = total_skipped = 0
    total_missing = total_broken = 0
    modes = set()
    input_sources = set()
    for relative, record in sorted(expected.items()):
        result = load_json(actual[relative] / "run.json")
        if (result.get("catalog_sha256"), result.get("input_count")) != (
                record["catalog_sha256"], record["input_count"]):
            raise ValueError("local run does not match inventory: {}".format(relative))
        verified = verify_local_run(actual[relative], root_verifier=root_verifier)
        if verified["input_source"] not in archive_input_sources(snapshot, relative):
            raise ValueError("archive input source differs from its catalog inventory snapshot")
        total_artifacts += verified["files"]
        total_bytes += verified["bytes"]
        total_inputs += verified["duration_rows"]
        total_skipped += verified["skipped_inputs"]
        total_missing += verified["missing_skipped_inputs"]
        total_broken += verified["broken_skipped_inputs"]
        total_entries += verified["source_entries"]
        total_kept += verified["kept_entries"]
        modes.add(verified["ad_selection"])
        input_sources.add(verified["input_source"])
    if len(modes) != 1:
        raise ValueError("archive mixes AD selection modes: {}".format(sorted(modes)))
    policy = input_source_mode(snapshot)
    return {
        "complete": total_skipped == 0,
        "accounted": True,
        "status": "finished_with_gaps" if total_skipped else "complete",
        "runs": len(records),
        "inputs": total_inputs,
        "expected_inputs": total_inputs + total_skipped,
        "processed_inputs": total_inputs,
        "skipped_inputs": total_skipped,
        "missing_skipped_inputs": total_missing,
        "broken_skipped_inputs": total_broken,
        "source_entries": total_entries,
        "kept_entries": total_kept,
        "ad_selection": modes.pop(),
        "input_source": policy,
        "input_sources_present": sorted(input_sources),
        "artifacts": total_artifacts,
        "bytes": total_bytes,
        "local_root": str(local_root),
    }


def archive_progress(local_root):
    """Count finalized local runs; monitoring does not repeat large artifact hashes."""
    local_root = Path(local_root)
    snapshot = load_json(local_root / "catalog-inventory.json")
    runs = []
    for expected in snapshot["records"]:
        directory = local_root / expected["period"] / expected["hall"] / expected["stem"]
        if not (directory / "run.json").exists():
            continue
        record = load_json(directory / "run.json")
        if (not record.get("complete") or (record["catalog_sha256"], record["input_count"]) !=
                (expected["catalog_sha256"], expected["input_count"]) or
                (directory / ".complete").read_text().strip() != record["catalog_sha256"]):
            raise ValueError("local run does not match the expected completed catalog: {}".format(directory))
        paths = parse_catalog_inputs((directory / "catalog.list").read_bytes(), str(directory))
        if len(recorded_inputs(paths, record)) != record["duration_rows"]:
            raise ValueError("processed input count differs from duration rows")
        runs.append(record)
    expected_inputs = sum(r["input_count"] for r in snapshot["records"])
    processed = sum(r["duration_rows"] for r in runs)
    skipped = sum(len(r.get("skipped_inputs", [])) for r in runs)
    missing = sum(i.get("category", "missing") == "missing" for r in runs for i in r.get("skipped_inputs", []))
    broken = sum(i.get("category") == "broken" for r in runs for i in r.get("skipped_inputs", []))
    finished = len(runs) == len(snapshot["records"])
    return dict(expected_runs=len(snapshot["records"]), expected_inputs=expected_inputs,
                completed_runs=len(runs), processed_inputs=processed, skipped_inputs=skipped,
                missing_skipped_inputs=missing, broken_skipped_inputs=broken,
                pending_inputs=expected_inputs - processed - skipped,
                artifact_bytes=sum(r["artifact_bytes"] for r in runs), finished=finished,
                status=("finished_with_gaps" if skipped else "complete") if finished else "in_progress",
                complete=finished and not skipped), runs


def refresh_pending_holds(local_root, held, active, automatic, new_run_admission, skip_broken_minis=False):
    path = Path(local_root).parent / "provenance/pending-input-issues.json"
    ledger = load_json(path) if path.exists() else {"kind": "dyb-pending-input-issues"}
    previous = {entry["run"]: entry for entry in ledger.get("held_runs", [])}
    issues = {(i["period"], i["hall"], i["run"], i["catalog_index"]): i
              for i in ledger.get("issues", [])}
    recorded_path = path.parent / "input-issues.json"
    recorded = {(i["period"], i["hall"], i["run"], i["catalog_index"]): i
                for i in load_json(recorded_path).get("issues", [])} if recorded_path.exists() else {}
    for identity, issue in issues.items():
        gap = recorded.get(identity, {})
        check = gap.get("mini_check", {})
        if (gap.get("category") == "broken" and check.get("path") == issue.get("path") and
                gap.get("catalog_sha256") == issue.get("catalog_sha256")):
            issue.update(disposition="broken_" + gap["status"], broken_evidence="input-issues.json",
                         broken_revalidated_at=check["observed_at"])
    entries = []
    for key in sorted(held):
        run = active[key]
        marker = run["marker"]
        entry = dict(previous.get(key, {}))
        entry.update(run=key, catalog=marker["catalog"], catalog_sha256=marker["catalog_sha256"],
                     input_count=marker["input_count"], pending_input_count=marker["input_count"],
                     status=run["status"], automatic=key in automatic or entry.get("automatic", False))
        entries.append(entry)
        for issue in automatic.get(key, {}).get("issues", []):
            identity = (issue["period"], issue["hall"], issue["run"], issue["catalog_index"])
            issues.setdefault(identity, issue)
    updated = dict(ledger, held_runs=entries, issues=[issues[k] for k in sorted(issues)],
                   status="pending" if held else "historical_content_observations",
                   new_run_admission=new_run_admission,
                   new_run_admission_reason="compression enabled; confirmed broken gaps recorded separately"
                   if new_run_admission else "operator requested staging drain")
    if skip_broken_minis:
        historical = dict(ledger.get("historical_hold_evidence", {}))
        for field in ("current_input_policy", "decision_needed", "held_run", "scope_samples"):
            if field in ledger:
                historical.setdefault(field, ledger[field])
            updated.pop(field, None)
        updated.update(historical_hold_evidence=historical,
                       current_input_policy="mini only; record confirmed ENOENT missing and independently proved empty-AdEnergy-only broken gaps separately; stop on other errors",
                       broken_skip_authorized_at="2026-10-11")
    if updated != ledger:
        updated["updated_at"] = utc_now()
        atomic_json(path, updated)


def refresh_input_issues(local_root, prepared=None):
    local_root = Path(local_root)
    progress, runs = archive_progress(local_root)
    provenance = local_root.parent / "provenance"
    provenance.mkdir(exist_ok=True)
    path = provenance / "input-issues.json"
    ledger = load_json(path) if path.exists() else {"kind": "dyb-input-issues"}
    issues = {(i["period"], i["hall"], i["run"], i["catalog_index"]): i
              for i in ledger.get("issues", [])}
    for record in runs + ([prepared] if prepared else []):
        finalized = record in runs
        relative = Path(record["period"]) / record["hall"] / record["stem"]
        catalog_file = (local_root / relative / "catalog.list" if finalized else
                        CATALOG_BACKUP_ROOT / relative.with_suffix(".list"))
        paths = parse_catalog_inputs(catalog_file.read_bytes(), str(catalog_file))
        observations = [(item, "skipped" if finalized else "skip_recorded")
                        for item in record.get("skipped_inputs", [])]
        observations += [(item, "preserved_from_exact_recon" if finalized else "prepared_from_exact_recon")
                         for item in record.get("reconstruction_fallbacks", [])]
        for item, status in observations:
            original = item.get("catalog_entry", item.get("recon"))
            index = paths.index(original)
            key = (record["period"], record["hall"], record["stem"], index)
            new = dict(item, period=key[0], hall=key[1], run=key[2], catalog_index=index,
                       category=item.get("category", "missing"),
                       catalog=record["catalog"], catalog_sha256=record["catalog_sha256"],
                       catalog_path=original, status=status,
                       observed_at=item.get("observed_at", record["created_at"]),
                       evidence=str(Path("data") / relative / "run.json") if finalized else "frozen run preparation")
            previous = issues.get(key)
            if previous:
                history = previous.get("previous_observations", [])
                if (previous.get("status"), previous.get("observed_at")) != (status, new["observed_at"]):
                    history = history + [{k: v for k, v in previous.items() if k != "previous_observations"}]
                if history:
                    new["previous_observations"] = history
            issues[key] = new
    ledger.update(report_version=3, updated_at=utc_now(), final=progress["finished"],
                  current_input_policy="mini only; separately skip confirmed ENOENT missing and independently proved empty-AdEnergy-only broken minis; stop on other errors",
                  catalog_check_complete=progress["finished"], summary=progress,
                  scope="Finalized local runs and recorded preparation observations; pending inputs are not assumed missing.",
                  issues=[issues[key] for key in sorted(issues)])
    ledger.pop("additional_check", None)
    ledger.pop("completed_runs", None)
    atomic_json(path, ledger)
    gaps = [i for i in ledger["issues"] if i["status"] in ("skipped", "skip_recorded")]
    missing = [i for i in gaps if i.get("category", "missing") == "missing"]
    text = ("# Unexpected input-file issues\n\nUpdated: {updated}\n\n"
            "- Finalized runs: {completed_runs} / {expected_runs}\n"
            "- Preserved inputs: {processed_inputs}; finalized gaps: {skipped_inputs}\n"
            "- Finalized missing skips: {missing_skipped_inputs}; broken skips: {broken_skipped_inputs}\n"
            "- Pending inputs: {pending_inputs}; expected catalog inputs: {expected_inputs}\n"
            "- Archive complete without gaps: {complete}; all runs finalized: {finished}\n\n"
            "Counts use finalized local metadata; monitoring does not repeat artifact hashes.\n"
            "New work uses minis only. Confirmed ENOENT missing and independently proved broken minis are distinct gaps; other errors stop the run.\n"
            "The full expected catalog is unchanged. Exact paths, separate missing/I/O reasons,\n"
            "retry timestamps, fallback observations and prior observations are in [input-issues.json](input-issues.json).\n"
            "A finalized run may have recorded gaps; it does not make the archive complete.\n\n"
            "See [BROKEN_INPUTS.md](BROKEN_INPUTS.md) for broken filenames and exact proof exports.\n\n"
            "## Confirmed missing mini skips\n\n").format(updated=ledger["updated_at"], **progress)
    text += "\n".join("- `{}` / catalog index {}: {}".format(
        i["mini_check"]["path"], i["catalog_index"], i["status"]) for i in missing) + "\n"
    atomic_text(provenance / "INPUT_ISSUES.md", text)
    export_broken_files(provenance, ledger)
    return progress


def export_broken_files(provenance, ledger):
    provenance = Path(provenance)
    progress = ledger["summary"]
    broken = [i for i in ledger["issues"] if i.get("category") == "broken" and
              i["status"] in ("skipped", "skip_recorded")]
    if (len({i["mini_check"]["path"] for i in broken}) != len(broken) or
            sum(i["status"] == "skipped" for i in broken) != progress.get("broken_skipped_inputs", 0)):
        raise ValueError("broken export has duplicate paths or a different finalized count")
    digests = catalog_backup_digests() if broken else {}
    catalogs = {}
    for i in broken:
        relative = str(Path(i["period"]) / i["hall"] / (i["run"] + ".list"))
        if relative not in catalogs:
            catalog = CATALOG_BACKUP_ROOT / relative
            if sha256_file(catalog) != digests[relative]:
                raise ValueError("broken export fixed catalog checksum mismatch")
            catalogs[relative] = parse_catalog_inputs(catalog.read_bytes(), relative)
        index = i["catalog_index"]
        if (type(index) is not int or not 0 <= index < len(catalogs[relative]) or
                i["catalog_sha256"] != digests[relative] or i["catalog_entry"] != catalogs[relative][index] or
                i["catalog_path"] != i["catalog_entry"]):
            raise ValueError("broken export does not match the fixed catalog")
        check = i["mini_check"]
        dt.datetime.fromisoformat(i["observed_at"])
        if (not valid_empty_mini_proof(check) or check.get("status") != "broken" or
                check.get("observed_at") != i["observed_at"] or
                check.get("path") != resolve_inputs([i["catalog_entry"]], "mini")[0]):
            raise ValueError("broken export lacks exact-mini empty-tuple proof")
    atomic_json(provenance / "broken-input-issues.json", dict(updated_at=ledger["updated_at"], final=progress["finished"],
        summary=progress, scope="Confirmed broken observations in finalized or prepared runs; not a census of pending inputs.", issues=broken))
    atomic_text(provenance / "broken_files.list", "".join(i["mini_check"]["path"] + "\n" for i in broken))
    table = io.StringIO()
    writer = csv.writer(table, lineterminator="\n")
    writer.writerow(("period", "hall", "run", "catalog_index", "catalog_entry", "mini_path", "status", "bytes", "sha256", "inspected_at", "reason"))
    for i in broken:
        check = i["mini_check"]
        writer.writerow((i["period"], i["hall"], i["run"], i["catalog_index"], i["catalog_entry"], check["path"],
                         i["status"], check["bytes"], check["sha256"], check["observed_at"], i["reason"]))
    atomic_text(provenance / "broken_files.csv", table.getvalue())
    atomic_text(provenance / "BROKEN_INPUTS.md", (
        "# Confirmed broken mini inputs\n\nUpdated: {}\n\n"
        "{} distinct confirmed broken paths recorded; {} are skipped in finalized local runs. "
        "Preparation observations remain pending until their run is verified locally. This is not a census of all expected inputs.\n\n"
        "Proof requires an existing readable, non-zombie, non-recovered ROOT file containing only a zero-entry "
        "AdEnergy TNtuple and neither required event tree. The file's SHA-256, size, catalog identity, "
        "inspection timestamp and reason are retained. No original reconstruction is substituted.\n\n"
        "- [Exact filename list](broken_files.list)\n- [CSV](broken_files.csv)\n"
        "- [Evidence ledger](broken-input-issues.json)\n- [Missing reports and reconciliation](INPUT_ISSUES.md)\n\n"
        "Every finalized gapped run checksums input-gaps.json; expected = processed + missing-skipped + broken-skipped. "
        "A finalized run with gaps is not a gap-free catalog or archive.\n\n{}").format(
            ledger["updated_at"], len(broken), progress.get("broken_skipped_inputs", 0),
            "\n".join("- `{}` / catalog index {}: {}".format(i["mini_check"]["path"], i["catalog_index"], i["status"]) for i in broken)).rstrip() + "\n")


def validate_local_mount(local_root, mount_root, mount_uuid=None):
    mount_root = Path(mount_root).resolve()
    if not mount_root.exists() or not os.path.ismount(str(mount_root)):
        raise ValueError("required local disk is not mounted at {}".format(mount_root))
    if mount_uuid:
        actual, filesystem, options = run_command([
            "findmnt", "-n", "-o", "UUID,FSTYPE,OPTIONS", "--target", str(mount_root)
        ]).stdout.split()
        if actual != mount_uuid:
            raise ValueError("mounted disk UUID {} differs from required {}".format(actual, mount_uuid))
        if filesystem == "ntfs3":
            raise ValueError("unsafe NTFS3 destination: iomap_write_end kernel crashes; use ntfs-3g")
        if "rw" not in options.split(","):
            raise ValueError("destination filesystem is not mounted read-write")
    local_root = Path(local_root).resolve()
    try:
        local_root.relative_to(mount_root)
    except ValueError:
        raise ValueError("local root is outside the required mount")
    local_root.mkdir(parents=True, exist_ok=True)
    return local_root


def sync_run(alias, remote_result, local_root, mount_root, mount_uuid=None):
    local_root = validate_local_mount(local_root, mount_root, mount_uuid)
    final = local_root / remote_result["period"] / remote_result["hall"] / remote_result["stem"]
    if final.exists():
        verified = verify_local_run(final)
        if (verified["catalog_sha256"], verified["schema_version"], verified["ad_selection"],
                verified["input_source"]) != (
                remote_result["catalog_sha256"], remote_result["schema_version"],
                remote_result["selection"]["mode"], input_source_mode(remote_result)):
            raise ValueError("existing local run does not match the remote run")
        return verified
    final.parent.mkdir(parents=True, exist_ok=True)
    incoming = final.with_name(".{}.incoming".format(final.name))
    incoming.mkdir(exist_ok=True)
    required = int(remote_result["artifact_bytes"] * 1.05)
    ssh_transport = "ssh " + " ".join(SSH_OPTIONS)
    command = [
        "rsync", "-rt", "--partial", "--progress", "--timeout=120", "-e", ssh_transport,
        "{}:{}/".format(alias, remote_result["run_dir"]), str(incoming) + "/",
    ]
    for attempt in range(3):
        validate_local_mount(local_root, mount_root, mount_uuid)
        free = shutil.disk_usage(str(local_root)).free
        if free < required:
            raise ValueError("local disk has {} bytes free; {} required".format(free, required))
        result = run_command(command, check=False)
        if result.returncode == 0:
            break
        error = RuntimeError("rsync failed ({}) for {}:\nstdout:\n{}\nstderr:\n{}".format(
            result.returncode, incoming, result.stdout, result.stderr))
        print("{} transfer attempt {}/3: {}".format(utc_now(), attempt + 1, error),
              file=sys.stderr, flush=True)
        transport_error = result.returncode in (10, 30, 35) or (
            result.returncode in (12, 255) and any(message in result.stderr.lower() for message in (
                "connection unexpectedly closed", "connection reset", "connection timed out",
                "broken pipe", "connection closed by", "network is unreachable", "no route to host")))
        storage_error = any(message in result.stderr.lower() for message in (
            "input/output error", "permission denied", "no space left", "read-only file system",
            "host key verification failed", "remote host identification has changed"))
        if attempt == 2 or not transport_error or storage_error:
            raise error
        time.sleep((5, 15)[attempt])
    validate_local_mount(local_root, mount_root, mount_uuid)
    verified = verify_local_run(incoming)
    if (verified["catalog_sha256"], verified["schema_version"], verified["ad_selection"],
            verified["input_source"]) != (
            remote_result["catalog_sha256"], remote_result["schema_version"],
            remote_result["selection"]["mode"], input_source_mode(remote_result)):
        raise ValueError("copied local run does not match the remote run")
    os.replace(str(incoming), str(final))
    verified["directory"] = str(final)
    return verified


def command_deploy(args):
    code_root = validate_remote_path(args.remote_code_root, "code root")
    project_root = Path(__file__).resolve().parent
    if not args.execute:
        print("DRY RUN: deploy {} to {}:{} and compile slim-root".format(
            project_root, args.alias, code_root))
        return 0
    run_command(ssh_command(args.alias, ["mkdir", "-p", code_root]))
    ssh_transport = "ssh " + " ".join(SSH_OPTIONS)
    run_command([
        "rsync", "-rlt", "--partial", "--exclude=.git/", "--exclude=bin/",
        "--exclude=build/", "--exclude=staging/", "--exclude=*.root", "--exclude=*.partial",
        "--exclude=__pycache__/", "--exclude=.DS_Store", "-e", ssh_transport,
        str(project_root) + "/", "{}:{}/".format(args.alias, code_root),
    ])
    result = run_command(ssh_command(args.alias, ["bash", code_root + "/scripts/build_remote.sh", code_root]))
    print(result.stdout.strip())
    return 0


def command_inventory(args):
    records = deployed_inventory(args.alias, args.remote_code_root)
    if args.json:
        print(json.dumps(records, indent=2, sort_keys=True))
        return 0
    good = [record for record in records if "error" not in record]
    bad = [record for record in records if "error" in record]
    print("catalogs={} readable={} unreadable={} backups={} inputs={}".format(
        len(records), len(good), len(bad),
        sum(record.get("catalog_source") == "repository-backup" for record in good),
        sum(record.get("input_count", 0) for record in good)))
    for period, _ in CATALOGS:
        for hall in ("EH1", "EH2", "EH3"):
            group = [record for record in good if record["period"] == period and record["hall"] == hall]
            if group:
                counts = sorted(record["input_count"] for record in group)
                print("{} {} runs={} inputs={} median={} max={}".format(
                    period, hall, len(group), sum(counts), counts[len(counts) // 2], counts[-1]))
    return 1 if bad else 0


def selected_records(records, args):
    selected = [record for record in records if "error" not in record]
    if args.period:
        selected = [record for record in selected if record["period"] in args.period]
    if args.hall:
        selected = [record for record in selected if record["hall"] in args.hall]
    if args.run_pattern:
        pattern = re.compile(args.run_pattern)
        selected = [record for record in selected if pattern.search(record["stem"])]
    if args.limit_runs is not None:
        selected = selected[:args.limit_runs]
    return selected


def command_run(args):
    records = deployed_inventory(args.alias, args.remote_code_root)
    bad = [record for record in records if "error" in record]
    if bad:
        raise RuntimeError("{} catalogs are unreadable; refusing an incomplete production run".format(len(bad)))
    selected = selected_records(records, args)
    print("selected_runs={} selected_inputs={} input_source={}".format(
        len(selected), sum(record["input_count"] for record in selected), args.input_source))
    if not args.execute:
        print("DRY RUN ONLY: no staging, submission, transfer, or deletion performed")
        return 0
    if args.confirm_start != START_CONFIRMATION:
        raise ValueError("production execution requires --confirm-start {}".format(START_CONFIRMATION))
    if min(args.files_per_job, args.memory_mb, args.max_attempts,
           args.run_timeout, args.poll_seconds, args.max_staged_gib) <= 0:
        raise ValueError("job sizing, attempts, timeout, and polling values must be positive")
    if not 1 <= args.max_jobs <= 300:
        raise ValueError("--max-jobs must be between 1 and 300")
    if not selected:
        raise ValueError("no runs selected")
    if len(selected) > 1 and not args.delete_remote_after_sync:
        raise ValueError("multiple runs require --delete-remote-after-sync to preserve staging space")
    if args.delete_remote_after_sync and args.confirm_remote_delete != DELETE_CONFIRMATION:
        raise ValueError("remote deletion requires --confirm-remote-delete {}".format(
            DELETE_CONFIRMATION))
    local_root = validate_local_mount(args.local_root, args.mount_root, args.mount_uuid)
    controller_lock = (local_root / ".controller.lock").open("a")
    fcntl.flock(controller_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    snapshot = load_json(save_inventory_snapshot(local_root, records, args.input_source, args.skip_missing_minis))
    refresh_input_issues(local_root)

    run_pipeline(args, selected, snapshot, local_root)
    print("campaign accounting: {}".format(json.dumps(refresh_input_issues(local_root))), flush=True)
    controller_lock.close()
    return 0


def run_pipeline(args, selected, snapshot, local_root):
    def relative(record):
        return "/".join(record[key] for key in ("period", "hall", "stem"))

    def call(command, marker=None, extra=()):
        options = [command, "--stage-root", args.remote_stage_root]
        if marker:
            options += ["--run-dir", marker["run_dir"]]
        return remote_json(args.alias, args.remote_code_root, options + list(extra))[0]

    def transfer_run(marker):
        result = call("_finalize", marker)
        return sync_run(args.alias, result, local_root, args.mount_root, args.mount_uuid)

    expected = {relative(r): r for r in selected}
    explicit_holds = set(args.hold_run)
    automatic = {}
    content_rechecks = {}
    pending_path = local_root.parent / "provenance/pending-input-issues.json"
    recorded_holds = {entry["run"] for entry in load_json(pending_path).get("held_runs", [])
                      if entry.get("automatic")} if args.hold_empty_minis and pending_path.exists() else set()
    held = explicit_holds | recorded_holds
    if args.hold_empty_minis and args.input_source != "mini":
        raise ValueError("empty-mini holds require mini-only inputs")
    if args.skip_broken_minis and (args.input_source != "mini" or not args.skip_missing_minis or args.hold_empty_minis):
        raise ValueError("broken skips require mini-only missing skips and cannot also request empty-mini holds")
    if held - set(expected):
        raise ValueError("held run is outside the selected fixed catalog: " + ", ".join(sorted(held - set(expected))))
    _, finalized = archive_progress(local_root)
    done = set()
    for record in finalized:
        key = relative(record)
        if (record["schema_version"] != SCHEMA_VERSION or selection_mode(record["selection"]) != args.ad_selection or
                input_source_mode(record) not in archive_input_sources(snapshot, key)):
            raise ValueError("existing local run has a different schema/selection/input source: " + key)
        done.add(key)
    if held & done:
        raise ValueError("cannot hold an already verified local run: " + ", ".join(sorted(held & done)))
    # Previously published runs passed hashes before rename. Rehash on cleanup recovery, or via verify-all.
    state_path = local_root.parent / "provenance/controller.json"
    transfer, transferring = None, None
    observed = set()
    report = dict(max_jobs=args.max_jobs, max_staged_bytes=args.max_staged_gib * GIB)
    scheduler_failures = 0
    # ponytail: one transfer thread bounds disk/network load; increase only after measured transfer saturation.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        try:
            while True:
                try:
                    pool = call("_pool")
                    scheduler_failures = 0
                    report.update(scheduler_status="available", scheduler_error=None, scheduler_failures=0,
                                  scheduler_retry_seconds=None, scheduler_retry_at=None,
                                  last_scheduler_success_at=pool.get("checked_at", utc_now()))
                    if pool["queue"]["outstanding"] > args.max_jobs:
                        raise RuntimeError("existing scheduler jobs exceed configured cap; no new jobs submitted")
                    if pool["queue"]["held"]:
                        raise RuntimeError("held campaign jobs: {}".format([j for j in pool["jobs"] if j["JobStatus"] == 5]))
                    if transfer is not None and transfer.done():
                        result = transfer.result()  # Any verification/copy failure prevents deletion and further submission.
                        print("verified local files={} bytes={} path={}".format(
                            result["files"], result["bytes"], result["directory"]), flush=True)
                        validate_local_mount(local_root, args.mount_root, args.mount_uuid)
                        refresh_input_issues(local_root)
                        if args.delete_remote_after_sync:
                            current = next((r for r in pool["runs"]
                                            if r["marker"]["run_dir"] == transferring["run_dir"]), None)
                            if (current is None or current["intent"] or current["status"]["running"] or
                                    any(j["Iwd"] == transferring["run_dir"] for j in pool["jobs"])):
                                raise RuntimeError("verified transfer still has possible writers; staging retained")
                            deleted = call("_cleanup", transferring, ["--catalog-sha256", transferring["catalog_sha256"]])
                            print("deleted remote staging {}".format(deleted["deleted"]), flush=True)
                        done.add(relative(transferring))
                        transfer, transferring = None, None
                        continue  # Refresh staging/queue after cleanup before another admission or retry.
                    active = {}
                    rechecking = set()
                    startup_pending = {}
                    for run in pool["runs"]:
                        marker = run["marker"]
                        key = relative(marker)
                        record = expected.get(key)
                        if (record is None or marker["catalog_sha256"] != record["catalog_sha256"] or
                                marker["input_count"] != record["input_count"] or
                                marker["files_per_job"] != args.files_per_job or
                                marker["code_root"] != args.remote_code_root or
                                selection_mode(marker["selection"]) != args.ad_selection or
                                input_source_mode(marker) != args.input_source or
                                marker.get("skip_missing_minis", False) != args.skip_missing_minis):
                            raise ValueError("staged run differs from requested fixed campaign: " + key)
                        if key not in observed:
                            paths = parse_catalog_inputs((CATALOG_BACKUP_ROOT / (key + ".list")).read_bytes(), key)
                            recorded_inputs(paths, marker)
                            refresh_input_issues(local_root, prepared=marker)
                            print("adopted {} chunks={} gaps={}".format(key, marker["chunk_count"],
                                  len(marker.get("skipped_inputs", []))), flush=True)
                            observed.add(key)
                        if key not in done or args.delete_remote_after_sync:
                            active[key] = run
                        own = [j for j in pool["jobs"] if j["Iwd"] == marker["run_dir"]]
                        if run.get("startup_recovery"):
                            journal = run["startup_recovery"]
                            if not isinstance(journal, list) or any(
                                    item["kind"] != "dyb-worker-startup-recovery" or
                                    item["run_dir"] != marker["run_dir"] or
                                    item["catalog_sha256"] != marker["catalog_sha256"] for item in journal):
                                raise ValueError("startup recovery journal differs from fixed campaign: " + key)
                            path = local_root.parent / "provenance/worker-startup-recovery" / (key + ".json")
                            if not path.exists() or load_json(path) != journal:
                                validate_local_mount(local_root, args.mount_root, args.mount_uuid)
                                path.parent.mkdir(parents=True, exist_ok=True)
                                atomic_json(path, journal)
                        startup = run.get("startup_failure") or {}
                        if startup.get("recheck") or (startup and
                                startup.get("failure_updates") != run["status"].get("failure_updates")):
                            content_rechecks[key] = content_rechecks.get(key, 0) + 1
                            if content_rechecks[key] > 8:
                                raise RuntimeError("worker failure snapshot did not settle after 8 rechecks; run retained: " + key)
                            rechecking.add(key)
                            continue
                        if startup.get("startup") and key not in explicit_holds:
                            content_rechecks.pop(key, None)
                            attempts = len(run["submissions"]) - marker.get("broken_recovery_submissions", 0)
                            startup_pending[key] = dict(run=key, failed_chunks=sorted(startup["failure_updates"]),
                                attempts=attempts, max_attempts=min(2, args.max_attempts),
                                state="waiting_for_writers" if own or run["status"]["running"] else
                                      "exhausted" if attempts >= min(2, args.max_attempts) else "ready_for_retry")
                            continue  # Never send an environment failure through missing/broken-input classification.
                        if (args.skip_broken_minis and key not in explicit_holds and
                                (run["status"]["failed"] or run.get("broken_recovery_pending")) and
                                not own and not run["status"]["running"]):
                            recovery = call("_skip_broken", marker)
                            if recovery.get("recheck"):
                                content_rechecks[key] = content_rechecks.get(key, 0) + 1
                                if content_rechecks[key] > 8:
                                    raise RuntimeError("broken-input snapshot did not settle after 8 rechecks; run retained: " + key)
                                rechecking.add(key)
                                continue
                            content_rechecks.pop(key, None)
                            run.update(recovery)
                            run["broken_recovery_pending"] = False
                            refresh_input_issues(local_root, prepared=run["marker"])
                            print("recorded broken gaps {}; expected={} processed={} missing={} broken={}; good chunks retained".format(
                                key, run["marker"]["input_count"], run["marker"]["processed_input_count"],
                                run["marker"]["missing_skipped_input_count"], run["marker"]["broken_skipped_input_count"]), flush=True)
                        elif ((args.hold_empty_minis or args.skip_broken_minis) and
                              key not in explicit_holds and run["status"]["failed"]):
                            signature = run["status"]["failure_updates"]
                            if automatic.get(key, {}).get("failure_updates") != signature:
                                proof = call("_content_errors", marker)
                                if proof.get("recheck") or proof["failure_updates"] != signature:
                                    # Other workers may publish failures during the read-only ROOT inspection.
                                    content_rechecks[key] = content_rechecks.get(key, 0) + 1
                                    if content_rechecks[key] > 8:
                                        raise RuntimeError("content-error snapshot did not settle after 8 rechecks; run retained: " + key)
                                    rechecking.add(key)
                                    continue
                                if len(proof["issues"]) != run["status"]["failed"]:
                                    raise RuntimeError("incomplete content-error proof: " + key)
                                content_rechecks.pop(key, None)
                                automatic[key] = proof
                                print("holding {} pending; confirmed empty-mini errors={}".format(
                                    key, len(proof["issues"])), flush=True)
                            if args.skip_broken_minis:
                                rechecking.add(key)  # Proved content errors await the run's existing writers; no mutation yet.
                    held = explicit_holds | recorded_holds | (set(automatic) if args.hold_empty_minis else set())
                    directories = {r["marker"]["run_dir"] for r in pool["runs"]}
                    if any(j["Iwd"] not in directories for j in pool["jobs"]):
                        raise RuntimeError("queued campaign job has no matching staged run; refusing new submissions")
                    if held - set(active):
                        raise RuntimeError("held run has no retained staging: " + ", ".join(sorted(held - set(active))))
                    if held or (args.skip_broken_minis and pending_path.exists()):
                        refresh_pending_holds(local_root, held, active, automatic, not args.drain_staging, args.skip_broken_minis)

                    pending = [r for key, r in expected.items() if key not in done and key not in active]
                    overdue = []
                    for key, run in active.items():
                        own = [j for j in pool["jobs"] if j["Iwd"] == run["marker"]["run_dir"]]
                        if key not in held and own and run["submissions"]:
                            submitted_at = run["submissions"][-1]["submitted_at"]
                            elapsed = time.time() - dt.datetime.fromisoformat(submitted_at).timestamp()
                            if elapsed > args.run_timeout:
                                overdue.append(dict(run=key, catalog_sha256=run["marker"]["catalog_sha256"],
                                    submitted_at=submitted_at, elapsed_seconds=int(elapsed),
                                    jobs=[dict(cluster_id=j["ClusterId"], proc_id=j["ProcId"],
                                               state=j["JobStatus"]) for j in own]))
                    report.update(checked_at=utc_now(), heartbeat_time=time.time(), queue=pool["queue"],
                                  quota=pool["quota"], reserved_bytes=pool["reserved_bytes"],
                                  output_bytes=pool["output_bytes"], staged_runs=len(active),
                                  ready_runs=sum(r["status"]["state"] == "COMPLETE" for r in active.values()),
                                  transferring=relative(transferring) if transferring else None,
                                  pending_runs=len(pending), backpressure=None, error=None,
                                  new_run_admission=not args.drain_staging,
                                  skip_broken_minis=args.skip_broken_minis,
                                  overdue_runs=overdue,
                                  pending_content_checks=sorted(rechecking),
                                  startup_recovery_pending=list(startup_pending.values()),
                                  held_runs=[dict(run=key, catalog_sha256=active[key]["marker"]["catalog_sha256"],
                                                  status=active[key]["status"],
                                                  reason="operator hold for existing mini content errors; inputs remain pending")
                                             for key in sorted(held)])
                    atomic_json(state_path, report)
                    if not active and (not pending or args.drain_staging):
                        break

                    submitted = False
                    for key, run in active.items():
                        marker, status = run["marker"], run["status"]
                        own = [j for j in pool["jobs"] if j["Iwd"] == marker["run_dir"]]
                        if key in rechecking:
                            continue
                        if key in held:
                            if key in explicit_holds and (own or status["running"]):
                                raise RuntimeError("held run still has scheduler jobs or worker markers: " + key)
                            if status["state"] == "COMPLETE":
                                raise RuntimeError("held run is now complete; inspect before releasing hold: " + key)
                            continue
                        if key in startup_pending:
                            if run["intent"]:
                                raise RuntimeError("startup failure has an unresolved submission intent; retain it: " + key)
                            if own or status["running"]:
                                continue
                        if status["state"] == "COMPLETE" and not own and not status["running"]:
                            if transfer is None:
                                transferring = marker
                                transfer = executor.submit(transfer_run, marker)
                            continue
                        expired = False
                        if run["submissions"]:
                            elapsed = time.time() - dt.datetime.fromisoformat(run["submissions"][-1]["submitted_at"]).timestamp()
                            expired = elapsed > args.run_timeout
                            if expired and own:
                                continue  # Retain writers and report overdue work without blocking other transfers.
                        # Scheduler kills before worker startup can leave no .fail or .running file.
                        retry = not own and (status["state"] == "FAILED" or expired or
                                             marker.get("broken_recovery_submissions") == len(run["submissions"]))
                        attempt_limit = min(2, args.max_attempts) if key in startup_pending else args.max_attempts
                        if retry and len(run["submissions"]) - marker.get("broken_recovery_submissions", 0) >= attempt_limit:
                            raise RuntimeError("run exhausted submission attempts: " + key)
                        if run["intent"] or not run["submissions"] or retry:
                            if pool["queue"]["outstanding"] + marker["chunk_count"] > args.max_jobs:
                                continue
                            reason = admission_reason(pool, 0, args.max_jobs, args.max_staged_gib * GIB,
                                                      shutil.disk_usage(local_root).free)
                            if reason:
                                report["backpressure"] = reason
                                continue
                            extra = ["--group", args.group, "--walltime", args.walltime,
                                     "--memory-mb", str(args.memory_mb), "--max-jobs", str(args.max_jobs)]
                            if retry:
                                extra.append("--resubmit")
                                if key in startup_pending:
                                    extra += ["--startup-retry", "--max-attempts", str(attempt_limit)]
                                if expired:
                                    print("retrying expired incomplete run {}; no scheduler jobs remain; "
                                          "verified chunks retained".format(key), flush=True)
                            submission = call("_submit", marker, extra)
                            print("submitted/adopted {} cluster {}".format(key, submission["cluster_id"]), flush=True)
                            submitted = True
                            break  # Refresh real queue counts before another submission.
                    if submitted:
                        continue
                    if active.keys() == held and not rechecking and (not pending or args.drain_staging):
                        raise RuntimeError("only held incomplete runs remain: " + ", ".join(sorted(held)))
                    if any(r["status"]["failed"] for key, r in active.items()
                           if key not in held and key not in rechecking and key not in startup_pending):
                        raise RuntimeError("failed chunks; other in-flight jobs retained for safe recovery")
                    if report["backpressure"] and not pool["jobs"] and transfer is None:
                        raise RuntimeError("cannot submit prepared runs: " + report["backpressure"])
                    if pending and not args.drain_staging:
                        record = pending[0]
                        chunks = math.ceil(record["input_count"] / args.files_per_job)
                        reason = admission_reason(pool, chunks, args.max_jobs, args.max_staged_gib * GIB,
                                                  shutil.disk_usage(local_root).free)
                        report["backpressure"] = reason
                        if not reason:
                            validate_local_mount(local_root, args.mount_root, args.mount_uuid)
                            options = ["--catalog", record["catalog"], "--period", record["period"],
                                       "--hall", record["hall"], "--code-root", args.remote_code_root,
                                       "--files-per-job", str(args.files_per_job), "--ad-selection", args.ad_selection,
                                       "--input-source", args.input_source]
                            if args.skip_missing_minis:
                                options.append("--skip-missing-minis")
                            call("_prepare", extra=options)
                            continue
                        if not active:
                            raise RuntimeError("cannot admit next run: " + reason)
                    atomic_json(state_path, report)
                    time.sleep(args.poll_seconds)
                except SchedulerQueryUnavailable as error:
                    validate_local_mount(local_root, args.mount_root, args.mount_uuid)
                    if transfer is not None and transfer.done():
                        transfer.result()  # A concurrent checksum/copy failure remains fatal during an outage.
                    scheduler_failures += 1
                    report.update(checked_at=utc_now(), heartbeat_time=time.time(), error=None,
                                  scheduler_status="unavailable", scheduler_error=str(error),
                                  scheduler_failures=scheduler_failures, scheduler_retry_seconds=60,
                                  scheduler_retry_at=time.time() + 60, queue=None, quota=None,
                                  reserved_bytes=None, output_bytes=None, staged_runs=None, ready_runs=None,
                                  overdue_runs=[], transferring=relative(transferring) if transferring else None,
                                  new_run_admission=False, backpressure="scheduler query unavailable")
                    atomic_json(state_path, report)
                    print("{} scheduler unavailable; queue unknown; submissions/cleanup paused; retry in 60s".format(
                        utc_now()), flush=True)
                    time.sleep(60)
                    continue
        except BaseException as error:
            report.update(checked_at=utc_now(), heartbeat_time=time.time(), error=str(error))
            atomic_json(state_path, report)
            if transfer is not None:
                try:
                    # The executor already waits on exit. Account for a published copy, retaining staging.
                    transfer.result()
                    validate_local_mount(local_root, args.mount_root, args.mount_uuid)
                    refresh_input_issues(local_root)
                except Exception as transfer_error:
                    report["transfer_error"] = str(transfer_error)
                    atomic_json(state_path, report)
            raise


def add_connection_options(parser):
    parser.add_argument("--alias", default=DEFAULT_ALIAS)
    parser.add_argument("--remote-code-root", default=DEFAULT_CODE_ROOT)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    deploy = commands.add_parser("deploy", help="copy code to IHEP and compile it")
    add_connection_options(deploy)
    deploy.add_argument("--execute", action="store_true")
    deploy.set_defaults(function=command_deploy)

    inventory = commands.add_parser("inventory", help="read the fixed run catalogs")
    add_connection_options(inventory)
    inventory.add_argument("--json", action="store_true")
    inventory.set_defaults(function=command_inventory)

    run = commands.add_parser("run", help="process concurrent runs with bounded jobs and staging")
    add_connection_options(run)
    run.add_argument("--remote-stage-root", default=DEFAULT_STAGE_ROOT)
    run.add_argument("--local-root", required=True)
    run.add_argument("--mount-root", required=True)
    run.add_argument("--mount-uuid", help="Linux: require this filesystem UUID before and after transfers")
    run.add_argument("--period", action="append", choices=[item[0] for item in CATALOGS])
    run.add_argument("--hall", action="append", choices=("EH1", "EH2", "EH3"))
    run.add_argument("--run-pattern")
    run.add_argument("--hold-run", action="append", default=[], metavar="PERIOD/HALL/RUN",
                     help="retain an incomplete staged run for input repair; never submit, finalize, or clean it")
    run.add_argument("--hold-empty-minis", action="store_true",
                     help="hold runs with independently verified empty AdEnergy-only minis; inputs remain pending")
    run.add_argument("--drain-staging", action="store_true",
                     help="finish retained staging without preparing new catalog runs; expected inputs remain pending")
    run.add_argument("--limit-runs", type=int)
    run.add_argument("--files-per-job", type=int, default=16)
    run.add_argument("--input-source", choices=INPUT_SOURCES, default=DEFAULT_INPUT_SOURCE,
                     help="mini (default): All-mini; recon: fixed originals; mini-or-recon: "
                          "explicitly record exact originals for unavailable minis")
    run.add_argument("--skip-missing-minis", action="store_true",
                     help="record and skip confirmed missing minis; stop on other errors or suspected outages")
    run.add_argument("--skip-broken-minis", action="store_true",
                     help="independently prove and separately record empty AdEnergy-only minis, preserving chunk boundaries")
    run.add_argument("--ad-selection", choices=AD_SELECTION_MODES, default="off",
                     help="on: keep AD entries above 0.6 MeV plus every other detector; "
                          "off (default): keep every entry (band counts are still recorded)")
    run.add_argument("--group", default="dyw")
    run.add_argument("--walltime", default="short")
    run.add_argument("--memory-mb", type=int, default=1024)
    run.add_argument("--max-attempts", type=int, default=2)
    run.add_argument("--run-timeout", type=int, default=7200)
    run.add_argument("--poll-seconds", type=int, default=15)
    run.add_argument("--max-jobs", type=int, default=300, help="cap all outstanding compression jobs (1-300)")
    run.add_argument("--max-staged-gib", type=int, default=400, help="staging admission budget; reserve 1 GiB per chunk")
    run.add_argument("--execute", action="store_true")
    run.add_argument("--confirm-start")
    run.add_argument("--delete-remote-after-sync", action="store_true")
    run.add_argument("--confirm-remote-delete")
    run.set_defaults(function=command_run)

    verify = commands.add_parser("verify-local", help="recheck one locally copied run")
    verify.add_argument("directory")
    verify.add_argument("--root-verifier", help="optional slim-root binary for structural checks")
    verify.set_defaults(function=lambda args: print(json.dumps(verify_local_run(
        args.directory, root_verifier=args.root_verifier))) or 0)

    verify_everything = commands.add_parser(
        "verify-all", help="verify every fixed-catalog run in a completed local archive")
    verify_everything.add_argument("local_root")
    verify_everything.add_argument(
        "--root-verifier", help="optional slim-root binary for structural checks")
    def verify_all_function(args):
        result = verify_all(args.local_root, root_verifier=args.root_verifier)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["complete"] else 2
    verify_everything.set_defaults(function=verify_all_function)

    verify_backups = commands.add_parser(
        "verify-catalog-backups", help="verify the complete fixed run-catalog snapshot")
    verify_backups.set_defaults(function=lambda args: print(json.dumps(
        verify_catalog_backups(), sort_keys=True)) or 0)

    internal = commands.add_parser("_inventory")
    internal.set_defaults(function=lambda args: print(json.dumps(inventory_records())) or 0)

    prepare = commands.add_parser("_prepare")
    prepare.add_argument("--catalog", required=True)
    prepare.add_argument("--period", required=True)
    prepare.add_argument("--hall", required=True)
    prepare.add_argument("--stage-root", required=True)
    prepare.add_argument("--code-root", required=True)
    prepare.add_argument("--files-per-job", type=int, required=True)
    prepare.add_argument("--ad-selection", choices=AD_SELECTION_MODES, default="off")
    prepare.add_argument("--input-source", choices=INPUT_SOURCES, default=DEFAULT_INPUT_SOURCE)
    prepare.add_argument("--skip-missing-minis", action="store_true")
    prepare.set_defaults(function=lambda args: print(json.dumps(prepare_run(
        args.catalog, args.period, args.hall, args.stage_root, args.code_root,
        args.files_per_job, ad_selection=args.ad_selection, input_source=args.input_source,
        skip_missing_minis=args.skip_missing_minis))) or 0)

    submit = commands.add_parser("_submit")
    submit.add_argument("--run-dir", required=True)
    submit.add_argument("--stage-root", required=True)
    submit.add_argument("--group", required=True)
    submit.add_argument("--walltime", required=True)
    submit.add_argument("--memory-mb", type=int, required=True)
    submit.add_argument("--resubmit", action="store_true")
    submit.add_argument("--max-jobs", type=int, default=300)
    submit.add_argument("--startup-retry", action="store_true")
    submit.add_argument("--max-attempts", type=int, default=2)
    submit.set_defaults(function=lambda args: print(json.dumps(submit_run(
        args.run_dir, args.stage_root, args.group, args.walltime, args.memory_mb,
        args.resubmit, args.max_jobs, args.startup_retry, args.max_attempts))) or 0)

    pool = commands.add_parser("_pool")
    pool.add_argument("--stage-root", required=True)
    pool.set_defaults(function=lambda args: print(json.dumps(pool_status(args.stage_root))) or 0)

    status = commands.add_parser("_status")
    status.add_argument("--run-dir", required=True)
    status.add_argument("--stage-root", required=True)
    status.set_defaults(function=lambda args: print(json.dumps(run_status(
        args.run_dir, args.stage_root))) or 0)

    content = commands.add_parser("_content_errors")
    content.add_argument("--run-dir", required=True)
    content.add_argument("--stage-root", required=True)
    content.set_defaults(function=lambda args: print(json.dumps(confirmed_empty_mini_errors(
        args.run_dir, args.stage_root))) or 0)

    broken = commands.add_parser("_skip_broken")
    broken.add_argument("--run-dir", required=True)
    broken.add_argument("--stage-root", required=True)
    broken.set_defaults(function=lambda args: print(json.dumps(skip_broken_minis(args.run_dir, args.stage_root))) or 0)

    check_chunk = commands.add_parser("_check_chunk")
    check_chunk.add_argument("--run-dir", required=True)
    check_chunk.add_argument("--stage-root", required=True)
    check_chunk.add_argument("--chunk", required=True, type=int)
    def check_chunk_function(args):
        directory, marker = load_run(args.run_dir, args.stage_root)
        check_chunk_sources(directory, marker, args.chunk)
        return 0
    check_chunk.set_defaults(function=check_chunk_function)

    wait = commands.add_parser("_wait")
    wait.add_argument("--run-dir", required=True)
    wait.add_argument("--stage-root", required=True)
    wait.add_argument("--timeout", type=int, required=True)
    wait.add_argument("--poll-seconds", type=int, required=True)

    def wait_function(args):
        deadline = time.time() + args.timeout
        while True:
            status_value = run_status(args.run_dir, args.stage_root)
            if status_value["state"] in ("COMPLETE", "FAILED") or time.time() >= deadline:
                print(json.dumps(status_value))
                return 0 if status_value["state"] == "COMPLETE" else 1
            time.sleep(args.poll_seconds)

    wait.set_defaults(function=wait_function)

    finalize = commands.add_parser("_finalize")
    finalize.add_argument("--run-dir", required=True)
    finalize.add_argument("--stage-root", required=True)
    finalize.set_defaults(function=lambda args: print(json.dumps(finalize_run(
        args.run_dir, args.stage_root))) or 0)

    cleanup = commands.add_parser("_cleanup")
    cleanup.add_argument("--run-dir", required=True)
    cleanup.add_argument("--stage-root", required=True)
    cleanup.add_argument("--catalog-sha256", required=True)
    cleanup.set_defaults(function=lambda args: print(json.dumps(cleanup_run(
        args.run_dir, args.stage_root, args.catalog_sha256))) or 0)
    return parser


def main(argv=None):
    os.umask(0o077)
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.function(args)
    except SchedulerQueryUnavailable as error:
        phase = SCHEDULER_QUERY_PHASES.get(args.command)
        if phase is None:
            print("ERROR: {}".format(error), file=sys.stderr)
            return 1
        print(json.dumps(dict(scheduler_unavailable=str(error), command=args.command, phase=phase)))
        return 75  # Explicit proved pre-mutation/read-only failure, never an empty successful queue.
    except (KeyError, OSError, RuntimeError, TypeError, ValueError, subprocess.TimeoutExpired) as error:
        print("ERROR: {}".format(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
