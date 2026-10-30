# Permanent loader and reliability checks

Approved product intent: one bundled permanent Revit entry point, no paid
certificate, no automatic trust-store/registry edits. Initial trust remains the
user's choice. Replacing the loader, moving the installation or using another
Revit installation can require trust again; ordinary worker updates retain it.

## Design

The Revit manifest points to `runtime/revit/<key>/loader/1/` inside the add-on.
The small loader is versioned independently from the add-on and remains byte
identical during worker updates. It delegates to the packaged versioned worker
only after validating the owned job, host, strict version and expected SHA256.
It stays inert in a normal Revit process without the add-on's job environment.

The old updater rejects additional DLL payload paths. To retain in-app migration,
the package builder embeds the frozen loader payload in a generated Python data
module (base64 plus hashes), covered by the existing package manifest. Setup
extracts the selected loader atomically below this add-on's runtime. The generated
module and compiled DLLs are build artifacts, not committed source. Subsequent
releases reuse frozen loader artifacts and their source-controlled hash lock.
No certificate/key/trust entry is created. Worker paths remain versioned and
immutable. Existing registration ownership, exclusive writes and rollback remain.

## Implementation and verification

- [x] Add loader guard tests, verify failures, implement and compile six variants.
- [x] Add pure payload/materialization tests (hash, version, reparse, atomicity,
      unchanged loader across worker versions); implement module and packaging.
- [x] Integrate runtime/setup/updater; preserve old packages and safe migration.
- [x] Native view fixture: baseline versus default template/hidden parameter filter,
      coarse/medium/fine and rollback. Fix only after native reproduction.
- [x] Check workshared/plain fixtures and public API restrictions. A proposed
      scenario-based link method is not public in the real API; no call to it is shipped.
- [ ] Third independent real model: opening stopped on door-insert errors during
      upgrade; direct-opening comparison was interrupted. This remains a coverage
      limitation, not a proven defect in the source and not a shipped model-specific fix.
- [x] Native loader first trust plus repeated launch and changed worker version.
      Package/registration rollback is verified in isolated host integration.
- [x] Isolated Blender 4.2/5.2 install, actual 0.4.7 transition, reimport and errors.
- [x] Review and package stable 0.5.0 as explicitly requested by the owner.
      Publication uses the verified ZIP and frozen-loader build artifact.

Final checks include native Revit 2027 family export, canonical occurrence frames,
35-chair surface grouping at 5 mm and reversal at 0.1/0 mm, raw-input preservation,
UVs and three decoded/packed images. UI thumbnail paths are excluded from real
bitmap dependencies. Default instance tolerance remains the conservative 0.1 mm.
This native family fixture is separate from the nine synthetic view scenarios.

## Coverage boundaries

Real native tests require installed Revit versions. Local Revit 2027 execution
does not prove 2024–2026 runtime behavior; compiling against their APIs is a
separate check. Other risks requiring fixtures include relative CAD references,
nested/unavailable links, eTransmit, inherited visibility, coordinates and
cancellation during API calls. A finite model corpus cannot prove every RVT
valid; reports must separate reproduced fixes, passed fixtures and untested cases.
