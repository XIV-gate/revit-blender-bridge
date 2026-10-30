import unittest
from xivgate_revit_bridge.core.origin import catalog_origin


class OriginTests(unittest.TestCase):
    def test_selected_building_ignores_far_reference_and_preserves_elevation(self):
        rows = [dict(category_name='Окна', selected=True, recommendation='include',
                     bbox_min=[1000000, 2000000, 50], bbox_max=[1000002, 2000004, 80]),
                dict(category_name='Точка съёмки', selected=True, recommendation='exclude',
                     bbox_min=[0, 0, 0], bbox_max=[0, 0, 0]),
                dict(category_name='Стены', selected=False, recommendation='include',
                     bbox_min=[-500000, -500000, -200], bbox_max=[-499900, -499900, 0])]
        self.assertEqual(catalog_origin(rows), (1000001, 2000002, 0))

    def test_empty_selection_falls_back_to_geometry_and_invalid_bounds_are_ignored(self):
        rows = [dict(category_name='Стены', selected=False, recommendation='exclude',
                     bbox_json='[[100,200,50],[110,220,90]]'),
                dict(category_name='Точка съемки', selected=False,
                     bbox_min=[0, 0, 0], bbox_max=[0, 0, 0]),
                dict(category_name='Стены', selected=True, bbox_json='invalid')]
        self.assertEqual(catalog_origin(rows), (105, 210, 0))
        self.assertEqual(catalog_origin([]), (0, 0, 0))
