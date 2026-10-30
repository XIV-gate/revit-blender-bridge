"""User-facing Blender panel. All Blender calls happen on its main thread."""
import os
import json
from pathlib import Path
import textwrap

import bpy
from bpy.app.handlers import persistent
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       IntProperty, PointerProperty, StringProperty)

from . import runtime, updater, workflow
from .core.catalog import initial_selection
from .core.selection_view import group_state, matches, exclusion_summary
from .core.reporting import raw_values


_SYNCING = False
_RELEASE_ITEMS = []
_REVIT_ITEMS = []


def revit_installations(self, context):
    return _REVIT_ITEMS or [('NONE', 'Установки не найдены', 'Укажите Revit.exe вручную')]


class RBB_OT_choose_revit(bpy.types.Operator):
    bl_idname = 'xivgate_rbb.choose_revit'
    bl_label = 'Выбрать установленный Revit'
    bl_description = 'Найти версии 2024–2027 в стандартных папках и зарегистрированных установках Windows; выбрать одну для запуска'
    installation: EnumProperty(name='Версия Revit', items=revit_installations)

    def invoke(self, context, event):
        global _REVIT_ITEMS
        from .core.revit_versions import discover
        found = discover()
        _REVIT_ITEMS = [(row['path'], f"Revit {row['year']} · {row['version']}", row['path']) for row in found]
        if not found:
            self.report({'INFO'}, 'Установки не найдены. Укажите путь Revit.exe вручную в настройках подключения.')
            return {'CANCELLED'}
        current = bpy.path.abspath(context.scene.xivgate_revit_bridge.revit_exe)
        self.installation = next((row['path'] for row in found if row['path'] == current), found[0]['path'])
        return context.window_manager.invoke_props_dialog(self, width=650)

    def draw(self, context):
        self.layout.prop(self, 'installation')
        self.layout.label(text=self.installation)
        self.layout.label(text='Будет запущена только выбранная версия Revit.')
        self.layout.label(text='Для других установок используйте ручной путь в настройках подключения.')

    def execute(self, context):
        props = context.scene.xivgate_revit_bridge
        if props.busy or runtime.MAINTENANCE:
            return {'CANCELLED'}
        from .core.revit_versions import inspect_revit
        try:
            props.revit_exe = inspect_revit(self.installation)['path']
        except (OSError, ValueError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


def release_items(self, context):
    global _RELEASE_ITEMS
    values = [(info['version'], info['version'] + (' · установлена' if info['version'] == updater.current_version() else ''),
               info['release_url']) for info in updater.visible_releases(self.release_channel)]
    # Blender retains references to callback strings; keep them alive between draws.
    if values != _RELEASE_ITEMS:
        _RELEASE_ITEMS = values
    return _RELEASE_ITEMS or [('NONE', 'Нет версий — получите список', '')]


def release_changed(self, context):
    if not updater.STATE.busy:
        updater.select_channel(self.release_channel)
        updater.select_release(self.release_version)


def channel_changed(self, context):
    if not updater.STATE.busy:
        updater.select_channel(self.release_channel)
        choices = updater.visible_releases()
        if choices:
            self.release_version = choices[0]['version']


def _props(item):
    return item.id_data.xivgate_revit_bridge


def rebuild_tree(props):
    from .core.catalog_tree import build_tree
    identity = None
    if 0 <= props.tree_index < len(props.tree_rows):
        old = props.tree_rows[props.tree_index]
        identity = (old.kind, old.item_index)
    rows = build_tree(props.categories, props.elements, props.search)
    if [(row.kind, row.item_index) for row in props.tree_rows] == rows:
        return
    props.tree_rows.clear()
    for kind, index in rows:
        row = props.tree_rows.add()
        row.kind, row.item_index = kind, index
    props.tree_index = rows.index(identity) if identity in rows else 0


def expansion_changed(item, context):
    if not _SYNCING:
        rebuild_tree(_props(item))


def tree_search_changed(props, context):
    global _SYNCING
    if _SYNCING:
        return
    if props.search.strip():
        from .core.catalog_tree import build_tree
        visible = build_tree(props.categories, props.elements, props.search)
        _SYNCING = True
        try:
            for kind, index in visible:
                if kind == 'category':
                    props.categories[index].expanded = True
        finally:
            _SYNCING = False
    rebuild_tree(props)


def centering_changed(props, context):
    if _SYNCING or props.busy or runtime.MAINTENANCE:
        return
    align_scene_previews(props)
    from .spatial_preview import PREVIEW_TAG, SCENE_TAG
    frame_previews(context, [obj for obj in props.id_data.objects
                            if obj.get(PREVIEW_TAG) is True and obj.get(SCENE_TAG) == props.id_data])


def align_scene_previews(props):
    from .spatial_preview import PREVIEW_TAG, SCENE_TAG, align_previews
    scene = props.id_data
    if scene.library is not None:
        return
    if any(obj.get(PREVIEW_TAG) is True and obj.get(SCENE_TAG) == scene for obj in scene.objects):
        align_previews(scene, catalog_preview_offset(props), preview_parent(scene, props))


def sync_groups(props):
    global _SYNCING
    previous = _SYNCING
    _SYNCING = True
    try:
        counts = {'category': {}, 'workset': {}, 'type': {}}
        for item in props.elements:
            for kind in counts:
                key = getattr(item, kind + '_id')
                total, selected = counts[kind].get(key, (0, 0))
                counts[kind][key] = (total + 1, selected + int(item.selected))
        for kind, rows in (('category', props.categories), ('workset', props.worksets), ('type', props.types)):
            for item in rows:
                item.count, item.selected_count = counts[kind].get(item.group_id, (0, 0))
                item.selected = item.count > 0 and item.count == item.selected_count
        props.selected_count = sum(item.selected for item in props.elements)
    finally:
        _SYNCING = previous
    rebuild_tree(props)


def element_changed(item, context):
    if not _SYNCING and not _props(item).busy:
        sync_groups(_props(item))


def group_changed(item, context):
    global _SYNCING
    props = _props(item)
    if _SYNCING or props.busy:
        return
    _SYNCING = True
    try:
        for element in props.elements:
            if getattr(element, item.group_kind + '_id') == item.group_id:
                element.selected = item.selected
    finally:
        _SYNCING = False
    sync_groups(props)


class RBB_Element(bpy.types.PropertyGroup):
    element_id: StringProperty()
    unique_id: StringProperty()
    name: StringProperty()
    category_id: StringProperty()
    category_name: StringProperty()
    workset_id: StringProperty()
    workset_name: StringProperty()
    type_id: StringProperty()
    type_name: StringProperty()
    family_name: StringProperty()
    bbox_json: StringProperty()
    recommendation: StringProperty(default='review')
    reason: StringProperty()
    selected: BoolProperty(default=True, update=element_changed)


class RBB_Group(bpy.types.PropertyGroup):
    group_id: StringProperty()
    group_kind: StringProperty()
    name: StringProperty()
    count: IntProperty()
    selected_count: IntProperty()
    selected: BoolProperty(update=group_changed)
    expanded: BoolProperty(name='Раскрыть категорию', description='Показать или скрыть элементы этой категории', update=expansion_changed)


class RBB_TreeRow(bpy.types.PropertyGroup):
    kind: StringProperty()
    item_index: IntProperty()


class RBB_Warning(bpy.types.PropertyGroup):
    message: StringProperty()
    severity: StringProperty(default='WARNING')
    count: IntProperty(default=1)
    raw_json: StringProperty()


def _default_revit():
    from .core.revit_versions import discover
    found = discover()
    return found[0]['path'] if found else ''


class RBB_Settings(bpy.types.PropertyGroup):
    release_channel: EnumProperty(name='Канал', items=[('stable', 'Стабильные', 'Обычные опубликованные релизы'),
        ('early', 'Ранний доступ', 'Тестовые сборки, возможны ошибки')], default='stable', update=channel_changed)
    release_version: EnumProperty(name='Версия', items=release_items, update=release_changed, options={'SKIP_SAVE'})
    source_rvt: StringProperty(name='Файл RVT', subtype='FILE_PATH')
    revit_exe: StringProperty(name='Revit.exe', subtype='FILE_PATH', default=_default_revit())
    cache_directory: StringProperty(name='Рабочая папка', subtype='DIR_PATH', description='Необязательно. Если пусто — Data/Cache/RevitBridge рядом с текущим сохранённым .blend; своего пути по умолчанию в аддоне нет')
    reuse_export_cache: BoolProperty(name='Использовать проверенный кэш выгрузки', default=True)
    catalog_worker_key: StringProperty()
    addin_folder: StringProperty(name='Каталог подключения Revit', subtype='DIR_PATH', description='Обычно определяется автоматически; нужен только при нестандартной установке')
    preserve_instances: BoolProperty(name='Сохранять инстансы', default=True, description='Редактируемые Mesh-объекты с общими данными, как Alt+D')
    import_materials: BoolProperty(name='Переносить материалы', default=True, description='Создать материалы Blender из доступных данных Revit')
    import_textures: BoolProperty(name='Переносить доступные текстуры', default=True, description='Перенести найденные локальные изображения; отсутствующие файлы перечисляются в отчёте')
    pack_images: BoolProperty(name='Упаковать текстуры в .blend', default=True, description='Встроить найденные изображения в сохранённый .blend для передачи модели')
    center_model: BoolProperty(name='Разместить около начала координат', default=True, description='Общее смещение XY для превью и модели; высоты сохраняются', update=centering_changed)
    load_links: BoolProperty(default=False)
    detail_level: EnumProperty(name='Детализация', items=[('coarse', 'Низкая', ''), ('medium', 'Средняя', ''), ('fine', 'Высокая', '')], default='medium')
    show_advanced: BoolProperty(name='Настройки', default=False, description='Подключение Revit, параметры импорта и рабочие данные — каждый в своём разделе')
    list_mode: EnumProperty(name='Показать', items=[('categories', 'Категории', 'Группировать элементы по категориям Revit'),
        ('worksets', 'Рабочие наборы', 'Группировать элементы по рабочим наборам Revit'),
        ('types', 'Типы', 'Группировать по семейству и типу; один тип можно включить целиком'),
        ('elements', 'Объекты', 'Выбирать отдельные элементы и видеть причину рекомендации')])
    search: StringProperty(name='Поиск', options={'TEXTEDIT_UPDATE'}, update=tree_search_changed)
    tree_rows: CollectionProperty(type=RBB_TreeRow)
    tree_index: IntProperty()
    selection_filter: EnumProperty(name='Состав списка', items=[('all', 'Все', ''),
        ('selected', 'Выбранные', 'Фактически отмеченные для импорта'),
        ('excluded', 'Исключённые', 'Не отмеченные для импорта; можно включить вручную')])
    scope_kind: StringProperty()
    scope_id: StringProperty()
    scope_label: StringProperty()
    elements: CollectionProperty(type=RBB_Element)
    categories: CollectionProperty(type=RBB_Group)
    worksets: CollectionProperty(type=RBB_Group)
    types: CollectionProperty(type=RBB_Group)
    element_index: IntProperty()
    category_index: IntProperty()
    workset_index: IntProperty()
    type_index: IntProperty()
    selected_count: IntProperty()
    busy: BoolProperty(default=False, options={'SKIP_SAVE'})
    status: StringProperty(default='IDLE', options={'SKIP_SAVE'})
    message: StringProperty(default='Выберите RVT и получите список элементов.')
    progress: FloatProperty(min=0, max=1, default=0)
    import_id: StringProperty()
    import_source: StringProperty()
    import_context_json: StringProperty()
    source_offset_json: StringProperty(default='[0,0,0]')
    preview_offset_json: StringProperty()
    preview_source: StringProperty()
    origin_valid: BoolProperty(default=False)
    origin_centered: BoolProperty(default=True)
    catalog_source: StringProperty()
    job_directory: StringProperty(subtype='DIR_PATH')
    result_collection: StringProperty()
    result_placements: IntProperty()
    result_prototypes: IntProperty()
    warning_count: IntProperty()
    warnings: CollectionProperty(type=RBB_Warning)
    warning_index: IntProperty()


class RBB_UL_warnings(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.message.replace('\n', ' '), icon='INFO' if item.severity == 'INFO' else 'ERROR')


class RBB_OT_warnings(bpy.types.Operator):
    bl_idname = 'xivgate_rbb.warnings'
    bl_label = 'Отчёт Revit'
    bl_description = 'Открыть ошибки, предупреждения и сводную информацию; записи без отдельной геометрии объединены'

    def invoke(self, context, event):
        props = context.scene.xivgate_revit_bridge
        runtime.set_warnings(props, raw_values(props.warnings))
        return context.window_manager.invoke_props_dialog(self, width=800)

    def draw(self, context):
        props = context.scene.xivgate_revit_bridge
        self.layout.template_list('RBB_UL_warnings', '', props, 'warnings', props, 'warning_index', rows=10)
        if 0 <= props.warning_index < len(props.warnings):
            for line in textwrap.wrap(props.warnings[props.warning_index].message, width=100):
                self.layout.label(text=line)
            self.layout.operator('xivgate_rbb.copy_report_details', text='Копировать подробности выбранной записи', icon='COPYDOWN')

    def execute(self, context):
        return {'FINISHED'}


class RBB_UL_catalog_tree(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        if item.kind == 'category' and 0 <= item.item_index < len(data.categories):
            group = data.categories[item.item_index]
            row.prop(group, 'expanded', text='', emboss=False,
                     icon='TRIA_DOWN' if group.expanded else 'TRIA_RIGHT')
            state = group_state(group.selected_count, group.count)
            toggle = row.operator('xivgate_rbb.toggle_group', text='', emboss=False,
                                  icon={'all': 'CHECKBOX_HLT', 'partial': 'REMOVE', 'none': 'CHECKBOX_DEHLT'}[state])
            toggle.kind, toggle.group_id = 'category', group.group_id
            row.label(text=group.name)
            row.label(text=f'{group.selected_count}/{group.count}')
            preview_button(row, data, 'category', group.group_id, group.name)
        elif item.kind == 'element' and 0 <= item.item_index < len(data.elements):
            element = data.elements[item.item_index]
            row.label(text='', icon='BLANK1')
            row.prop(element, 'selected', text='')
            info = row.operator('xivgate_rbb.element_info', text=element.name or element.type_name or element.element_id,
                                emboss=False)
            info.element_index = item.item_index
            preview_button(row, data, 'element', element.element_id, element.name or element.element_id)


class RBB_OT_element_info(bpy.types.Operator):
    bl_idname = 'xivgate_rbb.element_info'
    bl_label = 'Элемент Revit'
    bl_description = 'Показать тип, рабочий набор и причину рекомендации для этого элемента'
    element_index: IntProperty()

    @classmethod
    def description(cls, context, properties):
        props = context.scene.xivgate_revit_bridge if context and context.scene else None
        if props is not None and 0 <= properties.element_index < len(props.elements):
            item = props.elements[properties.element_index]
            return f'{item.type_name or item.name} · ID {item.element_id} · {item.reason}'
        return cls.bl_description

    def invoke(self, context, event):
        if not 0 <= self.element_index < len(context.scene.xivgate_revit_bridge.elements):
            return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self, width=700)

    def draw(self, context):
        props = context.scene.xivgate_revit_bridge
        if not 0 <= self.element_index < len(props.elements):
            return
        item = props.elements[self.element_index]
        values = (item.name, 'ID: ' + item.element_id, item.type_name,
                  'Рабочий набор: ' + item.workset_name,
                  'Выбран для импорта: ' + ('да' if item.selected else 'нет'),
                  'Рекомендация: ' + {'include': 'включить', 'exclude': 'исключить', 'review': 'проверить'}.get(item.recommendation, 'проверить'),
                  item.reason)
        for value in values:
            for line in textwrap.wrap(value, 90):
                self.layout.label(text=line)

    def execute(self, context):
        return {'FINISHED'}


class RBB_UL_groups(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        state = group_state(item.selected_count, item.count)
        toggle = row.operator('xivgate_rbb.toggle_group', text='', emboss=False,
                              icon={'all': 'CHECKBOX_HLT', 'partial': 'REMOVE', 'none': 'CHECKBOX_DEHLT'}[state])
        toggle.kind, toggle.group_id = item.group_kind, item.group_id
        row.label(text=item.name)
        row.label(text=f'{item.selected_count}/{item.count}' + (' · частично' if state == 'partial' else ''))
        preview_button(row, data, item.group_kind, item.group_id, item.name)

    def filter_items(self, context, data, propname):
        props = context.scene.xivgate_revit_bridge
        query = props.search.casefold().strip()
        flags = [self.bitflag_filter_item if not query or query in item.name.casefold() else 0 for item in getattr(data, propname)]
        return flags, []


class RBB_UL_elements(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, 'selected', text='')
        symbol = 'CHECKMARK' if item.recommendation == 'include' else 'QUESTION' if item.recommendation == 'review' else 'REMOVE'
        row.label(text=item.name or item.element_id, icon=symbol)
        row.label(text=item.category_name)
        preview_button(row, data, 'element', item.element_id, item.name or item.element_id)

    def filter_items(self, context, data, propname):
        props = context.scene.xivgate_revit_bridge
        flags = []
        for item in getattr(data, propname):
            visible = matches(item, query=props.search, scope_kind=props.scope_kind,
                              scope_id=props.scope_id, status=props.selection_filter)
            flags.append(self.bitflag_filter_item if visible else 0)
        return flags, []


def active_group(props):
    fields = {'categories': ('categories', 'category_index'), 'worksets': ('worksets', 'workset_index'),
              'types': ('types', 'type_index')}
    if props.list_mode not in fields:
        return None
    field, index_field = fields[props.list_mode]
    rows, index = getattr(props, field), getattr(props, index_field)
    if not 0 <= index < len(rows):
        return None
    group = rows[index]
    query = props.search.casefold().strip()
    return group if not query or query in group.name.casefold() else None


class RBB_OT_toggle_group(bpy.types.Operator):
    bl_idname = 'xivgate_rbb.toggle_group'
    bl_label = 'Изменить выбор группы'
    bl_description = 'Частично или не выбрано: выбрать всю группу. Выбрано всё: снять выбор'
    kind: StringProperty()
    group_id: StringProperty()

    def execute(self, context):
        props = context.scene.xivgate_revit_bridge
        if props.busy or runtime.MAINTENANCE:
            return {'CANCELLED'}
        rows = {'category': props.categories, 'workset': props.worksets, 'type': props.types}.get(self.kind, [])
        item = next((row for row in rows if row.group_id == self.group_id), None)
        if item is None:
            return {'CANCELLED'}
        item.selected = group_state(item.selected_count, item.count) != 'all'
        return {'FINISHED'}


class RBB_OT_inspect_group(bpy.types.Operator):
    # Retained for saved 0.4.x operator references; the panel uses only the tree.
    bl_options = {'INTERNAL'}
    bl_idname = 'xivgate_rbb.inspect_group'
    bl_label = 'Показать исключённые'
    bl_description = 'Открыть элементы этой группы с объяснением рекомендации; галочки задаёт пользователь'
    excluded_only: BoolProperty(default=True)

    def execute(self, context):
        props = context.scene.xivgate_revit_bridge
        group = active_group(props)
        if group is None:
            return {'CANCELLED'}
        props.scope_kind, props.scope_id, props.scope_label = group.group_kind, group.group_id, group.name
        props.selection_filter = 'excluded' if self.excluded_only else 'all'
        props.search = ''
        props.list_mode = 'elements'
        props.element_index = next((index for index, row in enumerate(props.elements)
            if matches(row, scope_kind=props.scope_kind, scope_id=props.scope_id, status=props.selection_filter)), 0)
        return {'FINISHED'}


class RBB_OT_clear_scope(bpy.types.Operator):
    bl_options = {'INTERNAL'}
    bl_idname = 'xivgate_rbb.clear_scope'
    bl_label = 'Показать все группы'
    bl_description = 'Сбросить ограничение текущей группой, поиск и фильтр списка; выбранные галочки сохраняются'

    def execute(self, context):
        props = context.scene.xivgate_revit_bridge
        props.scope_kind = props.scope_id = props.scope_label = ''
        props.search = ''
        props.selection_filter = 'all'
        return {'FINISHED'}


class RBB_OT_copy_report_details(bpy.types.Operator):
    bl_idname = 'xivgate_rbb.copy_report_details'
    bl_label = 'Копировать подробности'
    bl_description = 'Скопировать исходные сообщения и ID из выбранной сводной записи в буфер обмена'

    def execute(self, context):
        props = context.scene.xivgate_revit_bridge
        if not 0 <= props.warning_index < len(props.warnings):
            return {'CANCELLED'}
        context.window_manager.clipboard = '\n'.join(raw_values([props.warnings[props.warning_index]]))
        self.report({'INFO'}, 'Исходные сообщения и ID скопированы')
        return {'FINISHED'}


class RBB_OT_readable_names(bpy.types.Operator):
    bl_idname = 'xivgate_rbb.readable_names'
    bl_label = 'Обновить имена объектов и мешей'
    bl_description = 'Исправить служебные имена текущего импорта по данным Revit; пользовательские имена сохраняются'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.xivgate_revit_bridge
        if not props.import_id or props.busy or runtime.MAINTENANCE:
            return {'CANCELLED'}
        from .blender_import import rename_owned_import
        try:
            result = rename_owned_import(props.import_id)
        except (ValueError, RuntimeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Обновлено объектов: {result['objects']}; мешей: {result['meshes']}. Пользовательские имена сохранены.")
        return {'FINISHED'}


class RBB_OT_setup(bpy.types.Operator):
    bl_idname = 'xivgate_rbb.setup_revit'
    bl_label = 'Подключить Revit'
    bl_description = 'Один раз зарегистрировать локальный обработчик в выбранной версии Revit'

    def invoke(self, context, event):
        try:
            self.plan = runtime.setup_plan(context.scene.xivgate_revit_bridge)
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self, width=620)

    def draw(self, context):
        layout = self.layout
        layout.label(text='Будет подключён обработчик Revit через файл:')
        layout.label(text=self.plan['path'])
        layout.label(text='Текущий открытый Revit не изменяется. DLL работает только в процессе плагина.')

    def execute(self, context):
        try:
            plan = runtime.setup_plan(context.scene.xivgate_revit_bridge)
            path = updater.connect_revit(plan)
            self.report({'INFO'}, 'Обработчик подключён: ' + str(path))
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class RBB_OT_scan(bpy.types.Operator):
    bl_idname = 'xivgate_rbb.scan'
    bl_label = 'Получить список элементов'
    bl_description = 'Прочитать рабочую копию RVT в локальном Revit и показать каталог до импорта; предыдущий выбор сохраняется по ID'
    def invoke(self, context, event):
        try:
            self.cache = str(workflow.prepare_settings(context.scene))
            self.plan = runtime.setup_plan(context.scene.xivgate_revit_bridge)
            if not runtime.manifest_matches(self.plan) or not Path(self.cache).is_dir():
                return context.window_manager.invoke_props_dialog(self, width=680)
            return self.execute(context)
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}

    def draw(self, context):
        self.layout.label(text='Подключение локального обработчика Revit:')
        self.layout.label(text=self.plan['path'])
        self.layout.label(text='Копия RVT и промежуточные данные:')
        self.layout.label(text=self.cache)
        self.layout.label(text='Revit может один раз запросить доверие к надстройке.', icon='INFO')

    def execute(self, context):
        try:
            workflow.prepare_settings(context.scene)
            plan = runtime.setup_plan(context.scene.xivgate_revit_bridge)
            if not runtime.manifest_matches(plan):
                updater.connect_revit(plan)
            runtime.begin_scan(context.scene)
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class RBB_OT_import(bpy.types.Operator):
    bl_idname = 'xivgate_rbb.import_selected'
    bl_label = 'Импортировать выбранное'
    bl_description = 'Применить отмеченный состав: использовать уже загруженные элементы, а недостающие получить из Revit'
    bl_options = {'REGISTER', 'UNDO'}
    force_refresh: BoolProperty(default=False, options={'SKIP_SAVE'})
    @classmethod
    def description(cls, context, properties):
        if properties.force_refresh:
            return 'Заново прочитать RVT и перевыгрузить выбранный набор; это полное обновление из исходника, которое может занять время'
        return cls.bl_description
    def execute(self, context):
        try:
            runtime.begin_export(context.scene, force_refresh=self.force_refresh)
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class RBB_OT_select(bpy.types.Operator):
    bl_idname = 'xivgate_rbb.select'
    bl_label = 'Изменить выбор'
    bl_description = 'Изменить только галочки выбора до импорта; изменения модели применяются кнопкой импорта'
    mode: EnumProperty(items=[('all', 'Всё', 'Отметить все элементы выбранной области'),
        ('none', 'Ничего', 'Снять отметки у всех элементов выбранной области'),
        ('recommended', 'Рекомендуемое', 'Восстановить предложенный плагином набор; ручной выбор этой области будет заменён')])
    scope: EnumProperty(items=[('model', 'Вся модель', ''), ('group', 'Текущая группа', ''),
                              ('visible', 'Видимые строки', '')])
    @classmethod
    def description(cls, context, properties):
        areas = {'model': 'во всей модели', 'group': 'в текущей группе', 'visible': 'только в показанных строках с учётом поиска и фильтра'}
        actions = {'all': 'Отметить все элементы', 'none': 'Снять отметки',
                   'recommended': 'Восстановить рекомендации плагина вместо ручного выбора'}
        return actions.get(properties.mode, 'Изменить выбор') + ' ' + areas.get(properties.scope, '') + '. Затем нажмите импорт.'
    def execute(self, context):
        global _SYNCING
        props = context.scene.xivgate_revit_bridge
        if props.busy or runtime.MAINTENANCE:
            return {'CANCELLED'}
        group = active_group(props) if self.scope == 'group' else None
        if self.scope == 'group' and group is None:
            return {'CANCELLED'}
        _SYNCING = True
        try:
            for item in props.elements:
                if group is not None and getattr(item, group.group_kind + '_id') != group.group_id:
                    continue
                if self.scope == 'visible' and not matches(item, query=props.search,
                        scope_kind=props.scope_kind, scope_id=props.scope_id, status=props.selection_filter):
                    continue
                item.selected = self.mode == 'all' or (self.mode == 'recommended' and item.recommendation != 'exclude')
        finally:
            _SYNCING = False
        sync_groups(props)
        return {'FINISHED'}


class RBB_OT_cancel(bpy.types.Operator):
    bl_idname = 'xivgate_rbb.cancel'
    bl_label = 'Отменить'
    bl_description = 'Остановить текущее задание и закрыть рабочий Revit плагина; уже импортированная модель сохраняется'
    def execute(self, context):
        runtime.cancel()
        return {'FINISHED'}


class RBB_OT_show_worker(bpy.types.Operator):
    bl_idname = 'xivgate_rbb.show_worker'
    bl_label = 'Показать Revit'
    bl_description = 'Показать только процесс Revit, запущенный этим плагином, для ответа на его вопрос'
    def execute(self, context):
        try:
            runtime.show_worker()
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class RBB_OT_cache(bpy.types.Operator):
    bl_idname = 'xivgate_rbb.clear_cache'
    bl_label = 'Очистить завершённые задания'
    bl_description = 'Показать объём завершённых заданий и подтвердить очистку; используемые текущей сценой файлы защищены'

    def invoke(self, context, event):
        try:
            self.info = workflow.cache_info(context.scene.xivgate_revit_bridge)
            return context.window_manager.invoke_props_dialog(self, width=600)
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


    def draw(self, context):
        self.layout.label(text=f"Завершённых заданий: {self.info['eligible_jobs']}; {self.info['eligible_bytes'] / 1024**2:.1f} МБ")
        self.layout.label(text=self.info['root'])
        self.layout.label(text='Для следующего импорта может потребоваться новая выгрузка.')
        self.layout.label(text='Активные и неизвестные каталоги сохраняются.')
        self.layout.label(text='Файлы, используемые текущими сценами, тоже сохраняются.')

    def execute(self, context):
        try:
            result = workflow.clean_cache(context.scene.xivgate_revit_bridge)
            self.report({'WARNING'} if result['errors'] else {'INFO'},
                        f"Удалено заданий: {result['deleted_jobs']}; ошибок: {len(result['errors'])}")
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


def preview_group_key(props, kind, identifier):
    return json.dumps([props.catalog_source, kind, identifier], ensure_ascii=True)


def preview_button(layout, props, kind, identifier, name):
    from .spatial_preview import has_preview
    shown = has_preview(props.id_data, preview_group_key(props, kind, identifier))
    button = layout.operator('xivgate_rbb.toggle_preview', text='',
        icon='HIDE_OFF' if shown else 'SHADING_BBOX', depress=shown)
    button.kind, button.group_id, button.group_name = kind, identifier, name


def catalog_preview_offset(props):
    if not props.center_model:
        return (0, 0, 0)
    if props.origin_valid and props.origin_centered and props.import_source == props.catalog_source:
        return json.loads(props.source_offset_json)
    if props.preview_source == props.catalog_source and props.preview_offset_json:
        return json.loads(props.preview_offset_json)
    from .core.origin import catalog_origin
    offset = catalog_origin(props.elements)
    props.preview_source = props.catalog_source
    props.preview_offset_json = json.dumps(offset)
    return offset


def preview_parent(scene, props):
    if props.origin_valid and props.origin_centered == props.center_model:
        return imported_root(scene, props)
    return None


def imported_root(scene, props):
    collection = bpy.data.collections.get(props.result_collection)
    if (collection is None or collection not in scene.collection.children_recursive
            or props.import_source != props.catalog_source):
        return None
    return next((obj for obj in collection.objects if obj.type == 'EMPTY'
                 and 'revit:source_offset_metres' in obj), None)


def frame_preview(context, obj):
    frame_previews(context, [obj])


def frame_previews(context, objects):
    # Explicit preview clicks frame their layer without changing selected objects.
    space = context.space_data if context else None
    if space is None or space.type != 'VIEW_3D' or space.region_3d is None:
        return
    from mathutils import Vector
    context.view_layer.update()
    corners = [obj.matrix_world @ Vector(corner) for obj in objects for corner in obj.bound_box]
    if not corners:
        return
    low = Vector(tuple(min(point[axis] for point in corners) for axis in range(3)))
    high = Vector(tuple(max(point[axis] for point in corners) for axis in range(3)))
    space.region_3d.view_location = (low + high) / 2
    space.region_3d.view_distance = max((high - low).length, .5)


class RBB_OT_toggle_preview(bpy.types.Operator):
    bl_idname = 'xivgate_rbb.toggle_preview'
    bl_label = 'Превью границ слоя'
    bl_description = 'Показать или убрать рамки всех элементов этой строки независимо от галочек импорта; модель не загружается'
    bl_options = {'REGISTER', 'UNDO'}
    kind: StringProperty()
    group_id: StringProperty()
    group_name: StringProperty()

    @classmethod
    def description(cls, context, properties):
        return ('Показать/скрыть рамки слоя «' + properties.group_name +
                '». Работает до импорта и не зависит от выбранной строки или галочек. '
                'Повторное нажатие убирает только это превью; при показе вид приближается к слою.')

    def execute(self, context):
        from .spatial_preview import has_preview, clear_preview, preview_bounds, align_previews
        props = context.scene.xivgate_revit_bridge
        if props.busy or runtime.MAINTENANCE or self.kind not in ('category', 'workset', 'type', 'element'):
            return {'CANCELLED'}
        key = preview_group_key(props, self.kind, self.group_id)
        if has_preview(context.scene, key):
            clear_preview(context.scene, key)
            return {'FINISHED'}
        field = 'element_id' if self.kind == 'element' else self.kind + '_id'
        candidates = [row for row in props.elements if getattr(row, field) == self.group_id]
        rows = []
        for row in candidates:
            if row.bbox_json:
                try:
                    low, high = json.loads(row.bbox_json)
                    rows.append(dict(id=row.element_id, bbox_min=low, bbox_max=high))
                except (ValueError, TypeError):
                    pass
        if not rows:
            self.report({'INFO'}, 'В каталоге нет габаритов этого слоя. Нажмите «Получить список» заново; некоторые служебные элементы не имеют габаритов.')
            return {'CANCELLED'}
        try:
            offset = catalog_preview_offset(props)
            result = preview_bounds(context.scene, rows, offset, limit=len(rows), group_key=key, label=self.group_name)
            if not result['object_name']:
                self.report({'INFO'}, 'У элементов этого слоя нет корректных габаритов для превью')
                return {'CANCELLED'}
            align_previews(context.scene, offset, preview_parent(context.scene, props))
            frame_preview(context, bpy.data.objects[result['object_name']])
            self.report({'INFO'}, f"Превью «{self.group_name}»: {result['shown']} рамок; повторная кнопка скрывает этот слой")
            return {'FINISHED'}
        except (ValueError, TypeError) as error:
            self.report({'INFO'}, 'Не удалось показать превью: ' + str(error))
            return {'CANCELLED'}


class RBB_OT_check_update(bpy.types.Operator):
    bl_idname = 'xivgate_rbb.check_update'
    bl_label = 'Проверить обновления'
    bl_description = 'Получить опубликованные версии с GitHub для стабильного канала и раннего доступа; установка отдельно'
    def execute(self, context):
        try:
            updater.start_check()
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class RBB_OT_install_update(bpy.types.Operator):
    bl_idname = 'xivgate_rbb.install_update'
    bl_label = 'Установить обновление'
    bl_description = 'Скачать проверенный релиз AGR с GitHub и загрузить новую версию в этом Blender'
    def invoke(self, context, event):
        props = context.scene.xivgate_revit_bridge
        info = updater.info_for_props(props)
        if not updater._installable(info):
            self.report({'ERROR'}, 'Выберите версию для установки')
            return {'CANCELLED'}
        self.target_version = info['version']
        updater.select_channel(props.release_channel)
        updater.select_release(self.target_version)
        return context.window_manager.invoke_props_dialog(self, width=560)

    def draw(self, context):
        info = updater.STATE.info or {}
        self.layout.label(text=f'Установить AGR Revit Bridge {self.target_version}?')
        if info.get('channel') == 'early':
            self.layout.label(text='Ранний доступ: сборка может содержать ошибки.', icon='ERROR')
        if updater.updates._version(self.target_version) < updater.updates._version(updater.current_version()):
            self.layout.label(text='Переход на предыдущую версию. Новые функции станут недоступны.', icon='INFO')
        self.layout.label(text='Рабочий процесс Revit будет закрыт перед установкой.')

    def execute(self, context):
        try:
            if hasattr(self, 'target_version') and (not updater.STATE.info or updater.STATE.info['version'] != self.target_version):
                raise RuntimeError('Выбранная версия изменилась; повторите установку')
            updater.start_install()
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


def draw_settings(layout, props):
    connection = layout.box()
    connection.label(text='Подключение Revit', icon='PLUGIN')
    connection.prop(props, 'revit_exe', text='Программа Revit')
    row = connection.row(align=True)
    row.operator('xivgate_rbb.choose_revit', text='Автопоиск Revit', icon='VIEWZOOM')
    row.operator('xivgate_rbb.setup_revit', text='Подключить Revit', icon='PLUGIN')
    if runtime.STATE.worker is not None:
        connection.operator('xivgate_rbb.cancel', text='Закрыть рабочий Revit', icon='CANCEL')

    options = layout.box()
    options.label(text='Настройки импорта', icon='IMPORT')
    for field in ('preserve_instances', 'import_materials', 'import_textures',
                  'pack_images', 'center_model', 'detail_level'):
        options.prop(props, field)
    if props.import_id:
        options.operator('xivgate_rbb.import_selected', text='Перевыгрузить из Revit', icon='FILE_REFRESH').force_refresh = True

    storage = layout.box()
    storage.label(text='Рабочие данные', icon='FILE_FOLDER')
    storage.prop(props, 'cache_directory', text='Своя папка')
    if not props.cache_directory:
        storage.label(text='Авто: рядом с .blend' if bpy.data.filepath else 'Авто: сначала сохраните .blend')
    storage.prop(props, 'reuse_export_cache')
    storage.operator('xivgate_rbb.clear_cache', icon='TRASH')


class RBB_PT_panel(bpy.types.Panel):
    bl_label = 'AGR Revit Bridge'
    bl_idname = 'RBB_PT_local_revit'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'AGR'

    def draw(self, context):
        layout = self.layout
        props = context.scene.xivgate_revit_bridge
        files = layout.column()
        files.enabled = not props.busy and not runtime.MAINTENANCE
        files.prop(props, 'source_rvt')
        row = files.row(align=True)
        row.operator('xivgate_rbb.scan', text='Получить список', icon='VIEWZOOM')
        box = layout.box()
        icon = 'ERROR' if props.status == 'ERROR' else 'QUESTION' if props.status == 'REQUIRES_ACTION' else 'INFO'
        for index, line in enumerate(textwrap.wrap(props.message, width=48)[:8]):
            box.label(text=line, icon=icon if index == 0 else 'NONE')
        if props.busy:
            box.progress(factor=props.progress, type='BAR', text='Обработка модели')
            row = box.row(align=True)
            row.operator('xivgate_rbb.cancel', icon='CANCEL')
            row.operator('xivgate_rbb.show_worker', icon='WINDOW')
        if props.elements:
            selection = layout.column()
            selection.enabled = not props.busy and not runtime.MAINTENANCE
            selection.prop(props, 'search', text='', icon='VIEWZOOM')
            selection.template_list('RBB_UL_catalog_tree', '', props, 'tree_rows', props, 'tree_index', rows=10)
            row = selection.row(align=True)
            for mode, label in (('all', 'Все'), ('none', 'Ничего'), ('recommended', 'Рекомендуемые')):
                action = row.operator('xivgate_rbb.select', text=label)
                action.mode, action.scope = mode, 'model'
            selection.label(text=f'Выбрано: {props.selected_count} / {len(props.elements)}')
        settings = layout.column()
        settings.enabled = not props.busy and not runtime.MAINTENANCE
        settings.prop(props, 'show_advanced', icon='TRIA_DOWN' if props.show_advanced else 'TRIA_RIGHT', emboss=False)
        if props.show_advanced:
            draw_settings(settings, props)
        action = layout.row()
        action.enabled = not runtime.MAINTENANCE and not props.busy and bool(props.catalog_source) and props.selected_count > 0
        action.operator('xivgate_rbb.import_selected', text='Обновить импорт' if props.import_id else 'Импортировать выбранное', icon='IMPORT')
        if props.result_collection:
            layout.label(text=props.result_collection, icon='OUTLINER_COLLECTION')
            layout.label(text=f'{props.result_placements} размещений · {props.result_prototypes} прототипов')
            rename = layout.row()
            rename.enabled = not props.busy and not runtime.MAINTENANCE
            rename.operator('xivgate_rbb.readable_names', icon='SORTALPHA')
        if props.warning_count:
            layout.operator('xivgate_rbb.warnings', text=f'Отчёт: {props.warning_count} записей', icon='INFO')
        update = layout.box()
        update.label(text='AGR Revit Bridge ' + updater.current_version())
        check = update.row(align=True)
        check.enabled = not updater.STATE.busy
        check.operator('xivgate_rbb.check_update', text='Получить версии', icon='FILE_REFRESH')
        check.operator('wm.url_open', text='', icon='URL').url = 'https://github.com/' + updater.updates.REPOSITORY
        channels = update.column()
        channels.enabled = not updater.STATE.busy
        channels.prop(props, 'release_channel', expand=True)
        if updater.STATE.releases:
            channels.prop(props, 'release_version')
        if props.release_channel == 'early':
            update.label(text='Ранний доступ: возможны ошибки', icon='ERROR')
        info = updater.info_for_props(props)
        if updater._installable(info):
            install = update.row()
            install.enabled = not props.busy and not updater.STATE.busy and runtime.STATE.action is None
            install.operator('xivgate_rbb.install_update', text='Установить ' + info['version'], icon='IMPORT')
        for line in textwrap.wrap(updater.STATE.message, width=48):
            update.label(text=line)


@persistent
def _load_pre(*args):
    updater.cleanup()
    runtime.cleanup()


@persistent
def _load_post(*args):
    for scene in bpy.data.scenes:
        if scene.library is not None:
            continue
        sync_groups(scene.xivgate_revit_bridge)
        align_scene_previews(scene.xivgate_revit_bridge)


def _initialize_tree():
    # addon_utils.enable restricts bpy.data during register; restore scene data
    # on the next Blender event after that restriction has been lifted.
    if not getattr(RBB_Settings, 'is_registered', False):
        return None
    if not hasattr(bpy.data, 'scenes'):
        return .1
    _load_post()
    return None


CLASSES = (RBB_Element, RBB_Group, RBB_TreeRow, RBB_Warning, RBB_Settings, RBB_UL_catalog_tree, RBB_OT_element_info, RBB_UL_groups, RBB_UL_elements, RBB_UL_warnings, RBB_OT_warnings,
           RBB_OT_choose_revit,
           RBB_OT_toggle_group, RBB_OT_inspect_group, RBB_OT_clear_scope, RBB_OT_copy_report_details, RBB_OT_readable_names,
           RBB_OT_setup, RBB_OT_scan, RBB_OT_import, RBB_OT_select, RBB_OT_cancel,
           RBB_OT_show_worker, RBB_OT_cache, RBB_OT_toggle_preview, RBB_OT_check_update, RBB_OT_install_update, RBB_PT_panel)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.xivgate_revit_bridge = PointerProperty(type=RBB_Settings)
    if _load_pre not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_load_pre)
    if _load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_load_post)
    if hasattr(bpy.data, 'scenes'):
        _load_post()
    elif not bpy.app.timers.is_registered(_initialize_tree):
        bpy.app.timers.register(_initialize_tree, first_interval=.1)


def unregister():
    if bpy.app.timers.is_registered(_initialize_tree):
        bpy.app.timers.unregister(_initialize_tree)
    updater.cleanup()
    runtime.cleanup()
    if _load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_load_pre)
    if _load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post)
    del bpy.types.Scene.xivgate_revit_bridge
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
