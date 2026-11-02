"""Exact, user-overridable element selection. Revit IDs stay strings."""
import copy
import math
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
    'Разделитель помещений', 'Эскиз лестницы/пандуса: Граница',
    'Эскиз лестницы/пандуса: траектория лестницы', 'Ограничения',
    'Выносные линии траектории ограждения',
    'Rooms', 'Spaces', 'Zones', 'Zone Boundaries', 'Area Boundaries',
    'Legend Components', 'Sketch', 'Sketches', 'Curtain Grids', 'Curtain Grid Lines',
    'Curtain Wall Grids', 'Cameras', 'Material Assets', 'Material Property Sets',
    'Project Information', 'Survey Point', 'Project Base Point', 'Internal Origin',
    'Lines', 'Model Lines', 'Sun Path', 'Sunpath', 'Primary Contours', 'Secondary Contours',
    'Property Lines', 'Site Property Lines',
    'Room Separation', 'Room Separation Lines', 'Stair/Ramp Sketch: Boundary',
    'Stair/Ramp Sketch: Stair Path', 'Constraints', 'Railing Path Extension Lines',
))
# Revit adds a numeric copy suffix to duplicate CAD imports. Arbitrary text
# after an extension is deliberately not evidence of a CAD category.
_CAD_CATEGORY = re.compile(r'\.(?:dwg|dxf)(?:[ \t]+\([0-9]+\))?$', re.IGNORECASE)
_FACADE_ANCHORS = frozenset(name.casefold() for name in (
    'Окна', 'Окно', 'Windows', 'Window', 'Панели витража',
    'Curtain Panels', 'Curtain Wall Panels', 'Импосты витража',
    'Curtain Wall Mullions', 'Curtain Mullions', 'Mullions',
))
_MASS_CATEGORIES = frozenset(('формы', 'mass', 'masses', 'forms'))


def _finite_bounds(item):
    low, high = item.get('bbox_min'), item.get('bbox_max')
    try:
        if len(low) != 3 or len(high) != 3:
            return None
        if any(isinstance(value, (str, bool)) for point in (low, high) for value in point):
            return None
        if not all(math.isfinite(a) and math.isfinite(b) and a <= b for a, b in zip(low, high)):
            return None
    except (TypeError, ValueError, OverflowError):
        return None
    return low, high


def _facade_region(elements):
    boxes = []
    for item in elements:
        category = str(item.get('category_name') or item.get('category') or '').strip().casefold()
        box = _finite_bounds(item) if category in _FACADE_ANCHORS else None
        if box is not None:
            boxes.append(box)
    if len(boxes) < 20:
        return None
    low = tuple(min(box[0][axis] for box in boxes) for axis in range(3))
    high = tuple(max(box[1][axis] for box in boxes) for axis in range(3))
    margin = max(100.0, 2 * math.dist(low, high))
    return (low, high, margin) if math.isfinite(margin) else None


def _distant_mass_reason(item, region):
    box = _finite_bounds(item)
    if region is None or box is None:
        return None
    low, high, margin = region
    if not any(box[1][axis] < low[axis] - margin or box[0][axis] > high[axis] + margin for axis in range(3)):
        return None
    gap = math.hypot(*(max(low[axis] - box[1][axis], box[0][axis] - high[axis], 0) for axis in range(3)))
    return ('Формообразующий объект целиком вне области фасада: расстояние '
            f'{gap:.1f} м, запас области {margin:.1f} м; можно включить вручную')


def is_reference_category(name):
    return str(name or '').strip().casefold().strip('<>').strip() in _HELPER_CATEGORIES


def annotate_catalog(catalog):
    """Return advisory facade choices without changing source or user overrides.

    Localized names are deliberately only hints. Unknown elements remain review
    (selected by default); wall Function/host tags never exclude windows or walls.
    Backend exclusions retain their reasons. Explicit user choices are applied
    afterwards by initial_selection using stable unique_id values.
    """
    annotated = copy.deepcopy(catalog)
    region = _facade_region(annotated.get('elements', []))
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
        elif item.get('recommendation') == 'review' and (
                category_key in _MASS_CATEGORIES or str(item.get('category_id')) == '-2003400'):
            # -2003400 is the actual Mass category in the source catalog;
            # Mass Floors (-2003403) and mixed central/remote slabs are untouched.
            reason = _distant_mass_reason(item, region)
            if reason:
                item.update(recommendation='exclude', reason=reason)
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
