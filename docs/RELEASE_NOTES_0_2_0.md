# AGR Revit Bridge 0.2.0

- AGR branding and an AGR sidebar panel alongside the other tools.
- Check and install the latest stable GitHub Release from Blender.
- Verify archive/file checksums before installation; restore the previous version if loading fails.
- Versioned Revit workers allow updates while an earlier DLL is held by another Revit process.
- Migrate this installation's 0.1 Revit registration through the Connect Revit button.
- Preserve scene geometry, import settings, selected elements and Blender Preferences.
- Cancel pending update work when another blend is opened; serialize installation across Blender processes.

Requires Windows and installed Revit 2027. Blender 5.2 LTS is primary; 4.2 LTS is
also supported. Existing category/workset/object selection, shared geometry,
material import and selective reimport remain available. Linked RVT geometry and
missing source textures retain their existing limitations.

Install this ZIP once to gain the updater if upgrading from 0.1. Subsequent stable
releases can be installed from the panel. Revit may still request permission to
load a newly built unsigned companion; its trust settings are not bypassed.
