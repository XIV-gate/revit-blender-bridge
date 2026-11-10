# XIVgate Revit Blender Bridge

User-facing text is Russian. Author and brand: XIVgate. Never attribute authorship to AI vendors.
Preserve originals by default; Revit opens only job-owned copies. The explicitly selected overwrite_source option may atomically replace the selected local RVT with the verified prepared snapshot. Test that option only on disposable fixtures, never on user assets. Blender scenes and Preferences remain protected.
No mouse/keyboard automation or window activation by the development agent.
Do not write Revit .addin manifests or install into live application profiles without explicit approval of the exact destination.
Source belongs here; all generated binaries, Blender profiles, fixtures, and caches go to the approved task workspace outside this repository. Do not commit absolute workstation paths, credentials, SDK DLLs, private models, or generated binaries.
Use the protocol in docs/PROTOCOL.md. Keep bpy and Autodesk API integration separate from host-independent logic.
Run targeted tests before claiming behavior. DCC test processes must be isolated as specified by the workspace owner.
