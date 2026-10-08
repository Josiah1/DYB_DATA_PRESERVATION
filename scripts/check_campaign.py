#!/usr/bin/env python3
"""Five-minute local campaign checks; read metadata, never start another controller."""
import json
from pathlib import Path
import subprocess
import sys
import time

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
import preserve

DISK_UUID = "2A4F48F8BDFEE355"


def status_record(progress, service, previous, now, controller=None):
    changed = (progress.get("artifact_bytes") != previous.get("artifact_bytes") or
               service.get("InvocationID") != previous.get("service", {}).get("InvocationID"))
    last_progress = now if changed else previous.get("last_progress_time", now)
    alert = None
    if service.get("ActiveState") == "failed" or (
            not progress["finished"] and service.get("ActiveState") not in ("active", "activating")):
        alert = "Controller is {} (exit {})".format(service.get("ActiveState"), service.get("ExecMainStatus"))
    elif not progress["finished"] and now - last_progress >= 3600:
        alert = "No new verified output for one hour; inspect the controller and jobs"
    if not alert and not progress["finished"] and controller is not None:
        if controller.get("error"):
            alert = "Controller error: " + controller["error"]
        elif now - controller.get("heartbeat_time", 0) > 300:
            alert = "Controller/scheduler heartbeat is more than five minutes old"
        elif controller.get("queue", {}).get("held"):
            alert = "Compression jobs are held by the scheduler"
        elif controller.get("queue", {}).get("outstanding", 0) > controller["max_jobs"]:
            alert = "Compression job cap exceeded"
        elif controller.get("reserved_bytes", 0) > controller["max_staged_bytes"]:
            alert = "Staging reservation budget exceeded; inspect storage"
    return dict(progress, service=service, checked_at=preserve.utc_now(),
                last_progress_time=last_progress, alert=alert, controller=controller, metadata_summary_only=True)


def main():
    state_path = PROJECT / "build/campaign-status.json"
    state_path.parent.mkdir(exist_ok=True)
    previous = preserve.load_json(state_path) if state_path.exists() else {}
    now, archive = time.time(), None
    try:
        mounts = json.loads(subprocess.check_output(
            ["findmnt", "-J", "-S", "UUID=" + DISK_UUID, "-o", "TARGET"], text=True))["filesystems"]
        if len(mounts) != 1:
            raise ValueError("expected exactly one destination disk")
        mount = Path(mounts[0]["target"])
        candidate = mount / "DYB_DATA_PRESERVATION"
        preserve.validate_local_mount(candidate / "data", mount, DISK_UUID)
        archive = candidate
        progress, _ = preserve.archive_progress(archive / "data")
        output = subprocess.check_output(["systemctl", "--user", "show", "dyb-preservation.service",
                                          "-p", "ActiveState", "-p", "SubState", "-p", "ExecMainStatus",
                                          "-p", "MainPID", "-p", "InvocationID"], text=True)
        service = dict(line.split("=", 1) for line in output.splitlines())
        controller = preserve.load_json(archive / "provenance/controller.json")
        report = status_record(progress, service, previous, now, controller)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        report = dict(checked_at=preserve.utc_now(), alert="Campaign check failed: " + str(error))
    log = PROJECT / "build/campaign.log"
    if log.exists():
        with log.open("rb") as stream:
            stream.seek(max(0, log.stat().st_size - 4096))
            report["log_tail"] = stream.read().decode("utf-8", errors="replace")
    report["last_notice_time"] = previous.get("last_notice_time", 0)
    if archive:
        try:
            preserve.atomic_json(archive / "provenance/progress.json", report)
        except OSError as error:
            report['alert'] = 'Cannot save progress on destination disk: ' + str(error)
    preserve.atomic_json(state_path, report)
    alert = report.get("alert")
    notice = (alert != previous.get("alert") or
              (alert and now - report["last_notice_time"] >= 1800) or
              (not report.get("finished") and now - report["last_notice_time"] >= 3600) or
              (report.get("finished") and not previous.get("finished")))
    if notice:
        body = alert or "{} runs verified; {} inputs preserved; {} missing minis skipped; {} inputs pending".format(
            report["completed_runs"], report["processed_inputs"], report["skipped_inputs"], report["pending_inputs"])
        if not alert and report.get("controller"):
            queue = report["controller"].get("queue", {})
            body += "; jobs running={} idle={} / cap={}".format(
                queue.get("running"), queue.get("idle"), report["controller"]["max_jobs"])
        title = "DYB preservation needs attention" if alert else (
            "DYB finished with gaps" if report.get("finished") and not report.get("complete") else "DYB preservation progress")
        try:
            subprocess.run(["notify-send", "--app-name=DYB preservation", "--urgency=" + ("critical" if alert else "normal"),
                            "--expire-time=" + ("0" if alert else "10000"), title, body], check=True, timeout=10)
            report["last_notice_time"] = now
        except (OSError, subprocess.SubprocessError) as error:
            report["notification_error"] = str(error)
        if archive:
            try:
                preserve.atomic_json(archive / "provenance/progress.json", report)
            except OSError as error:
                report["alert"] = "Cannot save progress on destination disk: " + str(error)
        preserve.atomic_json(state_path, report)
    print(json.dumps({key: value for key, value in report.items() if key != "log_tail"}, sort_keys=True))
    return 1 if report.get("alert") else 0


if __name__ == "__main__":
    sys.exit(main())
