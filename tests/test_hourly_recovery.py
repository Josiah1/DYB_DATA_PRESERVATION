import copy
import fcntl
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts import hourly_recovery as hourly


class FakeOwner:
    def __init__(self):
        self.active = True
        self.entries = []
        self.messages = []
        self.deleted = []
        self.uncertain = False

    def pending(self):
        return list(self.entries)

    def delete(self, entry):
        self.deleted.append(entry["id"])
        self.entries.remove(entry)

    def enqueue(self, message):
        self.messages.append(message)
        self.entries.append(dict(id=str(len(self.messages)), input=[dict(type="text", text=message)]))
        if self.uncertain:
            raise subprocess.TimeoutExpired("codex queue", 30)


class HourlyRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.config = dict(thread_id="existing-owner", monitor_id="test", model="gpt-6.1-sol",
                           reasoning_effort="max", cwd=str(hourly.PROJECT))
        self.report = dict(checked_at=hourly.preserve.utc_now(), finished=False, complete=False,
                           completed_runs=873, expected_runs=4009, processed_inputs=207776,
                           missing_skipped_inputs=1014, broken_skipped_inputs=1730,
                           alert="Controller is failed (exit 1)", warning=None,
                           service=dict(ActiveState="failed", MainPID="0"), controller=dict(queue=None))
        self.owner = FakeOwner()
        self.state = {}
        self.saved = []

    def dispatch(self):
        return hourly.dispatch(self.report, self.config, self.state,
                               lambda value: self.saved.append(copy.deepcopy(value)), lambda _: self.owner)

    def test_stopped_case_queues_once_without_any_campaign_mutation_and_deduplicates(self):
        self.assertEqual(self.dispatch(), "requested")
        self.assertEqual(self.saved[0]["request"]["phase"], "submitting")
        self.assertEqual(self.state["request"]["phase"], "accepted")
        self.assertEqual(self.dispatch(), "already_queued")
        self.assertEqual(len(self.owner.messages), 1)
        self.assertIn("Never turn infrastructure/unknown errors", self.owner.messages[0])
        self.assertIn("THIS existing conversation", self.owner.messages[0])
        human = dict(id="human", input=[dict(type="text", text="unrelated human request")])
        duplicate = copy.deepcopy(self.owner.entries[0]); duplicate["id"] = "duplicate"
        self.owner.entries.extend([human, duplicate])
        self.assertEqual(self.dispatch(), "already_queued")
        self.assertEqual(self.owner.deleted, ["duplicate"])
        self.assertIn(human, self.owner.entries)
        self.assertEqual(len(self.owner.messages), 1)

    def test_consumed_request_defers_through_active_work_then_allows_idle_recheck(self):
        self.dispatch()
        self.owner.entries.clear()
        for _ in range(3):
            self.assertEqual(self.dispatch(), "owner_active")
        self.assertEqual(len(self.owner.messages), 1)
        self.owner.active = False
        self.assertEqual(self.dispatch(), "requested")
        self.assertEqual(len(self.owner.messages), 2)
        self.assertEqual(len(self.owner.entries), 1)

    def test_uncertain_delivery_is_journaled_before_call_and_not_replayed(self):
        self.owner.uncertain = True
        with self.assertRaises(subprocess.TimeoutExpired):
            self.dispatch()
        self.assertEqual(self.saved[0]["request"]["phase"], "submitting")
        self.assertEqual(self.dispatch(), "already_queued")
        self.owner.entries.clear()
        self.assertEqual(self.dispatch(), "owner_active")
        self.assertEqual(len(self.owner.messages), 1)

    def test_healthy_snapshot_during_recovery_does_not_forget_the_active_request(self):
        self.dispatch()
        self.owner.entries.clear()
        self.report["alert"] = None
        self.assertEqual(self.dispatch(), "healthy_owner_active")
        self.report["alert"] = "new failure during the same recovery"
        self.assertEqual(self.dispatch(), "owner_active")
        self.assertEqual(len(self.owner.messages), 1)

    def test_healthy_is_cheap_and_recovery_cancels_only_our_queued_request(self):
        self.report["alert"] = None
        with mock.patch.object(hourly, "NativeOwner", side_effect=AssertionError("healthy must be cheap")):
            self.assertEqual(hourly.dispatch(self.report, self.config, {}, lambda _: None), "healthy")
        self.report["alert"] = "failed"
        self.dispatch()
        human = dict(id="human", input=[dict(type="text", text="keep me")])
        self.owner.entries.append(human)
        self.report["alert"] = None
        self.assertEqual(self.dispatch(), "healthy_cancelled_pending")
        self.assertEqual(self.owner.entries, [human])
        self.assertIsNone(self.state["request"])

    def test_degraded_unknown_scheduler_warns_and_gaps_do_not_prevent_finished_noop(self):
        self.report.update(alert=None, warning="Scheduler query unavailable; queue unknown")
        self.assertEqual(self.dispatch(), "requested")
        self.assertIsNone(hourly.summary(self.report)["controller"]["queue"])
        self.report.update(finished=True, complete=False)
        self.assertEqual(self.dispatch(), "finished_noop")
        self.assertTrue(self.state["completed"])
        self.assertEqual(self.owner.entries, [])

    def test_finished_is_persisted_even_if_old_owner_is_unavailable(self):
        self.report.update(finished=True, complete=False)
        self.state["request"] = dict(phase="accepted")
        with self.assertRaisesRegex(RuntimeError, "absent"):
            hourly.dispatch(self.report, self.config, self.state,
                            lambda value: self.saved.append(copy.deepcopy(value)),
                            mock.Mock(side_effect=RuntimeError("owner absent")))
        self.assertTrue(self.saved[0]["completed"])

    def test_immediate_check_overlap_private_bounded_state_and_cached_completion(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            hourly.save_private(root / "config.json", self.config)
            with mock.patch.object(hourly, "refresh_report", return_value=self.report) as refresh, \
                    mock.patch.object(hourly, "NativeOwner", return_value=self.owner):
                self.assertEqual(hourly.run_once(root)["action"], "requested")
                self.assertEqual(hourly.run_once(root)["action"], "already_queued")
                with (root / "check.lock").open("a") as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.assertEqual(hourly.run_once(root)["action"], "overlap_deferred")
                self.assertEqual(refresh.call_count, 2)
                self.assertEqual(len(self.owner.messages), 1)
                for name in ("config.json", "state.json", "status.json"):
                    self.assertEqual((root / name).stat().st_mode & 0o777, 0o600)
                self.assertEqual(root.stat().st_mode & 0o777, 0o700)
                self.assertLess((root / "status.json").stat().st_size, 6000)
                self.report.update(finished=True, complete=False)
                self.assertEqual(hourly.run_once(root)["action"], "finished_noop")
                self.assertEqual(hourly.run_once(root)["action"], "finished_noop")
                self.assertEqual(refresh.call_count, 3)

    def test_daemon_absence_records_visible_blocker_without_new_owner_or_job(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            hourly.save_private(root / "config.json", self.config)
            with mock.patch.object(hourly, "refresh_report", return_value=self.report), \
                    mock.patch.object(hourly, "NativeOwner", side_effect=RuntimeError("existing owner unavailable")):
                status = hourly.run_once(root)
            self.assertEqual(status["action"], "blocked")
            self.assertIn("existing owner unavailable", status["error"])
            self.assertFalse((root / "state.json").exists())

    def test_native_binding_resolves_endpoint_each_time_and_never_changes_model(self):
        thread = dict(id=self.config["thread_id"], cwd=str(hourly.PROJECT), status=dict(type="active"), modelProvider="openai")
        versions = [json.dumps(dict(status="running", socketPath=value)) for value in ("/first.sock", "/next.sock")]
        with mock.patch.object(hourly.subprocess, "check_output", side_effect=versions) as daemon, \
                mock.patch.object(hourly.NativeOwner, "rpc", return_value=dict(thread=thread)), \
                mock.patch.object(hourly.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as queue:
            first, second = hourly.NativeOwner(self.config), hourly.NativeOwner(self.config)
            self.assertEqual((first.socket, second.socket), ("/first.sock", "/next.sock"))
            second.enqueue("one authorized request")
        argv = queue.call_args.args[0]
        self.assertEqual(argv[1], "queue")
        self.assertIn("unix:///next.sock", argv)
        self.assertEqual(argv[argv.index("--thread") + 1], "existing-owner")
        self.assertEqual(argv[argv.index("--model") + 1], "gpt-6.1-sol")
        self.assertIn('model_reasoning_effort="max"', argv)
        self.assertEqual(daemon.call_count, 2)
        for call in daemon.call_args_list:
            self.assertEqual(call.args[0][1:], ["app-server", "daemon", "version"])

    def test_unloaded_wrong_owner_model_or_incomplete_queue_is_never_replaced(self):
        with mock.patch.object(hourly.subprocess, "check_output", return_value=json.dumps(dict(status="stopped"))):
            with self.assertRaisesRegex(RuntimeError, "no replacement"):
                hourly.NativeOwner(self.config)
        for thread in (
                dict(id="different", cwd=str(hourly.PROJECT), status=dict(type="active"), modelProvider="openai"),
                dict(id="existing-owner", cwd="/tmp", status=dict(type="active"), modelProvider="openai"),
                dict(id="existing-owner", cwd=str(hourly.PROJECT), status=dict(type="notLoaded"), modelProvider="openai")):
            with mock.patch.object(hourly.subprocess, "check_output", return_value=json.dumps(dict(status="running", socketPath="/current.sock"))), \
                    mock.patch.object(hourly.NativeOwner, "rpc", return_value=dict(thread=thread)):
                with self.assertRaisesRegex(RuntimeError, "no new writer"):
                    hourly.NativeOwner(self.config)
        with self.assertRaisesRegex(ValueError, "gpt-6.1-sol/max"):
            hourly.NativeOwner(dict(self.config, model="fallback"))
        owner = object.__new__(hourly.NativeOwner)
        with mock.patch.object(owner, "rpc", return_value=dict(data=[], nextCursor="more")):
            with self.assertRaisesRegex(RuntimeError, "deduplication"):
                owner.pending()

    def test_fresh_monitor_required_and_mount_storage_failure_requests_diagnosis(self):
        mount = dict(target="/portable", source="/dev/test", uuid="2A4F48F8BDFEE355", fstype="fuseblk", options="rw,nosuid")
        for code, alert, stale, accepted in ((0, None, False, True), (1, "failed", False, True),
                                             (1, None, False, False), (0, None, True, False)):
            report = dict(self.report, alert=alert)
            if stale:
                report["checked_at"] = "2000-01-01T00:00:00+00:00"
            with self.subTest(code=code, alert=alert, stale=stale), \
                    mock.patch.object(hourly.subprocess, "run", return_value=subprocess.CompletedProcess([], code)), \
                    mock.patch.object(hourly.preserve, "load_json", return_value=report), \
                    mock.patch.object(hourly.subprocess, "check_output", return_value=json.dumps(dict(filesystems=[mount]))), \
                    mock.patch.object(hourly.shutil, "disk_usage", return_value=mock.Mock(free=1000)):
                if accepted:
                    value = hourly.refresh_report()
                    self.assertEqual(value["storage"]["destination"]["uuid"], mount["uuid"])
                    self.assertEqual(value["storage"]["archive_free_bytes"], 1000)
                else:
                    with self.assertRaisesRegex(RuntimeError, "fresh usable"):
                        hourly.refresh_report()
        for changes in (dict(uuid="wrong"), dict(fstype="ntfs3"), dict(options="ro")):
            with self.subTest(changes=changes), \
                    mock.patch.object(hourly.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), \
                    mock.patch.object(hourly.preserve, "load_json", return_value=dict(self.report, alert=None)), \
                    mock.patch.object(hourly.subprocess, "check_output", return_value=json.dumps(dict(filesystems=[dict(mount, **changes)]))), \
                    mock.patch.object(hourly.shutil, "disk_usage", return_value=mock.Mock(free=1000)):
                value = hourly.refresh_report()
                self.assertIn("guard failed", value["alert"])
                self.assertNotIn("archive_free_bytes", value["storage"])


if __name__ == "__main__":
    unittest.main()
