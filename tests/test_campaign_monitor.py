import unittest

from scripts.check_campaign import status_record


class CampaignMonitorTests(unittest.TestCase):
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
