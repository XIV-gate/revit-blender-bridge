import unittest
from xivgate_revit_bridge.core.catalog_tree import build_tree


class TreeTests(unittest.TestCase):
    def test_expand_is_local_and_indexes_are_canonical(self):
        groups = [dict(group_id='walls', name='Стены', expanded=False),
                  dict(group_id='windows', name='Окна', expanded=True)]
        elements = [dict(category_id='walls', name='Внутренняя'),
                    dict(category_id='windows', name='1800'), dict(category_id='walls', name='Фасад')]
        self.assertEqual(build_tree(groups, elements), [('category', 0), ('category', 1), ('element', 1)])
        groups[0]['expanded'] = True
        self.assertEqual(build_tree(groups, elements), [('category', 0), ('element', 0), ('element', 2), ('category', 1), ('element', 1)])
        self.assertEqual(build_tree(groups, elements, 'фасад'), [('category', 0), ('element', 2)])
        self.assertEqual(build_tree(groups, elements, 'стены'), [('category', 0), ('element', 0), ('element', 2)])

