import unittest
from types import SimpleNamespace

from xivgate_revit_bridge.core.import_modes import normalize_mode, settings_mode, grouped_selection_changed


class ImportModeTests(unittest.TestCase):
    def test_legacy_boolean_choices_survive(self):
        self.assertEqual(settings_mode(SimpleNamespace(preserve_instances=False)),'OBJECTS')
        self.assertEqual(settings_mode(SimpleNamespace(preserve_instances=True)),'INSTANCES')
        self.assertEqual(settings_mode(SimpleNamespace(import_mode='FAMILY',preserve_instances=False)),'FAMILY')

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ValueError):normalize_mode('joined')

    def test_group_membership_changes_require_rebuild(self):
        self.assertTrue(grouped_selection_changed('FAMILY',{'A','B'},{'A'}))
        self.assertTrue(grouped_selection_changed('FAMILY',{'A'},{'A','B'}))
        self.assertFalse(grouped_selection_changed('FAMILY',{'A','B'},{'B','A'}))
        self.assertFalse(grouped_selection_changed('INSTANCES',{'A','B'},{'A'}))
