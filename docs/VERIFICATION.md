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

The user allowed the revised unsigned local DLL once. The worker opened/upgraded
the copied source, collected warnings without manual model-warning confirmation,
and returned a 19,531-element catalog. Advisory defaults selected 14,811 elements,
excluding known helper categories and internal wall types. Export produced 12,108
placements, 5,203 prototypes, 391,971 unique triangles and 59 material variants.
The Blender 5.2.3 real-export smoke test imported every placement, verified matrices
(maximum observed error 2.1e-7), preserved unrelated geometry, and saved a new test
`.blend`. Missing source V: textures were reported.

The final FileSystemWatcher/ExternalEvent worker consumed an export request in
0.068 seconds without window activation. Its full export has the same 12,108
placements and 5,203 prototypes. Exact library-relative lookup recovered four
available texture files, including three base-color maps. Missing V: assets remain
reported rather than replaced with guessed images.

A large-scene reimport regression opens an immutable full result and imports a
real four-element subset. It verifies exact selection, unchanged retained world
matrices, preserved unrelated geometry and no leftover empty import collection.
The original individual-ID deletion failed the 20-second test timeout and a Python
stack dump identified `_remove_revision`. The batch-ID version passes: approximately
2.44 seconds on Blender 5.2.3 and 4.39 seconds on 4.2.14 in this session (these
measure Blender replacement, excluding Revit export time). Foreign child-object
preservation also passes both versions' fixture tests after this change.

The complete live-controller rerun passed in isolated Blender 5.2.3: catalog →
full import → UI selection → real Revit subset export → asynchronous conversion →
selective reimport. The full scene contains three packed base-color images. Four
selected IDs replace the 12,108-placement result, with stable coordinates and
unrelated geometry preserved. Both a full scene and a separate subset scene were
saved to explicit new test paths; no original Blender scene or Preferences was saved.

Fresh checks: 26 Python tests, 83 .NET assertions, SDK compilation, Blender fixture
integration, complete ZIP installation in isolated 4.2/5.2 profiles, large-scene
reimport regression and the real-Revit controller run. The SDK build is deterministic
within the same installed toolchain. The original RVT SHA256 is unchanged after
the completed opening/export/reimport sequence. The owned worker accepted an
authenticated shutdown request without saving its document.

## Limits

Only one source model and one Revit major version are available for live validation.
Recommendations are heuristics, not proof of geometric exterior visibility.
Linked RVT geometry, complete Revit renderer reproduction, automatic repair of
error-severity failures, and missing source texture recovery are unsupported.
First-load trust/licensing and unknown dialogs may require the user. The plugin
never disables Revit security checks or resolves failures by deleting model elements.

## 0.2.0 AGR/update verification

The display name and sidebar are AGR Revit Bridge / AGR; the module, scene keys,
add-in GUID and geometry protocol retain compatibility. Revit code changes in
this version are assembly branding/version metadata; geometry behavior is unchanged.

Fresh host-independent checks include 52 Python tests and the existing 83 .NET
assertions. The updater tests cover HTTP/offline errors, metadata/archive bounds,
GitHub digest/identity checks, malformed ZIPs, in-place rollback, old DLL retention,
registration migration/no-clobber recovery and a real second-process OS lock.

Isolated Blender 5.2.3 and 4.2.14 exercise update 0.2.0 → synthetic 0.2.1 with an
injected network transport. They verify that new submodule code actually loads,
settings/selection/scene objects survive, an old DLL held by a Windows read lock
is unchanged, and registration points at the new worker. An intentionally broken
0.2.2 register callback restores 0.2.1 and its pointer. Tests also cover refused
worker shutdown, file loading during checks/downloads, and release of the OS lock
even when registration rollback itself fails. Blender Preferences are not saved.

Following the owner's explicit approval, the repository was made public and
v0.2.0 was published on 2026-10-30. A post-publication check used the production
fetch/download/validation functions without GitHub credentials. The downloaded
archive SHA256 matched the published asset. The same check passed inside isolated
Blender 5.2.3 with per-process online access: the actual UI controller reports 0.2.0
as current, and an older-version query downloads and validates the live release.
No Preferences were saved. Private drafts remain intentionally rejected.

Repeat the opt-in host check with `--script test_public_update.py --online` on
`tools/run_blender_test.py`; the flag enables network only for that isolated process.


## 0.3.0 candidate

89 pure tests (one Windows symlink-privilege skip). Real worker API reference
compilation:2024/.NETFramework4.8,2025/2026.NET8,2025/2026.NET10variants,2027.NET10.
Framework4.8 runtime90assertions; actual.NET8.0.33 and.NET10 each86assertions.
2025/2026.NET10 use same-year baselineAPIrefs and document CS1701 reference unification.
Full Revit2024–2026 document open/export has not been run: these hosts are absent.

Isolated Blender4.2.14 and5.2.3: native linked-mesh edits propagate, independent
copies, mirror transforms, name stability across fullreimports, subset removal,
foreign-object and shared-scene guards; stable/early selector scene switching;
upgrade/explicit downgrade/failed-code rollback. All processes use isolated profiles.
Real user export12108 placements/5203prototypes: import as direct Mesh objects45.917s;
remove exact72mass/massfloor placements0.419s, retained identities/names unchanged.
SourceUSD read-only, no user scene or Preferences written.


## 0.3.1 Early Access candidate

89puretests (one Windows symlink-privilege skip). All6workerDLLs compiled with
assemblyversion0.3.1.0; onlyassemblymetadata differs from0.3.0 C# behavior.
RealBlender4.2.14 and5.2.3 tests cover staged delta merge and rollback, signature
mesh/material reuse, moved roots, parent/source boundaries, shared scenes, local
stash/restore, object names, EditMode shared mesh updates and bounds-preview safety.
End-to-endcontroller fixtures use real bundled-Python conversion and test-only
RevitI/O: onlynewID12 exported, oldwindow mesh/pointer retained; toggleoff/on uses
noexport; compositeoverlap andemptydelta safelytransitiontofullscan/export.
Cache reuseaftershutdown andcorruptcachemiss verified. A currentlyreferenced
unpackedimage preserves its finishedjob duringcleanup. No live userprofile writes.
Fullmodel missing-block diagnosis is not claimed: screenshot lacks elementIDs;
newbbox catalog/type tools provide a spatial investigation path.

Actualbuildingdelta benchmark0.3.1 in isolatedBlender5.2.3: baseline12026placements,
5131uniqueprototypes imported61.126s; add10windows conversion0.011s, merge8.432s.
All12026existing objectIDs/names/mesh pointers retained; all10newwindows reused
the same intentionally edited sharedmesh. No collection-instance placeholders,
transformerror0,3packedimages. Readonlysource manifest hash unchanged.
Fullhost4.2/5.2 ZIPswitch stable0.3.0→EA0.3.1→stable0.3.0 preserves scene/settings,
uses only owned isolated registration and never saves Preferences.

Published0.3.0 stable and0.3.1 prerelease on2026-10-30. Anonymous GitHub checks
inside isolatedBlender5.2.3 verified both channel lists, lateststable=0.3.0,
both downloads and exact archive/fileSHA256. No Preferences saved.


## 0.3.2 migration regression

92puretests (one Windows symlink-creation privilege skip). New tests recover a
missing OWN old DLL but refuse a missing replacement or foreign registration.
RealZIP0.3.0 installation→stale0.2.0pointerfailure→disable/install0.3.2/enable in
sameBlenderprocess→newUI and repairedregistration PASS5.2.3+4.2.14. Newupdater also
recovers the stale pointer during a verified release transition. No Preferences
saved and no live profile writes. All6 worker assemblies rebuilt0.3.2.0; only
assembly version changed in C#.
