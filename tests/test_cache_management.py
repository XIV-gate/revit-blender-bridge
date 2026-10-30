import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from xivgate_revit_bridge.core import cache_management
from xivgate_revit_bridge.core.jobs import JobSession, JOB_MARKER, FINISH_MARKER


class CacheManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cache-maintenance-', dir=os.environ['XIVGATE_TEST_ROOT'])
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def finished(self):
        session = JobSession.create(self.root)
        (session.directory / 'data.bin').write_bytes(b'cache payload')
        session.finish()
        return session

    def test_saved_project_cache_is_deterministic_and_never_creates_directories(self):
        blend = self.root / 'scene.blend'
        blend.write_bytes(b'scene')
        expected = self.root / 'Data/Cache/RevitBridge'
        self.assertEqual(cache_management.project_cache(blend), expected)
        self.assertFalse(expected.exists())
        with self.assertRaises(ValueError):
            cache_management.project_cache('')
        override = self.root / 'explicit-cache'
        self.assertEqual(cache_management.project_cache('', str(override)), override)
        self.assertFalse(override.exists())
        with self.assertRaises(ValueError):
            cache_management.project_cache('', '../relative-cache')

    def test_job_markers_identify_owner_and_finished_sessions(self):
        session = JobSession.create(self.root)
        marker = json.loads((session.directory / JOB_MARKER).read_text())
        self.assertEqual(marker['session_id'], session.directory.name)
        self.assertEqual(marker['package_id'], 'xivgate_revit_bridge')
        self.assertFalse((session.directory / FINISH_MARKER).exists())
        session.finish()
        self.assertEqual(json.loads((session.directory / FINISH_MARKER).read_text())['session_id'], session.session_id)
        with self.assertRaises(ValueError):
            session.request('shutdown')

    def test_summary_and_clear_preserve_active_unfinished_and_unknown(self):
        finished = self.finished()
        active = self.finished()
        unfinished = JobSession.create(self.root)
        unknown = self.root / str(uuid.uuid4())
        unknown.mkdir()
        (unknown / 'user.blend').write_bytes(b'original')
        (self.root / 'unmanaged.txt').write_bytes(b'keep')
        summary = cache_management.cache_summary(self.root, active.directory)
        self.assertEqual(summary['eligible_jobs'], 1)
        expected_size = sum(path.stat().st_size for path in finished.directory.rglob('*') if path.is_file())
        self.assertEqual(summary['eligible_bytes'], expected_size)
        self.assertGreaterEqual(summary['total_bytes'], expected_size)
        result = cache_management.clear_completed(self.root, active.directory)
        self.assertEqual(result['deleted_jobs'], 1)
        self.assertEqual(result['deleted_bytes'], expected_size)
        self.assertFalse(finished.directory.exists())
        self.assertTrue(active.directory.exists())
        self.assertTrue(unfinished.directory.exists())
        self.assertEqual((unknown / 'user.blend').read_bytes(), b'original')
        self.assertTrue((self.root / 'unmanaged.txt').exists())

    def test_foreign_broken_or_mismatched_markers_never_authorize_deletion(self):
        for mutate in (lambda value: value.update(package_id='foreign'),
                       lambda value: value.update(session_id=str(uuid.uuid4())),
                       lambda value: value.update(schema=99)):
            session = self.finished()
            marker_path = session.directory / JOB_MARKER
            marker = json.loads(marker_path.read_text())
            mutate(marker)
            marker_path.write_text(json.dumps(marker))
        broken = self.finished()
        (broken.directory / FINISH_MARKER).write_text('{broken')
        fake = self.root / 'not-a-uuid'
        fake.mkdir()
        identity = {'schema': 1, 'package_id': 'xivgate_revit_bridge', 'session_id': fake.name}
        for name in (JOB_MARKER, FINISH_MARKER):
            (fake / name).write_text(json.dumps(identity))
        with patch.object(cache_management.shutil, 'rmtree') as delete:
            self.assertEqual(cache_management.clear_completed(self.root)['deleted_jobs'], 0)
        delete.assert_not_called()

    def test_external_active_path_and_reparse_descendants_stop_deletion(self):
        session = self.finished()
        with self.assertRaises(ValueError):
            cache_management.clear_completed(self.root, self.root.parent)
        original = Path.lstat

        def junction(path):
            if path == session.directory / 'input':
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            return original(path)

        with patch.object(Path, 'lstat', junction), patch.object(cache_management.shutil, 'rmtree') as delete:
            self.assertEqual(cache_management.cache_summary(self.root)['eligible_jobs'], 0)
            self.assertEqual(cache_management.clear_completed(self.root)['deleted_jobs'], 0)
        delete.assert_not_called()
        self.assertTrue(session.directory.exists())

    def test_recheck_before_delete_preserves_a_session_with_removed_finish_marker(self):
        session = self.finished()
        original = cache_management.cache_summary

        def changed(*args, **kwargs):
            summary = original(*args, **kwargs)
            (session.directory / FINISH_MARKER).unlink()
            return summary

        with patch.object(cache_management, 'cache_summary', side_effect=changed):
            result = cache_management.clear_completed(self.root)
        self.assertEqual(result['deleted_jobs'], 0)
        self.assertTrue(session.directory.exists())

    def test_nonexistent_cache_is_empty_and_finish_refuses_foreign_ownership(self):
        missing = self.root / 'does-not-exist'
        self.assertEqual(cache_management.cache_summary(missing)['total_bytes'], 0)
        self.assertEqual(cache_management.clear_completed(missing)['deleted_jobs'], 0)
        self.assertFalse(missing.exists())
        session = JobSession.create(self.root)
        (session.directory / JOB_MARKER).write_text('{}')
        with self.assertRaises(ValueError):
            session.finish()
        self.assertFalse((session.directory / FINISH_MARKER).exists())

    def test_completed_jobs_referenced_by_scene_are_preserved(self):
        protected = self.finished()
        removable = self.finished()
        reference = protected.directory / 'output' / 'image.png'
        reference.write_bytes(b'scene texture')
        outside = self.root.parent / 'unrelated-textures'
        summary = cache_management.cache_summary(self.root, protected_directories=[reference, outside])
        self.assertEqual(summary['eligible_jobs'], 1)
        self.assertEqual(summary['eligible_directories'], [str(removable.directory)])
        result = cache_management.clear_completed(self.root, protected_directories=(path for path in [reference, outside]))
        self.assertEqual(result['deleted_jobs'], 1)
        self.assertTrue(protected.directory.exists())
        self.assertEqual(reference.read_bytes(), b'scene texture')
        self.assertFalse(removable.directory.exists())


if __name__ == '__main__':
    unittest.main()
