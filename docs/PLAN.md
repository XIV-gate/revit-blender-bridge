# Implementation plan

Goal: the first working local Blender panel for catalog selection and Revit-backed import/reimport, without Dynamo or desktop automation.

- [ ] Establish protocol and clean private repository, version 0.1.0.
- [ ] Implement pure Python catalog/selection/job/launcher core with stale-response, cancellation, path, and source-preservation tests.
- [ ] Implement and compile the Revit 2027 scan/export worker; keep installer writes dry-run until approved.
- [ ] Implement Blender UI and main-thread polling; integrate native/shared-prototype import and selective collection replacement.
- [ ] Validate Blender adapters and generated geometry/materials in isolated Blender 4.2/5.2.
- [ ] Build complete ZIP without SDK DLLs or machine-specific paths; inspect a concrete Revit registration pointer.
- [ ] Obtain approval of the exact Revit profile installation path, then test launch, old-model opening, scan, selective import, and reimport.
- [ ] Review source, run fresh checks, commit tested changes, and verify the private remote.

Parallel tasks have disjoint ownership: core Python, Revit C#, Blender adapter/UI. The root agent owns integration, packaging, live-host permission boundaries, and final verification.
