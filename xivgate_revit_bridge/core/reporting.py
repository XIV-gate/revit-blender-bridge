"""Compact display entries; retain original diagnostics for inspection/copy."""
import json

NO_GEOMETRY = 'Выбранный элемент не дал видимой геометрии ('


def report_entries(values):
    entries, geometry = [], None
    for value in dict.fromkeys(str(value) for value in values):
        if value.startswith(NO_GEOMETRY):
            if geometry is None:
                geometry = dict(message='', severity='INFO', count=0, raw_json='[]')
                entries.append(geometry)
            # Accumulate outside JSON to avoid quadratic decoding for large models.
            geometry.setdefault('_raw', []).append(value)
        else:
            entries.append(dict(message=value, severity='WARNING', count=1,
                                raw_json=json.dumps([value], ensure_ascii=False)))
    if geometry is not None:
        raw = geometry.pop('_raw')
        geometry.update(count=len(raw), raw_json=json.dumps(raw, ensure_ascii=False),
            message=f'Без отдельной видимой геометрии: {len(raw)} выбранных элементов. '
                    'Сводная информация; возможны фаза, вариант, составной элемент или неподдерживаемая связь.')
    return entries


def raw_values(entries):
    values = []
    for entry in entries:
        raw = entry.get('raw_json', '') if isinstance(entry, dict) else getattr(entry, 'raw_json', '')
        message = entry.get('message', '') if isinstance(entry, dict) else entry.message
        try:
            rows = json.loads(raw)
            if not isinstance(rows, list) or not all(isinstance(row, str) for row in rows):
                raise ValueError('Invalid diagnostic details')
        except (ValueError, TypeError):
            rows = [message]
        values.extend(rows)
    return list(dict.fromkeys(values))
