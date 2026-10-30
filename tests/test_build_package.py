import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile

from tools.build_package import build


class PackageTests(unittest.TestCase):
    def test_frozen_loader_is_bundled_for_the_existing_updater_format(self):
        from xivgate_revit_bridge.core.updates import validate_archive
        from xivgate_revit_bridge.core.loader import payload_record, ensure_loader
        with tempfile.TemporaryDirectory(dir=os.environ['XIVGATE_TEST_ROOT']) as directory:
            root = Path(directory)
            worker = root / 'XIVgate.RevitBridge.Worker.dll'
            worker.write_bytes(b'MZ test-only worker')
            loader = root / 'XIVgate.RevitBridge.Loader.dll'
            loader.write_bytes(b'MZ test-only loader')
            digest = hashlib.sha256(loader.read_bytes()).hexdigest()
            target = root / 'addon.zip'
            build(worker, target, loaders={'2027': loader}, loader_lock={'2027': digest})
            with zipfile.ZipFile(target) as archive:
                version = archive.read('xivgate_revit_bridge/VERSION').decode().strip()
                raw = archive.read('xivgate_revit_bridge/core/loader_payload.py')
                _, data, stored = payload_record(raw, '2027')
                self.assertEqual((data, stored), (loader.read_bytes(), digest))
            # No schema expansion/additional runtime DLL allowed by old clients.
            package = validate_archive(target, version, (5, 2, 0))
            self.assertNotIn('loaders', package.manifest)
            self.assertEqual(package.manifest['loader_payloads'], {'abi': 1, 'sha256': {'2027': digest}})
            self.assertFalse(any(name.endswith('Loader.dll') for name in package.files))
            installed = root / 'installed'
            installed.mkdir()
            (installed / 'PACKAGE_MANIFEST.json').write_text(json.dumps(package.manifest))
            for name, data in package.files.items():
                path = installed / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            self.assertEqual(ensure_loader(installed, '2027').read_bytes(), loader.read_bytes())

    def test_changed_loader_must_not_pass_the_freeze_lock(self):
        with tempfile.TemporaryDirectory(dir=os.environ['XIVGATE_TEST_ROOT']) as directory:
            root = Path(directory)
            worker = root / 'XIVgate.RevitBridge.Worker.dll'; worker.write_bytes(b'MZ test worker')
            loader = root / 'XIVgate.RevitBridge.Loader.dll'; loader.write_bytes(b'MZ changed loader')
            with self.assertRaises(ValueError):
                build(worker, root / 'bad.zip', loaders={'2027': loader}, loader_lock={'2027': '0' * 64})
            self.assertFalse((root / 'bad.zip').exists())

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
