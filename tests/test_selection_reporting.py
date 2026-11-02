import unittest

from xivgate_revit_bridge.core.catalog import annotate_catalog, initial_selection, is_reference_category

from xivgate_revit_bridge.core.selection_view import group_state, matches, exclusion_summary
from xivgate_revit_bridge.core.reporting import report_entries, raw_values


class SelectionReportingTests(unittest.TestCase):
    def test_room_separation_stair_sketch_constraints_and_railing_path_are_helpers(self):
        helpers = ('<Разделитель помещений>', '<Room Separation>', 'Room Separation Lines',
                   '<Эскиз лестницы/пандуса: Граница>',
                   '<Эскиз лестницы/пандуса: траектория лестницы>',
                   '<Stair/Ramp Sketch: Boundary>', '<Stair/Ramp Sketch: Stair Path>',
                   'Ограничения', 'Constraints', 'Выносные линии траектории ограждения',
                   'Railing Path Extension Lines')
        catalog = {'elements': [dict(id=str(index), unique_id='uid-' + str(index),
                                    category_name=name, recommendation='review')
                                for index, name in enumerate(helpers)]}
        result = annotate_catalog(catalog)
        for original, annotated in zip(catalog['elements'], result['elements']):
            with self.subTest(category=original['category_name']):
                self.assertTrue(is_reference_category(original['category_name']))
                self.assertEqual(annotated['recommendation'], 'exclude')
                self.assertEqual(original['recommendation'], 'review')
        self.assertFalse(any(initial_selection(result).values()))
        self.assertTrue(initial_selection(result, {'uid-0': True})['0'])

    def test_cad_numeric_copy_suffix_is_advisory_and_case_insensitive(self):
        names = ('Геоподоснова.dwg (2)', 'Схема.DXF (12)', 'model.DwG', 'plan.dxf (001)')
        result = annotate_catalog({'elements': [dict(id=str(index), unique_id='cad-' + str(index),
                                                    category_name=name) for index, name in enumerate(names)]})
        self.assertFalse(any(initial_selection(result).values()))
        self.assertTrue(initial_selection(result, {'cad-1': True})['1'])

    def test_unfamiliar_brackets_arbitrary_cad_suffix_and_facade_geometry_stay(self):
        names = ('<Разделитель фасада>', '<Неизвестная категория>', 'x.dwg фасад', 'x.dwg (фасад)',
                 'x.dwg (2) фасад', 'x.dxf (2.5)', 'x.dwg (2)(3)',
                 'Импосты витража', 'Панели витража', 'Ограждения', 'Стены', 'Оборудование')
        result = annotate_catalog({'elements': [dict(id=str(index), unique_id='keep-' + str(index),
                                                    category_name=name, recommendation='review')
                                                for index, name in enumerate(names)]})
        self.assertTrue(all(initial_selection(result).values()))
        self.assertTrue(all(row['recommendation'] == 'review' for row in result['elements']))
        self.assertFalse(initial_selection(result, {'keep-7': False})['7'])

    def test_mixed_is_explicit_and_filter_does_not_change_choices(self):
        self.assertEqual(group_state(4710, 5380), 'partial')
        self.assertEqual(group_state(0, 5380), 'none')
        self.assertEqual(group_state(5380, 5380), 'all')
        item = dict(selected=False, category_id='walls', type_name='Вн_200',
                    recommendation='exclude', reason='По имени типа')
        self.assertTrue(matches(item, query='вн_', scope_kind='category', scope_id='walls', status='excluded'))
        self.assertFalse(matches(item, scope_kind='category', scope_id='windows'))
        self.assertFalse(matches(item, status='selected'))
        item['selected'] = True  # User override remains visible as selected.
        self.assertTrue(matches(item, status='selected'))
        self.assertFalse(matches(item, status='excluded'))

    def test_exclusion_summary_distinguishes_recommendation_and_user_choice(self):
        rows = [dict(selected=False, recommendation='exclude', reason='По имени типа'),
                dict(selected=False, recommendation='include', reason='Окно'),
                dict(selected=True, recommendation='exclude', reason='По имени типа')]
        result = exclusion_summary(rows)
        self.assertCountEqual(result, [('По имени типа', 1), ('Исключено пользователем; рекомендация: Окно', 1)])

    def test_no_geometry_is_one_information_entry_with_lossless_details(self):
        prefix = 'Выбранный элемент не дал видимой геометрии (фаза, вариант, отсутствие сетки или неподдерживаемая связь): '
        values = [prefix + str(i) for i in range(2210)] + ['Настоящее предупреждение', prefix + '0']
        entries = report_entries(values)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]['severity'], 'INFO')
        self.assertEqual(entries[0]['count'], 2210)
        self.assertIn('2210', entries[0]['message'])
        self.assertEqual(raw_values(entries), list(dict.fromkeys(values)))
        self.assertEqual(report_entries(raw_values(entries)), entries)
        self.assertEqual(entries[1]['severity'], 'WARNING')

    def test_other_warnings_are_not_silenced(self):
        result = report_entries(['Не найдена текстура: a.jpg', 'Не найдена текстура: b.jpg', 'Ошибка Revit'])
        self.assertEqual(len(result), 3)
        self.assertTrue(all(row['severity'] == 'WARNING' for row in result))
