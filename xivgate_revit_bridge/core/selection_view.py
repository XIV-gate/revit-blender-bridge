"""Explain selection without modifying it or inferring visibility from counts."""
from collections import Counter


def group_state(selected, total):
    return 'all' if total > 0 and selected == total else 'partial' if selected else 'none'


def _value(item, name, default=''):
    return item.get(name, default) if isinstance(item, dict) else getattr(item, name, default)


def matches(item, *, query='', scope_kind='', scope_id='', status='all'):
    if scope_kind and str(_value(item, scope_kind + '_id')) != scope_id:
        return False
    selected = bool(_value(item, 'selected', False))
    if status == 'selected' and not selected or status == 'excluded' and selected:
        return False
    text = ' '.join(str(_value(item, key)) for key in (
        'name', 'category_name', 'workset_name', 'type_name', 'family_name', 'element_id', 'reason')).casefold()
    return not query.strip() or query.strip().casefold() in text


def exclusion_summary(items):
    reasons = Counter()
    for item in items:
        if not _value(item, 'selected', False):
            reason = _value(item, 'reason') or 'Причина рекомендации не указана'
            if _value(item, 'recommendation') != 'exclude':
                reason = 'Исключено пользователем; рекомендация: ' + reason
            reasons[reason] += 1
    return sorted(reasons.items(), key=lambda pair: (-pair[1], pair[0]))
