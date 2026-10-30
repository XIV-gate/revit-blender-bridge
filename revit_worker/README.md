# Revit 2027 companion

Entry point: `XIVgate.RevitBridge.WorkerApp`, implementing `IExternalApplication`.
Package location: `runtime/revit/2027/XIVgate.RevitBridge.Worker.dll`.

Build with `build.ps1 -OutputDir <existing-approved-build-directory>`.
The script references the locally installed Revit 2027 SDK and .NET 10;
it does not restore NuGet packages, create repository build caches, copy Autodesk
assemblies, or install an add-in registration. `test.ps1` compiles and runs the
Revit-independent protocol assertions in the same approved output directory.

The application does nothing without both `XIVGATE_RBB_JOB_ROOT` and
`XIVGATE_RBB_SESSION_TOKEN`. The optional `XIVGATE_RBB_SESSION_ID` also pins the
session UUID. Document, transaction and geometry APIs run only in Revit's UI-thread
callbacks. A job-local `FileSystemWatcher` detects `request.json` creation, change
and atomic rename/replacement. Its background callback only signals an `ExternalEvent`
created in the authenticated startup API context; Revit dispatches the actual work
through `IExternalEventHandler.Execute` on its main thread. External-event reads
bypass the idle polling throttle. Default Idling remains the startup/fallback path;
`SetRaiseWithoutDelay` and desktop input/window messages are not used. Notifications
coalesce, and only failed wake requests receive a bounded retry. Idle waiting has
no periodic polling timer. The watcher and external event are disposed on shutdown
and failed bootstrap. This lets an inactive worker accept export/reimport/shutdown
without requiring interaction with its Revit window.
Transient request-file I/O/JSON read failures after an accepted wake receive at
most eight deferred rereads, spaced 250 ms apart. A successful read or a new file
notification resets that budget; absent, already consumed and foreign requests do
not schedule rereads. This avoids losing a request to a brief sharing/antivirus
lock without creating an unlimited malformed-file polling loop.
`worker_requests.json` records consumed request IDs before execution so a worker
restart cannot repeat a partially executed request. Diagnostic files are retained.
Bootstrap logs begin immediately after validation of the owned job directory.
`starting` status identifies an authenticated prewritten request before the first
Idling event; bootstrap failures write `startup_error.json` within the validated
job directory and a matching error response when request authentication succeeded.
The first Idling sender type is logged once. Older model opening reports both
source and running Revit versions while the owned copy upgrades in memory.

Source copies must use a fresh path below `job/input/` for every scan. A failed or
cancelled scan invalidates export readiness and closes its copied document; an
earlier catalog cannot authorize export against a newly opened model. The original
is read through a read-only stream. Link suppression uses `TransmissionData` on
the closed copy; the public installed Revit 2027 assembly does not expose
`OpenOptions.DoNotLoadLinks` despite its inclusion in the shipped XML reference.
Relative external references are made absolute against the original source folder
in the copy, preserving existing unloaded states. Workshared copies are detached
with worksets preserved. Models are never saved.

The export selection is authoritative; recommendations never filter the export.
Owned assembly/group members, curtain-wall panels/mullions, nested family components,
railings and stair parts retain
their selected parent's geometry ownership when their callbacks are emitted
separately. Other unselected parent containers are traversed to reach explicitly
selected descendants. Exact float32 mesh and material signatures determine shared
prototypes. Reflected occurrences can have a negative transform determinant;
consumers baking them must preserve correct world-space winding.

Mesh chunks spill to one temporary spool on element boundaries and at 4 MiB,
avoiding retention of the whole model in managed memory before deduplication.
Deduplication reads one selected owner's geometry at a time; a single owner larger
than 2 GiB fails clearly and should be selected by its constituent parts. The spool
is removed after export, while error diagnostics and partial binary files remain.

The export view and visibility changes are rolled back. Missing geometry is
reported per selected ID, and an entirely empty export fails. New revisions are
required; existing outputs are not overwritten. Missing/ambiguous textures and
unsupported links/RPC geometry are reported. Texture lookup uses existing explicit
local paths, the original RVT folder and installed Autodesk material texture
libraries; it does not download assets. Basic material properties and copied image
paths preserve the source metadata; this does not reproduce Revit's full renderer.
Autodesk texture library relocation preserves the complete suffix below
`Autodesk Shared/Materials/Textures/`, including `1`, `2` or `3` resolution folders.
Missing x86 library paths and relative library paths are tried exactly beneath
the installed native/x86 library roots before basename fallback. Different
resolution files sharing a basename remain ambiguous; the worker never guesses
their quality. Identical mirrors at an exact suffix are interchangeable, while
differing mirrors are reported as ambiguous.

Warnings during authenticated operations on the owned copy are captured with
description, failure ID, severity and element IDs in `warnings.json`, also exposed
as `warnings_report_path` in the response. `FailuresProcessing` removes only
`FailureSeverity.Warning` messages from the failure queue and continues normally;
it never resolves errors or deletes model elements. Persistent `Document.GetWarnings()`
messages are collected after opening and preserved in scan warnings.

The single exact `Dialog_Revit_DocWarnDialog` is acknowledged with OK (result 1)
only while the owned `OpenDocumentFile` call is active, the request is authenticated,
and no error-level failure has been captured. Its acknowledgement is reported even
when Revit does not expose the dialog text. The opening flag always clears in
`finally`. This exception does not apply during export or outside the owned session.

Error-level failures invalidate the operation. When Revit exposes an active failure
accessor for a committing transaction in the verified copied document, the handler
requests rollback with clear-after-rollback, after recording all diagnostics. If
that context is unavailable (including an already rolling-back transaction or an
unverifiable document), default Revit handling remains; human action may be required.
No automatic repair or retry is attempted. Warning deletion affects only the
in-memory failure queue and copied document; the original RVT is never saved.
Document ownership uses Revit's `Document.Equals` override, which recognizes
different managed wrappers of the same currently open document. It does not depend
on `ReferenceEquals` or a nonempty `PathName`: detached copies have an empty path.
During opening, before the returned owned document is available, an exact copied
path remains the conservative fallback for failure-queue changes.

All other dialogs report `requires_action` without overriding their result. Shutdown
closes only the retained worker-owned document without saving, and posts Exit only
when no other non-linked document is open in this process.
