# XIVgate Revit Blender Bridge

Version 0.1.0, under development. A local Windows workflow operated from Blender: select Revit and an RVT, scan categories/worksets/elements, choose geometry, and import or reimport with shared prototypes and materials.

Revit is a required local processing host. The companion Revit add-in runs API calls on Revit's main thread. No cloud conversion or desktop input automation is used. Original RVT files are copied before opening; upgrades apply only to the copy. Unknown dialogs are reported as requiring user action.

The first target is Revit 2027 and Blender 4.2/5.2. Other Revit builds are not promised without a version-specific worker and validation. Installation and end-to-end verification are still pending.

See [design](docs/DESIGN.md), [protocol](docs/PROTOCOL.md), and [implementation plan](docs/PLAN.md).
