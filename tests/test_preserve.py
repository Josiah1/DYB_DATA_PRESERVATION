import hashlib
import errno
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import preserve


class PreserveTests(unittest.TestCase):
    def test_missing_minis_are_gaps_but_io_permissions_and_outages_stop(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            canonical = ["/dybfs/rec/P17B/rec/runs_0021000/runs_0021500/run_0021540/"
                         "recon.Neutrino.0021540.Physics.EH1-Merged.P17B-I._{:04d}.root".format(i)
                         for i in range(1, 6)]
            catalog = root / "run21540.list"
            catalog.write_text("\n".join(canonical) + "\n")
            with mock.patch.object(preserve, "MINI_ROOT", str(root / "mini")):
                minis = preserve.resolve_inputs(canonical)
                for path in minis:
                    Path(path).parent.mkdir(parents=True, exist_ok=True)
                    Path(path).write_bytes(b"root synthetic")
                Path(minis[1]).unlink()
                real_open = open

                def mini_only(path, *args, **kwargs):
                    self.assertFalse(str(path).startswith('/dybfs/'), "must never open an original")
                    return real_open(path, *args, **kwargs)

                with mock.patch('builtins.open', side_effect=mini_only) as opened:
                    marker = preserve.prepare_run(str(catalog), "P17B", "EH1", str(root / "stage"),
                                                  str(root / "code"), 2, require_registered=False,
                                                  skip_missing_minis=True)
                self.assertEqual((marker['input_count'], marker['processed_input_count'],
                                  marker['skipped_input_count']), (5, 4, 1))
                self.assertEqual(preserve.recorded_inputs(canonical, marker), [minis[0]] + minis[2:])
                self.assertEqual(marker['skipped_inputs'][0]['mini_check']['attempts'][-1]['errno'], errno.ENOENT)
                self.assertEqual(Path(marker['run_dir'], 'catalog.list').read_bytes(), catalog.read_bytes())
                changed = dict(marker, resolved_inputs=list(marker['resolved_inputs']))
                changed['resolved_inputs'][0] = canonical[0]
                with self.assertRaisesRegex(ValueError, 'exact mini/recon identities'):
                    preserve.recorded_inputs(canonical, changed)
                changed = dict(marker, skipped_inputs=[])
                with self.assertRaises(ValueError):
                    preserve.recorded_inputs(canonical, changed)
                with mock.patch('builtins.open', side_effect=[OSError(errno.EIO, 'transient'),
                                OSError(errno.EIO, 'transient'), real_open(minis[0], 'rb')]), \
                        mock.patch('preserve.time.sleep') as sleep:
                    check = preserve.probe_input(minis[0])
                self.assertEqual([a['status'] for a in check['attempts']], ['io_error', 'io_error', 'ok'])
                self.assertEqual(sleep.call_count, 2)
                with mock.patch('builtins.open', side_effect=OSError(errno.EIO, 'I/O error')), \
                        mock.patch('preserve.time.sleep'):
                    with self.assertRaisesRegex(ValueError, 'not confirmed missing'):
                        preserve.available_inputs(canonical)
                with mock.patch('builtins.open', side_effect=PermissionError(errno.EACCES, 'permission')):
                    with self.assertRaises(PermissionError):
                        preserve.available_inputs(canonical)
                Path(minis[0]).write_bytes(b'bad header')
                with self.assertRaisesRegex(ValueError, 'not confirmed missing'):
                    preserve.available_inputs(canonical)
                for path in minis:
                    Path(path).unlink(missing_ok=True)
                with mock.patch('builtins.open', side_effect=mini_only) as opened:
                    with self.assertRaisesRegex(ValueError, 'possible mini storage outage'):
                        preserve.available_inputs(canonical)
                self.assertEqual(opened.call_count, 6)

    def test_exact_original_fallback_is_explicit_and_frozen_on_resume(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = ["/dybfs/rec/P17B/rec/runs_0021000/runs_0021200/run_0021228/"
                        "recon.Neutrino.0021228.Physics.EH1-Merged.P17B-I._{:04d}.root".format(i)
                        for i in (1, 2)]
            minis = preserve.resolve_inputs(original)
            catalog = root / "run21228.list"
            catalog.write_text("\n".join(original) + "\n")

            def check(paths):
                if minis[1] in paths:
                    raise ValueError("missing second mini")

            with mock.patch("preserve.check_input_files", side_effect=check):
                with self.assertRaisesRegex(ValueError, "missing second mini"):
                    preserve.prepare_run(str(catalog), "P17B", "EH1", str(root / "strict"),
                                         str(root / "code"), 16, require_registered=False)
                marker = preserve.prepare_run(str(catalog), "P17B", "EH1", str(root / "stage"),
                                              str(root / "code"), 16, require_registered=False,
                                              input_source="mini-or-recon")
            expected = [minis[0], original[1]]
            self.assertEqual(preserve.recorded_inputs(original, marker), expected)
            self.assertEqual((Path(marker["run_dir"]) / "chunks/000000.list").read_text().splitlines(), expected)
            self.assertEqual(marker["reconstruction_fallbacks"][0]["recon"], original[1])
            with mock.patch("preserve.check_input_files", side_effect=AssertionError("must use frozen inputs")):
                resumed = preserve.prepare_run(str(catalog), "P17B", "EH1", str(root / "stage"),
                                               str(root / "code"), 16, require_registered=False,
                                               input_source="mini-or-recon")
            self.assertEqual(resumed["resolved_inputs"], expected)
            for wrong in ([], [minis[0], original[0]], [minis[0], "/other/version.root"]):
                with self.assertRaisesRegex(ValueError, "exact mini/recon identities"):
                    preserve.recorded_inputs(original, dict(marker, resolved_inputs=wrong))
            with mock.patch("preserve.check_input_files", side_effect=ValueError("unavailable")):
                with self.assertRaisesRegex(ValueError, "unavailable"):
                    preserve.prepare_run(str(catalog), "P17B", "EH1", str(root / "unavailable"),
                                         str(root / "code"), 16, require_registered=False,
                                         input_source="mini-or-recon")
            self.assertFalse((root / "unavailable/P17B/EH1/run21228").exists())

    def test_mount_identity_is_checked_before_creating_the_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            mount = Path(temporary)
            archive = mount / "data"
            with mock.patch("preserve.os.path.ismount", return_value=True), \
                    mock.patch("preserve.run_command", return_value=mock.Mock(stdout="disk-a fuseblk rw,nosuid\n")) as mount_info:
                with self.assertRaisesRegex(ValueError, "UUID"):
                    preserve.validate_local_mount(archive, mount, "disk-b")
                self.assertFalse(archive.exists())
                for description, message in (("disk-a ntfs3 rw\n", "NTFS3"),
                                             ("disk-a fuseblk ro\n", "read-write")):
                    mount_info.return_value.stdout = description
                    with self.assertRaisesRegex(ValueError, message):
                        preserve.validate_local_mount(archive, mount, "disk-a")
                    self.assertFalse(archive.exists())
                mount_info.return_value.stdout = "disk-a fuseblk rw,nosuid\n"
                self.assertEqual(preserve.validate_local_mount(archive, mount, "disk-a"), archive)

    def test_transfer_retries_resume_partial_and_never_publish_failed_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            remote = dict(period='P17B', hall='EH1', stem='run37945', run_dir='/stage/run37945',
                          artifact_bytes=10, catalog_sha256='catalog', schema_version=preserve.SCHEMA_VERSION,
                          selection=preserve.selection_record('on'), input_source='mini')
            final = root / 'P17B/EH1/run37945'
            incoming = final.with_name('.run37945.incoming')
            incoming.mkdir(parents=True)
            partial = incoming / 'partial.root'
            partial.write_bytes(b'previous partial')
            success = mock.Mock(returncode=0, stdout='', stderr='')
            disconnected = mock.Mock(returncode=12, stdout='partial transfer',
                                     stderr='rsync: connection unexpectedly closed')
            verified = dict(catalog_sha256='catalog', schema_version=preserve.SCHEMA_VERSION,
                            ad_selection='on', input_source='mini')

            def transfer(command, check):
                self.assertFalse(check)
                self.assertIn('--partial', command)
                self.assertIn('--timeout=120', command)
                self.assertEqual(command[-1], str(incoming) + '/')
                self.assertEqual(partial.read_bytes(), b'previous partial')
                return responses.pop(0)

            responses = [disconnected, success]
            with mock.patch('preserve.validate_local_mount', return_value=root) as mount_check, \
                    mock.patch('preserve.run_command', side_effect=transfer) as copy, \
                    mock.patch('preserve.time.sleep') as sleep, \
                    mock.patch('preserve.verify_local_run', side_effect=ValueError('checksum mismatch')):
                with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                    preserve.sync_run('host', remote, root, root, 'disk')
            self.assertEqual((copy.call_count, mount_check.call_count), (2, 4))
            sleep.assert_called_once_with(5)
            self.assertTrue(partial.is_file())
            self.assertFalse(final.exists())
            for response, attempts in ((disconnected, 3),
                    (mock.Mock(returncode=11, stdout='', stderr='Input/output error'), 1),
                    (mock.Mock(returncode=23, stdout='', stderr='source vanished'), 1),
                    (mock.Mock(returncode=12, stdout='', stderr='Permission denied; connection unexpectedly closed'), 1),
                    (mock.Mock(returncode=255, stdout='', stderr='Host key verification failed'), 1)):
                with mock.patch('preserve.validate_local_mount', return_value=root), \
                        mock.patch('preserve.run_command', return_value=response) as copy, \
                        mock.patch('preserve.time.sleep'), mock.patch('preserve.verify_local_run') as verify:
                    with self.assertRaises(RuntimeError):
                        preserve.sync_run('host', remote, root, root, 'disk')
                self.assertEqual(copy.call_count, attempts)
                verify.assert_not_called()
                self.assertTrue(partial.is_file())
                self.assertFalse(final.exists())
            with mock.patch('preserve.validate_local_mount', side_effect=[root, root, ValueError('UUID changed')]), \
                    mock.patch('preserve.run_command', return_value=disconnected) as copy, \
                    mock.patch('preserve.time.sleep'):
                with self.assertRaisesRegex(ValueError, 'UUID changed'):
                    preserve.sync_run('host', remote, root, root, 'disk')
            self.assertEqual(copy.call_count, 1)
            responses = [disconnected, success]
            with mock.patch('preserve.validate_local_mount', return_value=root), \
                    mock.patch('preserve.run_command', side_effect=transfer), mock.patch('preserve.time.sleep'), \
                    mock.patch('preserve.verify_local_run', return_value=verified):
                self.assertEqual(preserve.sync_run('host', remote, root, root, 'disk')['directory'], str(final))
            self.assertEqual((final / partial.name).read_bytes(), b'previous partial')
            self.assertFalse(incoming.exists())

    def test_catalog_backups_are_complete_and_used(self):
        report = preserve.verify_catalog_backups()
        self.assertEqual(
            (report["catalogs"], report["inputs"], report["bytes"]),
            (4009, 831334, 98092666),
        )
        catalog = dict(preserve.CATALOGS)["P17B"] + "/EH2/run46003.list"
        data, source, relative = preserve.read_catalog_data("P17B", "EH2", catalog)
        self.assertEqual(source, "repository-backup")
        self.assertEqual(relative, "P17B/EH2/run46003.list")
        self.assertEqual(
            hashlib.sha256(data).hexdigest(),
            "6d54b8b3e1bf63b27c9a1ba142e7f6558c97fc0b716a85dafdee127f594f3d3f",
        )

    def test_inventory_uses_the_complete_snapshot(self):
        records = preserve.inventory_records()
        self.assertEqual(len(records), 4009)
        self.assertEqual(sum(record["input_count"] for record in records), 831334)
        self.assertTrue(all(
            record["catalog_source"] == "repository-backup" for record in records))

    @mock.patch("preserve.check_input_files")
    def test_prepare_chunks_and_resume(self, check_inputs):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog = root / "run12345.list"
            inputs = ["/dybfs/rec/input-{}.root".format(index) for index in range(5)]
            catalog.write_text("\n".join(inputs) + "\n", encoding="utf-8")
            stage = root / "remote" / "stage"
            code = root / "remote" / "code"
            marker = preserve.prepare_run(
                str(catalog), "P17B", "EH1", str(stage), str(code), 2,
                require_registered=False, input_source="recon",
            )
            self.assertEqual(marker["input_count"], 5)
            self.assertEqual(marker["chunk_count"], 3)
            run_dir = Path(marker["run_dir"])
            self.assertTrue((run_dir / "reports").is_dir())
            self.assertEqual((run_dir / "chunks" / "000001.list").read_text().splitlines(),
                             inputs[2:4])
            self.assertEqual((run_dir / "ad-selection").read_text(), "off\n")
            self.assertEqual(marker["selection"], preserve.selection_record("off"))
            self.assertEqual(marker["output_prefix"], "preserved.0012345")
            self.assertEqual((run_dir / "output-prefix").read_text(), "preserved.0012345\n")
            self.assertEqual(
                preserve.prepare_run(str(catalog), "P17B", "EH1", str(stage), str(code), 2,
                                     require_registered=False, input_source="recon")["catalog_sha256"],
                marker["catalog_sha256"],
            )
            with self.assertRaisesRegex(ValueError, "does not match"):
                preserve.prepare_run(str(catalog), "P17B", "EH1", str(stage), str(code), 2,
                                     require_registered=False, ad_selection="on", input_source="recon")
            del marker["output_prefix"]
            (run_dir / preserve.MARKER_NAME).write_text(json.dumps(marker))
            with self.assertRaisesRegex(ValueError, "does not match"):
                preserve.prepare_run(str(catalog), "P17B", "EH1", str(stage), str(code), 2,
                                     require_registered=False, input_source="recon")
            for run, prefix in ((12345, "preserved.0012346"), (10000000, "preserved.10000000")):
                with self.assertRaisesRegex(ValueError, "run-number convention"):
                    preserve.output_prefix({"stem": "run12345", "run_number": run,
                                            "output_prefix": prefix})

    def test_mini_inputs_preserve_catalog_identity_and_fail_on_missing_files(self):
        canonical = ("/dybfs/rec/P21A/rec/runs_0082000/runs_0082300/run_0082379/"
                     "recon.Neutrino.0082379.Physics.EH3-Merged.P21A-I._0092.root")
        expected = (preserve.MINI_ROOT + "/runs_0082000/runs_0082300/run_0082379/"
                    "minirecon.Neutrino.0082379.Physics.EH3-Merged.P21A-I._0092.root")
        self.assertEqual(preserve.resolve_inputs([canonical]), [expected])
        self.assertEqual(preserve.resolve_inputs([canonical], "recon"), [canonical])
        self.assertEqual(preserve.resolve_inputs([canonical.replace('Neutrino', 'NoTag')]),
                         [expected.replace('Neutrino', 'NoTag')])
        for malformed in (canonical.replace('/rec/P21A/', '/rec/P17B/'),
                          canonical.replace('/run_0082379/', '/run_0082380/'),
                          canonical.replace('/rec/runs_', '/rec/../runs_')):
            with self.assertRaisesRegex(ValueError, "cannot map"):
                preserve.resolve_inputs([malformed])
        args = preserve.build_parser().parse_args([
            "run", "--local-root", "/Volumes/test/data", "--mount-root", "/Volumes/test"])
        self.assertEqual(args.input_source, "mini")
        self.assertEqual(args.ad_selection, "off")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog = root / "run82379.list"
            catalog.write_text(canonical + "\n")
            stage, code = root / "stage", root / "code"
            with mock.patch.object(preserve, "MINI_ROOT", str(root / "mini")):
                mini = Path(preserve.resolve_inputs([canonical])[0])
                with self.assertRaisesRegex(ValueError, "1 unavailable inputs"):
                    preserve.prepare_run(str(catalog), "P21A", "EH3", str(stage), str(code), 16,
                                         require_registered=False)
                self.assertFalse(stage.exists())
                mini.parent.mkdir(parents=True)
                mini.write_bytes(b"not ROOT")
                with self.assertRaisesRegex(ValueError, "not a ROOT file header"):
                    preserve.check_input_files([str(mini)])
                mini.write_bytes(b"root synthetic header")
                marker = preserve.prepare_run(str(catalog), "P21A", "EH3", str(stage), str(code), 16,
                                              require_registered=False)
                run_dir = Path(marker["run_dir"])
                self.assertEqual(marker["input_source"], "mini")
                self.assertEqual((run_dir / "catalog.list").read_bytes(), catalog.read_bytes())
                self.assertEqual((run_dir / "chunks/000000.list").read_text(), str(mini) + "\n")
                with self.assertRaisesRegex(ValueError, "does not match"):
                    preserve.prepare_run(str(catalog), "P21A", "EH3", str(stage), str(code), 16,
                                         require_registered=False, input_source="recon")

    def test_local_verification_detects_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "archive"
            run_dir = archive / "P17B" / "EH1" / "chunk"
            output_dir = run_dir / "outputs"
            output_dir.mkdir(parents=True)
            output = output_dir / "chunk.slim.000000.root"
            output.write_bytes(b"verified bytes")
            catalog_path = ("/dybfs/rec/P17B/rec/runs_0021000/runs_0021200/run_0021221/"
                            "recon.Neutrino.0021221.Physics.EH1-Merged.P17B-I._0001.root")
            catalog = run_dir / "catalog.list"
            catalog.write_text(catalog_path + "\n", encoding="utf-8")
            duration = run_dir / "durations.tsv"
            duration.write_text(
                "\t".join(preserve.DURATION_COLUMNS) + "\n" +
                "0\t0\t0\t{}\t1\t14\t100\t100\t102\t350\t2.00000025\t1.25\t1.0\t1"
                "\t0\t0\t0\t0\t0\t0\t0\t0\n".format(catalog_path), encoding="utf-8")
            (run_dir / "SHA256SUMS").write_text(
                "{}  outputs/chunk.slim.000000.root\n{}  durations.tsv\n".format(
                    hashlib.sha256(output.read_bytes()).hexdigest(),
                    hashlib.sha256(duration.read_bytes()).hexdigest()), encoding="utf-8")
            (run_dir / ".complete").write_text(
                hashlib.sha256(catalog.read_bytes()).hexdigest() + "\n", encoding="utf-8")
            (run_dir / "run.json").write_text(json.dumps({
                "complete": True,
                "schema_version": preserve.SCHEMA_VERSION,
                "checksums": "SHA256SUMS",
                "catalog_sha256": hashlib.sha256(catalog.read_bytes()).hexdigest(),
                "input_count": 1,
                "files_per_job": 16,
                "stem": "chunk",
                "chunk_count": 1,
                "duration_file": "durations.tsv",
                "duration_rows": 1,
                "source_entries": 1,
                "source_kept_entries": 1,
                "selection": preserve.selection_record("on"),
                "output_count": 1,
                "output_bytes": output.stat().st_size,
                "artifact_count": 2,
                "artifact_bytes": output.stat().st_size + duration.stat().st_size,
            }), encoding="utf-8")
            verified = preserve.verify_local_run(run_dir)
            self.assertEqual((verified["files"], verified["kept_entries"], verified["ad_selection"]),
                             (2, 1, "on"))
            (archive / "catalog-inventory.json").write_text(json.dumps({
                "kind": "dyb-preserve-catalog-inventory",
                "schema_version": preserve.SCHEMA_VERSION,
                "records": [{
                    "period": "P17B", "hall": "EH1", "stem": "chunk",
                    "catalog": "/fixed/EH1/chunk.list",
                    "catalog_sha256": hashlib.sha256(catalog.read_bytes()).hexdigest(),
                    "input_count": 1,
                }],
            }), encoding="utf-8")
            everything = preserve.verify_all(archive)
            self.assertEqual((everything["runs"], everything["kept_entries"],
                              everything["ad_selection"]), (1, 1, "on"))
            self.assertEqual(everything["input_source"], "recon")
            result = preserve.load_json(run_dir / "run.json")
            remote_result = dict(result, input_source="mini", period="P17B", hall="EH1")
            with mock.patch("preserve.validate_local_mount", return_value=archive):
                with self.assertRaisesRegex(ValueError, "does not match the remote run"):
                    preserve.sync_run("unused", remote_result, archive, archive)
            mini_path = preserve.resolve_inputs([catalog_path])[0]
            duration.write_text(duration.read_text().replace(catalog_path, mini_path))
            result.update(input_source="mini", artifact_bytes=output.stat().st_size + duration.stat().st_size)
            preserve.atomic_json(run_dir / "run.json", result)
            (run_dir / "SHA256SUMS").write_text(
                "{}  outputs/chunk.slim.000000.root\n{}  durations.tsv\n".format(
                    preserve.sha256_file(output), preserve.sha256_file(duration)))
            snapshot = preserve.load_json(archive / "catalog-inventory.json")
            with self.assertRaisesRegex(ValueError, "differs from its catalog inventory"):
                preserve.verify_all(archive)
            snapshot["input_source"] = "mini"
            preserve.atomic_json(archive / "catalog-inventory.json", snapshot)
            self.assertEqual(preserve.verify_all(archive)["input_source"], "mini")
            with self.assertRaisesRegex(ValueError, "input source differs"):
                preserve.save_inventory_snapshot(archive, snapshot["records"], "recon")
            preserve.save_inventory_snapshot(archive, snapshot["records"], "mini-or-recon")
            self.assertEqual(preserve.verify_all(archive)["input_source"], "mini-or-recon")
            result.update(input_source="mini-or-recon", resolved_inputs=[mini_path])
            preserve.atomic_json(run_dir / "run.json", result)
            self.assertEqual(preserve.verify_all(archive)["input_source"], "mini-or-recon")
            before = (run_dir / 'run.json').read_bytes()
            preserve.save_inventory_snapshot(archive, snapshot['records'], 'mini', skip_missing_minis=True)
            self.assertEqual((run_dir / 'run.json').read_bytes(), before)
            updated = preserve.load_json(archive / 'catalog-inventory.json')
            self.assertEqual(preserve.archive_input_sources(updated, 'P17B/EH1/chunk'), {'mini-or-recon'})
            self.assertEqual(preserve.archive_input_sources(updated, 'P17B/EH1/newrun'), {'mini'})
            self.assertEqual(preserve.verify_all(archive)['input_sources_present'], ['mini-or-recon'])
            with self.assertRaisesRegex(ValueError, 'mini-only'):
                preserve.save_inventory_snapshot(archive, snapshot['records'], 'mini-or-recon')
            result["resolved_inputs"] = ["/wrong/source.root"]
            preserve.atomic_json(run_dir / "run.json", result)
            with self.assertRaisesRegex(ValueError, "exact mini/recon identities"):
                preserve.verify_local_run(run_dir)
            result["input_source"] = "recon"
            preserve.atomic_json(run_dir / "run.json", result)
            with self.assertRaisesRegex(ValueError, "order does not match"):
                preserve.verify_local_run(run_dir)
            result["input_source"] = "mini"
            preserve.atomic_json(run_dir / "run.json", result)
            output.write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                preserve.verify_local_run(run_dir)

    @mock.patch("preserve.check_input_files")
    def test_duration_reports_are_aggregated_in_catalog_order(self, check_inputs):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog = root / "run12345.list"
            inputs = ["/dybfs/rec/input-{}.root".format(index) for index in range(3)]
            catalog.write_text("\n".join(inputs) + "\n", encoding="utf-8")
            marker = preserve.prepare_run(
                str(catalog), "P17B", "EH1", str(root / "stage"), str(root / "code"), 2,
                require_registered=False, input_source="recon", ad_selection="on",
            )
            run_dir = Path(marker["run_dir"])
            prefix = marker["output_prefix"]
            header = "\t".join(preserve.SOURCE_REPORT_COLUMNS) + "\n"
            for chunk_index, paths in enumerate((inputs[:2], inputs[2:])):
                rows = ["{}\t{}\t10\t20\t100\t0\t101\t0\t1\t0.5\t0.4\t7\t1\t2\t0\t0\t0\t0\t0\t0".format(
                    source_index, path) for source_index, path in enumerate(paths)]
                (run_dir / "reports" / "{}.slim.{:06d}.sources.tsv".format(
                    prefix, chunk_index)).write_text(header + "\n".join(rows) + "\n", encoding="utf-8")
            durations = preserve.aggregate_duration_reports(run_dir, marker, prefix)
            duration = durations["path"]
            self.assertEqual(
                (durations["rows"], durations["entries"], durations["input_bytes"],
                 durations["kept"], durations["chunk_totals"]),
                (3, 30, 60, 21, [(14, 20, 2), (7, 10, 1)]))
            table = preserve.validate_duration_table(duration, inputs, 2)
            self.assertEqual((table["rows"], table["kept"], table["chunk_totals"]),
                             (3, 21, [(14, 20, 2), (7, 10, 1)]))
            lines = duration.read_text(encoding="utf-8").splitlines()
            original = list(lines)
            fields = lines[1].split("\t")
            fields[10] = "1.5"
            lines[1] = "\t".join(fields)
            duration.write_text("\n".join(lines) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "does not match its timestamps"):
                preserve.validate_duration_table(duration, inputs, 2)
            fields = original[1].split("\t")
            fields[13] = "8"
            lines = list(original)
            lines[1] = "\t".join(fields)
            duration.write_text("\n".join(lines) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "selection accounting"):
                preserve.validate_duration_table(duration, inputs, 2)
            duration.write_text("\n".join(original) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "selection accounting"):
                preserve.validate_duration_table(duration, inputs, 2, mode="off")
            (run_dir / "chunks/000000.list").write_text("/wrong/input.root\n")
            with self.assertRaisesRegex(ValueError, "chunk paths do not match"):
                preserve.aggregate_duration_reports(run_dir, marker, prefix)

    def test_selection_records_and_verify_lines(self):
        self.assertEqual(preserve.selection_mode(preserve.selection_record("off")), "off")
        with self.assertRaisesRegex(ValueError, "selection record"):
            preserve.selection_mode({"mode": "on"})
        fields = ["0", "/dybfs/rec/a.root", "10", "20", "100", "0", "101", "0", "1", "0.5", "0.4",
                  "10", "1", "2", "0", "0", "0", "0", "0", "0"]
        self.assertEqual(preserve.validate_source_fields(fields, fields[1], 0, "off"), (10, 20, 10))
        with self.assertRaisesRegex(ValueError, "selection accounting"):
            preserve.validate_source_fields(fields, fields[1], 0, "on")
        line = "VERIFY entries=14 source_entries=20 sources=2 selection=on bytes=99\n"
        self.assertEqual(preserve.check_verified_chunk(line, (14, 20, 2), "on")["selection"], "on")
        with self.assertRaisesRegex(ValueError, "does not match its timing rows"):
            preserve.check_verified_chunk(line, (14, 20, 2), "off")
        with self.assertRaisesRegex(ValueError, "does not match its timing rows"):
            preserve.check_verified_chunk(line, (13, 20, 2), "on")
        with self.assertRaisesRegex(ValueError, "did not report"):
            preserve.parse_verify_output("VERIFY entries=14 sources=2\n")

    @mock.patch("preserve.check_input_files")
    def test_cleanup_is_marker_and_checksum_gated(self, check_inputs):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog = root / "run54321.list"
            catalog.write_text("/dybfs/rec/input.root\n", encoding="utf-8")
            stage = root / "remote" / "stage"
            code = root / "remote" / "code"
            marker = preserve.prepare_run(
                str(catalog), "P17B", "EH1", str(stage), str(code), 1,
                require_registered=False, input_source="recon",
            )
            run_dir = Path(marker["run_dir"])
            with self.assertRaisesRegex(ValueError, "not been finalized"):
                preserve.cleanup_run(run_dir, stage, marker["catalog_sha256"])
            (run_dir / "run.json").write_text(json.dumps({
                "complete": True,
                "schema_version": preserve.SCHEMA_VERSION,
                "catalog_sha256": marker["catalog_sha256"],
            }) + "\n", encoding="utf-8")
            (run_dir / ".complete").write_text(marker["catalog_sha256"] + "\n", encoding="utf-8")
            (run_dir / "SHA256SUMS").write_text("pilot checksum marker\n", encoding="utf-8")
            preserve.cleanup_run(run_dir, stage, marker["catalog_sha256"])
            self.assertFalse(run_dir.exists())


if __name__ == "__main__":
    unittest.main()
