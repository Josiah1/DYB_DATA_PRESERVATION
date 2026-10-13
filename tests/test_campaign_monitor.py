import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from scripts import check_campaign
from scripts.check_campaign import status_record


class CampaignMonitorTests(unittest.TestCase):
    def test_startup_recovery_warning_clears_and_never_hides_fatal_errors(self):
        progress = dict(finished=False, complete=False, artifact_bytes=100, pending_inputs=20, skipped_inputs=2)
        service = dict(ActiveState='active', InvocationID='one')
        controller = dict(heartbeat_time=100, max_jobs=300, max_staged_bytes=400, reserved_bytes=100,
                          queue=dict(held=0, outstanding=1), error=None,
                          startup_recovery_pending=[dict(run='P17B/EH1/run71820', state='waiting_for_writers')])
        report = status_record(progress, service, {}, 100, controller)
        self.assertIsNone(report['alert'])
        self.assertIn('Known LCG startup', report['warning']); self.assertIn('waiting_for_writers', report['warning'])
        self.assertEqual((report['pending_inputs'], report['skipped_inputs']), (20, 2))
        for error in ('unknown worker failure', 'source I/O error', 'checksum mismatch', 'quota failure'):
            controller['error'] = error
            self.assertIn(error, status_record(progress, service, {}, 100, controller)['alert'])
        controller.update(error=None, startup_recovery_pending=[])
        self.assertIsNone(status_record(progress, service, {}, 100, controller)['warning'])

    def test_scheduler_unavailability_warns_with_unknown_counts_and_clears_after_recovery(self):
        progress = dict(finished=False, complete=False, artifact_bytes=100, pending_inputs=20, skipped_inputs=2)
        service = dict(ActiveState='active', InvocationID='one')
        controller = dict(heartbeat_time=100, max_jobs=300, max_staged_bytes=400,
                          queue=None, quota=None, reserved_bytes=None, scheduler_status='unavailable',
                          scheduler_error='SECMAN:2007 read failure', scheduler_retry_seconds=60,
                          new_run_admission=False, error=None)
        report = status_record(progress, service, {}, 100, controller)
        self.assertIsNone(report['alert'])
        self.assertIn('queue unknown', report['warning'])
        self.assertIn('automatic retry', report['warning'])
        self.assertNotIn('staging drains', report['warning'])
        self.assertIsNone(report['controller']['queue'])
        self.assertEqual((report['pending_inputs'], report['skipped_inputs'], report['complete']), (20, 2, False))
        self.assertIn('heartbeat', status_record(progress, service, {}, 401, controller)['alert'])
        controller['error'] = 'checksum mismatch'
        self.assertIn('checksum mismatch', status_record(progress, service, {}, 100, controller)['alert'])
        controller.update(error=None, scheduler_status='available', new_run_admission=True,
                          queue=dict(held=0, outstanding=0), reserved_bytes=100)
        self.assertIsNone(status_record(progress, service, {}, 100, controller)['warning'])
        controller['queue'] = None
        self.assertIn('Unknown scheduler state', status_record(progress, service, {}, 100, controller)['alert'])

    def test_overdue_job_warns_without_skipping_inputs_or_hiding_real_errors(self):
        progress = dict(finished=False, complete=False, artifact_bytes=100,
                        pending_inputs=16, skipped_inputs=0)
        service = dict(ActiveState='active', InvocationID='one')
        controller = dict(heartbeat_time=100, max_jobs=300, max_staged_bytes=400,
                          reserved_bytes=300, queue=dict(outstanding=1, held=0),
                          overdue_runs=[dict(run='P17B/EH1/run54964')])
        report = status_record(progress, service, {}, 100, controller)
        self.assertIsNone(report['alert'])
        self.assertIn('Overdue', report['warning'])
        self.assertIn('run54964', report['warning'])
        self.assertEqual((report['pending_inputs'], report['skipped_inputs'], report['complete']),
                         (16, 0, False))
        controller['error'] = 'checksum mismatch'
        self.assertIn('checksum mismatch', status_record(progress, service, {}, 100, controller)['alert'])
        controller['error'] = None
        controller['overdue_runs'] = []
        self.assertIsNone(status_record(progress, service, {}, 100, controller)['warning'])

    def test_notification_failure_is_separate_from_campaign_health(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / 'DYB_DATA_PRESERVATION'
            (archive / 'provenance').mkdir(parents=True)
            (archive / 'provenance/controller.json').write_text(json.dumps(dict(
                heartbeat_time=time.time(), max_jobs=300, max_staged_bytes=400)))
            progress = dict(finished=False, artifact_bytes=100, completed_runs=1,
                            processed_inputs=1, skipped_inputs=5, missing_skipped_inputs=2,
                            broken_skipped_inputs=3, pending_inputs=1)
            for state, exit_status in (('active', 0), ('failed', 1)):
                reads = [json.dumps(dict(filesystems=[dict(target=str(root))])),
                         'ActiveState={}\nExecMainStatus={}\nInvocationID=test\n'.format(state, exit_status)]
                for error in (subprocess.TimeoutExpired('notify-send', 10),
                              subprocess.CalledProcessError(1, 'notify-send')):
                    with self.subTest(state=state, error=type(error).__name__), \
                            mock.patch.object(check_campaign, 'PROJECT', root), \
                            mock.patch('scripts.check_campaign.subprocess.check_output', side_effect=reads), \
                            mock.patch('scripts.check_campaign.subprocess.run', side_effect=error) as notice, \
                            mock.patch('preserve.validate_local_mount'), \
                            mock.patch('preserve.archive_progress', return_value=(progress, [])):
                        self.assertEqual(check_campaign.main(), exit_status)
                    report = json.loads((root / 'build/campaign-status.json').read_text())
                    if exit_status:
                        self.assertIn('failed', report['alert'])
                    else:
                        self.assertIsNone(report['alert'])
                        self.assertIn('2 missing / 3 broken', notice.call_args.args[0][-1])
                    self.assertIn('notify-send', report['notification_error'])
                    self.assertEqual(report['last_notice_time'], 0)
                    self.assertEqual(notice.call_args.kwargs['timeout'], 10)
                    self.assertEqual(report, json.loads((archive / 'provenance/progress.json').read_text()))

    def test_failure_stall_progress_and_finished_gaps_are_distinct(self):
        progress = dict(finished=False, complete=False, artifact_bytes=100)
        service = dict(ActiveState="active", InvocationID="one", ExecMainStatus="0")
        previous = status_record(progress, service, {}, 100)
        self.assertIsNone(previous['alert'])
        self.assertIn('one hour', status_record(progress, service, previous, 3700)['alert'])
        service['ActiveState'] = 'failed'
        self.assertIn('failed', status_record(progress, service, previous, 101)['alert'])
        service['ActiveState'] = 'active'
        progress['artifact_bytes'] = 200
        self.assertIsNone(status_record(progress, service, previous, 3700)['alert'])
        progress['finished'] = True
        service['ActiveState'] = 'inactive'
        finished = status_record(progress, service, previous, 3700)
        self.assertIsNone(finished['alert'])
        self.assertFalse(finished['complete'])
        service['ActiveState'] = 'failed'
        self.assertIn('failed', status_record(progress, service, previous, 3700)['alert'])

    def test_pool_heartbeat_limits_and_errors_are_monitored(self):
        progress = dict(finished=False, artifact_bytes=100)
        service = dict(ActiveState='active', InvocationID='one')
        controller = dict(heartbeat_time=100, max_jobs=300, max_staged_bytes=400,
                          reserved_bytes=300, queue=dict(outstanding=300, running=100, idle=200, held=0))
        self.assertIsNone(status_record(progress, service, {}, 100, controller)['alert'])
        self.assertIn('heartbeat', status_record(progress, service, {}, 401, controller)['alert'])
        controller['queue']['outstanding'] = 301
        self.assertIn('cap exceeded', status_record(progress, service, {}, 100, controller)['alert'])
        controller['queue']['outstanding'] = 300
        controller['queue']['held'] = 1
        self.assertIn('held', status_record(progress, service, {}, 100, controller)['alert'])
        controller['error'] = 'checksum mismatch'
        self.assertIn('checksum mismatch', status_record(progress, service, {}, 100, controller)['alert'])

    def test_known_input_hold_warns_without_hiding_service_or_scheduler_failure(self):
        progress = dict(finished=False, complete=False, artifact_bytes=100, pending_inputs=2, skipped_inputs=0)
        service = dict(ActiveState='active', InvocationID='one')
        controller = dict(heartbeat_time=100, max_jobs=300, max_staged_bytes=400,
                          held_runs=[dict(run='P17B/EH1/run51237')], queue=dict(held=0), new_run_admission=False)
        report = status_record(progress, service, {}, 100, controller)
        self.assertIsNone(report['alert'])
        self.assertIn('run51237', report['warning'])
        self.assertIn('admission paused', report['warning'])
        self.assertEqual((report['pending_inputs'], report['skipped_inputs'], report['complete']), (2, 0, False))
        controller['queue']['held'] = 1
        self.assertIn('held by the scheduler', status_record(progress, service, {}, 100, controller)['alert'])
        service['ActiveState'] = 'failed'
        self.assertIn('failed', status_record(progress, service, {}, 100, controller)['alert'])
