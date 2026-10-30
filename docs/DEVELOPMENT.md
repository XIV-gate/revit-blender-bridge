# Разработка и сборка

Автор и бренд — XIVgate. Основная стабильная ветка — main; early-access начинается
от той же базы и содержит будущие тестовые исправления. Версии: 0.4.0 stable,
0.4.1/0.4.2… Early Access, после доводки 0.5.0 stable.

## Структура

| Путь | Назначение |
|---|---|
| xivgate_revit_bridge/core | Выбор, задания, версии Revit, установка и проверка пакетов без bpy |
| ui.py | Панель, свойства, операторы и подсказки Blender |
| runtime.py / workflow.py | Задания, локальный состав, кэш и внешние процессы |
| blender_import.py | Проверка и сборка Mesh-инстансов, имена и владение данными |
| spatial_preview.py | Независимые превью слоёв и совмещение координат |
| conversion.py | Промежуточный USD из выгрузки Revit |
| revit_worker | C#-обработчик Revit API |
| tools / tests | Упаковка и изолированные проверки |

## Проверки

Оригинальные модели и живые профили не используются как тестовая среда.
Задайте существующую разрешённую папку на D: через XIVGATE_TEST_ROOT и выполните:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m unittest discover -s tests -q
```

Для Blender используйте tools/run_blender_test.py с --blender, --workspace и
--script. Он изолирует конфигурацию, scripts, datafiles, extensions и временные
файлы. Полезные сценарии: test_selection_ui.py, test_group_preview.py,
test_readable_names.py, test_early_workflow.py (теперь общий стабильный workflow),
test_package_install.py, test_update_integration.py, test_manual_zip_migration.py.
Сетевая проверка опубликованных релизов test_public_update.py требует --online.

## Обработчики и ZIP

VERSION — каноническая версия; её должны повторять bl_info и AssemblyInfo.cs.
Отдельно собираются варианты 2024, 2025, 2025-net10, 2026, 2026-net10 и 2027.
Используйте revit_worker/build.ps1 с WorkerKey, ReferencesRoot и заранее
разрешённым существующим OutputDir. Подробности SDK и CLR —
[в документации обработчика](../revit_worker/README.md).

```powershell
python tools/build_package.py --workers-root <six-variant-directory> --output <fresh.zip>
```

Autodesk API-сборки нужны только для компиляции и не включаются в ZIP.
Пакет содержит VERSION, PACKAGE_MANIFEST.json с контрольными суммами, Python-код,
обработчики и необходимые распространяемые зависимости. Перед публикацией
проверяются ZIP, установка, обновление и откат в обоих поддерживаемых Blender.

## Публикация

GitHub stable — prerelease=false; Early Access — prerelease=true. Имя ассета:
agr_revit_bridge-X.Y.Z.zip, тег vX.Y.Z. Релиз должен быть публичным опубликованным
релизом; черновики не предлагаются обновителем. Уже опубликованный ZIP под тем
же номером не заменяется. Ранний доступ не назначается последним стабильным.
Публикация выполняется только после разрешения владельца продукта.

[Контракт обновителя](UPDATES.md) · [Протокол](PROTOCOL.md) · [Проверки](VERIFICATION.md).
