import copy
import errno
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import preserve


class MissingOnlyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.local = self.root / 'data'
        self.local.mkdir()
        self.key = 'P20A/EH3/run79938'
        self.record = dict(period='P20A', hall='EH3', stem='run79938', input_count=1,
            catalog=str(Path(dict(preserve.CATALOGS)['P20A']) / 'EH3/run79938.list'),
            catalog_sha256=preserve.MISSING_ONLY_CATALOGS[self.key])
        self.data = (preserve.CATALOG_BACKUP_ROOT / (self.key + '.list')).read_bytes()
        self.paths = preserve.parse_catalog_inputs(self.data, self.key)
        self.mini = preserve.resolve_inputs(self.paths)[0]
        now = preserve.utc_now()
        self.proof = dict(kind='dyb-reviewed-missing-only-run', run=self.key,
            catalog_sha256=self.record['catalog_sha256'], checked_at=now,
            mini_check=dict(path=self.mini, status='missing', attempts=[
                dict(timestamp=now, status='missing', errno=errno.ENOENT) for _ in range(2)]),
            loss_index=dict(path=str(Path(preserve.MINI_ROOT).parent / 'list/file_lost.txt'),
                            matches=[self.mini], sha256='a'*64),
            neighbors=[dict(path=self.mini.replace('0079938', n), status='ok',
                            attempts=[dict(timestamp=now, status='ok', errno=None)])
                       for n in ('0079931', '0079941')])
        self.destination = self.local / self.key
        preserve.atomic_json(self.local / 'catalog-inventory.json', dict(
            kind='dyb-preserve-catalog-inventory', schema_version=preserve.SCHEMA_VERSION,
            input_source='mini', skip_missing_minis=True, records=[self.record]))

    def build(self, proof=None):
        return preserve.finalize_missing_only(self.destination, self.record, proof or self.proof)

    def test_zero_output_resolution_is_verified_and_reported_as_missing_not_preserved(self):
        result = self.build()
        self.assertEqual((self.destination / 'catalog.list').read_bytes(), self.data)
        self.assertEqual(list((self.destination / 'outputs').iterdir()), [])
        self.assertEqual((result['chunk_count'], result['output_count'], result['duration_rows']), (0, 0, 0))
        verified = preserve.verify_local_run(self.destination)
        self.assertEqual((verified['processed_inputs'], verified['missing_skipped_inputs'],
                          verified['broken_skipped_inputs'], verified['catalog_complete']), (0, 1, 0, False))
        progress, records = preserve.archive_progress(self.local)
        self.assertEqual((progress['pending_inputs'], progress['processed_inputs'], progress['skipped_inputs']), (0, 0, 1))
        self.assertEqual((progress['status'], progress['complete']), ('finished_with_gaps', False))
        self.assertEqual(preserve.verify_all(self.local)['status'], 'finished_with_gaps')
        preserve.refresh_input_issues(self.local)
        ledger = preserve.load_json(self.root / 'provenance/input-issues.json')
        issue = next(i for i in ledger['issues'] if i['run'] == 'run79938')
        self.assertEqual((issue['category'], issue['status']), ('missing', 'skipped'))
        self.assertEqual(issue['mini_check']['path'], self.mini)
        preserve.refresh_pending_holds(self.local, set(), {}, {}, True, True)
        self.assertEqual(preserve.load_json(self.root / 'provenance/pending-input-issues.json')['held_runs'], [])

    def test_no_general_bypass_of_all_missing_preparation(self):
        with mock.patch('preserve.check_input_storage'), \
                mock.patch('preserve.probe_input', return_value=self.proof['mini_check']):
            with self.assertRaisesRegex(ValueError, 'no usable inputs'):
                preserve.available_inputs(self.paths)
        self.assertFalse(self.destination.exists())

    def test_evidence_and_accounting_cannot_be_replaced_or_omitted(self):
        result = self.build()
        variants = [dict(result, missing_only_resolution=None), dict(result, stem='run79939'),
                    dict(result, catalog_sha256='0'*64), dict(result, catalog_complete=True),
                    dict(result, processed_input_count=1), dict(result, output_count=1),
                    dict(result, source_entries=1), dict(result, broken_skipped_input_count=1)]
        for mutation in ('io_error', 'one_observation', 'index_mismatch', 'unreadable_neighbor', 'wrong_neighbor'):
            changed = copy.deepcopy(result)
            proof = changed['missing_only_resolution']
            if mutation == 'io_error':
                proof['mini_check']['attempts'][0]['errno'] = errno.EIO
            elif mutation == 'one_observation':
                proof['mini_check']['attempts'].pop()
            elif mutation == 'index_mismatch':
                proof['loss_index']['matches'] = []
            elif mutation == 'unreadable_neighbor':
                proof['neighbors'][0]['status'] = 'missing'
            else:
                proof['neighbors'][0]['path'] = self.mini
            variants.append(changed)
        for record in variants:
            with self.subTest(record=record):
                with self.assertRaises(ValueError):
                    preserve.recorded_inputs(self.paths, record)

    def test_resolution_evidence_is_checksummed_and_fake_output_is_rejected(self):
        self.build()
        fake = self.destination / 'outputs/extra.root'
        fake.write_bytes(b'root unexpected')
        with self.assertRaisesRegex(ValueError, 'ROOT output set'):
            preserve.verify_local_run(self.destination)
        fake.unlink()
        gap = self.destination / 'input-gaps.json'
        gap.write_text(gap.read_text() + '\n')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            preserve.verify_local_run(self.destination)

    def test_invalid_proof_or_existing_destination_never_overwrites_work(self):
        proof = copy.deepcopy(self.proof)
        proof['loss_index']['matches'] = []
        with self.assertRaisesRegex(ValueError, 'loss-index'):
            self.build(proof)
        self.assertFalse(self.destination.exists())
        self.destination.mkdir(parents=True)
        sentinel = self.destination / 'user-file'
        sentinel.write_text('preserve this')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.build()
        self.assertEqual(sentinel.read_text(), 'preserve this')


if __name__ == '__main__':
    unittest.main()
