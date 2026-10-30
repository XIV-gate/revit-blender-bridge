"""One architectural XY origin for bounds and geometry; elevation is preserved."""
import json
import math

from .catalog import is_reference_category
from .catalog_tree import value


def catalog_origin(rows):
    valid = []
    for row in rows:
        try:
            box = value(row, 'bbox_json')
            low, high = json.loads(box) if box else (value(row, 'bbox_min'), value(row, 'bbox_max'))
            if len(low) != 3 or len(high) != 3:
                continue
            if any(isinstance(part, (str, bool)) for point in (low, high) for part in point):
                continue
            if not all(math.isfinite(a) and math.isfinite(b) and a <= b for a, b in zip(low, high)):
                continue
        except (ValueError, TypeError):
            continue
        valid.append((row, low, high))
    geometry = [entry for entry in valid if not is_reference_category(value(entry[0], 'category_name'))]
    selected = [entry for entry in geometry if value(entry[0], 'selected', False)]
    recommended = [entry for entry in geometry if value(entry[0], 'recommendation') != 'exclude']
    candidates = selected or recommended or geometry or valid
    if not candidates:
        return (0, 0, 0)
    return tuple(min(low[axis] for _, low, _ in candidates)/2 +
                 max(high[axis] for _, _, high in candidates)/2 for axis in range(2)) + (0,)
