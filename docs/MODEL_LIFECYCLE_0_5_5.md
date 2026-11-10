# Model lifecycle 0.5.5 implementation plan

Approved product behaviour: four independent settings: overwrite source (off),
reuse prepared model (on), keep temporary data (on), pack images (existing on).
Model-owned <stem>.Data beside RVT works with unsaved and different Blender scenes.
Explicit reuse may select an older prepared snapshot; original is not opened by
Revit on a cache hit. Show source replacement and stale-copy warnings, no new modal.
When overwrite is selected, atomically replace the selected local original with
that exact verified prepared snapshot. Do not synchronize a server/central project.
Prepared shared copies are standalone local snapshots; explain this in the tooltip.
Tests only overwrite disposable input fixtures. Originals/preferences stay untouched.

- [x] Pure policy/path and preparation-cache tests, including all 16 flag combinations.
- [x] Worker: reuse/fresh, verified source replacement, provenance and safe failure.
- [x] Blender: independent controls, installed-Revit choice, graceful post-import
      cleanup retaining every unpacked image actually referenced by Blender.
- [ ] Native fixture matrix, isolated Blender 4.2/5.2 checks, six SDK builds,
      package/update tests and 0.5.5 Early Access release.

Delete temporary files only after a successful import and graceful worker close.
Move unpacked images to a stable texture-only part of the model Data first.
Preserve files used by other open scenes; a cleanup error reports remaining files.
A failed/cancelled operation keeps recoverable data. No silently ignored locks.
Existing 0.5.4 caches are not destroyed; do not relocate them without approval.
