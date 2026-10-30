# File protocol, schema 1

Each session has an absolute job directory, session_id UUID, and random token. The Blender launcher sets XIVGATE_RBB_JOB_ROOT and XIVGATE_RBB_SESSION_TOKEN in the owned Revit child's environment. The worker is inert without them. API calls must never occur on file-watcher/timer threads.

JSON files are UTF-8; readers accept a BOM. Writers write a same-directory temporary file and atomically replace the destination. Never consume a partially written request or accept another session's result. request_id values are unique. Worker startup ignores request files from a previous session.

request.json:
```json
{"schema":1,"request_id":"uuid","session_id":"uuid","token":"random","action":"scan","source_rvt":"absolute input.rvt","source_copy":"absolute job/input/model.rvt","output_dir":"absolute job/output/revision","selection":{"include_ids":["5050748"]},"options":{"detail_level":"medium","load_links":false}}
```

Actions: scan, export, shutdown. A scan copies source_rvt to the job-owned source_copy before opening it and retains the opened copy for subsequent exports. Do not save it. include_ids is the complete exact selected element-ID set, not an advisory filter. An empty set exports nothing (fail clearly rather than accidentally exporting all). IDs are strings preserving 64-bit values. options should tolerate omitted fields with documented defaults.

status.json: schema, session_id, request_id, state, message, progress (0..1), optional dialog_id and details. States: starting, opening, scanning, ready, exporting, requires_action, completed, error, closing. It is progress, not a terminal response.

response.json: schema, session_id, request_id, success boolean, action, optional catalog_path, manifest_path, error, warnings. Only a matching request/session may be accepted. Errors retain diagnostic files and do not replace Blender geometry.

catalog.json:
```json
{"schema":1,"source":{"path":"original.rvt","sha256":"...","revit_version":"2027"},"categories":[{"id":"-2000011","name":"Стены","count":5380}],"worksets":[{"id":"1","name":"Фасады","count":100}],"elements":[{"id":"5050748","unique_id":"...","name":"Окно","category_id":"-2000014","category_name":"Окна","workset_id":"1","workset_name":"Фасады","type_id":"123","type_name":"Тип окна","recommendation":"include","reason":"Окно"}],"warnings":[]}
```

Recommendations: include, review, exclude. Review is selected by default. Defaults must not drop all windows based on misleading Interior host tags. Category/workset toggles update element selections; the final explicit ID set is authoritative. Preserve choices by unique_id when rescanning; new items use recommendations. Users can override every suggestion.

Geometry export: manifest.json and geometry.bin inside output_dir. Same schema as the verified pilot with new generic filenames. manifest: schema=1, units=metres, up=Z, source metadata, summary, prototypes, occurrences, materials, warnings. Prototype: id integer, offset, bytes, triangles, sha256. Each triangle uses 104 little-endian bytes: 3 vertices of 8 float32 (position XYZ, normal XYZ, UV), followed by int64 material key. Occurrence: id string, unique_id, name, category, category_id, workset, workset_id, prototype, decision, transform (12 values: basis X,Y,Z rows then translation in metres). Material table keyed by integer-as-string; values retain source IDs, name, RGB in sRGB, transparency 0..1, smoothness, appearance properties and any available copied texture paths. Textures reside below output_dir with relative manifest references. Preserve exact mesh/material signatures when deduplicating.

The manifest is promoted only after binary/textures are complete. Never overwrite an existing successful revision; create a new output revision. Links not supported by a worker must be reported explicitly, never silently treated as exported.
