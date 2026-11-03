"""Import layout and legacy-choice compatibility, independent of Blender."""
MODES=('OBJECTS','INSTANCES','FAMILY')


def normalize_mode(value=None, preserve_instances=True):
    if value in (None,''):
        return 'INSTANCES' if preserve_instances else 'OBJECTS'
    if value not in MODES:
        raise ValueError('Неизвестный режим импорта: '+str(value))
    return value


def settings_mode(settings):
    value=getattr(settings,'import_mode',None)
    return normalize_mode(value,getattr(settings,'preserve_instances',True))


def grouped_selection_changed(mode, previous, selected):
    return normalize_mode(mode)=='FAMILY' and set(previous)!=set(selected)
