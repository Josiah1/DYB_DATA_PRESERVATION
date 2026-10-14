#!/usr/bin/env python3
"""Hourly metadata check; request recovery from the existing Codex owner, never restart jobs here."""
import fcntl
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
import preserve

PRIVATE = PROJECT / "build/hourly-recovery"
CODEX = Path.home() / ".local/bin/codex"
RPC_HELPER = Path.home() / ".agents/skills/ssh-ops/scripts/codex-rpc.py"


def save_private(path, value):
    preserve.atomic_json(path, value)
    path.chmod(0o600)


class NativeOwner:
    def __init__(self, config):
        self.thread_id = config["thread_id"]
        if config.get("model") != "gpt-6.1-sol" or config.get("reasoning_effort") != "max":
            raise ValueError("hourly recovery requires the authorized gpt-6.1-sol/max binding")
        version = json.loads(subprocess.check_output(
            [str(CODEX), "app-server", "daemon", "version"], text=True, timeout=15))
        if version.get("status") != "running":
            raise RuntimeError("existing Codex daemon unavailable; no replacement started")
        self.socket = version["socketPath"]  # Resolve every invocation; never persist the endpoint.
        thread = self.rpc("thread/read", includeTurns=False)["thread"]
        if (thread["id"] != self.thread_id or Path(thread["cwd"]).resolve() != PROJECT or
                thread["status"]["type"] not in ("active", "idle") or thread["modelProvider"] != "openai"):
            raise RuntimeError("authorized conversation is not loaded in this backend/project; no new writer started")
        self.active = thread["status"]["type"] == "active"

    def rpc(self, method, **params):
        response = subprocess.run([sys.executable, str(RPC_HELPER), "--socket", self.socket],
            input=json.dumps(dict(method=method, params=dict(threadId=self.thread_id, **params))),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20)
        if response.returncode:
            raise RuntimeError("native conversation RPC failed: " + response.stderr[-1200:])
        value = json.loads(response.stdout)
        if "error" in value:
            raise RuntimeError("native conversation RPC rejected: " + str(value["error"])[:1200])
        return value["result"]

    def pending(self):
        queue = self.rpc("thread/queue/list")
        if queue.get("nextCursor"):
            raise RuntimeError("queue pagination prevents a complete deduplication check; nothing enqueued")
        return queue["data"]

    def delete(self, entry):
        self.rpc("thread/queue/delete", queuedSubmissionId=entry["id"])

    def enqueue(self, message):
        result = subprocess.run([str(CODEX), "queue", "--remote", "unix://" + self.socket,
            "--thread", self.thread_id, "--model", "gpt-6.1-sol", "-c", 'model_reasoning_effort="max"',
            "--message", message], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError("native queue failed or delivery uncertain; inspect before any replay: " + result.stderr[-1200:])


def refresh_report():
    result = subprocess.run(["systemctl", "--user", "start", "dyb-preservation-check.service"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=130)
    report = preserve.load_json(PROJECT / "build/campaign-status.json")
    observed = preserve.dt.datetime.fromisoformat(report["checked_at"]).timestamp()
    if abs(time.time() - observed) > 180 or (result.returncode and not report.get("alert")):
        raise RuntimeError("five-minute monitor did not produce a fresh usable status")
    report["storage"] = dict(internal_free_bytes=shutil.disk_usage(PROJECT).free)
    try:
        mounts = json.loads(subprocess.check_output([
            "findmnt", "-J", "-S", "UUID=2A4F48F8BDFEE355", "-o", "TARGET,SOURCE,UUID,FSTYPE,OPTIONS"
        ], text=True, timeout=15))["filesystems"]
        if len(mounts) != 1:
            raise ValueError("expected exactly one archive mount")
        mount = mounts[0]
        if (mount["uuid"] != "2A4F48F8BDFEE355" or mount["fstype"] == "ntfs3" or
                "rw" not in mount["options"].split(",")):
            raise ValueError("archive UUID/driver/read-write guard failed")
        report["storage"].update(destination=mount, archive_free_bytes=shutil.disk_usage(mount["target"]).free)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        report["alert"] = "Hourly mount/storage check failed: " + str(error)
    return report


def summary(report):
    result = {key: report.get(key) for key in (
        "checked_at", "finished", "complete", "completed_runs", "expected_runs", "processed_inputs",
        "missing_skipped_inputs", "broken_skipped_inputs", "pending_inputs", "artifact_bytes", "alert", "warning", "storage")}
    result["service"] = {key: report.get("service", {}).get(key) for key in ("ActiveState", "MainPID", "ExecMainStatus")}
    controller = report.get("controller") or {}
    result["controller"] = {key: controller.get(key) for key in (
        "checked_at", "heartbeat_time", "scheduler_status", "last_scheduler_success_at", "queue", "quota",
        "reserved_bytes", "max_staged_bytes", "max_jobs", "transferring", "backpressure", "error")}
    return result


def dispatch(report, config, state, persist, owner_factory=None):
    if report.get("finished"):
        state["completed"] = True
        persist(state)
    needs_recovery = bool(report.get("alert") or report.get("warning")) and not report.get("finished")
    if not needs_recovery and not state.get("request"):
        state["completed"] = bool(report.get("finished"))
        return "finished_noop" if state["completed"] else "healthy"
    owner = (owner_factory or NativeOwner)(config)
    marker = "[DYB hourly recovery {}]".format(config["monitor_id"])
    own = [entry for entry in owner.pending() if any(
        item.get("type") == "text" and item.get("text", "").startswith(marker) for item in entry["input"])]
    if not needs_recovery:
        for entry in own:
            owner.delete(entry)
        if state.get("request") and owner.active and not own:
            return "finished_noop" if state.get("completed") else "healthy_owner_active"
        state.update(request=None, completed=bool(report.get("finished")))
        return "finished_noop" if state["completed"] else "healthy_cancelled_pending"
    if own:
        for duplicate in own[1:]:
            owner.delete(duplicate)
        state["request"] = dict(state.get("request") or {}, phase="queued", id=own[0]["id"])
        return "already_queued"
    request = state.get("request")
    if request and owner.active:
        # A consumed or uncertain request must not accumulate behind its active recovery turn.
        return "owner_active"
    snapshot = summary(report)
    message = (marker + " The user authorized hourly DYB checks and safe recovery through THIS existing "
        "conversation using gpt-6.1-sol/max only. Inspect fresh mount UUID 2A4F48F8BDFEE355, service/controller "
        "lock, global scheduler/writers/intents and exact original failures before recovering the single "
        "campaign. Preserve verified outputs, partial transfers, fixed catalogs and failure evidence. "
        "Keep mini-only inputs, AD >0.6 MeV/all non-AD retention, 300 single-core jobs/400 GiB staging, "
        "quota guards and verification before cleanup. Never turn infrastructure/unknown errors into "
        "missing/broken skips, replay live/uncertain submissions, or restart blindly. No GPU/reboot, "
        "dependencies, unrelated changes, converter-share deployment, new conversation or fallback model. "
        "Prove fresh heartbeat/queue and real transfer/worker progress or report a concrete safe blocker. "
        "Do not create another schedule or queue recursive prompts. If the campaign is now finished, "
        "leave this hourly checker as a no-op. Metadata snapshot: " + json.dumps(snapshot, sort_keys=True))
    state["request"] = dict(phase="submitting", requested_at=preserve.utc_now())
    persist(state)  # A timeout may follow an accepted mutation; never immediately replay it.
    owner.enqueue(message)
    state["request"]["phase"] = "accepted"
    return "requested"


def run_once(directory=PRIVATE):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    with (directory / "check.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return dict(checked_at=preserve.utc_now(), action="overlap_deferred")
        state_path, status_path = directory / "state.json", directory / "status.json"
        state = preserve.load_json(state_path) if state_path.exists() else {}
        status = dict(checked_at=preserve.utc_now())
        try:
            if state.get("completed"):
                status["action"] = "finished_noop"
            else:
                report = refresh_report()
                status["campaign"] = summary(report)
                config = preserve.load_json(directory / "config.json")
                status["action"] = dispatch(report, config, state, lambda value: save_private(state_path, value))
            save_private(state_path, state)
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
            status.update(action="blocked", error=str(error)[:2000])
        status["checked_at"] = preserve.utc_now()
        save_private(status_path, status)
        return status


if __name__ == "__main__":
    result = run_once()
    print(json.dumps({key: value for key, value in result.items() if key != "campaign"}, sort_keys=True))
    sys.exit(1 if result["action"] == "blocked" else 0)
