import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile

from tools.build_package import build


class PackageTests(unittest.TestCase):
    def test_all_worker_variants_and_dependencies_are_verified(self):
        from xivgate_revit_bridge.core.updates import validate_archive
        with tempfile.TemporaryDirectory(dir=os.environ['XIVGATE_TEST_ROOT']) as directory:
            root = Path(directory)
            workers = {}
            for key in ('2024', '2025', '2025-net10', '2026', '2026-net10', '2027'):
                folder = root / key; folder.mkdir()
                worker = folder / 'XIVgate.RevitBridge.Worker.dll'
                worker.write_bytes(('test-only-' + key).encode())
                workers[key] = worker
            from xivgate_revit_bridge.core.updates import _NET48_DEPENDENCIES
            for name in _NET48_DEPENDENCIES:
                (workers['2024'].parent / name).write_bytes(b'dependency-fixture')
            target = root / 'multi.zip'
            build(workers, target)
            with zipfile.ZipFile(target) as archive:
                manifest = json.loads(archive.read('xivgate_revit_bridge/PACKAGE_MANIFEST.json'))
            result = validate_archive(target, manifest['version'], (4, 2, 0))
            self.assertEqual(set(result.manifest['workers']), set(workers))
            self.assertTrue(any(name.endswith('/System.Text.Json.dll') for name in result.manifest['runtime_files']))

    def test_package_manifest_and_versioned_worker_match_every_payload(self):
        with tempfile.TemporaryDirectory(dir=os.environ['XIVGATE_TEST_ROOT']) as directory:
            root = Path(directory)
            worker = root / 'XIVgate.RevitBridge.Worker.dll'
            worker.write_bytes(b'explicit packaging fixture, not a Revit assembly')
            target = root / 'package.zip'
            build(worker, target)
            with zipfile.ZipFile(target) as archive:
                prefix = 'xivgate_revit_bridge/'
                manifest = json.loads(archive.read(prefix + 'PACKAGE_MANIFEST.json'))
                version = archive.read(prefix + 'VERSION').decode().strip()
                self.assertEqual(manifest['id'], 'xivgate_revit_bridge')
                self.assertEqual(manifest['version'], version)
                self.assertEqual(manifest['worker'], f'runtime/revit/2027/{version}/{worker.name}')
                files = {name[len(prefix):] for name in archive.namelist()}
                self.assertEqual(files, set(manifest['files']) | {'PACKAGE_MANIFEST.json'})
                for name, expected in manifest['files'].items():
                    self.assertEqual(hashlib.sha256(archive.read(prefix + name)).hexdigest(), expected)

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory(dir=os.environ['XIVGATE_TEST_ROOT']) as directory:
            root = Path(directory)
            worker = root / 'XIVgate.RevitBridge.Worker.dll'
            worker.write_bytes(b'test-only')
            target = root / 'existing.zip'
            target.write_bytes(b'preserve')
            with self.assertRaises(ValueError):
                build(worker, target)
            self.assertEqual(target.read_bytes(), b'preserve')
