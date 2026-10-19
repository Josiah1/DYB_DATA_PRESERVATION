import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock

import preserve


class PipelineTests(unittest.TestCase):
    def test_bare_ad_fetch_failure_preserves_submission_state_before_mutation(self):
        diagnostic = ('\n-- Failed to fetch ads from: <192.168.51.62:9618?'
                      'alias=schedd06.ihep.ac.cn> : schedd06.ihep.ac.cn\n\n')
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary)
            run = stage / 'run'
            (run / 'status').mkdir(parents=True)
            (run / 'status/000000.ok').write_text('verified good chunk')
            (run / 'submissions.json').write_text('[]')
            marker = dict(chunk_count=2)
            for returncode in (0, 1):
                for intent_present in (False, True):
                    intent = run / 'submission-intent.json'
                    if intent_present:
                        intent.write_text('{"unknown_submission":true}')
                    before = {str(p.relative_to(run)): p.read_bytes()
                              for p in run.rglob('*') if p.is_file()}
                    response = mock.Mock(returncode=returncode, stdout='', stderr=diagnostic)
                    with self.subTest(returncode=returncode, intent=intent_present), \
                            mock.patch('preserve.subprocess.run', return_value=response) as backend:
                        with self.assertRaises(preserve.SchedulerQueryUnavailable):
                            preserve.submit_locked(run, marker, str(stage), 'dyw', 'short', 1024, True, 300)
                        self.assertEqual(backend.call_count, 1)
                        self.assertEqual(backend.call_args.args[0][0], 'condor_q')
                    self.assertEqual({str(p.relative_to(run)): p.read_bytes()
                                      for p in run.rglob('*') if p.is_file()}, before)
                    if intent.exists():
                        intent.unlink()
        for stderr in ('', diagnostic + 'Permission denied opening quota data\n',
                       diagnostic + 'Input/output error\n', diagnostic + 'unrecognized failure\n'):
            response = mock.Mock(returncode=1, stdout='', stderr=stderr)
            with self.subTest(stderr=stderr), mock.patch('preserve.subprocess.run', return_value=response):
                with self.assertRaises(RuntimeError) as error:
                    preserve.scheduler_jobs('/stage')
                self.assertNotIsInstance(error.exception, preserve.SchedulerQueryUnavailable)

    def test_only_known_read_only_query_failures_are_retryable(self):
        message = 'SECMAN:2007:Read failure during security negotiation.'
        failure = mock.Mock(returncode=0, stdout='[{"partial": true}]', stderr=(
            '-- Failed to fetch ads from: schedd06\n' + message))
        with mock.patch('preserve.subprocess.run', return_value=failure):
            with self.assertRaises(preserve.SchedulerQueryUnavailable):
                preserve.scheduler_jobs('/stage')
        failure.stderr += '\nPermission denied opening quota data'
        with mock.patch('preserve.subprocess.run', return_value=failure):
            with self.assertRaises(RuntimeError) as error:
                preserve.scheduler_jobs('/stage')
            self.assertNotIsInstance(error.exception, preserve.SchedulerQueryUnavailable)
        for error in (OSError(5, 'Input/output error'), preserve.subprocess.TimeoutExpired('condor_q', 60)):
            with self.subTest(error=type(error).__name__), mock.patch('preserve.subprocess.run', side_effect=error):
                with self.assertRaises(preserve.SchedulerQueryUnavailable if isinstance(error, preserve.subprocess.TimeoutExpired)
                                       else OSError):
                    preserve.scheduler_jobs('/stage')
        for command, helper, extra in (
                ('_pool', 'pool_status', []),
                ('_submit', 'submit_run', ['--run-dir', '/stage/run', '--group', 'dyw',
                                         '--walltime', 'short', '--memory-mb', '1024']),
                ('_skip_broken', 'skip_broken_minis', ['--run-dir', '/stage/run'])):
            proof = dict(scheduler_unavailable=message, command=command, phase=preserve.SCHEDULER_QUERY_PHASES[command])
            with mock.patch('preserve.' + helper, side_effect=preserve.SchedulerQueryUnavailable(message)), \
                    mock.patch('builtins.print') as output:
                self.assertEqual(preserve.main([command, '--stage-root', '/stage'] + extra), 75)
            self.assertEqual(json.loads(output.call_args.args[0]), proof)
            response = mock.Mock(returncode=75, stdout=json.dumps(proof), stderr='')
            with mock.patch('preserve.remote_call', return_value=response) as call, mock.patch('preserve.time.sleep'):
                with self.assertRaises(preserve.SchedulerQueryUnavailable):
                    preserve.remote_json('host', '/code', [command])
            self.assertEqual(call.call_count, 3 if command == '_pool' else 1)
        proof = dict(scheduler_unavailable=message, command='_pool', phase='read_only')
        responses = [mock.Mock(returncode=75, stdout=json.dumps(proof), stderr=''),
                     mock.Mock(returncode=255, stdout='', stderr='ssh: Connection reset by peer'),
                     mock.Mock(returncode=255, stdout='', stderr='Connection closed by 198.18.0.18 port 22\n'),
                     mock.Mock(returncode=255, stdout='', stderr='Connection closed by 2001:db8::1 port 2222')]
        for response in responses:
            with mock.patch('preserve.remote_call', return_value=response) as call, mock.patch('preserve.time.sleep'):
                with self.assertRaises(preserve.SchedulerQueryUnavailable):
                    preserve.remote_json('host', '/code', ['_pool'])
            self.assertEqual(call.call_count, 3)
        for response in (mock.Mock(returncode=1, stdout='', stderr='quota Input/output error'),
                         mock.Mock(returncode=255, stdout='', stderr='Permission denied (publickey)'),
                         mock.Mock(returncode=255, stdout='', stderr='Permission denied\nConnection reset by peer'),
                         mock.Mock(returncode=255, stdout='{}', stderr='Connection closed by 198.18.0.18 port 22'),
                         mock.Mock(returncode=255, stdout='', stderr='Permission denied: Connection closed by host port 22'),
                         mock.Mock(returncode=255, stdout='', stderr='Input/output error\nConnection closed by host port 22'),
                         mock.Mock(returncode=255, stdout='', stderr='Connection closed by host port unknown'),
                         mock.Mock(returncode=75, stdout='{"scheduler_unavailable": 1}', stderr='')):
            with mock.patch('preserve.remote_call', return_value=response), mock.patch('preserve.time.sleep'):
                with self.assertRaises(RuntimeError) as error:
                    preserve.remote_json('host', '/code', ['_pool'])
            self.assertNotIsInstance(error.exception, preserve.SchedulerQueryUnavailable)
        for command in ('_submit', '_skip_broken'):
            for response in (mock.Mock(returncode=255, stdout='', stderr='ssh: Connection reset by peer'),
                             mock.Mock(returncode=255, stdout='', stderr='Connection closed by 198.18.0.18 port 22'),
                             mock.Mock(returncode=75, stdout=json.dumps(proof), stderr='')):
                with mock.patch('preserve.remote_call', return_value=response) as call, mock.patch('preserve.time.sleep'):
                    with self.assertRaises(RuntimeError) as error:
                        preserve.remote_json('host', '/code', [command])
                self.assertNotIsInstance(error.exception, preserve.SchedulerQueryUnavailable)
                self.assertEqual(call.call_count, 1, 'SSH loss or wrong-phase proof never authorizes mutation replay')

    def test_scheduler_proof_accepts_only_the_observed_login_warning(self):
        warning = '/dybfs2/users/lijj16/Libs/root-6.32.08-install/bin/thisroot.csh: No such file or directory.\n'
        for command in ('_pool', '_submit', '_skip_broken'):
            proof = dict(scheduler_unavailable='SECMAN:2007:Read failure during security negotiation.',
                         command=command, phase=preserve.SCHEDULER_QUERY_PHASES[command])
            for count in (0, 1, 2):
                response = mock.Mock(returncode=75, stdout=json.dumps(proof), stderr=warning * count)
                with self.subTest(command=command, warnings=count), \
                        mock.patch('preserve.remote_call', return_value=response) as call, \
                        mock.patch('preserve.time.sleep'):
                    with self.assertRaises(preserve.SchedulerQueryUnavailable) as error:
                        preserve.remote_json('host', '/code', [command])
                    if count:
                        self.assertIn(warning, str(error.exception), 'startup diagnostics remain visible')
                self.assertEqual(call.call_count, 3 if command == '_pool' else 1)
            invalid = [(75, proof, stderr) for stderr in (
                warning * 3, warning + 'Input/output error\n', 'Permission denied\n' + warning,
                warning.replace('6.32.08', '6.32.09'), 'ERROR: ' + warning)]
            invalid += [(75, dict(proof, phase='after_mutation'), warning),
                        (75, dict(proof, command='_cleanup'), warning),
                        (75, dict(proof, unexpected=True), warning),
                        (75, dict(proof, scheduler_unavailable=''), warning),
                        (1, proof, warning), (255, proof, warning)]
            for status, value, stderr in invalid:
                response = mock.Mock(returncode=status, stdout=json.dumps(value), stderr=stderr)
                with self.subTest(command=command, status=status, proof=value, stderr=stderr), \
                        mock.patch('preserve.remote_call', return_value=response) as call, \
                        mock.patch('preserve.time.sleep'):
                    with self.assertRaises(RuntimeError) as error:
                        preserve.remote_json('host', '/code', [command])
                    self.assertNotIsInstance(error.exception, preserve.SchedulerQueryUnavailable)
                self.assertEqual(call.call_count, 3 if command == '_pool' else 1)
            response = mock.Mock(returncode=75, stdout='unexpected log\n' + json.dumps(proof), stderr=warning)
            with mock.patch('preserve.remote_call', return_value=response), mock.patch('preserve.time.sleep'):
                with self.assertRaises(json.JSONDecodeError):
                    preserve.remote_json('host', '/code', [command])

    def test_pre_mutation_scheduler_failure_preserves_intents_good_chunks_and_broken_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary)
            run = stage / 'run'
            (run / 'status').mkdir(parents=True)
            (run / 'outputs').mkdir()
            (run / 'outputs/good.root').write_bytes(b'good')
            (run / 'status/000000.ok').write_text('verified')
            (run / 'status/000001.fail').write_text('exact content evidence')
            marker = dict(chunk_count=2, input_source='mini', skip_missing_minis=True)
            for intent_present in (False, True):
                intent = run / 'submission-intent.json'
                if intent_present:
                    intent.write_text('{"unknown_submission":true}')
                before = {str(p.relative_to(run)):p.read_bytes() for p in run.rglob('*') if p.is_file()}
                with mock.patch('preserve.scheduler_jobs', side_effect=preserve.SchedulerQueryUnavailable('read failure')), \
                        mock.patch('preserve.subprocess.run') as backend:
                    with self.assertRaises(preserve.SchedulerQueryUnavailable):
                        preserve.submit_locked(run, marker, str(stage), 'dyw', 'short', 1024, True, 300)
                backend.assert_not_called()
                self.assertEqual({str(p.relative_to(run)):p.read_bytes() for p in run.rglob('*') if p.is_file()}, before)
                if intent.exists():
                    intent.unlink()
            before = {str(p.relative_to(run)):p.read_bytes() for p in run.rglob('*') if p.is_file()}
            with mock.patch('preserve.load_run', return_value=(run, marker)), \
                    mock.patch('preserve.scheduler_jobs', side_effect=preserve.SchedulerQueryUnavailable('read failure')), \
                    mock.patch('preserve.atomic_json') as write, mock.patch('preserve.confirmed_empty_mini_errors') as proof:
                with self.assertRaises(preserve.SchedulerQueryUnavailable):
                    preserve.skip_broken_minis(str(run), str(stage))
            write.assert_not_called()
            proof.assert_not_called()
            self.assertEqual({str(p.relative_to(run)):p.read_bytes() for p in run.rglob('*') if p.is_file()}, before)

    def test_pre_mutation_helper_failure_defers_to_fresh_pool_before_resuming(self):
        record = preserve.inventory_records()[0]
        for failing_command in ('_submit', '_skip_broken'):
            with self.subTest(command=failing_command), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                local = root / 'data'
                local.mkdir()
                (root / 'provenance').mkdir()
                extra = ['--skip-broken-minis'] if failing_command == '_skip_broken' else []
                args = preserve.build_parser().parse_args([
                    'run', '--local-root', str(local), '--mount-root', str(root),
                    '--remote-stage-root', '/stage', '--remote-code-root', '/code',
                    '--files-per-job', '2000', '--skip-missing-minis', '--delete-remote-after-sync'] + extra)
                marker = dict(record, run_dir='/stage/run', code_root='/code', chunk_count=1, files_per_job=2000,
                              selection=preserve.selection_record('off'), input_source='mini', skip_missing_minis=True,
                              processed_input_count=record['input_count'], missing_skipped_input_count=0, broken_skipped_input_count=0)
                run = dict(marker=marker, submissions=[], intent=False,
                           status=dict(state='FAILED' if extra else 'PENDING', failed=int(bool(extra)), running=0))
                original = copy.deepcopy(run)
                queries, failure_at, calls, verified, cleaned = [0], [None], [], set(), []
                read_command = preserve.remote_json

                def remote(alias, code, arguments):
                    command = arguments[0]
                    if command == '_pool':
                        queries[0] += 1
                        return copy.deepcopy(dict(runs=[] if cleaned else [run], jobs=[], queue=preserve.queue_counts([]),
                            reserved_bytes=1, output_bytes=1,
                            quota=dict(free_bytes=500 * preserve.GIB, used_files=1, limit_files=200000))), 0
                    calls.append(command)
                    if command == failing_command and failure_at[0] is None:
                        failure_at[0] = queries[0]
                        proof = dict(scheduler_unavailable='-- Failed to fetch ads from: schedd06.ihep.ac.cn',
                                     command=command, phase='before_mutation')
                        warning = '/dybfs2/users/lijj16/Libs/root-6.32.08-install/bin/thisroot.csh: No such file or directory.\n'
                        response = mock.Mock(returncode=75, stdout=json.dumps(proof), stderr=warning * 2)
                        with mock.patch('preserve.remote_call', return_value=response) as connection:
                            try:
                                return read_command(alias, code, arguments)
                            finally:
                                self.assertEqual(connection.call_count, 1, 'no immediate mutable helper replay')
                    if command in ('_skip_broken', '_submit'):
                        self.assertGreater(queries[0], failure_at[0], 'must refresh global writer state before retry')
                    if command == '_skip_broken':
                        run['status'].update(state='PENDING', failed=0)
                        return copy.deepcopy(run), 0
                    if command == '_submit':
                        run['status']['state'] = 'COMPLETE'
                        run['submissions'] = [dict(cluster_id=42, submitted_at=preserve.utc_now())]
                        return dict(cluster_id=42), 0
                    if command == '_finalize':
                        return marker, 0
                    self.assertEqual(command, '_cleanup')
                    self.assertIn('run', verified)
                    cleaned.append('run')
                    return dict(deleted='/stage/run'), 0

                def sleep(seconds):
                    if seconds == 60:
                        report = preserve.load_json(root / 'provenance/controller.json')
                        self.assertIsNone(report['queue'])
                        self.assertEqual(report['scheduler_status'], 'unavailable')
                        self.assertFalse(report['new_run_admission'])
                        self.assertIn('thisroot.csh: No such file or directory.', report['scheduler_error'])
                        self.assertIn('-- Failed to fetch ads from:', report['scheduler_error'])
                        self.assertEqual(run, original)
                        self.assertEqual(calls, [failing_command])

                def sync(*unused):
                    verified.add('run')
                    return dict(files=1, bytes=1, directory='/local/run')

                with mock.patch('preserve.remote_json', side_effect=remote), \
                        mock.patch('preserve.archive_progress', return_value=({}, [])), \
                        mock.patch('preserve.recorded_inputs'), mock.patch('preserve.refresh_input_issues'), \
                        mock.patch('preserve.validate_local_mount'), mock.patch('preserve.sync_run', side_effect=sync), \
                        mock.patch('preserve.time.sleep', side_effect=sleep):
                    preserve.run_pipeline(args, [record], {}, local)
                self.assertEqual(calls.count(failing_command), 2)
                self.assertEqual(cleaned, ['run'])

    def test_scheduler_outage_pauses_mutations_and_completed_transfer_cleanup_until_fresh_query(self):
        records = preserve.inventory_records()[:2]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            local = root / 'data'
            local.mkdir()
            (root / 'provenance').mkdir()
            args = preserve.build_parser().parse_args([
                'run', '--local-root', str(local), '--mount-root', str(root),
                '--remote-stage-root', '/stage', '--remote-code-root', '/code',
                '--files-per-job', '2000', '--skip-missing-minis', '--delete-remote-after-sync'])
            args.poll_seconds = 0.001
            staged, verified, cleaned, submitted = {}, set(), [], []
            copying, release_copy, published = threading.Event(), threading.Event(), threading.Event()
            queries, degraded = [0], []
            read_snapshot = preserve.remote_json

            def prepare(record, state):
                marker = dict(record, run_dir='/stage/' + record['stem'], code_root='/code',
                    chunk_count=1, files_per_job=2000, selection=preserve.selection_record('off'),
                    input_source='mini', skip_missing_minis=True)
                staged[marker['run_dir']] = dict(marker=marker, submissions=[], intent=False,
                                                status=dict(state=state, running=0, failed=0))
                return marker

            first = prepare(records[0], 'COMPLETE')
            original = copy.deepcopy(staged)

            def remote(alias, code, arguments):
                command = arguments[0]
                if command == '_pool':
                    queries[0] += 1
                    if queries[0] in (1, 2, 4, 5):
                        if queries[0] >= 4:
                            self.assertTrue(copying.wait(2))
                            closed = mock.Mock(returncode=255, stdout='',
                                               stderr='Connection closed by 198.18.0.18 port 22\n')
                            with mock.patch('preserve.remote_call', return_value=closed) as connection:
                                try:
                                    return read_snapshot(alias, code, arguments)
                                finally:
                                    self.assertEqual(connection.call_count, 3)
                        proof = dict(scheduler_unavailable='SECMAN:2007 read failure',
                                     command='_pool', phase='read_only')
                        warning = '/dybfs2/users/lijj16/Libs/root-6.32.08-install/bin/thisroot.csh: No such file or directory.\n'
                        response = mock.Mock(returncode=75, stdout=json.dumps(proof), stderr=warning * 2)
                        with mock.patch('preserve.remote_call', return_value=response) as connection:
                            try:
                                return read_snapshot(alias, code, arguments)
                            finally:
                                self.assertEqual(connection.call_count, 3)
                    return copy.deepcopy(dict(runs=list(staged.values()), jobs=[], queue=preserve.queue_counts([]),
                        reserved_bytes=len(staged) * preserve.GIB, output_bytes=0,
                        quota=dict(free_bytes=500 * preserve.GIB, used_files=1, limit_files=200000))), 0
                self.assertNotIn(queries[0], (1, 2, 4, 5), 'no mutation using an unknown queue')
                if command == '_prepare':
                    self.assertTrue(copying.wait(2))
                    return prepare(records[1], 'PENDING'), 0
                directory = arguments[arguments.index('--run-dir') + 1]
                if command == '_submit':
                    self.assertEqual(directory, '/stage/' + records[1]['stem'])
                    self.assertNotIn(directory, submitted)
                    self.assertNotIn('--resubmit', arguments)
                    submitted.append(directory)
                    staged[directory]['submissions'] = [dict(cluster_id=42, submitted_at=preserve.utc_now())]
                    staged[directory]['status']['state'] = 'COMPLETE'
                    return dict(cluster_id=42), 0
                if command == '_finalize':
                    return staged[directory]['marker'], 0
                self.assertEqual(command, '_cleanup')
                self.assertIn(directory, verified, 'checksum verification must precede cleanup')
                cleaned.append(directory)
                del staged[directory]
                return dict(deleted=directory), 0

            def sync(alias, marker, *unused):
                if marker == first:
                    copying.set()
                    self.assertTrue(release_copy.wait(3))
                verified.add(marker['run_dir'])
                published.set()
                return dict(files=1, bytes=1, directory=marker['run_dir'])

            def sleep(seconds):
                if seconds != 60:
                    return
                state = preserve.load_json(root / 'provenance/controller.json')
                degraded.append(state)
                self.assertIsNone(state['error'])
                self.assertIsNone(state['queue'])
                self.assertIsNone(state['reserved_bytes'])
                self.assertEqual(state['scheduler_status'], 'unavailable')
                self.assertGreater(state['heartbeat_time'], 0)
                self.assertFalse(state['new_run_admission'])
                self.assertEqual(staged[first['run_dir']], original[first['run_dir']])
                self.assertEqual(cleaned, [])
                self.assertEqual(submitted, [])
                if queries[0] in (1, 2):
                    self.assertIn('thisroot.csh: No such file or directory.', state['scheduler_error'])
                if queries[0] == 4:
                    self.assertIn('Connection closed by 198.18.0.18 port 22', state['scheduler_error'])
                    release_copy.set()
                    self.assertTrue(published.wait(2))
                if queries[0] == 5:
                    self.assertIn(first['run_dir'], verified, 'published copy waits safely without cleanup')

            with mock.patch('preserve.remote_json', side_effect=remote), \
                    mock.patch('preserve.archive_progress', return_value=({}, [])), \
                    mock.patch('preserve.recorded_inputs'), mock.patch('preserve.refresh_input_issues'), \
                    mock.patch('preserve.validate_local_mount'), mock.patch('preserve.sync_run', side_effect=sync), \
                    mock.patch('preserve.time.sleep', side_effect=sleep):
                preserve.run_pipeline(args, records, {}, local)
            self.assertEqual([r['scheduler_failures'] for r in degraded], [1, 2, 1, 2])
            self.assertEqual(len(submitted), 1)
            self.assertEqual(set(cleaned), verified)
            self.assertEqual(len(verified), 2)
            state = preserve.load_json(root / 'provenance/controller.json')
            self.assertEqual(state['scheduler_status'], 'available')
            self.assertIsNone(state['scheduler_error'])
            self.assertIsNone(state['error'])
            self.assertTrue(state['new_run_admission'])

    def test_scheduler_outage_does_not_hide_concurrent_checksum_failure(self):
        record = preserve.inventory_records()[0]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            local = root / 'data'
            local.mkdir()
            (root / 'provenance').mkdir()
            args = preserve.build_parser().parse_args([
                'run', '--local-root', str(local), '--mount-root', str(root),
                '--remote-stage-root', '/stage', '--remote-code-root', '/code',
                '--files-per-job', '2000', '--skip-missing-minis', '--delete-remote-after-sync'])
            args.poll_seconds = 0.001
            marker = dict(record, run_dir='/stage/run', code_root='/code', chunk_count=1, files_per_job=2000,
                          selection=preserve.selection_record('off'), input_source='mini', skip_missing_minis=True)
            run = dict(marker=marker, submissions=[], intent=False, status=dict(state='COMPLETE', failed=0, running=0))
            failed, queries, cleanup = threading.Event(), [0], []

            def remote(alias, code, arguments):
                command = arguments[0]
                if command == '_pool':
                    queries[0] += 1
                    if queries[0] > 1:
                        self.assertTrue(failed.wait(2))
                        raise preserve.SchedulerQueryUnavailable('SECMAN:2007 read failure')
                    return dict(runs=[run], jobs=[], queue=preserve.queue_counts([]), reserved_bytes=1,
                                output_bytes=1, quota={}), 0
                if command == '_finalize':
                    return marker, 0
                cleanup.append(command)
                raise AssertionError('no cleanup after checksum failure')

            def sync(*unused):
                failed.set()
                raise ValueError('checksum mismatch')

            with mock.patch('preserve.remote_json', side_effect=remote), \
                    mock.patch('preserve.archive_progress', return_value=({}, [])), \
                    mock.patch('preserve.recorded_inputs'), mock.patch('preserve.refresh_input_issues'), \
                    mock.patch('preserve.validate_local_mount'), mock.patch('preserve.sync_run', side_effect=sync), \
                    mock.patch('preserve.time.sleep'):
                with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                    preserve.run_pipeline(args, [record], {}, local)
            self.assertEqual(cleanup, [])
            self.assertIn('checksum mismatch', preserve.load_json(root / 'provenance/controller.json')['error'])

    def test_command_diagnostics_and_bounded_read_only_status_retries(self):
        with self.assertRaises(RuntimeError) as error:
            preserve.run_command([sys.executable, '-c',
                                  'import sys; print("partial output"); sys.stderr.write("quota unavailable"); sys.exit(7)'])
        self.assertIn('command failed (7)', str(error.exception))
        self.assertIn('partial output', str(error.exception))
        self.assertIn('stderr:\nquota unavailable', str(error.exception))
        good = mock.Mock(returncode=0, stdout='{"queue": {"outstanding": 0}}', stderr='')
        bad_json = mock.Mock(returncode=0, stdout='interrupted JSON', stderr='')
        with mock.patch('preserve.remote_call', side_effect=[RuntimeError('temporary status failure'), bad_json, good]) as call, \
                mock.patch('preserve.time.sleep') as sleep:
            result, status = preserve.remote_json('host', '/code', ['_pool'])
        self.assertEqual((result['queue']['outstanding'], status, call.call_count), (0, 0, 3))
        self.assertEqual(sleep.call_args_list, [mock.call(1), mock.call(2)])
        for command, attempts in [('_pool', 3), ('_submit', 1), ('_prepare', 1), ('_cleanup', 1)]:
            with mock.patch('preserve.remote_call', side_effect=RuntimeError('persistent failure')) as call, \
                    mock.patch('preserve.time.sleep'):
                with self.assertRaisesRegex(RuntimeError, 'persistent failure'):
                    preserve.remote_json('host', '/code', [command])
            self.assertEqual(call.call_count, attempts, 'mutations must never be replayed automatically')

    def test_completed_arrays_reserve_actual_bytes_only_after_jobs_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary) / 'staging'
            run = stage / 'P17B/EH1/run12345'
            (run / 'outputs').mkdir(parents=True)
            (run / 'outputs/chunk.root').write_bytes(b'root')
            preserve.atomic_json(run / preserve.MARKER_NAME,
                                 dict(kind='dyb-preserve-run', run_dir=str(run), chunk_count=16))
            with mock.patch('preserve.scheduler_jobs', return_value=[]) as jobs, \
                    mock.patch('preserve.scratch_quota', return_value={}), \
                    mock.patch('preserve.run_status', return_value=dict(state='RUNNING', running=1)) as status:
                self.assertEqual(preserve.pool_status(str(stage))['reserved_bytes'], 16 * preserve.GIB)
                status.return_value = dict(state='COMPLETE', running=0)
                jobs.return_value = [dict(Iwd=str(run), JobStatus=2, RequestCpus=1)]
                self.assertEqual(preserve.pool_status(str(stage))['reserved_bytes'], 16 * preserve.GIB)
                jobs.return_value = []
                self.assertEqual(preserve.pool_status(str(stage))['reserved_bytes'], 4 + 16 * 1024 ** 2)

    def test_admission_quota_and_scheduler_fail_closed(self):
        pool = dict(queue=dict(outstanding=284), reserved_bytes=284 * preserve.GIB,
                    output_bytes=20 * preserve.GIB,
                    quota=dict(free_bytes=480 * preserve.GIB, used_files=5000, limit_files=200000))
        check = lambda n: preserve.admission_reason(pool, n, 300, 400 * preserve.GIB, 1000 * preserve.GIB)
        self.assertIsNone(check(16))
        self.assertEqual(check(17), "job cap")
        pool['queue']['outstanding'] = 0
        pool['reserved_bytes'] = 390 * preserve.GIB
        self.assertEqual(check(16), "staging reservation budget")
        pool['reserved_bytes'] = 284 * preserve.GIB
        pool['quota']['free_bytes'] = 300 * preserve.GIB
        self.assertEqual(check(16), "scratch quota headroom")
        response = mock.Mock(returncode=0, stdout='[]', stderr='failed to query one schedd')
        with mock.patch('preserve.subprocess.run', return_value=response):
            with self.assertRaisesRegex(RuntimeError, 'scheduler query failed'):
                preserve.scheduler_jobs('/stage')
        response.stderr = ''
        response.stdout = '\n'
        with mock.patch('preserve.subprocess.run', return_value=response):
            self.assertEqual(preserve.scheduler_jobs('/stage'), [])
        response.stdout = '[{"Cmd":"/stage/run/array-job.sh","JobStatus":1,"RequestCpus":1}]\n[]\n'
        with mock.patch('preserve.subprocess.run', return_value=response):
            self.assertEqual(preserve.queue_counts(preserve.scheduler_jobs('/stage'))['idle'], 1)
        response.stdout = json.dumps([dict(Cmd='/stage/run/array-job.sh', JobStatus=2, RequestCpus=4)])
        with mock.patch('preserve.subprocess.run', return_value=response):
            with self.assertRaisesRegex(ValueError, 'CPU request'):
                preserve.scheduler_jobs('/stage')
        quota = mock.Mock(stdout='Disk quotas:\n /scratchfs2 1024 524288000 532466892 - 4000 200000 200000 -\n')
        with mock.patch('preserve.run_command', return_value=quota), \
                mock.patch('preserve.shutil.disk_usage', return_value=mock.Mock(free=1000 * preserve.GIB)):
            self.assertEqual(preserve.scratch_quota('/scratchfs2/stage')['limit_bytes'], 500 * preserve.GIB)
            quota.stdout = quota.stdout.replace('/scratchfs2 ', '/scratchfs2/long/staging/path\n   ')
            self.assertEqual(preserve.scratch_quota('/scratchfs2/long/staging/path')['limit_bytes'], 500 * preserve.GIB)

    def test_submission_intent_recovery_and_no_duplicate_or_cap_overshoot(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary) / 'run'
            run.mkdir()
            marker = dict(chunk_count=16)
            invoke = lambda: preserve.submit_locked(run, marker, temporary, 'dyw', 'short', 1024, False, 300)
            with mock.patch('preserve.scheduler_jobs', return_value=[dict(Cmd='/other')] * 285), \
                    mock.patch('preserve.subprocess.run') as submit:
                with self.assertRaisesRegex(RuntimeError, 'cap'):
                    invoke()
                submit.assert_not_called()
            preserve.atomic_json(run / 'submission-intent.json', dict(submitted_at=preserve.utc_now(), chunk_count=16))
            own = dict(Cmd=str(run / 'array-job.sh'), ClusterId=42)
            with mock.patch('preserve.scheduler_jobs', return_value=[own]), \
                    mock.patch('preserve.subprocess.run') as submit:
                self.assertEqual(invoke()['cluster_id'], 42)
                self.assertEqual(invoke()['cluster_id'], 42)
                submit.assert_not_called()
            preserve.atomic_json(run / 'submission-intent.json', {})
            with mock.patch('preserve.scheduler_jobs', return_value=[]):
                with self.assertRaisesRegex(RuntimeError, 'ambiguous submission'):
                    invoke()

    def test_expired_incomplete_arrays_retry_only_after_jobs_exit_and_within_attempt_limit(self):
        record = preserve.inventory_records()[0]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'provenance').mkdir()
            local = root / 'data'
            local.mkdir()
            args = preserve.build_parser().parse_args([
                'run', '--local-root', str(local), '--mount-root', str(root),
                '--remote-stage-root', '/stage', '--remote-code-root', '/code',
                '--files-per-job', '2000', '--skip-missing-minis'])
            marker = dict(record, run_dir='/stage/run', code_root='/code', chunk_count=1,
                          files_per_job=2000, selection=preserve.selection_record('off'),
                          input_source='mini', skip_missing_minis=True)
            for state, queued, attempts, expired, intent, retries, error in (
                    ('PENDING', False, 1, True, False, 1, 'snapshot boundary'),
                    ('RUNNING', False, 1, True, False, 1, 'snapshot boundary'),
                    ('PENDING', False, 1, False, False, 0, 'snapshot boundary'),
                    ('RUNNING', True, 1, True, False, 0, 'snapshot boundary'),
                    ('PENDING', False, 2, True, False, 0, 'snapshot boundary'),
                    ('PENDING', False, 2, True, True, 0, 'exhausted submission attempts'),
                    ('FAILED', False, 2, True, False, 0, 'exhausted submission attempts'),
                    ('RUNNING', False, 2, True, False, 0, 'exhausted submission attempts')):
                with self.subTest(state=state, queued=queued, attempts=attempts, expired=expired):
                    submissions = [dict(cluster_id=i, submitted_at=(
                        '2000-01-01T00:00:00+00:00' if expired else preserve.utc_now()))
                        for i in range(attempts)]
                    jobs = [dict(Iwd=marker['run_dir'], JobStatus=2, RequestCpus=1,
                                 ClusterId=42, ProcId=0)] if queued else []
                    pool = dict(runs=[dict(marker=marker, submissions=submissions, intent=intent,
                                          status=dict(state=state, running=int(state == 'RUNNING'), failed=0))],
                                jobs=jobs, queue=preserve.queue_counts(jobs), reserved_bytes=preserve.GIB,
                                output_bytes=0, quota=dict(free_bytes=500 * preserve.GIB, used_files=10,
                                                         limit_files=200000))
                    queries, submitted = [], []

                    def remote(alias, code, arguments):
                        if arguments[0] == '_pool':
                            if queries:
                                raise RuntimeError('snapshot boundary')
                            queries.append(True)
                            return pool, 0
                        self.assertEqual(arguments[0], '_submit', 'no finalization or cleanup of incomplete data')
                        self.assertIn('--resubmit', arguments)
                        submitted.append(arguments)
                        return dict(cluster_id=100), 0

                    with mock.patch('preserve.remote_json', side_effect=remote), \
                            mock.patch('preserve.archive_progress', return_value=({}, [])), \
                            mock.patch('preserve.recorded_inputs'), mock.patch('preserve.refresh_input_issues'), \
                            mock.patch('preserve.time.sleep'):
                        with self.assertRaisesRegex(RuntimeError, error):
                            preserve.run_pipeline(args, [record], {}, local)
                    self.assertEqual(len(submitted), retries)
                    report = preserve.load_json(root / 'provenance/controller.json')
                    self.assertEqual(len(report.get('overdue_runs', [])), int(queued and expired))
                    self.assertEqual(len(report.get('exhausted_pending_runs', [])),
                                     int(state == 'PENDING' and not queued and not intent and expired and attempts == 2))
                    if queued and expired:
                        self.assertEqual(report['overdue_runs'][0]['jobs'],
                                         [dict(cluster_id=42, proc_id=0, state=2)])

    def test_exhausted_pending_run_retained_while_other_run_verifies_cleans_and_admits(self):
        records = preserve.inventory_records()[:3]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'provenance').mkdir()
            local = root / 'data'
            local.mkdir()
            args = preserve.build_parser().parse_args([
                'run', '--local-root', str(local), '--mount-root', str(root),
                '--remote-stage-root', '/stage', '--remote-code-root', '/code',
                '--max-jobs', '2', '--max-staged-gib', '3', '--files-per-job', '2000',
                '--skip-missing-minis', '--delete-remote-after-sync'])
            args.poll_seconds = 0.001
            stages, verified, submitted = {}, set(), []
            repaired = False

            def prepare(record, state='PENDING'):
                marker = dict(record, run_dir='/stage/' + record['stem'], code_root='/code',
                              chunk_count=1, files_per_job=2000, selection=preserve.selection_record('off'),
                              input_source='mini', skip_missing_minis=True)
                stages[marker['run_dir']] = dict(marker=marker, submissions=[], intent=False,
                    status=dict(state=state, running=0, failed=0, ok=0))
                return marker

            exhausted = prepare(records[0])  # The exhausted run precedes the ready run in every pool snapshot.
            ready = prepare(records[1], 'COMPLETE')
            stages[exhausted['run_dir']]['submissions'] = [
                dict(cluster_id=i, submitted_at='2000-01-01T00:00:00+00:00') for i in (41, 42)]
            retained = copy.deepcopy(stages[exhausted['run_dir']])
            healthy_reports = []

            def remote(alias, code, arguments):
                nonlocal repaired
                command = arguments[0]
                options = dict(zip(arguments[1::2], arguments[2::2]))
                directory = options.get('--run-dir')
                if command == '_pool':
                    report_path = root / 'provenance/controller.json'
                    if report_path.exists():
                        report = preserve.load_json(report_path)
                        self.assertIsNone(report['error'])
                        if report['exhausted_pending_runs']:
                            healthy_reports.append(report)
                    if ready['run_dir'] not in stages and submitted and not repaired:
                        self.assertEqual(stages[exhausted['run_dir']], retained)
                        # An external repair can later finish it; only then may the pending warning clear.
                        for run in stages.values():
                            run['status']['state'] = 'COMPLETE'
                        repaired = True
                    return copy.deepcopy(dict(runs=list(stages.values()), jobs=[],
                        queue=preserve.queue_counts([]), reserved_bytes=len(stages) * preserve.GIB,
                        output_bytes=0, quota=dict(free_bytes=500 * preserve.GIB, used_files=1,
                                                 limit_files=200000))), 0
                if command == '_prepare':
                    self.assertEqual(stages[exhausted['run_dir']], retained)
                    return prepare(records[2]), 0
                if command == '_submit':
                    self.assertNotEqual(directory, exhausted['run_dir'], 'never submit a third attempt')
                    submitted.append(directory)
                    stages[directory]['submissions'] = [dict(cluster_id=43, submitted_at=preserve.utc_now())]
                    return dict(cluster_id=43), 0
                if command == '_finalize':
                    self.assertEqual(stages[directory]['status']['state'], 'COMPLETE')
                    return stages[directory]['marker'], 0
                self.assertEqual(command, '_cleanup')
                self.assertIn(directory, verified, 'verify before cleanup')
                if directory == ready['run_dir']:
                    self.assertEqual(stages[exhausted['run_dir']], retained)
                del stages[directory]
                return dict(deleted=directory), 0

            def sync(alias, marker, *unused):
                if marker['run_dir'] == ready['run_dir']:
                    self.assertEqual(stages[exhausted['run_dir']], retained)
                    report = preserve.load_json(root / 'provenance/controller.json')
                    self.assertEqual(report['exhausted_pending_runs'][0]['attempts'], 2)
                verified.add(marker['run_dir'])
                return dict(files=1, bytes=1, directory=marker['run_dir'])

            with mock.patch('preserve.remote_json', side_effect=remote), \
                    mock.patch('preserve.archive_progress', return_value=({}, [])), \
                    mock.patch('preserve.recorded_inputs'), mock.patch('preserve.refresh_input_issues') as gaps, \
                    mock.patch('preserve.validate_local_mount'), mock.patch('preserve.sync_run', side_effect=sync):
                preserve.run_pipeline(args, records, {}, local)
            self.assertGreaterEqual(len(healthy_reports), 2)
            self.assertEqual(submitted, ['/stage/' + records[2]['stem']])
            self.assertEqual(verified, {'/stage/' + r['stem'] for r in records})
            self.assertEqual(gaps.call_args_list[0].kwargs['prepared'], exhausted)
            self.assertEqual(exhausted['input_count'], records[0]['input_count'])
            self.assertFalse(preserve.load_json(root / 'provenance/controller.json')['exhausted_pending_runs'])

    def test_overdue_job_retained_while_other_run_transfers_and_new_work_is_admitted(self):
        records = preserve.inventory_records()[:3]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'provenance').mkdir()
            local = root / 'data'
            local.mkdir()
            args = preserve.build_parser().parse_args([
                'run', '--local-root', str(local), '--mount-root', str(root),
                '--remote-stage-root', '/stage', '--remote-code-root', '/code',
                '--max-jobs', '2', '--max-staged-gib', '3', '--files-per-job', '2000',
                '--skip-missing-minis', '--delete-remote-after-sync'])
            args.poll_seconds = 0.001
            stages, verified, submitted, jobs = {}, set(), [], []

            def prepare(record, state='PENDING'):
                marker = dict(record, run_dir='/stage/' + record['stem'], code_root='/code',
                              chunk_count=1, files_per_job=2000, selection=preserve.selection_record('off'),
                              input_source='mini', skip_missing_minis=True)
                stages[marker['run_dir']] = dict(marker=marker, submissions=[], intent=False,
                    status=dict(state=state, running=0, failed=0))
                return marker

            overdue = prepare(records[0])
            ready = prepare(records[1], 'COMPLETE')
            stages[overdue['run_dir']]['submissions'] = [dict(cluster_id=42, submitted_at='2000-01-01T00:00:00+00:00')]
            retained = copy.deepcopy(stages[overdue['run_dir']])
            jobs.append(dict(Iwd=overdue['run_dir'], JobStatus=2, RequestCpus=1, ClusterId=42, ProcId=0))

            def remote(alias, code, arguments):
                command = arguments[0]
                options = dict(zip(arguments[1::2], arguments[2::2]))
                directory = options.get('--run-dir')
                if command == '_pool':
                    if ready['run_dir'] in verified and submitted:
                        jobs.clear()
                        for run in stages.values():
                            run['status']['state'] = 'COMPLETE'
                    return copy.deepcopy(dict(runs=list(stages.values()), jobs=jobs,
                        queue=preserve.queue_counts(jobs), reserved_bytes=len(stages) * preserve.GIB,
                        output_bytes=0, quota=dict(free_bytes=500 * preserve.GIB, used_files=1,
                                                 limit_files=200000))), 0
                if command == '_prepare':
                    self.assertTrue(any(j['Iwd'] == overdue['run_dir'] for j in jobs))
                    report = preserve.load_json(root / 'provenance/controller.json')
                    self.assertIsNone(report['error'])
                    self.assertEqual(report['overdue_runs'][0]['run'], 'P17B/EH1/' + overdue['stem'])
                    return prepare(records[2]), 0
                if command == '_submit':
                    self.assertNotEqual(directory, overdue['run_dir'], 'never duplicate an overdue writer')
                    self.assertNotIn('--resubmit', arguments)
                    submitted.append(directory)
                    jobs.append(dict(Iwd=directory, JobStatus=2, RequestCpus=1, ClusterId=43, ProcId=0))
                    self.assertLessEqual(len(jobs), 2)
                    stages[directory]['submissions'] = [dict(cluster_id=43, submitted_at=preserve.utc_now())]
                    return dict(cluster_id=43), 0
                if command == '_finalize':
                    self.assertFalse(any(j['Iwd'] == directory for j in jobs))
                    return stages[directory]['marker'], 0
                self.assertEqual(command, '_cleanup')
                self.assertIn(directory, verified)
                del stages[directory]
                return dict(deleted=directory), 0

            def sync(alias, marker, *unused):
                if marker['run_dir'] == ready['run_dir']:
                    self.assertTrue(any(j['Iwd'] == overdue['run_dir'] for j in jobs))
                    self.assertEqual(stages[overdue['run_dir']], retained)
                    report = preserve.load_json(root / 'provenance/controller.json')
                    self.assertEqual(report['overdue_runs'][0]['run'], 'P17B/EH1/' + overdue['stem'])
                    self.assertIsNone(report['error'])
                verified.add(marker['run_dir'])
                return dict(files=1, bytes=1, directory=marker['run_dir'])

            with mock.patch('preserve.remote_json', side_effect=remote), \
                    mock.patch('preserve.archive_progress', return_value=({}, [])), \
                    mock.patch('preserve.recorded_inputs'), mock.patch('preserve.refresh_input_issues'), \
                    mock.patch('preserve.validate_local_mount'), mock.patch('preserve.sync_run', side_effect=sync):
                preserve.run_pipeline(args, records, {}, local)
            self.assertEqual(len(submitted), 1)
            self.assertEqual(verified, {'/stage/' + r['stem'] for r in records})
            self.assertFalse(preserve.load_json(root / 'provenance/controller.json')['overdue_runs'])

    def test_startup_failure_waits_for_writers_while_other_work_transfers_and_admits(self):
        records = preserve.inventory_records()[:3]
        for exhausted, phase in ((False, 'lcg_setup'), (True, 'lcg_setup'), (False, 'cxx_loader'), (True, 'cxx_loader')):
            with self.subTest(exhausted=exhausted, phase=phase), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / 'provenance').mkdir()
                local = root / 'data'; local.mkdir()
                args = preserve.build_parser().parse_args([
                    'run', '--local-root', str(local), '--mount-root', str(root),
                    '--remote-stage-root', '/stage', '--remote-code-root', '/code',
                    '--max-jobs', '2', '--max-staged-gib', '3', '--files-per-job', '2000',
                    '--skip-missing-minis', '--skip-broken-minis', '--delete-remote-after-sync'])
                args.poll_seconds = 0.001
                stages, jobs, verified, submitted, retried, cleaned = {}, [], set(), [], [], []

                def stage(record, state='PENDING'):
                    marker = dict(record, run_dir='/stage/' + record['stem'], code_root='/code', chunk_count=1,
                                  files_per_job=2000, selection=preserve.selection_record('off'),
                                  input_source='mini', skip_missing_minis=True)
                    stages[marker['run_dir']] = dict(marker=marker, submissions=[], intent=False,
                        status=dict(state=state, running=0, failed=int(state == 'FAILED')))
                    return marker

                startup = stage(records[0], 'FAILED')
                ready = stage(records[1], 'COMPLETE')
                failed = stages[startup['run_dir']]
                failed['submissions'] = [dict(cluster_id=42, submitted_at=preserve.utc_now())]
                failed['status']['failure_updates'] = {'000000': [1, 1]}
                failed['startup_failure'] = dict(startup=True, failure_updates={'000000': [1, 1]},
                                                 failures=[dict(phase=phase)])
                retained = copy.deepcopy(failed)
                jobs.append(dict(Iwd=startup['run_dir'], JobStatus=2, RequestCpus=1, ClusterId=42, ProcId=0))

                def remote(alias, code, arguments):
                    command = arguments[0]
                    options = dict(zip(arguments[1::2], arguments[2::2]))
                    directory = options.get('--run-dir')
                    if command == '_pool':
                        if ready['run_dir'] in verified and submitted:
                            jobs.clear()
                            for run in stages.values():
                                if run is not failed: run['status']['state'] = 'COMPLETE'
                            if exhausted and len(failed['submissions']) == 1:
                                failed['submissions'].append(dict(cluster_id=43, submitted_at=preserve.utc_now()))
                        return copy.deepcopy(dict(runs=list(stages.values()), jobs=jobs, queue=preserve.queue_counts(jobs),
                            reserved_bytes=len(stages)*preserve.GIB, output_bytes=0,
                            quota=dict(free_bytes=500*preserve.GIB, used_files=1, limit_files=200000))), 0
                    if command == '_prepare':
                        self.assertTrue(any(j['Iwd'] == startup['run_dir'] for j in jobs))
                        self.assertEqual(failed, retained)
                        return stage(records[2]), 0
                    if command == '_submit':
                        if directory == startup['run_dir']:
                            self.assertFalse(exhausted, 'never submit a third attempt')
                            self.assertFalse(jobs, 'retry requires a fresh successful no-writer query')
                            self.assertIn(ready['run_dir'], verified)
                            self.assertIn('--resubmit', arguments); self.assertIn('--startup-retry', arguments)
                            self.assertEqual(arguments[arguments.index('--max-attempts')+1], '2')
                            retried.append(directory)
                            failed['status'].update(state='COMPLETE', failed=0)
                            failed['startup_failure'] = None
                            failed['startup_recovery'] = [dict(kind='dyb-worker-startup-recovery',
                                run_dir=directory, catalog_sha256=startup['catalog_sha256'])]
                        else:
                            self.assertNotIn('--startup-retry', arguments)
                            submitted.append(directory)
                            jobs.append(dict(Iwd=directory, JobStatus=2, RequestCpus=1, ClusterId=44, ProcId=0))
                        stages[directory]['submissions'].append(dict(cluster_id=44, submitted_at=preserve.utc_now()))
                        return dict(cluster_id=44), 0
                    if command == '_finalize':
                        self.assertFalse(any(j['Iwd'] == directory for j in jobs))
                        return stages[directory]['marker'], 0
                    self.assertEqual(command, '_cleanup', 'startup failures must never reach missing/broken classification')
                    self.assertIn(directory, verified, 'verification must precede staging deletion')
                    cleaned.append(directory); del stages[directory]
                    return dict(deleted=directory), 0

                def sync(alias, marker, *unused):
                    if marker['run_dir'] == ready['run_dir']:
                        self.assertTrue(any(j['Iwd'] == startup['run_dir'] for j in jobs))
                        self.assertEqual(failed, retained)
                        report = preserve.load_json(root/'provenance/controller.json')
                        self.assertEqual(report['startup_recovery_pending'][0]['state'], 'waiting_for_writers')
                        self.assertTrue(report['new_run_admission']); self.assertIsNone(report['error'])
                    verified.add(marker['run_dir'])
                    return dict(files=1, bytes=1, directory=marker['run_dir'])

                with mock.patch('preserve.remote_json', side_effect=remote), \
                        mock.patch('preserve.archive_progress', return_value=({}, [])), \
                        mock.patch('preserve.recorded_inputs'), mock.patch('preserve.refresh_input_issues'), \
                        mock.patch('preserve.validate_local_mount'), mock.patch('preserve.sync_run', side_effect=sync):
                    if exhausted:
                        with self.assertRaisesRegex(RuntimeError, 'exhausted submission attempts'):
                            preserve.run_pipeline(args, records, {}, local)
                    else: preserve.run_pipeline(args, records, {}, local)
                self.assertEqual(len(submitted), 1)
                self.assertEqual(len(retried), 0 if exhausted else 1)
                self.assertIn(ready['run_dir'], verified)
                report = preserve.load_json(root/'provenance/controller.json')
                if exhausted:
                    self.assertEqual(report['startup_recovery_pending'][0]['state'], 'exhausted')
                    self.assertEqual(failed['status']['failed'], 1)
                else:
                    self.assertFalse(report['startup_recovery_pending'])
                    self.assertEqual(len(cleaned), 3)
                    exported = root/'provenance/worker-startup-recovery' / ("P17B/EH1/"+startup['stem']+".json")
                    self.assertEqual(preserve.load_json(exported), failed['startup_recovery'])

    def test_pipeline_adopts_inflight_refills_during_transfer_and_only_cleans_verified(self):
        records = preserve.inventory_records()[:5]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'provenance').mkdir()
            local = root / 'data'
            local.mkdir()
            args = preserve.build_parser().parse_args([
                'run', '--local-root', str(local), '--mount-root', str(root),
                '--remote-stage-root', '/stage', '--remote-code-root', '/code',
                '--max-jobs', '2', '--max-staged-gib', '3', '--files-per-job', '2000',
                '--skip-missing-minis', '--delete-remote-after-sync'])
            args.poll_seconds = 0.001
            staged, jobs, submitted, verified, cleaned = {}, {}, [], set(), []
            transferring = threading.Event()
            refilled = threading.Event()
            peak = [0]

            def prepare(record):
                marker = dict(record, run_dir='/stage/' + record['stem'], code_root='/code',
                              chunk_count=1, files_per_job=2000, selection=preserve.selection_record('off'),
                              input_source='mini', skip_missing_minis=True)
                staged[marker['run_dir']] = dict(marker=marker, submissions=[], intent=False,
                                                status=dict(state='PENDING', running=0, failed=0))
                return marker

            first = prepare(records[0])
            staged[first['run_dir']]['submissions'] = [dict(cluster_id=100, submitted_at=preserve.utc_now())]
            jobs[first['run_dir']] = dict(Iwd=first['run_dir'], JobStatus=2, RequestCpus=1)

            def remote(alias, code, arguments):
                command = arguments[0]
                options = dict(zip(arguments[1::2], arguments[2::2]))
                directory = options.get('--run-dir')
                if command == '_pool':
                    if len(submitted) >= 1:
                        for path in list(jobs):
                            if path == first['run_dir'] or refilled.is_set():
                                jobs.pop(path)
                                staged[path]['status'] = dict(state='COMPLETE', running=0, failed=0)
                    return copy.deepcopy(dict(runs=list(staged.values()), jobs=list(jobs.values()),
                        queue=preserve.queue_counts(list(jobs.values())), reserved_bytes=len(staged) * preserve.GIB,
                        output_bytes=0, quota=dict(free_bytes=500 * preserve.GIB, used_files=10, limit_files=200000))), 0
                if command == '_prepare':
                    self.assertLess(len(staged), 3, 'staging backpressure must include completed transfers')
                    return prepare(next(r for r in records if r['catalog'] == options['--catalog'])), 0
                if command == '_submit':
                    self.assertNotEqual(directory, first['run_dir'], 'must adopt existing cluster')
                    self.assertNotIn(directory, submitted)
                    submitted.append(directory)
                    jobs[directory] = dict(Iwd=directory, JobStatus=2, RequestCpus=1)
                    peak[0] = max(peak[0], len(jobs))
                    self.assertLessEqual(len(jobs), 2)
                    submission = dict(cluster_id=100 + len(submitted), submitted_at=preserve.utc_now())
                    staged[directory]['submissions'] = [submission]
                    if len(submitted) == 2:
                        self.assertTrue(transferring.wait(2), 'first transfer must already be running')
                        refilled.set()
                    return submission, 0
                if command == '_finalize':
                    self.assertNotIn(directory, jobs, 'must wait until array exits scheduler')
                    return staged[directory]['marker'], 0
                if command == '_cleanup':
                    self.assertIn(directory, verified, 'must verify disk copy before deletion')
                    cleaned.append(directory)
                    del staged[directory]
                    return dict(deleted=directory), 0
                raise AssertionError(command)

            def sync(alias, marker, *unused):
                if marker['run_dir'] == first['run_dir']:
                    transferring.set()
                    self.assertTrue(refilled.wait(5), 'submission must continue during transfer')
                verified.add(marker['run_dir'])
                return dict(files=1, bytes=10, directory=marker['run_dir'])

            with mock.patch('preserve.remote_json', side_effect=remote), \
                    mock.patch('preserve.archive_progress', return_value=({}, [])), \
                    mock.patch('preserve.recorded_inputs'), mock.patch('preserve.refresh_input_issues'), \
                    mock.patch('preserve.validate_local_mount'), mock.patch('preserve.sync_run', side_effect=sync):
                preserve.run_pipeline(args, records, {}, local)
            self.assertEqual((len(submitted), len(cleaned), peak[0]), (4, 5, 2))
            self.assertEqual(set(cleaned), verified)
            # A failed disk checksum retains staging and surfaces the error to monitoring.
            marker = prepare(records[0])
            staged[marker['run_dir']]['status']['state'] = 'COMPLETE'
            cleaned.clear()
            with mock.patch('preserve.remote_json', side_effect=remote), \
                    mock.patch('preserve.archive_progress', return_value=({}, [])), \
                    mock.patch('preserve.recorded_inputs'), mock.patch('preserve.refresh_input_issues'), \
                    mock.patch('preserve.sync_run', side_effect=ValueError('checksum mismatch')):
                with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                    preserve.run_pipeline(args, records[:1], {}, local)
            self.assertEqual(cleaned, [])
            self.assertIn(marker['run_dir'], staged)
            self.assertIn('checksum mismatch', preserve.load_json(root / 'provenance/controller.json')['error'])

            # A fatal status query may overlap a successful copy. Publish its ledger, but retain staging.
            copying, status_failed = threading.Event(), threading.Event()
            verified.discard(marker['run_dir'])
            queries = [0]

            def failing_remote(alias, code, arguments):
                if arguments[0] == '_pool':
                    queries[0] += 1
                    if queries[0] == 2:
                        self.assertTrue(copying.wait(2))
                        status_failed.set()
                        raise RuntimeError('read-only status failed')
                return remote(alias, code, arguments)

            def finishing_copy(*unused):
                copying.set()
                self.assertTrue(status_failed.wait(2))
                verified.add(marker['run_dir'])
                return dict(files=1, bytes=10, directory=marker['run_dir'])

            def reconcile(*unused, **kwargs):
                if not kwargs:
                    self.assertIn(marker['run_dir'], verified)

            with mock.patch('preserve.remote_json', side_effect=failing_remote), \
                    mock.patch('preserve.archive_progress', return_value=({}, [])), \
                    mock.patch('preserve.recorded_inputs'), \
                    mock.patch('preserve.refresh_input_issues', side_effect=reconcile) as ledger, \
                    mock.patch('preserve.validate_local_mount'), \
                    mock.patch('preserve.sync_run', side_effect=finishing_copy):
                with self.assertRaisesRegex(RuntimeError, 'read-only status failed'):
                    preserve.run_pipeline(args, records[:1], {}, local)
            ledger.assert_called_with(local)
            self.assertEqual(cleaned, [])
            self.assertIn(marker['run_dir'], staged)
            self.assertIn('read-only status failed', preserve.load_json(root / 'provenance/controller.json')['error'])

    def test_explicit_hold_retains_pending_inputs_and_drains_unaffected_retries_and_transfers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            local = root / 'data'
            local.mkdir()
            (root / 'provenance').mkdir()
            records, staged = [], {}
            for number in (10001, 10002, 10003, 10004):
                record = dict(period='P17B', hall='EH1', stem='run' + str(number),
                              catalog='/catalog/run{}.list'.format(number), catalog_sha256=str(number), input_count=2)
                records.append(record)
                backup = root / 'catalogs/P17B/EH1' / (record['stem'] + '.list')
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_text('/tiny/input1.root\n/tiny/input2.root\n')
                marker = dict(record, run_dir='/stage/' + record['stem'], code_root='/code', chunk_count=2,
                              files_per_job=1, selection=preserve.selection_record('on'),
                              input_source='mini', skip_missing_minis=True)
                staged[record['stem']] = dict(marker=marker, intent=False,
                    submissions=[dict(cluster_id=number, submitted_at='2000-01-01T00:00:00+00:00')],
                    status=dict(state='FAILED' if number in (10001, 10004) else 'PENDING' if number == 10002 else 'COMPLETE',
                                ok=1 if number != 10003 else 2, failed=int(number in (10001, 10004)), running=0, chunks=2))
            records.append(dict(records[-1], stem='run10005', catalog='/catalog/run10005.list', catalog_sha256='10005'))
            held_before = {stem: copy.deepcopy(staged[stem]) for stem in ('run10001', 'run10004')}
            args = preserve.build_parser().parse_args([
                'run', '--local-root', str(local), '--mount-root', str(root),
                '--remote-stage-root', '/stage', '--remote-code-root', '/code',
                '--files-per-job', '1', '--skip-missing-minis', '--ad-selection', 'on',
                '--delete-remote-after-sync', '--hold-run', 'P17B/EH1/run10001',
                '--hold-run', 'P17B/EH1/run10004', '--drain-staging'])
            args.poll_seconds = 0.001
            submitted, verified, cleaned = [], set(), []

            def remote(alias, code, arguments):
                command = arguments[0]
                if command == '_pool':
                    return copy.deepcopy(dict(runs=list(staged.values()), jobs=[], queue=preserve.queue_counts([]),
                        reserved_bytes=3 * preserve.GIB, output_bytes=10,
                        quota=dict(free_bytes=500 * preserve.GIB, used_files=10, limit_files=200000))), 0
                stem = Path(arguments[arguments.index('--run-dir') + 1]).name
                self.assertNotIn(stem, held_before, 'held chunks and failure evidence must not be mutated')
                if command == '_submit':
                    self.assertEqual(stem, 'run10002')
                    self.assertIn('--resubmit', arguments)
                    submitted.append(stem)
                    staged[stem]['status'].update(state='COMPLETE', ok=2)
                    return dict(cluster_id=20002), 0
                if command == '_finalize':
                    return staged[stem]['marker'], 0
                self.assertEqual(command, '_cleanup')
                self.assertIn(stem, verified)
                cleaned.append(stem)
                del staged[stem]
                return dict(deleted=stem), 0

            def sync(alias, marker, *unused):
                verified.add(marker['stem'])
                return dict(files=1, bytes=10, directory=marker['run_dir'])

            with mock.patch('preserve.CATALOG_BACKUP_ROOT', root / 'catalogs'), \
                    mock.patch('preserve.remote_json', side_effect=remote), \
                    mock.patch('preserve.archive_progress', return_value=({}, [])), \
                    mock.patch('preserve.recorded_inputs'), mock.patch('preserve.refresh_input_issues'), \
                    mock.patch('preserve.validate_local_mount'), mock.patch('preserve.sync_run', side_effect=sync):
                with self.assertRaisesRegex(RuntimeError, 'only held incomplete runs remain'):
                    preserve.run_pipeline(args, records, {}, local)
            self.assertEqual(submitted, ['run10002'])
            self.assertEqual(set(cleaned), {'run10002', 'run10003'})
            self.assertEqual(staged, held_before)
            state = preserve.load_json(root / 'provenance/controller.json')
            self.assertEqual(state['held_runs'][0]['run'], 'P17B/EH1/run10001')
            self.assertEqual(state['held_runs'][0]['status']['ok'], 1)
            self.assertFalse(state['new_run_admission'])
            self.assertEqual(state['pending_runs'], 1, 'drain mode retains unadmitted catalog runs')
            # Holding staging never adds a completed local run or a missing-file skip.
            preserve.atomic_json(local / 'catalog-inventory.json', dict(records=records))
            progress, saved = preserve.archive_progress(local)
            self.assertEqual((progress['expected_runs'], progress['expected_inputs'], progress['pending_inputs']), (5, 10, 10))
            self.assertEqual((progress['completed_runs'], progress['skipped_inputs'], saved), (0, 0, []))
            self.assertFalse(progress['complete'])

            args.hold_run = ['P17B/EH1/not-in-catalog']
            with self.assertRaisesRegex(ValueError, 'outside the selected fixed catalog'):
                preserve.run_pipeline(args, records, {}, local)
            args.hold_run = ['P17B/EH1/run10001']
            job = dict(Iwd=held_before['run10001']['marker']['run_dir'], JobStatus=2, RequestCpus=1)
            pool = dict(runs=[held_before['run10001']], jobs=[job], queue=preserve.queue_counts([job]),
                        quota={}, reserved_bytes=preserve.GIB, output_bytes=10)
            with mock.patch('preserve.remote_json', return_value=(pool, 0)) as call, \
                    mock.patch('preserve.CATALOG_BACKUP_ROOT', root / 'catalogs'), \
                    mock.patch('preserve.recorded_inputs'), mock.patch('preserve.refresh_input_issues'):
                with self.assertRaisesRegex(RuntimeError, 'scheduler jobs or worker markers'):
                    preserve.run_pipeline(args, records, {}, local)
            self.assertTrue(all(c.args[2][0] == '_pool' for c in call.call_args_list))


if __name__ == '__main__':
    unittest.main()
