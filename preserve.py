#!/usr/bin/env python3
"""One-run-at-a-time preservation pipeline for the fixed Daya Bay catalogs."""

import argparse
import concurrent.futures
import datetime as dt
import functools
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time


SCHEMA_VERSION = 3
AD_SELECTION_MODES = ("on", "off")
AD_COUNT_COLUMNS = tuple(
    "ad{}{}".format(detector, kind)
    for detector in (1, 2, 3, 4) for kind in ("Below0p3MeV", "From0p3To0p7MeV"))
MARKER_NAME = ".dyb-preserve-run.json"
START_CONFIRMATION = "START-DYB-PRESERVATION"
DELETE_CONFIRMATION = "DELETE-VERIFIED-RUN"
DEFAULT_ALIAS = "dayabay1"
DEFAULT_CODE_ROOT = "/dybfs2/users/lijj16/DYB_DATA_PRESERVATION/code"
DEFAULT_STAGE_ROOT = "/dybfs2/users/lijj16/DYB_DATA_PRESERVATION/staging"
DEFAULT_LCG_VIEW = "/cvmfs/sft.cern.ch/lcg/views/LCG_110/x86_64-el9-gcc13-opt"
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
)


def selection_record(mode):
    if mode not in AD_SELECTION_MODES:
        raise ValueError("AD selection must be 'on' or 'off'")
    return {
        "mode": mode,
        "ad_detectors": [1, 2, 3, 4],
        "ad_min_energy_mev": 0.7,
        "counted_band_mev": [0.3, 0.7],
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
                require_registered=True, ad_selection="on"):
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
    catalog_data, catalog_source, catalog_backup = read_catalog_data(period, hall, catalog)
    catalog_sha = hashlib.sha256(catalog_data).hexdigest()
    inputs = parse_catalog_inputs(catalog_data, catalog)

    run_dir = (Path(stage_root) / period / hall / stem).resolve()
    if run_dir.exists():
        marker = load_json(run_dir / MARKER_NAME)
        expected = (catalog_sha, files_per_job, period, hall, selection)
        actual = (marker.get("catalog_sha256"), marker.get("files_per_job"),
                  marker.get("period"), marker.get("hall"), marker.get("selection"))
        if actual != expected:
            raise ValueError("existing run staging does not match requested catalog")
        return marker

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
        atomic_text(temporary / "output-prefix", stem + "\n")
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
            "catalog": catalog,
            "catalog_source": catalog_source,
            "catalog_sha256": catalog_sha,
            "input_count": len(inputs),
            "files_per_job": files_per_job,
            "chunk_count": len(chunks),
            "run_dir": str(run_dir),
            "code_root": code_root,
            "selection": selection,
        }
        if catalog_backup:
            marker["catalog_backup"] = catalog_backup
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


def submit_run(run_dir, stage_root, group, walltime, memory_mb, resubmit=False):
    run_dir, marker = load_run(run_dir, stage_root)
    submissions_path = run_dir / "submissions.json"
    submissions = load_json(submissions_path) if submissions_path.exists() else []
    if submissions and not resubmit:
        return submissions[-1]
    if resubmit:
        for path in (run_dir / "status").glob("*.fail"):
            path.unlink()
    hepjob_bin = "/afs/ihep.ac.cn/soft/common/sysgroup/hep_job/bin"
    command = [
        os.path.join(hepjob_bin, "hep_sub"), "-g", group, "-wt", walltime,
        "-mem", str(memory_mb), str(run_dir / "array-job.sh"),
        "-argu", "%{ProcId}", "-n", str(marker["chunk_count"]),
    ]
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
    return {
        "state": state,
        "ok": len(ok),
        "failed": len(failed),
        "running": len(running),
        "chunks": marker["chunk_count"],
        "run_dir": str(run_dir),
    }


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


def aggregate_duration_reports(run_dir, marker, prefix):
    mode = selection_mode(marker.get("selection"))
    rows = []
    total_entries = 0
    total_input_bytes = 0
    total_kept = 0
    chunk_totals = []
    for chunk_index in range(marker["chunk_count"]):
        chunk = run_dir / "chunks" / "{:06d}.list".format(chunk_index)
        expected_paths = [line for line in chunk.read_text(encoding="utf-8").splitlines() if line]
        report = run_dir / "reports" / "{}.slim.{:06d}.sources.tsv".format(
            prefix, chunk_index)
        chunk_kept = chunk_entries = 0
        for source_index, fields in enumerate(read_source_report(report, expected_paths)):
            entries, input_bytes, kept = validate_source_fields(
                fields, expected_paths[source_index], source_index, mode)
            rows.append([str(len(rows)), str(chunk_index)] + fields)
            total_entries += entries
            total_input_bytes += input_bytes
            total_kept += kept
            chunk_kept += kept
            chunk_entries += entries
        chunk_totals.append((chunk_kept, chunk_entries, len(expected_paths)))
    if len(rows) != marker["input_count"]:
        raise ValueError("source timing total does not match run input count")
    destination = run_dir / "durations.tsv"
    atomic_text(destination, "\t".join(DURATION_COLUMNS) + "\n" +
                "\n".join("\t".join(row) for row in rows) + "\n")
    return {
        "path": destination, "rows": len(rows), "entries": total_entries,
        "input_bytes": total_input_bytes, "kept": total_kept, "chunk_totals": chunk_totals,
    }


def validate_duration_table(path, catalog_paths, files_per_job, mode="on"):
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    if not lines or tuple(lines[0].split("\t")) != DURATION_COLUMNS:
        raise ValueError("invalid run duration header: {}".format(path))
    rows = [line.split("\t") for line in lines[1:]]
    if len(rows) != len(catalog_paths):
        raise ValueError("run duration row count does not match catalog")
    total_entries = total_kept = 0
    chunk_totals = [[0, 0, len(chunk)] for chunk in split_chunks(catalog_paths, files_per_job)]
    for file_index, (fields, expected_path) in enumerate(zip(rows, catalog_paths)):
        if len(fields) != len(DURATION_COLUMNS):
            raise ValueError("invalid run duration row width")
        chunk_index, source_index = divmod(file_index, files_per_job)
        try:
            row_file_index, row_chunk_index = int(fields[0]), int(fields[1])
        except ValueError as error:
            raise ValueError("invalid run duration index: {}".format(error))
        if row_file_index != file_index or row_chunk_index != chunk_index:
            raise ValueError("run duration indices are not contiguous")
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
    outputs = [run_dir / "outputs" / "{}.slim.{:06d}.root".format(prefix, index)
               for index in range(marker["chunk_count"])]
    missing = [str(path) for path in outputs if not path.is_file()]
    if missing:
        raise ValueError("missing outputs: {}".format(missing[:3]))

    verifier = Path(marker["code_root"]) / "bin" / "slim-root"
    if not verifier.is_file() or not os.access(str(verifier), os.X_OK):
        raise ValueError("missing ROOT verifier: {}".format(verifier))
    durations = aggregate_duration_reports(run_dir, marker, prefix)
    duration_file = durations["path"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(outputs))) as executor:
        list(executor.map(
            lambda item: verify_remote_root(verifier, item[0], item[1], marker["selection"]["mode"]),
            zip(outputs, durations["chunk_totals"])))

    artifacts = outputs + [duration_file]
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(artifacts))) as executor:
        hashes = list(executor.map(sha256_file, artifacts))
    checksum_lines = ["{}  outputs/{}".format(digest, path.name)
                      for digest, path in zip(hashes, outputs)]
    checksum_lines.append("{}  durations.tsv".format(hashes[-1]))
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
            result.get("schema_version") != SCHEMA_VERSION):
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
        raise RuntimeError("command failed ({}):\n{}\n{}".format(
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
    result = remote_call(alias, code_root, arguments, check=check)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("remote command returned no JSON: {}".format(result.stderr))
    return json.loads(lines[-1]), result.returncode


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
    if result.get("duration_file") != "durations.tsv" or result.get("checksums") != "SHA256SUMS":
        raise ValueError("run.json names unexpected integrity artifacts")
    validate_token(result.get("stem"), "run stem")
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
        directory / result["duration_file"], catalog_paths, result["files_per_job"], mode)
    duration_rows = durations["rows"]
    if (duration_rows, durations["entries"], durations["kept"]) != (
            result.get("duration_rows"), result.get("source_entries"),
            result.get("source_kept_entries")):
        raise ValueError("duration rows or entry totals do not match run.json")
    if len(durations["chunk_totals"]) != result["chunk_count"]:
        raise ValueError("duration chunk count does not match run.json")

    expected_outputs = {
        "outputs/{}.slim.{:06d}.root".format(result["stem"], index)
        for index in range(result["chunk_count"])
    }
    actual_outputs = {
        str(path.relative_to(directory)) for path in (directory / "outputs").glob("*.root")
    }
    if actual_outputs != expected_outputs:
        raise ValueError("local ROOT output set is incomplete or contains unexpected files")
    expected_artifacts = expected_outputs | {result["duration_file"]}
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
            relative = "outputs/{}.slim.{:06d}.root".format(result["stem"], index)
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
        "catalog_sha256": result["catalog_sha256"],
        "schema_version": result["schema_version"],
        "directory": str(directory),
    }


def catalog_signature(records):
    fields = ("period", "hall", "catalog", "stem", "catalog_sha256", "input_count")
    try:
        return sorted(tuple(record[field] for field in fields) for record in records)
    except (KeyError, TypeError) as error:
        raise ValueError("invalid catalog inventory record: {}".format(error))


def save_inventory_snapshot(local_root, records):
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
        return path
    atomic_json(path, {
        "kind": "dyb-preserve-catalog-inventory",
        "schema_version": SCHEMA_VERSION,
        "created_at": utc_now(),
        "records": records,
    })
    return path


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

    total_artifacts = total_bytes = total_inputs = total_entries = total_kept = 0
    modes = set()
    for relative, record in sorted(expected.items()):
        result = load_json(actual[relative] / "run.json")
        if (result.get("catalog_sha256"), result.get("input_count")) != (
                record["catalog_sha256"], record["input_count"]):
            raise ValueError("local run does not match inventory: {}".format(relative))
        verified = verify_local_run(actual[relative], root_verifier=root_verifier)
        total_artifacts += verified["files"]
        total_bytes += verified["bytes"]
        total_inputs += verified["duration_rows"]
        total_entries += verified["source_entries"]
        total_kept += verified["kept_entries"]
        modes.add(verified["ad_selection"])
    if len(modes) != 1:
        raise ValueError("archive mixes AD selection modes: {}".format(sorted(modes)))
    return {
        "complete": True,
        "runs": len(records),
        "inputs": total_inputs,
        "source_entries": total_entries,
        "kept_entries": total_kept,
        "ad_selection": modes.pop(),
        "artifacts": total_artifacts,
        "bytes": total_bytes,
        "local_root": str(local_root),
    }


def validate_local_mount(local_root, mount_root):
    mount_root = Path(mount_root).resolve()
    if not mount_root.exists() or not os.path.ismount(str(mount_root)):
        raise ValueError("required local disk is not mounted at {}".format(mount_root))
    local_root = Path(local_root).resolve()
    try:
        local_root.relative_to(mount_root)
    except ValueError:
        raise ValueError("local root is outside the required mount")
    local_root.mkdir(parents=True, exist_ok=True)
    return local_root


def sync_run(alias, remote_result, local_root, mount_root):
    local_root = validate_local_mount(local_root, mount_root)
    final = local_root / remote_result["period"] / remote_result["hall"] / remote_result["stem"]
    if final.exists():
        verified = verify_local_run(final)
        if (verified["catalog_sha256"], verified["schema_version"], verified["ad_selection"]) != (
                remote_result["catalog_sha256"], remote_result["schema_version"],
                remote_result["selection"]["mode"]):
            raise ValueError("existing local run does not match the remote run")
        return verified
    final.parent.mkdir(parents=True, exist_ok=True)
    incoming = final.with_name(".{}.incoming".format(final.name))
    incoming.mkdir(exist_ok=True)
    free = shutil.disk_usage(str(local_root)).free
    required = int(remote_result["artifact_bytes"] * 1.05)
    if free < required:
        raise ValueError("local disk has {} bytes free; {} required".format(free, required))
    ssh_transport = "ssh " + " ".join(SSH_OPTIONS)
    command = [
        "rsync", "-rt", "--partial", "--progress", "-e", ssh_transport,
        "{}:{}/".format(alias, remote_result["run_dir"]), str(incoming) + "/",
    ]
    run_command(command)
    validate_local_mount(local_root, mount_root)
    verified = verify_local_run(incoming)
    if (verified["catalog_sha256"], verified["schema_version"], verified["ad_selection"]) != (
            remote_result["catalog_sha256"], remote_result["schema_version"],
            remote_result["selection"]["mode"]):
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
    print("selected_runs={} selected_inputs={}".format(
        len(selected), sum(record["input_count"] for record in selected)))
    if not args.execute:
        print("DRY RUN ONLY: no staging, submission, transfer, or deletion performed")
        return 0
    if args.confirm_start != START_CONFIRMATION:
        raise ValueError("production execution requires --confirm-start {}".format(START_CONFIRMATION))
    if min(args.files_per_job, args.memory_mb, args.max_attempts,
           args.run_timeout, args.poll_seconds) <= 0:
        raise ValueError("job sizing, attempts, timeout, and polling values must be positive")
    if not selected:
        raise ValueError("no runs selected")
    if len(selected) > 1 and not args.delete_remote_after_sync:
        raise ValueError("multiple runs require --delete-remote-after-sync to preserve staging space")
    if args.delete_remote_after_sync and args.confirm_remote_delete != DELETE_CONFIRMATION:
        raise ValueError("remote deletion requires --confirm-remote-delete {}".format(
            DELETE_CONFIRMATION))
    local_root = validate_local_mount(args.local_root, args.mount_root)
    save_inventory_snapshot(local_root, records)

    for index, record in enumerate(selected, 1):
        print("[{}/{}] {} {} {}".format(index, len(selected), record["period"],
                                         record["hall"], record["stem"]), flush=True)
        local_run = local_root / record["period"] / record["hall"] / record["stem"]
        if local_run.exists():
            existing = verify_local_run(local_run)
            if (existing["catalog_sha256"], existing["schema_version"], existing["ad_selection"]) != (
                    record["catalog_sha256"], SCHEMA_VERSION, args.ad_selection):
                raise ValueError("existing local run does not match the fixed catalog/schema/selection")
            print("already verified locally; skipping cluster processing", flush=True)
            continue
        prepare_args = [
            "_prepare", "--catalog", record["catalog"], "--period", record["period"],
            "--hall", record["hall"], "--stage-root", args.remote_stage_root,
            "--code-root", args.remote_code_root, "--files-per-job", str(args.files_per_job),
            "--ad-selection", args.ad_selection,
        ]
        marker, _ = remote_json(args.alias, args.remote_code_root, prepare_args)
        status, _ = remote_json(args.alias, args.remote_code_root, [
            "_status", "--run-dir", marker["run_dir"], "--stage-root", args.remote_stage_root,
        ])
        attempt = 0
        while status["state"] != "COMPLETE" and attempt < args.max_attempts:
            submit_args = [
                "_submit", "--run-dir", marker["run_dir"], "--stage-root", args.remote_stage_root,
                "--group", args.group, "--walltime", args.walltime,
                "--memory-mb", str(args.memory_mb),
            ]
            if attempt > 0 or status["state"] == "FAILED":
                submit_args.append("--resubmit")
            submission, _ = remote_json(args.alias, args.remote_code_root, submit_args)
            print("submitted cluster {} attempt {}".format(submission["cluster_id"], attempt + 1),
                  flush=True)
            wait_args = [
                "_wait", "--run-dir", marker["run_dir"], "--stage-root", args.remote_stage_root,
                "--timeout", str(args.run_timeout), "--poll-seconds", str(args.poll_seconds),
            ]
            status, _ = remote_json(args.alias, args.remote_code_root, wait_args, check=False)
            attempt += 1
        if status["state"] != "COMPLETE":
            raise RuntimeError("run failed after {} attempts: {}".format(attempt, status))

        result, _ = remote_json(args.alias, args.remote_code_root, [
            "_finalize", "--run-dir", marker["run_dir"], "--stage-root", args.remote_stage_root,
        ])
        local_result = sync_run(args.alias, result, local_root, args.mount_root)
        print("verified local files={} bytes={} path={}".format(
            local_result["files"], local_result["bytes"], local_result["directory"]), flush=True)
        if args.delete_remote_after_sync:
            deleted, _ = remote_json(args.alias, args.remote_code_root, [
                "_cleanup", "--run-dir", marker["run_dir"], "--stage-root", args.remote_stage_root,
                "--catalog-sha256", marker["catalog_sha256"],
            ])
            print("deleted remote staging {}".format(deleted["deleted"]), flush=True)
    return 0


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

    run = commands.add_parser("run", help="process, sync, verify, and optionally clean one run at a time")
    add_connection_options(run)
    run.add_argument("--remote-stage-root", default=DEFAULT_STAGE_ROOT)
    run.add_argument("--local-root", required=True)
    run.add_argument("--mount-root", required=True)
    run.add_argument("--period", action="append", choices=[item[0] for item in CATALOGS])
    run.add_argument("--hall", action="append", choices=("EH1", "EH2", "EH3"))
    run.add_argument("--run-pattern")
    run.add_argument("--limit-runs", type=int)
    run.add_argument("--files-per-job", type=int, default=16)
    run.add_argument("--ad-selection", choices=AD_SELECTION_MODES, default="on",
                     help="on: keep AD entries above 0.7 MeV plus every other detector; "
                          "off: keep every entry (band counts are still recorded)")
    run.add_argument("--group", default="dyw")
    run.add_argument("--walltime", default="short")
    run.add_argument("--memory-mb", type=int, default=1024)
    run.add_argument("--max-attempts", type=int, default=2)
    run.add_argument("--run-timeout", type=int, default=7200)
    run.add_argument("--poll-seconds", type=int, default=15)
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
    verify_everything.set_defaults(function=lambda args: print(json.dumps(verify_all(
        args.local_root, root_verifier=args.root_verifier), sort_keys=True)) or 0)

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
    prepare.add_argument("--ad-selection", choices=AD_SELECTION_MODES, default="on")
    prepare.set_defaults(function=lambda args: print(json.dumps(prepare_run(
        args.catalog, args.period, args.hall, args.stage_root, args.code_root,
        args.files_per_job, ad_selection=args.ad_selection))) or 0)

    submit = commands.add_parser("_submit")
    submit.add_argument("--run-dir", required=True)
    submit.add_argument("--stage-root", required=True)
    submit.add_argument("--group", required=True)
    submit.add_argument("--walltime", required=True)
    submit.add_argument("--memory-mb", type=int, required=True)
    submit.add_argument("--resubmit", action="store_true")
    submit.set_defaults(function=lambda args: print(json.dumps(submit_run(
        args.run_dir, args.stage_root, args.group, args.walltime, args.memory_mb,
        args.resubmit))) or 0)

    status = commands.add_parser("_status")
    status.add_argument("--run-dir", required=True)
    status.add_argument("--stage-root", required=True)
    status.set_defaults(function=lambda args: print(json.dumps(run_status(
        args.run_dir, args.stage_root))) or 0)

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
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        print("ERROR: {}".format(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
