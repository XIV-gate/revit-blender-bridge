import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile

from xivgate_revit_bridge.core.diagnostics import Timeline, export_bundle
from xivgate_revit_bridge.core.jobs import JobSession, atomic_write_json
from xivgate_revit_bridge.core.resources import parse_nvidia_memory


class DiagnosticsTests(unittest.TestCase):
    def test_loader_failure_is_exported_without_job_token(self):
        with tempfile.TemporaryDirectory(dir=os.environ['XIVGATE_TEST_ROOT']) as folder:
            root = Path(folder)
            session = JobSession.create(root)
            session.request('shutdown')
            (session.directory / 'loader.log').write_text('Loader validation failed', encoding='utf-8')
            (session.directory / 'loader_error.txt').write_text('Hash mismatch ' + session.token, encoding='utf-8')
            target = root / 'logs.zip'
            export_bundle(target, [session.directory], {})
            with zipfile.ZipFile(target) as archive:
                names = archive.namelist()
                self.assertTrue(any(name.endswith('loader.log') for name in names))
                error = archive.read(next(name for name in names if name.endswith('loader_error.txt')))
                self.assertIn(b'Hash mismatch', error)
                self.assertNotIn(session.token.encode(), error)

    def test_stage_durations_ignore_repeated_polls_and_idle_time(self):
        clock = [10.0]
        trace = Timeline(clock=lambda: clock[0])
        trace.transition('starting', request_id='scan')
        clock[0] = 14
        trace.transition('starting', request_id='scan')
        trace.transition('opening', request_id='scan')
        clock[0] = 20
        trace.transition('ready', request_id='scan', terminal=True)
        clock[0] = 40
        trace.transition('exporting', request_id='export')
        clock[0] = 43
        snapshot = trace.snapshot()
        self.assertEqual(len(snapshot['events']), 4)
        self.assertEqual([row['seconds'] for row in snapshot['stages']], [4, 6, 3])
        self.assertTrue(snapshot['stages'][-1]['running'])
        self.assertTrue(all(row['utc'] and row['local_time'] for row in snapshot['events']))

    def test_bundle_allowlist_redacts_token_excludes_model_and_keeps_tail(self):
        with tempfile.TemporaryDirectory(dir=os.environ['XIVGATE_TEST_ROOT']) as folder:
            root = Path(folder)
            session = JobSession.create(root)
            atomic_write_json(session.directory / 'request.json', dict(token='SECRET_SENTINEL'))
            (session.directory / 'worker.log').write_text('old\n' * 100 + 'SECRET_SENTINEL END', encoding='utf-8')
            (session.directory / 'input' / 'source.rvt').write_text('PRIVATE MODEL')
            output = session.directory / 'output' / 'test-request'
            output.mkdir()
            (output / 'geometry.bin').write_bytes(b'PRIVATE GEOMETRY')
            (output / 'conversion.log').write_text('conversion failed')
            (output / 'manifest.json').write_text(json.dumps(dict(summary={'triangles': 123}, prototypes=['PRIVATE MESH'])))
            path = root / 'logs.zip'
            result = export_bundle(path, [session.directory], dict(message='SECRET_SENTINEL'), per_file_limit=128)
            with zipfile.ZipFile(path) as archive:
                payloads = [archive.read(name) for name in archive.namelist()]
                combined = b' '.join(payloads)
                self.assertNotIn(b'SECRET_SENTINEL', combined)
                self.assertNotIn(b'PRIVATE', combined)
                self.assertIn(b'END', combined)
                self.assertIn(b'123', combined)
                self.assertTrue(any('conversion.log' in name for name in archive.namelist()))
                self.assertFalse(any('request.json' in name or name.endswith('.rvt') for name in archive.namelist()))
            self.assertTrue(result['truncated'])
            with self.assertRaises(FileExistsError):
                export_bundle(path, [], {})

    def test_unowned_job_is_not_read(self):
        with tempfile.TemporaryDirectory(dir=os.environ['XIVGATE_TEST_ROOT']) as folder:
            root = Path(folder)
            (root / 'worker.log').write_text('PRIVATE')
            result = export_bundle(root / 'logs.zip', [root], {})
            self.assertTrue(result['skipped'])
            with zipfile.ZipFile(root / 'logs.zip') as archive:
                self.assertFalse(any(name.endswith('worker.log') for name in archive.namelist()))

    def test_gpu_values_remain_unknown_when_driver_reports_na(self):
        rows = parse_nvidia_memory('GPU-1, NVIDIA Example, 32768, 20000, 12000\nGPU-2, Other, N/A, N/A, N/A')
        self.assertEqual(rows[0]['free_bytes'], 12000 * 1024**2)
        self.assertEqual(rows[0]['total_bytes'], 32768 * 1024**2)
        self.assertIsNone(rows[1]['free_bytes'])

    def test_saved_timings_remain_available_without_live_runtime(self):
        with tempfile.TemporaryDirectory(dir=os.environ['XIVGATE_TEST_ROOT']) as folder:
            root = Path(folder)
            session = JobSession.create(root)
            trace = Timeline()
            trace.transition('opening')
            trace.transition('ready', terminal=True)
            atomic_write_json(session.directory / 'client_timing.json', trace.snapshot())
            export_bundle(root / 'logs.zip', [session.directory], {})
            with zipfile.ZipFile(root / 'logs.zip') as archive:
                snapshot = json.loads(archive.read('diagnostics.json'))['snapshot']
                self.assertTrue(snapshot['timings_saved_snapshot'])
                self.assertEqual(snapshot['timings']['stages'][0]['stage'], 'opening')
