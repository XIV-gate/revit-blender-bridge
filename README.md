# AGR Revit Bridge

Version 0.2.0, developed by XIVgate. Import selected Revit geometry from Blender: choose an RVT, scan categories/worksets/elements, and import or reimport with shared prototypes and materials. The panel is alongside AGR Prepare, AGR Output and AGR Collision in the **AGR** sidebar tab.

Revit is a required local processing host. The companion Revit add-in runs API calls on Revit's main thread. No cloud conversion or desktop input automation is used. Original RVT files are copied before opening; upgrades apply only to the copy. Unknown dialogs are reported as requiring user action.

The bundled worker targets Revit 2027 (.NET 10). Blender 5.2 LTS is the primary host, tested with 5.2.3; additional compatibility is tested with 4.2.14 LTS. Other Revit versions require a separately built and validated worker. The live old-model smoke test is tracked separately in [verification](docs/VERIFICATION.md); synthetic controller tests do not replace it.

[Инструкция для пользователя](docs/USER_GUIDE_RU.md).

Install `agr_revit_bridge-0.2.0.zip` through **Preferences → Add-ons → Install from Disk**.
For later versions, use **Check for Updates / Install Update** in the panel. The
updater reads the latest published stable GitHub Release from this repository,
verifies GitHub's archive SHA256 and every managed file, then reloads the add-on.
Scene data, settings and Preferences are preserved. Revit worker binaries use
separate version folders so an older DLL held by a running Revit is not replaced.
Unknown/foreign Revit registrations are never overwritten.

See [update design and release procedure](docs/UPDATES.md) and
[0.2.0 release notes](docs/RELEASE_NOTES_0_2_0.md). A public release is required
for anonymous updates; private drafts are deliberately not offered as updates.

Source layout: `core/` contains host-independent selection/jobs/installer/launcher;
`runtime.py` controls asynchronous work; `ui.py` contains Blender UI;
`updater.py` coordinates background downloads and main-thread reload;
`conversion.py` builds USD with bundled Python; `blender_import.py` validates
placements and replaces owned revisions; `revit_worker/` contains the C# exporter.

Build using `revit_worker/build.ps1 -OutputDir <approved-existing-directory>`,
then `python tools/build_package.py --worker <Worker.dll> --output <fresh.zip>`.
Only the compiled XIVgate DLL is included; Autodesk SDK assemblies are not redistributed.

Pure tests: set `XIVGATE_TEST_ROOT` to an explicitly approved existing directory,
disable bytecode, then run `python -m unittest discover -s tests`.
Host tests: `python tools/run_blender_test.py --blender <blender.exe> --workspace
<approved-existing-directory-on-D:>`; use `--script test_controller_integration.py`
to exercise the real converter subprocess with a test-only Revit I/O double.
For isolated ZIP installation set `RBB_PACKAGE_NAME` to the filename below that
workspace and use `--script test_package_install.py`. Tests never save Preferences.
Use `--script test_update_integration.py` to test verified installation, hot reload,
locked old DLLs, Revit registration migration, failed-release rollback, and
cancellation on file load with an injected network transport in the isolated profile.

See [design](docs/DESIGN.md), [protocol](docs/PROTOCOL.md), and [implementation plan](docs/PLAN.md).
