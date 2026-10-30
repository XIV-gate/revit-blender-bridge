# Local RVT import from Blender

The product owner approved a Blender-only normal workflow: choose the Revit executable and RVT; scan; select categories, worksets, or individual elements; import; adjust the selection and reimport. Expose preserve instances, materials, textures, and source origin options. Suggested selections are initial choices, not forced exclusions.

Architecture:
1. Blender panel, searchable lists, progress and recoverable errors. Blender API calls remain on the main thread.
2. Host-independent Python job protocol, file copying, asynchronous subprocess lifecycle, version checks and cache management. No network listener.
3. Revit 2027 companion IExternalApplication, registered by an explicit setup operation. It activates only in a process carrying this bridge's session environment. Revit API calls occur on Idling/main thread. An already open user Revit session is never controlled or closed.
4. Revit emits a semantic catalog and compact shared-prototype geometry cache. Blender converts/imports locally using its bundled Python/USD libraries, with no Houdini dependency.
5. Owned Blender collections are updated transactionally; unrelated objects are preserved. Keep source IDs and import identity for selection persistence and reimport.

Known informational upgrade conditions are logged. Unknown dialogs, license/sign-in, security prompts, and destructive model repair choices require human action; the plugin reports them and offers a user-invoked way to show its own Revit process. No blanket dialog acceptance.

Installation of the companion requires an owned .addin pointer in Revit's supported registration directory. Do not perform this external-profile write until the payload is built and the exact destination is approved. Existing manifests are never overwritten if foreign. No Autodesk DLLs are redistributed.

Runtime artifacts go to an explicitly configured working directory. Tests use approved isolated task directories. No original .rvt is upgraded/saved in place. Cache source identity includes the original hash and Revit build. Cancellation may stop only a process launched and owned by the current bridge session.

The pilot's native USD instance transform defect is a regression requirement: expected world transforms must include ancestors and axis conversion. Test mirrored placements and preserve material subsets, UVs, images, and source metadata.
