# 0.3 release plan

Approved scope: 0.3.0 stable includes explicit stable/early release selection and
Revit 2024–2027 worker selection. 0.3.1 early access adds automatic setup/cache UX
and reuse of verified exports. Numeric tags remain v0.3.0/v0.3.1; GitHub prerelease
flag defines the channel. main carries stable releases; early-access carries
experimental changes. A user may deliberately return to an older release.

1. Extend verified GitHub catalog and multi-worker package validation. Test
   malformed releases, both channels, downgrade, archives, rollback and locked DLLs.
2. Identify actual installed executable version/runtime, route registration and
   worker by year/runtime. Build against real API references for each supported
   year, retain no Autodesk assemblies in archives. Test routing and failure cases.
3. Add channel/version controls and explicit install confirmation; keep all
   networking off until user action. Check isolated Blender 4.2 and 5.2 reload,
   upgrade and downgrade. Preserve scene/settings and installed registration safety.
4. Build 0.3.0, record coverage limits per Revit version, commit on main.
5. Branch early-access, implement automatic selection/setup/cache with a reviewed
   registration path and conservative ownership. Cache reuse validates source hash,
   export options and payload bytes. Test cache invalidation and original safety.
6. Build 0.3.1, compare packages, publish stable/prerelease only with authorization.

Only Revit2027 is installed locally. Compilation for other versions is evidence of
API compatibility, not a complete host import test. This distinction must remain
visible in documentation and handoff. Original assets and live preferences remain
unchanged. Approved source/test roots and their nested directories are reused.
