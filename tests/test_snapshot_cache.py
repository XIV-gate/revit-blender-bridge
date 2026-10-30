import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from xivgate_revit_bridge.core import export_cache
from xivgate_revit_bridge.core.jobs import JobSession, JOB_MARKER


class SnapshotCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='snapshot-', dir=os.environ['XIVGATE_TEST_ROOT'])
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.session = JobSession.create(self.root)
        request = self.session.request('export', selection_ids=['9'])
        self.output = Path(request['output_dir'])
        self.manifest = self.output / 'manifest.json'
        self.source_hash = hashlib.sha256(b'original RVT').hexdigest()
        self.binary = struct.pack('<24fq', *range(24), 42)
        (self.output / 'geometry.bin').write_bytes(self.binary)
        self.texture_bytes = b'copied texture'
        self.texture = self.output / (hashlib.sha256(self.texture_bytes).hexdigest() + '.png')
        self.texture.write_bytes(self.texture_bytes)
        self.value = dict(schema=1, units='metres', up='Z', source={'sha256': self.source_hash},
            cache_reusable=False,
            cache_dependencies=[{'path': str(self.root / 'missing-original.png'), 'sha256': '0' * 64, 'size': 1}],
            prototypes=[dict(id=1, offset=0, bytes=104, triangles=1, sha256=hashlib.sha256(self.binary).hexdigest())],
            occurrences=[dict(id='9', prototype=1, transform=[1, 0, 0, 0, 1, 0, 0, 0, 1, 2, 3, 4])],
            materials={'42': dict(base_texture=self.texture.name, textures={'diffuse': self.texture.name})})
        self.save()

    def save(self):
        self.manifest.write_text(json.dumps(self.value), encoding='utf-8')

    def lookup(self, **kwargs):
        return export_cache.lookup_snapshot(kwargs.get('root', self.root),
            kwargs.get('manifest', self.manifest), kwargs.get('source_hash', self.source_hash))

    def test_explicit_snapshot_ignores_only_original_dependency_reusability(self):
        with patch.object(export_cache, '_verify_dependencies', side_effect=AssertionError('Must not read originals')):
            self.assertEqual(self.lookup(), self.manifest)
        with self.assertRaises(export_cache.CacheError):
            export_cache._payloads(self.root, self.manifest)
        self.assertIsNone(export_cache.lookup_export(self.root / 'absent-index.json', '1' * 64))

    def test_corrupted_binary_or_missing_texture_is_a_miss(self):
        (self.output / 'geometry.bin').write_bytes(b'X' + self.binary[1:])
        self.assertIsNone(self.lookup())
        (self.output / 'geometry.bin').write_bytes(self.binary)
        self.texture.unlink()
        self.assertIsNone(self.lookup())

    def test_changed_hash_named_texture_is_a_miss(self):
        self.texture.write_bytes(b'changed texture')
        self.assertIsNone(self.lookup())

    def test_unused_binary_bytes_and_damaged_manifest_are_rejected(self):
        (self.output / 'geometry.bin').write_bytes(self.binary + b'unused')
        self.assertIsNone(self.lookup())
        (self.output / 'geometry.bin').write_bytes(self.binary)
        self.manifest.write_text('{broken JSON')
        self.assertIsNone(self.lookup())

    def test_noncanonical_revision_and_missing_marker_are_rejected(self):
        invalid = self.output.parent / 'not-a-uuid' / 'manifest.json'
        with patch.object(export_cache, '_payloads', side_effect=AssertionError('Invalid revision reached reader')):
            self.assertIsNone(self.lookup(manifest=invalid))
        (self.session.directory / JOB_MARKER).unlink()
        self.assertIsNone(self.lookup())

    def test_source_identity_is_required_and_exact(self):
        for source_hash in ('invalid', 'F' * 64, '0' * 64, None):
            self.assertIsNone(self.lookup(source_hash=source_hash))
        for source in ({}, {'sha256': '0' * 64}, None, 'wrong'):
            self.value['source'] = source
            self.save()
            self.assertIsNone(self.lookup())

    def test_unknown_job_and_noncanonical_revision_are_rejected(self):
        marker = self.session.directory / JOB_MARKER
        marker.write_text('{}')
        self.assertIsNone(self.lookup())
        self.assertIsNone(self.lookup(manifest=self.output / 'other.json'))

    def test_outside_and_relative_paths_are_rejected_before_reading_payload(self):
        with patch.object(export_cache, '_payloads', side_effect=AssertionError('Unsafe path reached payload reader')):
            self.assertIsNone(self.lookup(root=self.output))
            self.assertIsNone(self.lookup(manifest=self.root.parent / 'manifest.json'))
            self.assertIsNone(self.lookup(manifest='manifest.json'))
            self.assertIsNone(self.lookup(root='.'))

    def test_texture_escape_is_rejected_without_reading_external_file(self):
        self.value['materials']['42']['base_texture'] = '../../../outside.png'
        self.save()
        self.assertIsNone(self.lookup())


if __name__ == '__main__':
    unittest.main()
