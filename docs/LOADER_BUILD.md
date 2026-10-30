# Frozen loader generation 1

The loader is an independently versioned Revit entry point. Its six frozen DLLs
are embedded inside the ordinary add-on ZIP as a generated literal data module.
The package manifest declares their ABI and hashes. Setup reads that module as
data, verifies it against the installed manifest and exclusively materializes the
selected DLL below `runtime/revit/<key>/loader/1/`. It never changes trust settings.

This representation preserves compatibility with the existing 0.4.7 updater,
which accepts verified Python resources but rejects new runtime DLL paths.
The generated data module is not executed or committed. Loader source and the
approved hash lock are committed; binaries are retained as release artifacts.

## Stable 0.5.0 release builds

Reuse the frozen loader artifact `agr_revit_loader-1.zip` published alongside stable release 0.5.0.
Verify every DLL against `revit_loader/LOADER_SHA256.json`. Recompiling the loader
for each add-on release defeats its purpose and can change its hash due to
compiler/reference differences. Ordinary worker and Python changes must not
change the loader generation or frozen bytes.

Build worker variants using `revit_worker/build.ps1`, then package with:

```text
python tools/build_package.py --workers-root <approved-worker-directory> --loaders-root <verified-frozen-loader-directory> --loader-lock revit_loader/LOADER_SHA256.json --output <new-package.zip>
```

The loader directory contains the six worker-key subdirectories, each containing
`XIVgate.RevitBridge.Loader.dll`. The builder refuses bytes differing from the lock.
No Autodesk SDK assembly, private key, certificate or application preferences is
included. A separate frozen-loader archive is a build input for maintainers;
users install only the normal add-on ZIP.

Changes to the loader itself require a deliberate new generation, updated guard
tests, native loading/update/rollback tests and new user trust. Do not silently
replace generation 1 or change its locked hashes.

## Test scope

`revit_loader/test.ps1` checks the Framework 4.8/.NET 10 path, marker, hash and
entry-point contracts with test-only API stubs. `revit_worker/tests/build-native.ps1`
builds a separate opt-in application that creates its own tiny test documents.
Neither test may target a live user profile or an original model. Use only an
explicitly approved project test root. Native Revit tests require a clear window
without concurrent user launches: registration is shared by all processes of the
same Revit version. Restore the original owned manifest byte for byte afterwards.


## Worker 0.5.0 native identity and tolerance

Build each of `2024`, `2025`, `2025-net10`, `2026`, `2026-net10`, `2027` with
`build.ps1 -WorkerKey <key> -ReferencesRoot <approved-reference-cache> -OutputDir
<existing-approved-output/key>`. AssemblyVersion is `0.5.0.0`. The frozen loader
ABI remains 1 and its locked bytes must not be recompiled for this worker update.

`InstanceNode.GetSymbolGeometryId().AsUniqueIdentifier()` and
`FamilyInstance.HasModifiedGeometry()` were verified by compilation against real
2024/2025/2026 API references and the installed 2027 SDK. The exporter emits safe
native geometry-variant identity only for one unmodified family root with all
polymesh inside it. Root axes are adopted before any vertex is written. Nested
owner tracking preserves world transforms; later ambiguity clears the key rather
than guessing. Direct/modified/multiple-root geometry keeps the raw fallback.
Material `uv_sensitive` includes missing raw bitmap references.

Python import grouping defaults to 0.1 mm. Native sharing additionally requires
one untextured material and compatible bounds. Fallback retains material, normal,
UV and topology checks. Every raw variant remains in the binary cache, permitting
conversion again at another tolerance without a new Revit export when that cache
is valid. This is not an arbitrary shape-similarity or type-name welding system.
The worker only removes float32 arithmetic residue within 1e-9 of zero; it does
not apply the user tolerance or collapse native-key variants itself.

Compilation across SDKs and pure policy tests do not prove all host/model cases.
Native family-frame coverage requires a separate Revit fixture or controlled model
run; the primitive test application explicitly reports whether it exercised it.
