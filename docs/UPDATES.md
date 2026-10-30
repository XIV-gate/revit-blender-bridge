# AGR Revit Bridge updates

The module ID `xivgate_revit_bridge`, scene properties and Revit add-in identity
remain unchanged for compatibility with 0.1. Display branding is AGR Revit Bridge.
The primary host is Blender 5.2; Blender 4.2 compatibility is retained.

## Release contract

Repository: `XIV-gate/revit-blender-bridge`. The user-triggered updater reads
`/repos/XIV-gate/revit-blender-bridge/releases?per_page=100&page=N` without
credentials (at most three pages and 2MiB total). Each listed release requires a
published numeric `vX.Y.Z` tag and exactly one uploaded asset named
`agr_revit_bridge-X.Y.Z.zip`, with a valid GitHub SHA256 digest and bounded size.
GitHub `prerelease=false` defines stable; `prerelease=true` defines Early Access.
The default is stable. Drafts and incomplete/invalid releases are omitted.
Downgrades are allowed only by explicit selection; the current version is not
reinstalled. A confirmation names the version and flags Early Access/downgrades.
The old latest-only API remains available for compatibility with 0.2.0.
Metadata and downloads have time/size limits and restricted HTTPS redirects.
Blender's offline setting is respected; preferences are never changed to allow networking.

The ZIP contains one `xivgate_revit_bridge/` package, a canonical `VERSION` and a
`PACKAGE_MANIFEST.json` with module ID, version, minimum Blender version, worker
path and SHA256 of every payload file. Python is compiled for validation without
executing it. Traversal, duplicate/case-colliding names, symlinks, unexpected
runtime binaries, mismatched versions and corrupted payloads are rejected.

## Installation and recovery

Network/validation work uses daemon threads; all Blender operations remain on the
main thread. Scanning/importing is blocked while installation is pending. Any
owned Revit worker must accept an authenticated graceful shutdown and exit; a
refusal or timeout aborts the update rather than force-killing it.

Files are replaced in place with rollback snapshots, rather than renaming the
whole add-on directory. The worker path is
`runtime/revit/<variant>/X.Y.Z/XIVgate.RevitBridge.Worker.dll`. Existing differing DLLs
are not overwritten, and earlier version directories remain untouched. The
owned `.addin` pointer is repointed with no-clobber publication and can be restored.
Foreign pointers and pointers outside this installation's runtime are refused.

A stable sibling OS lock serializes installation across Blender processes. It is
held through snapshotting, registration changes and code reload, and released
even if a secondary rollback fails. Its small lock file is intentionally retained
to avoid inode replacement races; the operating system releases its lock on exit.

Reload removes old package submodules and their parent attributes, invalidates
owned bytecode, and registers the new classes without writing Preferences. A
failed registration restores the earlier files and pointer. Settings and scene
objects are retained. Loading another blend cancels pending work and discards late
thread results. Unexpected external changes that prevent rollback retain recovery
snapshots and report that manual recovery is required.

Update caches and snapshots live beside the installed add-on in
`.agr_revit_bridge_updates/`; completed snapshots/downloads are removed. Existing
source Git checkouts are never overwritten by the updater.

## Preparing a release

1. Change canonical `VERSION`, static `bl_info.version`, and worker assembly metadata together.
2. Run targeted Python, SDK and isolated Blender checks.
3. Compile the worker with `revit_worker/build.ps1 -OutputDir <approved-existing-build-directory>`.
4. Build `agr_revit_bridge-X.Y.Z.zip` with `tools/build_package.py` and verify the archive.
5. Commit/push the tested source and prepare a draft GitHub Release targeting that commit.
6. Upload the ZIP; verify the GitHub asset digest equals the local SHA256.
7. Publish only after the product owner's explicit approval. Repository visibility
   is also an explicit owner decision; private drafts do not work for anonymous updates.

The builder never includes Autodesk SDK assemblies, models, preferences, caches,
credentials or test output. XIVgate workers and required Microsoft runtime support libraries are distributed;
Autodesk API assemblies are build references only. Third-party notices accompany
redistributed libraries. A declared2024worker requires the complete .NET48 JSON
runtime dependency set; the validator rejects missing dependencies. Runtime
payloads are immutable per version, including support libraries.
No public license is added automatically.

## Branches and promotion

`main` carries stable releases; `early-access` carries experimental changes.
GitHub prerelease flags, not branch names, determine the Blender channel.
0.3.0 remains latest stable while 0.3.1/0.3.2 test builds are prereleases.
A tested build can be promoted deliberately by changing its release status;
never replace an existing published ZIP with different bytes under the same version.
