using System;
using System.IO;
using System.Linq;
using System.Diagnostics;
using System.Reflection;
using System.Text.Json;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using Autodesk.Revit.UI.Events;

[assembly: AssemblyTitle("XIVgate test-only native server metadata probe")]
[assembly: AssemblyCompany("XIVgate")]
[assembly: AssemblyVersion("0.0.0.1")]

namespace XIVgate.RevitBridge;

// TEST ONLY. Never opens a document and never writes to the supplied source file.
public sealed class WorkerApp : IExternalApplication
{
    string root = "", token = "", session = "";
    JsonElement request;
    bool enabled, ran;
    public Result OnStartup(UIControlledApplication app)
    {
        root = Environment.GetEnvironmentVariable("XIVGATE_RBB_JOB_ROOT") ?? "";
        token = Environment.GetEnvironmentVariable("XIVGATE_RBB_SESSION_TOKEN") ?? "";
        session = Environment.GetEnvironmentVariable("XIVGATE_RBB_SESSION_ID") ?? "";
        if (root.Length == 0 || token.Length == 0) return Result.Succeeded;
        using var json = Protocol.Read(Protocol.Below(root, Path.Combine(root, "request.json")));
        request = json.RootElement.Clone();
        if (!Protocol.Authenticated(request, token, session)) return Result.Failed;
        if (!File.Exists(Protocol.Below(root, Path.Combine(root, "reference_probe_enabled")))) return Result.Succeeded;
        enabled = true;
        app.Idling += Run;
        app.DialogBoxShowing += OnDialog;
        return Result.Succeeded;
    }
    void OnDialog(object? sender, DialogBoxShowingEventArgs e)
    {
        Log("Dialog " + e.DialogId);
        if (e is TaskDialogShowingEventArgs task && WarningPolicy.DismissOptionalStartupFailure(enabled, true, !ran, false, e.DialogId, task.Message))
            e.OverrideResult((int)TaskDialogResult.Close);
    }
    void Run(object? sender, IdlingEventArgs e)
    {
        if (!enabled || ran || sender is not UIApplication ui) return;
        ran = true;
        string id = Protocol.String(request, "request_id");
        try {
            string source = Protocol.String(request, "source_rvt");
            string copy = Protocol.Below(Path.Combine(root, "input"), Protocol.String(request, "source_copy"));
            if (string.Equals(Path.GetFullPath(source), copy, StringComparison.OrdinalIgnoreCase)) throw new IOException("Copy equals source");
            File.Copy(source, copy, false);
            var timer = Stopwatch.StartNew();
            var prepared = OfflineReferencePreparer.Prepare(root, session, source, copy);
            Log("Prepared " + prepared.Ids.Length + "; verified entries=" + prepared.VerifiedEntries);
            var nativeTimer = Stopwatch.StartNew();
            OfflineReferenceValidation.Validate(copy, prepared);
            nativeTimer.Stop();
            Log("Native metadata validation returned");
            OfflineReferencePreparer.Verify(copy, prepared);
            Protocol.Atomic(Path.Combine(root, "response.json"), new { schema = 1, session_id = session, request_id = id,
                success = true, seconds = timer.Elapsed.TotalSeconds, native_seconds = nativeTimer.Elapsed.TotalSeconds,
                rewritten = prepared.Ids.Length, verified_streams = prepared.VerifiedStreams, verified_entries = prepared.VerifiedEntries, prepared_copy = copy });
        } catch (Exception ex) {
            Log(ex.ToString());
            Protocol.Atomic(Path.Combine(root, "response.json"), new { schema = 1, session_id = session, request_id = id, success = false, error = ex.ToString() });
        } finally {
            var exit = RevitCommandId.LookupPostableCommandId(PostableCommand.ExitRevit);
            if (ui.Application.Documents.Cast<Document>().All(d => d.IsLinked) && ui.CanPostCommand(exit)) ui.PostCommand(exit);
        }
    }
    void Log(string text) { try { File.AppendAllText(Path.Combine(root, "reference-probe.log"), DateTime.UtcNow.ToString("O") + " " + text + Environment.NewLine); } catch { } }
    public Result OnShutdown(UIControlledApplication app)
    { app.Idling -= Run; app.DialogBoxShowing -= OnDialog; return Result.Succeeded; }
}
