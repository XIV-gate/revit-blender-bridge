"""Exact, user-overridable element selection. Revit IDs stay strings."""
import copy
import re


_INTERNAL_WALL_TYPE = re.compile(r'^(ADSK[_ -]*)?(Внутрен|Вн[_ -]|Interior\b|Internal\b)', re.IGNORECASE)
_MEP_CATEGORY = re.compile(
    r'труб|воздуховод|кабельн\w*\s*лот|короб|электрооборудован|сантехник|'
    r'механическ\w*\s*оборудован|оборудован\w*\s*овк|'
    r'\bduct|\bpipe|\bconduit|\bcable\s*tray|\bmechanical\s+equipment|'
    r'\belectrical\s+equipment|\bsprinkler|\bair\s*terminal', re.IGNORECASE)
_FACADE_WORKSET = re.compile(r'фасад|оболоч|\bfacade|\bfaçade|\bexterior', re.IGNORECASE)
_HELPER_CATEGORIES = frozenset(name.casefold() for name in (
    'Схемы разрезки витражей', 'Эскиз', 'Эскизы', 'Компоненты легенды',
    'Помещения', 'Пространства', 'Зоны', 'Границы зон', 'Линии', 'Линии модели',
    'Наборы характеристик материала', 'Камеры', 'Траектория солнца',
    'Осн. горизонтали', 'Основные горизонтали', 'Доп. горизонтали', 'Дополнительные горизонтали',
    'Границы участков', 'Сведения о проекте', 'Точка съемки', 'Точка съёмки',
    'Базовая точка проекта', 'Внутреннее начало',
    'Rooms', 'Spaces', 'Zones', 'Zone Boundaries', 'Area Boundaries',
    'Legend Components', 'Sketch', 'Sketches', 'Curtain Grids', 'Curtain Grid Lines',
    'Curtain Wall Grids', 'Cameras', 'Material Assets', 'Material Property Sets',
    'Project Information', 'Survey Point', 'Project Base Point', 'Internal Origin',
    'Lines', 'Model Lines', 'Sun Path', 'Sunpath', 'Primary Contours', 'Secondary Contours',
    'Property Lines', 'Site Property Lines',
))
_CAD_CATEGORY = re.compile(r'\.(?:dwg|dxf)$', re.IGNORECASE)


def annotate_catalog(catalog):
    """Return advisory facade choices without changing source or user overrides.

    Localized names are deliberately only hints. Unknown elements remain review
    (selected by default); wall Function/host tags never exclude windows or walls.
    Backend exclusions retain their reasons. Explicit user choices are applied
    afterwards by initial_selection using stable unique_id values.
    """
    annotated = copy.deepcopy(catalog)
    for item in annotated.get('elements', []):
        if item.get('recommendation') == 'exclude':
            continue
        category = str(item.get('category_name') or item.get('category') or '').strip()
        category_key = category.casefold()
        # Brackets are removed only for lookup in the explicit semantic allowlist;
        # an unfamiliar <category> remains review and selected.
        helper_key = category_key.strip('<>').strip()
        if helper_key in _HELPER_CATEGORIES or _CAD_CATEGORY.search(category):
            item.update(recommendation='exclude', reason='Служебная категория или CAD-подложка обычно не нужна для фасада; можно включить вручную')
        elif category_key in ('окна', 'окно', 'windows', 'window'):
            item.update(recommendation='include', reason='Окна включены независимо от функции стены и рабочего набора')
        elif category_key in ('стены', 'стена', 'walls', 'wall'):
            type_name = str(item.get('type_name') or '').strip()
            if _INTERNAL_WALL_TYPE.search(type_name):
                item.update(recommendation='exclude', reason='Предположительно внутренняя стена — по имени типа; геометрия не проверялась')
            elif _FACADE_WORKSET.search(str(item.get('workset_name') or '')):
                item.update(recommendation='include', reason='Стена в рабочем наборе фасада или наружной оболочки')
            elif item.get('recommendation') not in ('include', 'review'):
                item.update(recommendation='review', reason='Назначение стены требует проверки')
        elif _MEP_CATEGORY.search(category):
            item.update(recommendation='exclude', reason='Инженерная категория обычно не нужна для фасада; можно включить вручную')
        elif item.get('recommendation') not in ('include', 'review'):
            item.update(recommendation='review', reason='Назначение элемента требует проверки')
    return annotated


def initial_selection(catalog, previous=None):
    previous = previous or {}
    return {str(item['id']): bool(previous.get(item.get('unique_id'), item.get('recommendation') != 'exclude'))
            for item in catalog.get('elements', [])}


def selection_by_unique_id(catalog, selected_ids):
    selected = {str(value) for value in selected_ids}
    return {item['unique_id']: str(item['id']) in selected
            for item in catalog.get('elements', []) if item.get('unique_id')}


def set_group_selection(catalog, selected_ids, group, group_id, enabled):
    if group not in ('category', 'workset'):
        raise ValueError('Неизвестный вид группы')
    selected = {str(value) for value in selected_ids}
    members = {str(item['id']) for item in catalog.get('elements', [])
               if str(item.get(group + '_id')) == str(group_id)}
    return selected | members if enabled else selected - members
