# AGR Revit Bridge 0.3.1 — Early Access

Experimental workflow built on stable0.3.0. Choose it explicitly in the Early
Access channel; stable0.3.0 remains the latest ordinary release.

- One scan action prepares the local connection after showing its registration
  path. Installed Revit is detected automatically; manual paths remain in settings.
- Default project cache below the saved.blend: Data/Cache/RevitBridge.
- Local reversible selection toggles preserve the same objects and their mesh edits.
- Export only previously unknown added IDs; merge verified new geometry while
  preserving existing objects, names, root transform and shared editable meshes.
- Ambiguous composites and geometry-free additions fall back to a full source scan
  and export. Existing geometry survives failures.
- Type/family grouping and search; lightweight bounds preview for excluded elements.
- Verified export reuse after Revit shutdown. Source/dependency changes and corrupt
  payloads invalidate cache. Unknown external references disable reuse.
- Explicit completed-job cleanup protects active/unknown jobs and external files
  referenced by currently loaded Blender scenes.

Profiles and original RVT/Blender assets are never overwritten by automated tests.
Blender4.2.14 and5.2.3 exercised with real USD conversion and controlled Revit I/O
fixtures. Revit2024–2026 workers compile against same-year API references, but full
document open/export in these installed hosts is not verified here; only2027 is
installed. Linked RVT geometry remains unsupported. New worker DLLs are unsigned;
Revit may require an explicit trust decision.

Missing facade details cannot be identified conclusively from a screenshot alone.
Use a fresh catalog, Types/Objects and excluded-element bounds to locate candidates
before loading geometry. A new type does not require replacing the whole building
unless its source dependencies require that.

From0.2.0 install0.3.0 ZIP once manually, then choose Early Access0.3.1 from the UI.
You can return to0.3.0 through the same release browser.
