# Verification of 0.1.0

## Automated evidence

- Host-independent Python: catalog recommendations and manual overrides, authenticated
  job transport, stale/partial response rejection, cancellation, contained paths,
  source preservation, conservative registration installation, geometry hashes/layout.
- Revit-independent .NET assertions: protocol validation and scoped warning policy.
- Revit 2027 worker compiles against the locally installed Autodesk SDK with warnings
  treated as errors, using .NET 10. No Autodesk runtime assemblies are bundled.
- Isolated Blender 4.2.14 LTS and 5.2.3 LTS: register/unregister, category/workset/object
  callbacks, USD prototypes, mirrored placements, UV/material/image handling,
  texture packing disabled/enabled, materials disabled, independent mesh option,
  selective reimport, stable world coordinates, preservation of unrelated objects
  and foreign children, and correct target scene during asynchronous import.
- Controller tests use a clearly marked Revit I/O test double and the actual bundled
  Python conversion subprocess. They cover catalog → selection → export request →
  conversion → import → changed selection → reimport. They do not claim real RVT parsing.
- Complete ZIP installation/enabling is tested only inside an isolated profile;
  no live Blender installation or Preferences save is performed.

## Live RVT smoke test

The supplied Revit 2024 source is copied before opening in a separately launched
Revit 2027. Its original SHA256 remains unchanged after the initial opening test.
The first run reached a standard document warning concerning an automatically
corrected lift-door family. The worker now has scoped automatic acknowledgement
for this exact document-warning dialog and captures API/persistent warnings.

The user allowed the revised unsigned local DLL once; bootstrap and Idling logs
confirm that the revised worker started. It is currently upgrading the copied RVT.
Ordinary model-warning handling,
catalog/export with that worker, and the complete real-RVT round trip are not yet
claimed as passed. This section must be updated from actual results after the user
opening and export finish.

## Limits

Only one source model and one Revit major version are available for live validation.
Recommendations are heuristics, not proof of geometric exterior visibility.
Linked RVT geometry, complete Revit renderer reproduction, automatic repair of
error-severity failures, and missing source texture recovery are unsupported.
First-load trust/licensing and unknown dialogs may require the user. The plugin
never disables Revit security checks or resolves failures by deleting model elements.
