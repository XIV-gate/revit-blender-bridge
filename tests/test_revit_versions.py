import os
from pathlib import Path
import tempfile
import unittest

from xivgate_revit_bridge.core import revit_versions as rv


class RevitVersionTests(unittest.TestCase):
    def test_custom_registered_installs_are_validated_deduplicated_and_sorted(self):
        with tempfile.TemporaryDirectory(dir=os.environ['XIVGATE_TEST_ROOT']) as temp:
            root = Path(temp)
            first, second = root / 'custom24' / 'Revit.exe', root / 'custom26' / 'Revit.exe'
            for path in (first, second):
                path.parent.mkdir()
                path.write_bytes(b'fixture')
            def inspect(path):
                year = 2024 if path == first else 2026
                return dict(path=str(path), year=year, key=str(year), version=str(year-2000)+'.0.0.0')
            result = rv.discover([], registry_paths=[first, second, first, root/'missing.exe'], inspect=inspect)
            self.assertEqual([row['year'] for row in result], [2026, 2024])

    def test_year_and_runtime_select_separate_worker(self):
        for version, runtime, expected in [('24.3.0.0', 4, '2024'),
                ('25.4.0.0', 8, '2025'), ('25.5.0.0', 10, '2025-net10'),
                ('26.4.0.0', 8, '2026'), ('26.5.0.0', 10, '2026-net10'),
                ('27.0.0.0', 10, '2027')]:
            self.assertEqual(rv.worker_key(version, runtime), expected)

    def test_unsupported_year_or_runtime_fails_before_launch(self):
        for version, runtime in [('23.1', 4), ('28.0', 10), ('24.0', 10), ('26.0', 4), ('27.0', 8)]:
            with self.assertRaises(ValueError):
                rv.worker_key(version, runtime)

    def test_discovery_validates_exe_version_not_folder_name(self):
        with tempfile.TemporaryDirectory(dir=os.environ['XIVGATE_TEST_ROOT']) as temp:
            root = Path(temp)
            fake = root / 'Autodesk' / 'Revit 2026'
            fake.mkdir(parents=True)
            exe = fake / 'Revit.exe'; exe.write_bytes(b'test')
            found = rv.discover([root], inspect=lambda path: dict(path=str(path), year=2027, key='2027', version='27.1.0.0'))
            self.assertEqual(found[0]['year'], 2027)
            self.assertEqual(found[0]['path'], str(exe))

    def test_runtime_config_takes_precedence_over_release_number(self):
        with tempfile.TemporaryDirectory(dir=os.environ['XIVGATE_TEST_ROOT']) as temp:
            exe = Path(temp) / 'Revit.exe'; exe.write_bytes(b'test')
            (exe.parent / 'Revit.runtimeconfig.json').write_text('{"runtimeOptions":{"framework":{"name":"Microsoft.NETCore.App","version":"10.0.1"}}}')
            self.assertEqual(rv.inspect_revit(exe, version_reader=lambda path: '26.4.0.0')['key'], '2026-net10')

    def test_missing_runtime_config_uses_known_release_boundary(self):
        with tempfile.TemporaryDirectory(dir=os.environ['XIVGATE_TEST_ROOT']) as temp:
            exe = Path(temp) / 'Revit.exe'; exe.write_bytes(b'test')
            self.assertEqual(rv.inspect_revit(exe, version_reader=lambda path: '25.5.0.0')['key'], '2025-net10')
