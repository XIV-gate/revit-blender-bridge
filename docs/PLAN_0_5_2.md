# 0.5.2 — family aggregation before Blender

User-authorized scope: publish tested 0.5.1, then implement, test and publish
0.5.2 in Early Access. Stable remains 0.5.0. No live Preferences or original
models are overwritten. All test profiles/artifacts stay under the previously
approved RevitBlenderBridge workspace.

1. Release 0.5.1 from its clean, verified checkpoint with canonical ZIP and
   public updater verification. Freeze its tag before 0.5.2 edits.
2. Converter/core: three modes OBJECTS, INSTANCES, FAMILY. FAMILY bypasses fuzzy
   matching and streams transformed raw geometry into one Mesh per category /
   family, falling back to category/type for system elements. Bound large chunks
   to 1 million triangles; preserve UV, face material, normals, mirrored winding
   and double-precision placement with local pivots. Never weld vertices.
3. Provenance: face int32 source_index plus JSON table of original string IDs
   and ranges; include provenance in geometry signatures. Preserve these explicitly
   in Blender. Contextual face selection permits editing one source element.
4. UI/runtime: replace the checkbox with the mode menu; migrate legacy choices.
   Existing scenes retain their mode. Cache snapshot identity stays independent
   of grouping; conversion context includes mode. FAMILY selection changes always
   reconvert a verified raw snapshot or request a full export, never remove only
   part of a merged mesh with object-level toggles or append duplicate families.
5. Validate pure transformation/chunk/provenance logic, mirror/UV/material
   preservation, all three host modes, selection subset/restore and updates in
   isolated Blender 4.2/5.2. Benchmark real expanded geometry/object counts and a
   many-object fixture. Report conversion and Blender import separately.
6. Build all versioned worker variants (metadata only), retain frozen loaders,
   verify/package/install/rollback, publish v0.5.2 prerelease and confirm its public
   download through the updater. Update user guide and release notes with measured
   results and limitations; do not promise an untested 803 MB RVT import time.
