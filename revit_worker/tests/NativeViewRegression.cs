using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Reflection;
using System.Text.Json;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using Autodesk.Revit.UI.Events;

[assembly: AssemblyTitle("XIVgate test-only native view regression")]
[assembly: AssemblyCompany("XIVgate")]
[assembly: AssemblyVersion("0.0.0.1")]

namespace XIVgate.RevitBridge;

// TEST ONLY. Excluded from the production root-*.cs build. Never opens an input document.
public sealed class WorkerApp : IExternalApplication
{
    string root = "", session = "", request = "";
    bool enabled, ran;
    Document? owned;
    readonly List<object> cases = new();
    readonly List<object> diagnostics = new();
    readonly List<string> fixturePaths = new();
    readonly Dictionary<string, object?> report = new();
    public Result OnStartup(UIControlledApplication app)
    {
        string? job = Environment.GetEnvironmentVariable("XIVGATE_RBB_JOB_ROOT");
        string? token = Environment.GetEnvironmentVariable("XIVGATE_RBB_SESSION_TOKEN");
        if (string.IsNullOrWhiteSpace(job) || string.IsNullOrWhiteSpace(token)) return Result.Succeeded;
        bool ownsNewReport = false;
        try {
            if (!Compat.FullyQualified(job)) return Result.Failed;
            root = Path.GetFullPath(job);
            if (!Directory.Exists(root)) return Result.Failed;
            string marker = Compat.Below(root, Path.Combine(root, "native_regression.json"));
            if (!File.Exists(marker)) return Result.Succeeded;
            using var json = Protocol.Read(marker);
            var value = json.RootElement;
            if (!Protocol.Authenticated(value, token, Environment.GetEnvironmentVariable("XIVGATE_RBB_SESSION_ID")) ||
                Protocol.String(value, "kind") != "native_view_regression" ||
                !value.TryGetProperty("run_native_fixture", out var run) || run.ValueKind != JsonValueKind.True) return Result.Succeeded;
            session = Protocol.String(value, "session_id"); request = Protocol.String(value, "request_id");
            string consumed = Compat.Below(root, Path.Combine(root, "native_started.marker"));
            using (var stream = new FileStream(consumed, FileMode.CreateNew, FileAccess.Write)) { }
            ownsNewReport = true;
            report["schema"] = 1; report["session_id"] = session; report["request_id"] = request;
            report["production_exporter"] = true; report["fixture_kind"] = "two_generic_directshape_boxes";
            report["cases"] = cases; report["diagnostics"] = diagnostics; report["fixtures"] = fixturePaths;
            report["status"] = "starting";
            enabled = true;
            app.Idling += OnIdling;
            app.DialogBoxShowing += OnDialog;
            Flush(); Status("starting", "Тестовый обработчик ожидает Idling");
            return Result.Succeeded;
        } catch (Exception ex) {
            enabled = false;
            try { app.Idling -= OnIdling; app.DialogBoxShowing -= OnDialog; } catch { }
            if (Directory.Exists(root)) {
                try {
                    if (ownsNewReport) { report["status"] = "startup_error"; report["error"] = ex.ToString(); Flush(); }
                    else File.AppendAllText(Compat.Below(root, Path.Combine(root, "native_bootstrap_error.log")), DateTime.UtcNow.ToString("O") + " " + ex + Environment.NewLine);
                } catch { }
            }
            return Result.Failed;
        }
    }
    public Result OnShutdown(UIControlledApplication app)
    {
        if (enabled) { app.Idling -= OnIdling; app.DialogBoxShowing -= OnDialog; }
        return Result.Succeeded;
    }
    void OnIdling(object? sender, IdlingEventArgs args)
    {
        if (!enabled || ran || sender is not UIApplication ui) return;
        ran = true;
        bool completed = false;
        try {
            if (ui.Application.Documents.Cast<Document>().Any(d => !d.IsLinked)) throw new InvalidOperationException("Тест требует отдельный процесс без открытых чужих документов.");
            Status("running", "Создание нового метрического тестового документа");
            owned = ui.Application.NewProjectDocument(UnitSystem.Metric);
            report["revit_version"] = ui.Application.VersionNumber;
            report["revit_build"] = ui.Application.VersionBuild;
            var type = FirstType(owned);
            ElementId hidden, shown;
            using (var transaction = new Transaction(owned, "XIVgate native fixture boxes")) {
                transaction.Start();
                hidden = Box(owned, 0, "hide");
                shown = Box(owned, 2, "shown");
                if (transaction.Commit() != TransactionStatus.Committed) throw new InvalidOperationException("Не удалось создать тестовые DirectShape.");
            }
            var selected = new HashSet<long> { hidden.Value, shown.Value };
            report["selected_ids"] = selected.OrderBy(x => x).Select(x => x.ToString()).ToArray();
            report["default_3d_type_id"] = type.Id.Value.ToString();
            SaveFixture(owned, "fixture_plain.rvt", false);
            foreach (string detail in new[] { "coarse", "medium", "fine" }) RunCase(owned, "plain", detail, selected);

            ElementId filterId, templateId;
            using (var transaction = new Transaction(owned, "XIVgate native default template")) {
                transaction.Start();
                var seed = View3D.CreateIsometric(owned, type.Id);
                seed.ViewTemplateId = ElementId.InvalidElementId;
                seed.Name = "XIVgate native template seed";
                seed.DetailLevel = ViewDetailLevel.Fine;
                var rule = ParameterFilterRuleFactory.CreateEqualsRule(new ElementId(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS), "hide");
                var filter = ParameterFilterElement.Create(owned, "XIVgate hide comments", new[] { new ElementId(BuiltInCategory.OST_GenericModel) }, new ElementParameterFilter(rule));
                filterId = filter.Id;
                seed.AddFilter(filterId); seed.SetFilterVisibility(filterId, false);
                var template = seed.CreateViewTemplate(); template.Name = "XIVgate locked Fine with hidden box";
                template.SetNonControlledTemplateParameterIds(new List<ElementId>());
                templateId = template.Id;
                type.DefaultTemplateId = templateId;
                if (transaction.Commit() != TransactionStatus.Committed) throw new InvalidOperationException("Не удалось создать шаблон и фильтр.");
            }
            report["template_id"] = templateId.Value.ToString(); report["filter_id"] = filterId.Value.ToString();
            SaveFixture(owned, "fixture_templated.rvt", false);
            var probe = JsonSerializer.SerializeToElement(ProbeTemplate(owned, type, filterId, templateId));
            report["template_inheritance_probe"] = probe;
            bool probeVerified = probe.GetProperty("setup_valid").GetBoolean() && probe.GetProperty("rollback_unchanged").GetBoolean();
            foreach (string detail in new[] { "coarse", "medium", "fine" }) RunCase(owned, "templated", detail, selected);

            try {
                owned.EnableWorksharing("Shared Levels and Grids", "XIVgate native geometry");
                SaveFixture(owned, "fixture_workshared.rvt", true);
                report["worksharing_available"] = true;
                foreach (string detail in new[] { "coarse", "medium", "fine" }) RunCase(owned, "workshared_templated", detail, selected);
            } catch (Exception ex) {
                report["worksharing_available"] = false;
                report["worksharing_error"] = ex.ToString();
            }
            report["native_family_identity_exercised"] = false; // Fixture uses DirectShape, not a loaded family.
            report["status"] = probeVerified && cases.Cast<CaseResult>().All(item => item.export_succeeded && item.all_selected_exported && item.rollback_unchanged && item.world_vertices_preserved && item.native_metadata_conservative) ? "passed" : "failed";
            completed = true;
        } catch (Exception ex) { report["status"] = "error"; report["error"] = ex.ToString(); }
        finally {
            if (owned != null) {
                try {
                    if (!owned.Close(false)) throw new InvalidOperationException("Revit отказался закрыть собственный тестовый документ.");
                    owned = null; report["owned_document_closed_without_saving"] = true;
                } catch (Exception ex) { report["status"] = "error"; report["close_error"] = ex.ToString(); }
            }
            try {
                bool foreign = ui.Application.Documents.Cast<Document>().Any(d => !d.IsLinked);
                var exit = RevitCommandId.LookupPostableCommandId(PostableCommand.ExitRevit);
                report["exit_requested"] = false;
                if (owned == null && !foreign && ui.CanPostCommand(exit)) { ui.PostCommand(exit); report["exit_requested"] = true; }
                else report["exit_skipped"] = "An owned/foreign document remains or Exit is unavailable; no document/process was forced closed.";
            } catch (Exception ex) { report["exit_error"] = ex.ToString(); }
            try { Flush(); Status(completed ? "completed" : "error", "Тест завершён; результаты в native_report.json"); } catch { }
        }
    }
    static ViewFamilyType FirstType(Document document) => new FilteredElementCollector(document).OfClass(typeof(ViewFamilyType)).Cast<ViewFamilyType>().First(v => v.ViewFamily == ViewFamily.ThreeDimensional);
    static ElementId Box(Document document, double xMetres, string comments)
    {
        double scale = 1 / 0.3048, x = xMetres * scale;
        XYZ[] points = { new(x, 0, 0), new(x + scale, 0, 0), new(x + scale, scale, 0), new(x, scale, 0) };
        var loop = new CurveLoop();
        for (int i = 0; i < 4; i++) loop.Append(Line.CreateBound(points[i], points[(i + 1) % 4]));
        var solid = GeometryCreationUtilities.CreateExtrusionGeometry(new List<CurveLoop> { loop }, XYZ.BasisZ, scale);
        var shape = DirectShape.CreateElement(document, new ElementId(BuiltInCategory.OST_GenericModel));
        shape.ApplicationId = "XIVgate native regression"; shape.ApplicationDataId = comments;
        shape.SetShape(new List<GeometryObject> { solid });
        var parameter = shape.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS);
        if (parameter == null || !parameter.Set(comments)) throw new InvalidOperationException("DirectShape не поддерживает Comments.");
        return shape.Id;
    }
    object ProbeTemplate(Document document, ViewFamilyType type, ElementId filterId, ElementId templateId)
    {
        string before = Snapshot(document);
        object? inherited = null, detached = null;
        bool setupValid = false;
        using var group = new TransactionGroup(document, "XIVgate template inheritance probe");
        group.Start();
        try {
            using var transaction = new Transaction(document, "XIVgate probe temporary view");
            transaction.Start();
            var view = View3D.CreateIsometric(document, type.Id); document.Regenerate();
            inherited = ViewState(view, filterId);
            setupValid = view.ViewTemplateId == templateId && view.DetailLevel == ViewDetailLevel.Fine && !view.CanModifyDetailLevel() &&
                view.GetFilters().Contains(filterId) && !view.GetFilterVisibility(filterId);
            view.ViewTemplateId = ElementId.InvalidElementId; document.Regenerate();
            detached = ViewState(view, filterId);
            if (transaction.Commit() != TransactionStatus.Committed) throw new InvalidOperationException("Probe transaction failed.");
        } finally { if (group.GetStatus() == TransactionStatus.Started) group.RollBack(); }
        string after = Snapshot(document);
        return new { expected_template_id = templateId.Value.ToString(), inherited, detached, setup_valid = setupValid, rollback_unchanged = before == after };
    }
    static object ViewState(View3D view, ElementId filterId)
    {
        var filters = view.GetFilters();
        return new { template_id = view.ViewTemplateId.Value.ToString(), detail = view.DetailLevel.ToString(), can_modify_detail = view.CanModifyDetailLevel(),
            filters = filters.Select(id => id.Value.ToString()).ToArray(), target_filter_present = filters.Contains(filterId),
            target_filter_visible = filters.Contains(filterId) ? (bool?)view.GetFilterVisibility(filterId) : null };
    }
    static string Snapshot(Document document)
    {
        // Revit collectors require an applied filter before enumeration. Preserve BOTH types
        // and instances so rollback verification cannot overlook a leaked temporary view type.
        var allElements = new LogicalOrFilter(new ElementIsElementTypeFilter(false), new ElementIsElementTypeFilter(true));
        var ids = new FilteredElementCollector(document).WherePasses(allElements).ToElementIds().Select(id => id.Value).OrderBy(id => id).ToArray();
        var types = new FilteredElementCollector(document).OfClass(typeof(ViewFamilyType)).Cast<ViewFamilyType>().Where(type => type.ViewFamily == ViewFamily.ThreeDimensional)
            .OrderBy(type => type.Id.Value).Select(type => new { id = type.Id.Value.ToString(), default_template = type.DefaultTemplateId.Value.ToString() }).ToArray();
        return JsonSerializer.Serialize(new { element_ids = ids, default_3d_types = types, workshared = document.IsWorkshared });
    }
    void RunCase(Document document, string stage, string detail, HashSet<long> selected)
    {
        string output = Compat.Below(root, Path.Combine(root, "exports", stage + "_" + detail));
        Directory.CreateDirectory(output);
        string before = Snapshot(document), error = "";
        var warnings = new List<string>(); var exported = new HashSet<long>();
        bool success = false, worldVertices = false, nativeMetadata = false;
        try {
            GeometryExporter.Run(document, output, selected, new { path = document.PathName, revit_version = document.Application.VersionNumber, native_fixture = true },
                Path.Combine(root, "fixture_plain.rvt"), detail, () => false, warnings);
            using var json = Protocol.Read(Path.Combine(output, "manifest.json"));
            nativeMetadata = true;
            foreach (var row in json.RootElement.GetProperty("occurrences").EnumerateArray()) {
                exported.Add(long.Parse(row.GetProperty("id").GetString()!));
                nativeMetadata &= row.GetProperty("family_name").ValueKind == JsonValueKind.String &&
                    !row.GetProperty("native_instance_safe").GetBoolean() && row.GetProperty("native_instance_key").GetString() == "";
            }
            worldVertices = PrimitiveWorldVertices(document, json.RootElement, Path.Combine(output, "geometry.bin"));
            success = true;
        } catch (Exception ex) { error = ex.ToString(); }
        string after = Snapshot(document);
        cases.Add(new CaseResult(stage, detail, success, selected.SetEquals(exported), before == after, worldVertices, nativeMetadata,
            selected.OrderBy(id => id).Select(id => id.ToString()).ToArray(), exported.OrderBy(id => id).Select(id => id.ToString()).ToArray(),
            selected.Except(exported).OrderBy(id => id).Select(id => id.ToString()).ToArray(), warnings.ToArray(), error, JsonSerializer.Deserialize<JsonElement>(before), JsonSerializer.Deserialize<JsonElement>(after)));
        Flush(); Status("running", stage + " / " + detail + ": " + exported.Count + " из " + selected.Count);
    }
    static bool PrimitiveWorldVertices(Document document, JsonElement manifest, string binaryPath)
    {
        var prototypes = manifest.GetProperty("prototypes").EnumerateArray().ToDictionary(item => item.GetProperty("id").GetInt32(), item => item.Clone());
        using var binary = File.OpenRead(binaryPath);
        const double tolerance = 1e-6;
        foreach (var occurrence in manifest.GetProperty("occurrences").EnumerateArray()) {
            var element = document.GetElement(new ElementId(long.Parse(occurrence.GetProperty("id").GetString()!)));
            using var box = element.get_BoundingBox(null);
            if (box == null) return false;
            double[] expectedMin = { double.PositiveInfinity, double.PositiveInfinity, double.PositiveInfinity };
            double[] expectedMax = { double.NegativeInfinity, double.NegativeInfinity, double.NegativeInfinity };
            for (int corner = 0; corner < 8; corner++) {
                XYZ point = box.Transform.OfPoint(new XYZ((corner & 1) == 0 ? box.Min.X : box.Max.X, (corner & 2) == 0 ? box.Min.Y : box.Max.Y, (corner & 4) == 0 ? box.Min.Z : box.Max.Z));
                double[] values = { point.X * 0.3048, point.Y * 0.3048, point.Z * 0.3048 };
                for (int axis = 0; axis < 3; axis++) { expectedMin[axis] = Math.Min(expectedMin[axis], values[axis]); expectedMax[axis] = Math.Max(expectedMax[axis], values[axis]); }
            }
            double[] minimum = { double.PositiveInfinity, double.PositiveInfinity, double.PositiveInfinity };
            double[] maximum = { double.NegativeInfinity, double.NegativeInfinity, double.NegativeInfinity };
            double[] transform = occurrence.GetProperty("transform").EnumerateArray().Select(value => value.GetDouble()).ToArray();
            var prototype = prototypes[occurrence.GetProperty("prototype").GetInt32()];
            byte[] bytes = new byte[prototype.GetProperty("bytes").GetInt32()];
            binary.Position = prototype.GetProperty("offset").GetInt64(); Compat.ReadExactly(binary, bytes, 0, bytes.Length);
            for (int triangle = 0; triangle < bytes.Length; triangle += 104) for (int vertex = 0; vertex < 3; vertex++) {
                int offset = triangle + vertex * 32;
                double x = BitConverter.ToSingle(bytes, offset), y = BitConverter.ToSingle(bytes, offset + 4), z = BitConverter.ToSingle(bytes, offset + 8);
                bool onSurface = false;
                for (int axis = 0; axis < 3; axis++) {
                    double world = transform[axis] * x + transform[3 + axis] * y + transform[6 + axis] * z + transform[9 + axis];
                    if (!Compat.Finite(world) || world < expectedMin[axis] - tolerance || world > expectedMax[axis] + tolerance) return false;
                    onSurface |= Math.Abs(world - expectedMin[axis]) <= tolerance || Math.Abs(world - expectedMax[axis]) <= tolerance;
                    minimum[axis] = Math.Min(minimum[axis], world); maximum[axis] = Math.Max(maximum[axis], world);
                }
                if (!onSurface) return false;
            }
            for (int axis = 0; axis < 3; axis++) if (Math.Abs(minimum[axis] - expectedMin[axis]) > tolerance || Math.Abs(maximum[axis] - expectedMax[axis]) > tolerance) return false;
        }
        return true;
    }
    void SaveFixture(Document document, string filename, bool central)
    {
        string path = Compat.Below(root, Path.Combine(root, filename));
        if (File.Exists(path)) throw new IOException("Тестовый RVT уже существует: " + filename);
        using var options = new SaveAsOptions { OverwriteExistingFile = false, MaximumBackups = 1 };
        if (central) { using var sharing = new WorksharingSaveAsOptions { SaveAsCentral = true }; options.SetWorksharingOptions(sharing); }
        document.SaveAs(path, options); fixturePaths.Add(path); Flush();
    }
    void OnDialog(object? sender, DialogBoxShowingEventArgs args)
    {
        if (!enabled) return;
        string message = args is TaskDialogShowingEventArgs task ? task.Message : args is MessageBoxShowingEventArgs box ? box.Message : "Native test dialog; no override applied";
        diagnostics.Add(new { dialog_id = args.DialogId, message });
        try { Flush(); Status("requires_action", message, args.DialogId); } catch { }
    }
    void Status(string state, string message, string? dialog = null) => Protocol.Atomic(Path.Combine(root, "native_status.json"),
        new { schema = 1, session_id = session, request_id = request, state, message, dialog_id = dialog });
    void Flush() => Protocol.Atomic(Path.Combine(root, "native_report.json"), report);
    sealed record CaseResult(string stage, string detail, bool export_succeeded, bool all_selected_exported, bool rollback_unchanged, bool world_vertices_preserved, bool native_metadata_conservative,
        string[] expected_ids, string[] occurrence_ids, string[] missing_ids, string[] warnings, string error, JsonElement before, JsonElement after);
}

// The exporter only needs this semantic workset helper from the production implementation.
internal static class WorkerImplementation
{
    internal static string Workset(Element element) { try { return element.Document.GetWorksetTable().GetWorkset(element.WorksetId)?.Name ?? ""; } catch { return ""; } }
}
