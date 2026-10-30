"""User-facing Blender panel. All Blender calls happen on its main thread."""
import os
from pathlib import Path
import textwrap

import bpy
from bpy.app.handlers import persistent
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       IntProperty, PointerProperty, StringProperty)

from . import runtime
from .core.catalog import initial_selection
from .core.installer import install_manifest


_SYNCING = False


def _props(item):
    return item.id_data.xivgate_revit_bridge


def sync_groups(props):
    global _SYNCING
    previous = _SYNCING
    _SYNCING = True
    try:
        counts = {'category': {}, 'workset': {}}
        for item in props.elements:
            for kind in counts:
                key = getattr(item, kind + '_id')
                total, selected = counts[kind].get(key, (0, 0))
                counts[kind][key] = (total + 1, selected + int(item.selected))
        for kind, rows in (('category', props.categories), ('workset', props.worksets)):
            for item in rows:
                item.count, item.selected_count = counts[kind].get(item.group_id, (0, 0))
                item.selected = item.count > 0 and item.count == item.selected_count
        props.selected_count = sum(item.selected for item in props.elements)
    finally:
        _SYNCING = previous


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


class RBB_Warning(bpy.types.PropertyGroup):
    message: StringProperty()


def _default_revit():
    root = os.environ.get('ProgramW6432') or os.environ.get('PROGRAMFILES')
    if root:
        path = Path(root) / 'Autodesk' / 'Revit 2027' / 'Revit.exe'
        if path.is_file():
            return str(path)
    return ''


class RBB_Settings(bpy.types.PropertyGroup):
    source_rvt: StringProperty(name='Файл RVT', subtype='FILE_PATH')
    revit_exe: StringProperty(name='Revit.exe', subtype='FILE_PATH', default=_default_revit())
    cache_directory: StringProperty(name='Рабочая папка', subtype='DIR_PATH', description='Папка для рабочей копии RVT, геометрии и журналов')
    addin_folder: StringProperty(name='Каталог подключения Revit', subtype='DIR_PATH', description='Обычно определяется автоматически; нужен только при нестандартной установке')
    preserve_instances: BoolProperty(name='Сохранять инстансы', default=True)
    import_materials: BoolProperty(name='Переносить материалы', default=True)
    import_textures: BoolProperty(name='Переносить доступные текстуры', default=True)
    pack_images: BoolProperty(name='Упаковать текстуры в .blend', default=True)
    center_model: BoolProperty(name='Разместить около начала координат', default=True)
    load_links: BoolProperty(default=False)
    detail_level: EnumProperty(name='Детализация', items=[('coarse', 'Низкая', ''), ('medium', 'Средняя', ''), ('fine', 'Высокая', '')], default='medium')
    show_advanced: BoolProperty(name='Настройки', default=False)
    list_mode: EnumProperty(name='Показать', items=[('categories', 'Категории', ''), ('worksets', 'Рабочие наборы', ''), ('elements', 'Объекты', '')])
    search: StringProperty(name='Поиск', options={'TEXTEDIT_UPDATE'})
    elements: CollectionProperty(type=RBB_Element)
    categories: CollectionProperty(type=RBB_Group)
    worksets: CollectionProperty(type=RBB_Group)
    element_index: IntProperty()
    category_index: IntProperty()
    workset_index: IntProperty()
    selected_count: IntProperty()
    busy: BoolProperty(default=False, options={'SKIP_SAVE'})
    status: StringProperty(default='IDLE', options={'SKIP_SAVE'})
    message: StringProperty(default='Выберите RVT и получите список элементов.')
    progress: FloatProperty(min=0, max=1, default=0)
    import_id: StringProperty()
    import_source: StringProperty()
    source_offset_json: StringProperty(default='[0,0,0]')
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
        layout.label(text=item.message.replace('\n', ' '), icon='INFO')


class RBB_OT_warnings(bpy.types.Operator):
    bl_idname = 'xivgate_rbb.warnings'
    bl_label = 'Отчёт Revit'

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=800)

    def draw(self, context):
        props = context.scene.xivgate_revit_bridge
        self.layout.template_list('RBB_UL_warnings', '', props, 'warnings', props, 'warning_index', rows=10)
        if 0 <= props.warning_index < len(props.warnings):
            for line in textwrap.wrap(props.warnings[props.warning_index].message, width=100):
                self.layout.label(text=line)

    def execute(self, context):
        return {'FINISHED'}


class RBB_UL_groups(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, 'selected', text='')
        row.label(text=item.name)
        row.label(text=f'{item.selected_count}/{item.count}')

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

    def filter_items(self, context, data, propname):
        query = context.scene.xivgate_revit_bridge.search.casefold().strip()
        flags = []
        for item in getattr(data, propname):
            text = ' '.join((item.name, item.category_name, item.workset_name, item.element_id)).casefold()
            flags.append(self.bitflag_filter_item if not query or query in text else 0)
        return flags, []


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
        layout.label(text='Будет создан служебный файл подключения Revit:')
        for line in textwrap.wrap(self.plan['path'], 80):
            layout.label(text=line)
        layout.label(text='Текущий открытый Revit не изменяется. DLL работает только в процессе плагина.')

    def execute(self, context):
        try:
            plan = runtime.setup_plan(context.scene.xivgate_revit_bridge)
            path = install_manifest(plan)
            self.report({'INFO'}, 'Обработчик подключён: ' + str(path))
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class RBB_OT_scan(bpy.types.Operator):
    bl_idname = 'xivgate_rbb.scan'
    bl_label = 'Получить список элементов'
    def execute(self, context):
        try:
            runtime.begin_scan(context.scene)
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class RBB_OT_import(bpy.types.Operator):
    bl_idname = 'xivgate_rbb.import_selected'
    bl_label = 'Импортировать выбранное'
    bl_options = {'REGISTER', 'UNDO'}
    def execute(self, context):
        try:
            runtime.begin_export(context.scene)
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class RBB_OT_select(bpy.types.Operator):
    bl_idname = 'xivgate_rbb.select'
    bl_label = 'Изменить выбор'
    mode: EnumProperty(items=[('all', 'Всё', ''), ('none', 'Ничего', ''), ('recommended', 'Рекомендуемое', '')])
    def execute(self, context):
        global _SYNCING
        props = context.scene.xivgate_revit_bridge
        _SYNCING = True
        try:
            for item in props.elements:
                item.selected = self.mode == 'all' or (self.mode == 'recommended' and item.recommendation != 'exclude')
        finally:
            _SYNCING = False
        sync_groups(props)
        return {'FINISHED'}


class RBB_OT_cancel(bpy.types.Operator):
    bl_idname = 'xivgate_rbb.cancel'
    bl_label = 'Отменить'
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


class RBB_PT_panel(bpy.types.Panel):
    bl_label = 'Revit → Blender'
    bl_idname = 'RBB_PT_local_revit'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'XIVgate'

    def draw(self, context):
        layout = self.layout
        props = context.scene.xivgate_revit_bridge
        files = layout.column()
        files.enabled = not props.busy
        files.prop(props, 'source_rvt')
        files.prop(props, 'revit_exe')
        files.prop(props, 'cache_directory')
        row = files.row(align=True)
        row.operator('xivgate_rbb.setup_revit', text='Подключить Revit', icon='PLUGIN')
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
            selection.enabled = not props.busy
            selection.prop(props, 'list_mode', expand=True)
            selection.prop(props, 'search', text='', icon='VIEWZOOM')
            if props.list_mode == 'categories':
                selection.template_list('RBB_UL_groups', 'categories', props, 'categories', props, 'category_index', rows=8)
            elif props.list_mode == 'worksets':
                selection.template_list('RBB_UL_groups', 'worksets', props, 'worksets', props, 'workset_index', rows=8)
            else:
                selection.template_list('RBB_UL_elements', '', props, 'elements', props, 'element_index', rows=8)
                if 0 <= props.element_index < len(props.elements):
                    item = props.elements[props.element_index]
                    selection.label(text='ID: ' + item.element_id + ' · ' + item.workset_name)
                    for line in textwrap.wrap(item.reason, 48)[:3]:
                        selection.label(text=line)
            row = selection.row(align=True)
            for mode, label in (('all', 'Все'), ('none', 'Ничего'), ('recommended', 'Рекомендуемые')):
                row.operator('xivgate_rbb.select', text=label).mode = mode
            selection.label(text=f'Выбрано: {props.selected_count} из {len(props.elements)}')
        options = layout.column()
        options.enabled = not props.busy
        options.prop(props, 'preserve_instances')
        options.prop(props, 'import_materials')
        options.prop(props, 'import_textures')
        options.prop(props, 'show_advanced', icon='TRIA_DOWN' if props.show_advanced else 'TRIA_RIGHT', emboss=False)
        if props.show_advanced:
            options.prop(props, 'pack_images')
            options.prop(props, 'center_model')
            options.prop(props, 'detail_level')
            options.prop(props, 'addin_folder')
        action = layout.row()
        action.enabled = not props.busy and bool(props.catalog_source) and props.selected_count > 0
        action.operator('xivgate_rbb.import_selected', text='Обновить импорт' if props.import_id else 'Импортировать выбранное', icon='IMPORT')
        if props.result_collection:
            layout.label(text=props.result_collection, icon='OUTLINER_COLLECTION')
            layout.label(text=f'{props.result_placements} размещений · {props.result_prototypes} прототипов')
        if props.warning_count:
            layout.operator('xivgate_rbb.warnings', text=f'Отчёт: {props.warning_count} предупреждений', icon='INFO')


@persistent
def _load_pre(*args):
    runtime.cleanup()


CLASSES = (RBB_Element, RBB_Group, RBB_Warning, RBB_Settings, RBB_UL_groups, RBB_UL_elements, RBB_UL_warnings, RBB_OT_warnings,
           RBB_OT_setup, RBB_OT_scan, RBB_OT_import, RBB_OT_select, RBB_OT_cancel,
           RBB_OT_show_worker, RBB_PT_panel)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.xivgate_revit_bridge = PointerProperty(type=RBB_Settings)
    if _load_pre not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_load_pre)


def unregister():
    runtime.cleanup()
    if _load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_load_pre)
    del bpy.types.Scene.xivgate_revit_bridge
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
