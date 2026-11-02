import unittest

from xivgate_revit_bridge.core.catalog import annotate_catalog, initial_selection, is_reference_category

from xivgate_revit_bridge.core.selection_view import group_state, matches, exclusion_summary
from xivgate_revit_bridge.core.reporting import report_entries, raw_values


class SelectionReportingTests(unittest.TestCase):
    def spatial_catalog(self, masses, second_building=False):
        anchors = [dict(id='anchor-' + str(i), category_name=('Windows' if i % 2 else 'Панели витража'),
                        bbox_min=[0, 0, 0], bbox_max=[10, 10, 10], recommendation='review') for i in range(20)]
        if second_building:
            anchors.append(dict(id='building-2', category_name='Curtain Wall Mullions',
                                bbox_min=[2000, 0, 0], bbox_max=[2010, 10, 10]))
        return {'elements': anchors + masses}

    def mass(self, identifier='mass', low=(1600, 0, 0), high=(1610, 10, 10), **kwargs):
        return dict(id=identifier, unique_id='uid-' + identifier, category_name='Формы',
                    bbox_min=list(low), bbox_max=list(high), recommendation='review', **kwargs)

    def test_spatial_advisory_excludes_only_wholly_distant_review_mass_and_preserves_overrides(self):
        rows = [self.mass(), self.mass('near', (100, 0, 0), (105, 10, 10)),
                self.mass('crossing', (-1600, 0, 0), (1600, 10, 10)),
                dict(self.mass('wall'), category_name='Стены'),
                dict(self.mass('equipment'), category_name='Оборудование'),
                dict(self.mass('unknown'), category_name='Unfamiliar category'),
                dict(self.mass('english-mass'), category_name='Mass'),
                dict(self.mass('english-forms'), category_name='Forms'),
                dict(self.mass('mass-floor'), category_name='Формообразующее перекрытие', category_id='-2003403'),
                dict(self.mass('include'), recommendation='include'),
                dict(self.mass('excluded'), recommendation='exclude', reason='Исходная причина'),
                dict(self.mass('localized'), category_name='Other translation', category_id='-2003400')]
        source = self.spatial_catalog(rows)
        result = annotate_catalog(source)
        after = {row['id']: row for row in result['elements']}
        self.assertEqual(after['mass']['recommendation'], 'exclude')
        self.assertEqual(after['localized']['recommendation'], 'exclude')
        self.assertEqual(after['english-mass']['recommendation'], 'exclude')
        self.assertEqual(after['english-forms']['recommendation'], 'exclude')
        self.assertIn('100', after['mass']['reason'])
        self.assertIn('вручную', after['mass']['reason'])
        for identifier in ('near', 'crossing', 'wall', 'equipment', 'unknown', 'mass-floor'):
            self.assertEqual(after[identifier]['recommendation'], 'review')
        self.assertEqual(after['include']['recommendation'], 'include')
        self.assertEqual(after['excluded']['reason'], 'Исходная причина')
        self.assertTrue(initial_selection(result, {'uid-mass': True})['mass'])
        self.assertEqual(source['elements'][20]['recommendation'], 'review')

    def test_spatial_advisory_uses_union_of_buildings_and_scale_aware_margin(self):
        far_from_first = self.mass('other-building', (2000, 0, 0), (2010, 10, 10))
        self.assertEqual(annotate_catalog(self.spatial_catalog([far_from_first], True))['elements'][-1]['recommendation'], 'review')
        catalog = self.spatial_catalog([self.mass('large-site', (1500, 0, 0), (1510, 10, 10))])
        for anchor in catalog['elements'][:20]:
            anchor['bbox_max'] = [1000, 10, 10]
        self.assertEqual(annotate_catalog(catalog)['elements'][-1]['recommendation'], 'review')

    def test_spatial_advisory_requires_twenty_valid_anchors_and_finite_mass_bounds(self):
        catalog = self.spatial_catalog([self.mass()])
        catalog['elements'][0]['bbox_max'] = [float('nan'), 10, 10]
        self.assertEqual(annotate_catalog(catalog)['elements'][-1]['recommendation'], 'review')
        for low, high in ((None, [1, 2, 3]), ([0, 0], [1, 2, 3]),
                          ([float('inf'), 0, 0], [2000, 1, 1]), ([2000, 0, 0], [1900, 1, 1]),
                          (['1600', 0, 0], [1700, 1, 1]), ([True, 0, 0], [1700, 1, 1])):
            row = self.mass(); row.update(bbox_min=low, bbox_max=high)
            self.assertEqual(annotate_catalog(self.spatial_catalog([row]))['elements'][-1]['recommendation'], 'review')

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
