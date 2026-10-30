"""A single category/element tree; row indexes refer to the canonical selection."""
from .selection_view import matches


def value(row, key, default=''):
    return row.get(key, default) if isinstance(row, dict) else getattr(row, key, default)


def build_tree(categories, elements, query=''):
    grouped = {}
    for index, element in enumerate(elements):
        grouped.setdefault(str(value(element, 'category_id')), []).append(index)
    rows = []
    query = query.strip().casefold()
    for index, group in enumerate(categories):
        members = grouped.get(str(value(group, 'group_id')), [])
        group_matches = not query or query in str(value(group, 'name')).casefold()
        visible = members if group_matches else [i for i in members if matches(elements[i], query=query)]
        if query and not group_matches and not visible:
            continue
        rows.append(('category', index))
        if value(group, 'expanded', False):
            rows.extend(('element', i) for i in visible)
    return rows
