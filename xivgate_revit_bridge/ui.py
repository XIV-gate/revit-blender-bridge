"""User-facing Blender panel. All Blender calls happen on its main thread."""
import os
from pathlib import Path
import textwrap

import bpy
from bpy.app.handlers import persistent
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       IntProperty, PointerProperty, StringProperty)

from . import runtime, updater, workflow
from .core.catalog import initial_selection


_SYNCING = False
_RELEASE_ITEMS = []


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


class RBB_Warning(bpy.types.PropertyGroup):
    message: StringProperty()


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
    cache_directory: StringProperty(name='Рабочая папка', subtype='DIR_PATH', description='Папка для рабочей копии RVT, геометрии и журналов')
    reuse_export_cache: BoolProperty(name='Использовать проверенный кэш выгрузки', default=True)
    catalog_worker_key: StringProperty()
    addin_folder: StringProperty(name='Каталог подключения Revit', subtype='DIR_PATH', description='Обычно определяется автоматически; нужен только при нестандартной установке')
    preserve_instances: BoolProperty(name='Сохранять инстансы', default=True, description='Редактируемые Mesh-объекты с общими данными, как Alt+D')
    import_materials: BoolProperty(name='Переносить материалы', default=True)
    import_textures: BoolProperty(name='Переносить доступные текстуры', default=True)
    pack_images: BoolProperty(name='Упаковать текстуры в .blend', default=True)
    center_model: BoolProperty(name='Разместить около начала координат', default=True)
    load_links: BoolProperty(default=False)
    detail_level: EnumProperty(name='Детализация', items=[('coarse', 'Низкая', ''), ('medium', 'Средняя', ''), ('fine', 'Высокая', '')], default='medium')
    show_advanced: BoolProperty(name='Настройки', default=False)
    list_mode: EnumProperty(name='Показать', items=[('categories', 'Категории', ''), ('worksets', 'Рабочие наборы', ''), ('types', 'Типы', ''), ('elements', 'Объекты', '')])
    search: StringProperty(name='Поиск', options={'TEXTEDIT_UPDATE'})
    elements: CollectionProperty(type=RBB_Element)
    categories: CollectionProperty(type=RBB_Group)
    worksets: CollectionProperty(type=RBB_Group)
    types: CollectionProperty(type=RBB_Group)
    element_index: IntProperty()
    category_index: IntProperty()
    workset_index: IntProperty()
    type_index: IntProperty()
    preview_only_unselected: BoolProperty(name='Границы только исключённых', default=True)
    selected_count: IntProperty()
    busy: BoolProperty(default=False, options={'SKIP_SAVE'})
    status: StringProperty(default='IDLE', options={'SKIP_SAVE'})
    message: StringProperty(default='Выберите RVT и получите список элементов.')
    progress: FloatProperty(min=0, max=1, default=0)
    import_id: StringProperty()
    import_source: StringProperty()
    import_context_json: StringProperty()
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
            text = ' '.join((item.name, item.category_name, item.workset_name, item.type_name, item.family_name, item.element_id)).casefold()
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
        layout.label(text='Будет подключён обработчик Revit через файл:')
        for line in textwrap.wrap(self.plan['path'], 80):
            layout.label(text=line)
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
        for line in textwrap.wrap(self.plan['path'], 90):
            self.layout.label(text=line)
        self.layout.label(text='Копия RVT и промежуточные данные:')
        for line in textwrap.wrap(self.cache, 90):
            self.layout.label(text=line)
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
    bl_options = {'REGISTER', 'UNDO'}
    force_refresh: BoolProperty(default=False, options={'SKIP_SAVE'})
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


class RBB_OT_cache(bpy.types.Operator):
    bl_idname = 'xivgate_rbb.clear_cache'
    bl_label = 'Очистить завершённые задания'

    def invoke(self, context, event):
        try:
            self.info = workflow.cache_info(context.scene.xivgate_revit_bridge)
            return context.window_manager.invoke_props_dialog(self, width=600)
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


    def draw(self, context):
        self.layout.label(text=f"Завершённых заданий: {self.info['eligible_jobs']}; {self.info['eligible_bytes'] / 1024**2:.1f} МБ")
        for line in textwrap.wrap(self.info['root'], 85):
            self.layout.label(text=line)
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


class RBB_OT_preview(bpy.types.Operator):
    bl_idname = 'xivgate_rbb.preview_bounds'
    bl_label = 'Показать границы группы'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        import json
        from .spatial_preview import preview_bounds
        props = context.scene.xivgate_revit_bridge
        try:
            if not props.origin_valid and props.center_model:
                raise ValueError('Сначала импортируйте основной набор, чтобы совместить предпросмотр с моделью')
            if props.list_mode == 'elements':
                candidates = [props.elements[props.element_index]] if 0 <= props.element_index < len(props.elements) else []
            else:
                group, index, kind = {'categories': (props.categories, props.category_index, 'category'),
                    'worksets': (props.worksets, props.workset_index, 'workset'),
                    'types': (props.types, props.type_index, 'type')}[props.list_mode]
                key = group[index].group_id if 0 <= index < len(group) else None
                candidates = [row for row in props.elements if getattr(row, kind + '_id') == key]
            rows = []
            for row in candidates:
                if row.bbox_json and (not props.preview_only_unselected or not row.selected):
                    minimum, maximum = json.loads(row.bbox_json)
                    rows.append(dict(id=row.element_id, name=row.name, bbox_min=minimum, bbox_max=maximum))
            if not rows:
                raise ValueError('Нет границ для предпросмотра. Получите новый список или отключите фильтр исключённых.')
            offset = json.loads(props.source_offset_json) if props.origin_valid and props.origin_centered else (0, 0, 0)
            result = preview_bounds(context.scene, rows, offset)
            # Follow a deliberately moved imported root, without changing the user's view.
            if result['object_name'] and props.result_collection in bpy.data.collections:
                root = next((obj for obj in bpy.data.collections[props.result_collection].objects
                             if obj.type == 'EMPTY' and 'revit:source_offset_metres' in obj), None)
                if root is not None:
                    bpy.data.objects[result['object_name']].matrix_world = root.matrix_world
            self.report({'INFO'}, f"Показано границ: {result['shown']} из {result['total']}; геометрия не загружалась")
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class RBB_OT_clear_preview(bpy.types.Operator):
    bl_idname = 'xivgate_rbb.clear_preview'
    bl_label = 'Убрать границы'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from .spatial_preview import clear_preview
        clear_preview(context.scene)
        return {'FINISHED'}



class RBB_OT_check_update(bpy.types.Operator):
    bl_idname = 'xivgate_rbb.check_update'
    bl_label = 'Проверить обновления'
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
        layout.label(text='Ранний доступ ' + updater.current_version(), icon='EXPERIMENTAL')
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
            selection.prop(props, 'list_mode', expand=True)
            selection.prop(props, 'search', text='', icon='VIEWZOOM')
            if props.list_mode == 'categories':
                selection.template_list('RBB_UL_groups', 'categories', props, 'categories', props, 'category_index', rows=8)
            elif props.list_mode == 'worksets':
                selection.template_list('RBB_UL_groups', 'worksets', props, 'worksets', props, 'workset_index', rows=8)
            elif props.list_mode == 'types':
                selection.template_list('RBB_UL_groups', 'types', props, 'types', props, 'type_index', rows=8)
            else:
                selection.template_list('RBB_UL_elements', '', props, 'elements', props, 'element_index', rows=8)
                if 0 <= props.element_index < len(props.elements):
                    item = props.elements[props.element_index]
                    selection.label(text='ID: ' + item.element_id + ' · ' + item.workset_name)
                    selection.label(text=item.type_name or item.name)
                    for line in textwrap.wrap(item.reason, 48)[:3]:
                        selection.label(text=line)
            row = selection.row(align=True)
            for mode, label in (('all', 'Все'), ('none', 'Ничего'), ('recommended', 'Рекомендуемые')):
                row.operator('xivgate_rbb.select', text=label).mode = mode
            selection.label(text=f'Выбрано: {props.selected_count} из {len(props.elements)}')
            selection.prop(props, 'preview_only_unselected')
            row = selection.row(align=True)
            row.operator('xivgate_rbb.preview_bounds', text='Границы', icon='SHADING_BBOX')
            row.operator('xivgate_rbb.clear_preview', text='Убрать', icon='X')
        options = layout.column()
        options.enabled = not props.busy and not runtime.MAINTENANCE
        options.prop(props, 'preserve_instances')
        options.prop(props, 'import_materials')
        options.prop(props, 'import_textures')
        options.prop(props, 'show_advanced', icon='TRIA_DOWN' if props.show_advanced else 'TRIA_RIGHT', emboss=False)
        if props.show_advanced:
            options.prop(props, 'revit_exe')
            options.prop(props, 'cache_directory', text='Кэш (пусто — автоматически)')
            options.prop(props, 'reuse_export_cache')
            options.operator('xivgate_rbb.setup_revit', icon='PLUGIN')
            try:
                options.label(text='Кэш: ' + str(workflow.cache_root(props)))
            except ValueError:
                options.label(text='Сохраните .blend или укажите папку кэша', icon='INFO')
            options.operator('xivgate_rbb.clear_cache', icon='TRASH')
            if runtime.STATE.worker is not None:
                options.operator('xivgate_rbb.cancel', text='Закрыть рабочий Revit', icon='CANCEL')
            options.prop(props, 'pack_images')
            options.prop(props, 'center_model')
            options.prop(props, 'detail_level')
            options.prop(props, 'addin_folder')
            if props.import_id:
                options.operator('xivgate_rbb.import_selected', text='Перевыгрузить из Revit', icon='FILE_REFRESH').force_refresh = True
        action = layout.row()
        action.enabled = not runtime.MAINTENANCE and not props.busy and bool(props.catalog_source) and props.selected_count > 0
        action.operator('xivgate_rbb.import_selected', text='Обновить импорт' if props.import_id else 'Импортировать выбранное', icon='IMPORT')
        if props.result_collection:
            layout.label(text=props.result_collection, icon='OUTLINER_COLLECTION')
            layout.label(text=f'{props.result_placements} размещений · {props.result_prototypes} прототипов')
        if props.warning_count:
            layout.operator('xivgate_rbb.warnings', text=f'Отчёт: {props.warning_count} предупреждений', icon='INFO')
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


CLASSES = (RBB_Element, RBB_Group, RBB_Warning, RBB_Settings, RBB_UL_groups, RBB_UL_elements, RBB_UL_warnings, RBB_OT_warnings,
           RBB_OT_setup, RBB_OT_scan, RBB_OT_import, RBB_OT_select, RBB_OT_cancel,
           RBB_OT_show_worker, RBB_OT_cache, RBB_OT_preview, RBB_OT_clear_preview, RBB_OT_check_update, RBB_OT_install_update, RBB_PT_panel)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.xivgate_revit_bridge = PointerProperty(type=RBB_Settings)
    if _load_pre not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_load_pre)


def unregister():
    updater.cleanup()
    runtime.cleanup()
    if _load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_load_pre)
    del bpy.types.Scene.xivgate_revit_bridge
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
