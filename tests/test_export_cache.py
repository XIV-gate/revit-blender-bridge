import copy
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import stat

from xivgate_revit_bridge.core import export_cache


class ExportCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='export-cache-', dir=os.environ['XIVGATE_TEST_ROOT'])
        self.root = Path(self.temporary.name)
        self.index = self.root / 'last_export.json'
        self.source = self.root / 'original.rvt'
        self.source.write_bytes(b'original source')
        self.output = self.root / 'job' / 'output'
        self.output.mkdir(parents=True)
        self.binary = struct.pack('<24fq', *range(24), 42)
        (self.output / 'geometry.bin').write_bytes(self.binary)
        (self.output / 'texture.png').write_bytes(b'image bytes')
        self.manifest = self.output / 'manifest.json'
        self.payload = {'schema': 1, 'units': 'metres', 'up': 'Z', 'cache_reusable': True, 'cache_dependencies': [],
                        'prototypes': [{'id': 1, 'offset': 0, 'bytes': 104, 'triangles': 1,
                                        'sha256': hashlib.sha256(self.binary).hexdigest()}],
                        'occurrences': [{'id': '9', 'prototype': 1, 'transform': [1, 0, 0, 0, 1, 0, 0, 0, 1, 2, 3, 4]}],
                        'materials': {'42': {'name': 'Paint', 'base_texture': 'texture.png'}},
                        'source': {'sha256': hashlib.sha256(b'original source').hexdigest()}}
        self.write_manifest()
        self.signature = self.sign()

    def tearDown(self):
        self.temporary.cleanup()

    def write_manifest(self):
        self.manifest.write_text(json.dumps(self.payload), encoding='utf-8')

    def sign(self, ids=('9',), detail='medium', worker='2027', version='0.3.1'):
        return export_cache.export_signature(self.source, ids, detail, worker, version)

    def test_signature_binds_content_complete_selection_detail_and_worker(self):
        first = self.sign(('9', '10', '9'))
        self.assertEqual(first, self.sign(('10', '9')))
        self.assertNotEqual(first, self.sign(('9',)))
        self.assertNotEqual(self.sign(), self.sign(()))
        self.assertNotEqual(self.sign(), self.sign(detail='fine'))
        self.assertNotEqual(self.sign(), self.sign(worker='2026'))
        self.assertNotEqual(self.sign(), self.sign(version='0.3.2'))
        old = self.sign()
        self.source.write_bytes(b'changed source')
        self.assertNotEqual(old, self.sign())
        self.assertRegex(first, r'^[0-9a-f]{64}$')

    def test_signature_binds_source_location_even_when_rvt_bytes_match(self):
        other_directory = self.root / 'other-source'
        other_directory.mkdir()
        other = other_directory / self.source.name
        other.write_bytes(self.source.read_bytes())
        self.assertNotEqual(self.sign(), export_cache.export_signature(other, ('9',), 'medium', '2027', '0.3.1'))
        normalized = self.source.parent / '.' / self.source.name
        self.assertEqual(self.sign(), export_cache.export_signature(normalized, ('9',), 'medium', '2027', '0.3.1'))

    def test_external_texture_dependency_is_read_only_and_changed_original_invalidates(self):
        cache = self.root / 'owned-cache'
        cache.mkdir()
        index = cache / 'last_export.json'
        output = cache / 'output'
        output.mkdir()
        for path in self.output.iterdir():
            (output / path.name).write_bytes(path.read_bytes())
        source_bitmap = self.root / 'external-original.png'
        source_bitmap.write_bytes(b'original bitmap')
        metadata = self.payload.copy()
        metadata['cache_dependencies'] = [{'path': str(source_bitmap), 'sha256': hashlib.sha256(source_bitmap.read_bytes()).hexdigest(),
                                           'size': source_bitmap.stat().st_size}]
        manifest = output / 'manifest.json'
        manifest.write_text(json.dumps(metadata))
        export_cache.record_export(index, self.signature, manifest)
        self.assertEqual(export_cache.lookup_export(index, self.signature), manifest)
        self.assertEqual(source_bitmap.read_bytes(), b'original bitmap')
        source_bitmap.write_bytes(b'changed bitmap')
        self.assertIsNone(export_cache.lookup_export(index, self.signature))
        with self.assertRaises(export_cache.CacheError):
            export_cache.record_export(index, self.signature, manifest)
        self.assertEqual(source_bitmap.read_bytes(), b'changed bitmap')

    def test_legacy_unsafe_and_incomplete_dependency_manifests_are_not_reused(self):
        original = copy.deepcopy(self.payload)
        for changes in [{'cache_reusable': False}, {'cache_dependencies': None},
                        {'cache_dependencies': [{'path': 'relative.png', 'sha256': 'a' * 64, 'size': 1}]},
                        {'cache_dependencies': [{'path': str(self.source), 'sha256': 'A' * 64, 'size': 1}]}]:
            self.payload = copy.deepcopy(original)
            self.payload.update(changes)
            self.write_manifest()
            with self.subTest(changes=changes), self.assertRaises(export_cache.CacheError):
                export_cache.record_export(self.index, self.signature, self.manifest)
            self.assertIsNone(export_cache.lookup_export(self.index, self.signature))
        self.payload = original
        self.payload.pop('cache_reusable')
        self.payload.pop('cache_dependencies')
        self.write_manifest()
        with self.assertRaises(export_cache.CacheError):
            export_cache.record_export(self.index, self.signature, self.manifest)

    def test_record_and_lookup_verified_relative_payloads(self):
        before = self.source.read_bytes()
        export_cache.record_export(self.index, self.signature, self.manifest)
        self.assertEqual(export_cache.lookup_export(self.index, self.signature), self.manifest)
        self.assertIsNone(export_cache.lookup_export(self.index, 'a' * 64))
        index = json.loads(self.index.read_text())
        self.assertNotIn(str(self.root), self.index.read_text())
        self.assertEqual(index['signature'], self.signature)
        self.assertEqual(set(index['files']), {'job/output/manifest.json', 'job/output/geometry.bin', 'job/output/texture.png'})
        self.assertEqual(self.source.read_bytes(), before)

    def test_changed_manifest_materials_geometry_or_texture_invalidate_entry(self):
        for name in ('manifest.json', 'geometry.bin', 'texture.png'):
            target = self.output / name
            original = target.read_bytes()
            export_cache.record_export(self.index, self.signature, self.manifest)
            target.write_bytes(original + b'changed')
            self.assertIsNone(export_cache.lookup_export(self.index, self.signature), name)
            target.write_bytes(original)
        export_cache.record_export(self.index, self.signature, self.manifest)
        self.payload['materials']['42']['name'] = 'Changed material'
        self.write_manifest()
        self.assertIsNone(export_cache.lookup_export(self.index, self.signature))

    def test_missing_payload_invalidates_lookup_and_cannot_be_recorded(self):
        export_cache.record_export(self.index, self.signature, self.manifest)
        (self.output / 'texture.png').unlink()
        self.assertIsNone(export_cache.lookup_export(self.index, self.signature))
        with self.assertRaises(export_cache.CacheError):
            export_cache.record_export(self.index, self.signature, self.manifest)

    def test_all_worker_material_texture_references_are_verified(self):
        extra = self.output / 'normal.png'
        extra.write_bytes(b'normal image')
        self.payload['materials']['42']['textures'] = {'diffuse': 'texture.png', 'normal': 'normal.png'}
        self.write_manifest()
        export_cache.record_export(self.index, self.signature, self.manifest)
        self.assertIn('job/output/normal.png', json.loads(self.index.read_text())['files'])
        extra.write_bytes(b'changed normal image')
        self.assertIsNone(export_cache.lookup_export(self.index, self.signature))
        extra.unlink()
        with self.assertRaises(export_cache.CacheError):
            export_cache.record_export(self.index, self.signature, self.manifest)

    def test_invalid_index_or_signature_is_cache_miss(self):
        for value in ('{partial', '[]', '{}', '\ufeff{"schema":99}'):
            self.index.write_text(value, encoding='utf-8')
            self.assertIsNone(export_cache.lookup_export(self.index, self.signature))
        self.assertIsNone(export_cache.lookup_export(self.index, 'not a signature'))

    def test_corrupt_geometry_is_not_recorded(self):
        (self.output / 'geometry.bin').write_bytes(b'wrong')
        with self.assertRaises(export_cache.CacheError):
            export_cache.record_export(self.index, self.signature, self.manifest)
        self.assertFalse(self.index.exists())

    def test_record_rejects_wrong_schema_invalid_materials_and_missing_geometry_reference(self):
        for changed in ({'schema': 2}, {'materials': []}, {'prototypes': []}, {'occurrences': []},
                        {'binary_file': 'missing.bin'}):
            original = copy.deepcopy(self.payload)
            self.payload.update(changed)
            self.write_manifest()
            with self.subTest(changed=changed), self.assertRaises(export_cache.CacheError):
                export_cache.record_export(self.index, self.signature, self.manifest)
            self.payload = original
        self.assertFalse(self.index.exists())

    def test_manifest_references_cannot_escape_output_or_use_noncanonical_paths(self):
        for reference in ('../original.rvt', str(self.source), 'sub\\texture.png', './texture.png'):
            self.payload['materials']['42']['base_texture'] = reference
            self.write_manifest()
            with self.subTest(reference=reference), self.assertRaises(export_cache.CacheError):
                export_cache.record_export(self.index, self.signature, self.manifest)
        outside = self.root.parent / 'outside-manifest.json'
        with self.assertRaises(export_cache.CacheError):
            export_cache.record_export(self.index, self.signature, outside)

    def test_tampered_index_cannot_escape_root_or_omit_payload(self):
        export_cache.record_export(self.index, self.signature, self.manifest)
        original = json.loads(self.index.read_text())
        tampered = copy.deepcopy(original)
        tampered['files'].pop('job/output/texture.png')
        self.index.write_text(json.dumps(tampered))
        self.assertIsNone(export_cache.lookup_export(self.index, self.signature))
        tampered = copy.deepcopy(original)
        tampered['manifest'] = '../outside.json'
        self.index.write_text(json.dumps(tampered))
        self.assertIsNone(export_cache.lookup_export(self.index, self.signature))

    def test_atomic_record_failure_preserves_previous_index(self):
        export_cache.record_export(self.index, self.signature, self.manifest)
        previous = self.index.read_bytes()
        with patch('xivgate_revit_bridge.core.jobs.os.replace', side_effect=PermissionError('busy')):
            with self.assertRaises(export_cache.CacheError):
                export_cache.record_export(self.index, 'b' * 64, self.manifest)
        self.assertEqual(self.index.read_bytes(), previous)
        self.assertFalse(any(self.root.glob('*.tmp')))

    def test_symlink_payload_and_cache_root_are_rejected(self):
        target = self.output / 'texture.png'
        target.unlink()
        try:
            target.symlink_to(self.source)
        except OSError:
            self.skipTest('Creating symbolic links is unavailable in this Windows session')
        with self.assertRaises(export_cache.CacheError):
            export_cache.record_export(self.index, self.signature, self.manifest)
        link = self.root / 'linked-root'
        link.symlink_to(self.output, target_is_directory=True)
        with self.assertRaises(export_cache.CacheError):
            export_cache.record_export(link / 'index.json', self.signature, self.manifest)

    def test_reparse_point_on_any_cache_parent_is_rejected(self):
        actual_lstat = Path.lstat

        def reparse(path):
            if path == self.root / 'job':
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            return actual_lstat(path)

        with patch.object(Path, 'lstat', reparse):
            with self.assertRaises(export_cache.CacheError):
                export_cache.record_export(self.index, self.signature, self.manifest)
            self.assertIsNone(export_cache.lookup_export(self.index, self.signature))


if __name__ == '__main__':
    unittest.main()
