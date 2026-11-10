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
            report["production_exporter"] = true; report["fixture_kind"] = "selective_boxes_groups_assemblies";
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
                for (int index = 0; index < 128; index++) Box(owned, 20 + 2 * index, "background");
                if (transaction.Commit() != TransactionStatus.Committed) throw new InvalidOperationException("Не удалось создать тестовые DirectShape.");
            }
            var selected = new HashSet<long> { hidden.Value, shown.Value };
            report["selected_ids"] = selected.OrderBy(x => x).Select(x => x.ToString()).ToArray();
            report["default_3d_type_id"] = type.Id.Value.ToString();
            SaveFixture(owned, "fixture_plain.rvt", false);
            foreach (string detail in new[] { "coarse", "medium", "fine" }) RunCase(owned, "plain", detail, selected);

            ElementId member, nestedGroup, outerGroup, assembly;
            using (var transaction = new Transaction(owned, "XIVgate selective containers")) {
                transaction.Start();
                transaction.SetFailureHandlingOptions(transaction.GetFailureHandlingOptions().SetFailuresPreprocessor(new FixtureFailures(diagnostics)).SetClearAfterRollback(true));
                member = Box(owned, 300, "group child");
                var inner = owned.Create.NewGroup(new[] { member, Box(owned, 302, "group sibling") });
                nestedGroup = inner.Id;
                outerGroup = owned.Create.NewGroup(new[] { inner.Id, Box(owned, 304, "outer sibling") }).Id;
                if (transaction.Commit() != TransactionStatus.Committed) throw new InvalidOperationException("Cannot create selective containers.");
            }
            RunCase(owned, "one_box_subset", "medium", new HashSet<long> { shown.Value });
            RunCase(owned, "nested_group", "medium", new HashSet<long> { nestedGroup.Value });
            RunCase(owned, "outer_group", "medium", new HashSet<long> { outerGroup.Value });
            RunCase(owned, "group_child_only", "medium", new HashSet<long> { member.Value });
            using (var transaction = new Transaction(owned, "XIVgate selective assembly")) {
                transaction.Start();
                transaction.SetFailureHandlingOptions(transaction.GetFailureHandlingOptions().SetFailuresPreprocessor(new FixtureFailures(diagnostics)).SetClearAfterRollback(true));
                var assemblyMembers = new[] { Box(owned, 310, "assembly a"), Box(owned, 312, "assembly b") };
                owned.Regenerate();
                assembly = AssemblyInstance.Create(owned, assemblyMembers, new ElementId(BuiltInCategory.OST_GenericModel)).Id;
                if (transaction.Commit() != TransactionStatus.Committed) throw new InvalidOperationException("Cannot create fixture assembly.");
            }
            RunCase(owned, "assembly", "medium", new HashSet<long> { assembly.Value });
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
    sealed class FixtureFailures : IFailuresPreprocessor
    {
        readonly List<object> diagnostics;
        public FixtureFailures(List<object> diagnostics) { this.diagnostics = diagnostics; }
        public FailureProcessingResult PreprocessFailures(FailuresAccessor failures)
        {
            bool error = false;
            foreach (var failure in failures.GetFailureMessages()) {
                diagnostics.Add(new { fixture_failure = failure.GetDescriptionText(), severity = failure.GetSeverity().ToString() });
                if (failure.GetSeverity() == FailureSeverity.Warning) failures.DeleteWarning(failure);
                else error = true;
            }
            return error ? FailureProcessingResult.ProceedWithRollBack : FailureProcessingResult.Continue;
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
        string before = Snapshot(document), geometryBefore = ReferenceWorldSnapshot(document, selected), error = "";
        var warnings = new List<string>(); var exported = new HashSet<long>();
        bool success = false, worldVertices = false, nativeMetadata = false;
        try {
            string baselineOutput = output + "_baseline";
            Directory.CreateDirectory(baselineOutput);
            BaselineGeometryExporter.Run(document, baselineOutput, selected, new { native_fixture = true },
                Path.Combine(root, "fixture_plain.rvt"), detail, () => false, new List<string>());
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
            using var baselineJson = Protocol.Read(Path.Combine(baselineOutput, "manifest.json"));
            bool identicalGeometry = File.ReadAllBytes(Path.Combine(baselineOutput, "geometry.bin")).SequenceEqual(File.ReadAllBytes(Path.Combine(output, "geometry.bin")));
            bool identicalOccurrences = baselineJson.RootElement.GetProperty("occurrences").GetRawText() == json.RootElement.GetProperty("occurrences").GetRawText();
            bool identicalMaterials = baselineJson.RootElement.GetProperty("materials").GetRawText() == json.RootElement.GetProperty("materials").GetRawText();
            if (!identicalGeometry || !identicalOccurrences || !identicalMaterials) throw new InvalidOperationException("Isolated output differs from full traversal: geometry=" + identicalGeometry + ", occurrences=" + identicalOccurrences + ", materials=" + identicalMaterials);
            long baselineVisited = baselineJson.RootElement.GetProperty("summary").GetProperty("visited_elements").GetInt64();
            long baselineCallbacks = baselineJson.RootElement.GetProperty("summary").GetProperty("material_callbacks").GetInt64();
            long visited = json.RootElement.GetProperty("summary").GetProperty("visited_elements").GetInt64();
            long callbacks = json.RootElement.GetProperty("summary").GetProperty("material_callbacks").GetInt64();
            diagnostics.Add(new { stage, visited_elements = visited, material_callbacks = callbacks, baseline_visited_elements = baselineVisited, baseline_material_callbacks = baselineCallbacks, identical_geometry_bytes = identicalGeometry, identical_occurrences = identicalOccurrences, identical_materials = identicalMaterials });
            if (visited >= 128) throw new InvalidOperationException("Subset traversed background boxes: " + visited);
            success = true;
        } catch (Exception ex) { error = ex.ToString(); }
        string after = Snapshot(document);
        if (geometryBefore != ReferenceWorldSnapshot(document, selected)) { success = false; error += " Primitive membership/world geometry changed after rollback."; }
        cases.Add(new CaseResult(stage, detail, success, selected.SetEquals(exported), before == after, worldVertices, nativeMetadata,
            selected.OrderBy(id => id).Select(id => id.ToString()).ToArray(), exported.OrderBy(id => id).Select(id => id.ToString()).ToArray(),
            selected.Except(exported).OrderBy(id => id).Select(id => id.ToString()).ToArray(), warnings.ToArray(), error, JsonSerializer.Deserialize<JsonElement>(before), JsonSerializer.Deserialize<JsonElement>(after)));
        Flush(); Status("running", stage + " / " + detail + ": " + exported.Count + " из " + selected.Count);
    }
    static IEnumerable<Element> PrimitiveDescendants(Document document, Element owner)
    {
        var pending = new Queue<ElementId>(); pending.Enqueue(owner.Id);
        var visited = new HashSet<long>();
        while (pending.Count > 0) {
            var id = pending.Dequeue(); if (!visited.Add(id.Value)) continue;
            var element = document.GetElement(id) ?? throw new InvalidOperationException("Missing fixture member " + id.Value);
            if (element is Group group) { foreach (var member in group.GetMemberIds()) pending.Enqueue(member); }
            else if (element is AssemblyInstance assembly) { foreach (var member in assembly.GetMemberIds()) pending.Enqueue(member); }
            else if (element is DirectShape) yield return element;
            else throw new InvalidOperationException("Unsupported fixture primitive " + id.Value + ": " + element.GetType().FullName);
        }
    }
    static IEnumerable<XYZ> GeometryWorldPoints(GeometryElement geometry, Transform transform)
    {
        foreach (var item in geometry) {
            if (item is GeometryInstance instance) {
                using var symbol = instance.GetSymbolGeometry();
                foreach (var point in GeometryWorldPoints(symbol, transform.Multiply(instance.Transform))) yield return point;
            } else if (item is Solid solid) {
                foreach (Edge edge in solid.Edges) foreach (var point in edge.Tessellate()) yield return transform.OfPoint(point) * 0.3048;
            } else if (item is Mesh mesh) {
                foreach (var point in mesh.Vertices) yield return transform.OfPoint(point) * 0.3048;
            }
        }
    }
    static List<XYZ> PrimitivePoints(Element primitive)
    {
        using var options = new Options { DetailLevel = ViewDetailLevel.Fine, IncludeNonVisibleObjects = false };
        using var geometry = primitive.get_Geometry(options);
        if (geometry == null) throw new InvalidOperationException("Missing fixture geometry " + primitive.Id.Value);
        var points = GeometryWorldPoints(geometry, Transform.Identity).ToList();
        if (points.Count == 0) throw new InvalidOperationException("Empty fixture geometry " + primitive.Id.Value);
        return points;
    }
    static string ReferenceWorldSnapshot(Document document, HashSet<long> selected)
    {
        // Read actual membership and geometry independently of CustomExporter, both before
        // and after its transaction rollback. No group/assembly bounding-box assumptions.
        return JsonSerializer.Serialize(selected.OrderBy(id => id).Select(id => new {
            owner = id,
            primitives = PrimitiveDescendants(document, document.GetElement(new ElementId(id))).OrderBy(element => element.Id.Value).Select(element => new {
                id = element.Id.Value,
                points = PrimitivePoints(element).Select(point => new[] { point.X, point.Y, point.Z })
                    .OrderBy(point => point[0]).ThenBy(point => point[1]).ThenBy(point => point[2]).ToArray()
            }).ToArray()
        }).ToArray());
    }
    bool PrimitiveWorldVertices(Document document, JsonElement manifest, string binaryPath)
    {
        var prototypes = manifest.GetProperty("prototypes").EnumerateArray().ToDictionary(item => item.GetProperty("id").GetInt32(), item => item.Clone());
        using var binary = File.OpenRead(binaryPath);
        // Revit's tessellated internal-feet coordinates and the exported metre coordinates
        // both pass through float32. At this fixture's ~300 m origin their combined error
        // can exceed 30 micrometres; 0.1 mm is the explicit absolute acceptance budget.
        const double tolerance = 1e-4;
        double Error(XYZ a, XYZ b) => Math.Max(Math.Abs(a.X - b.X), Math.Max(Math.Abs(a.Y - b.Y), Math.Abs(a.Z - b.Z)));
        bool Near(XYZ a, XYZ b) => Math.Abs(a.X - b.X) <= tolerance && Math.Abs(a.Y - b.Y) <= tolerance && Math.Abs(a.Z - b.Z) <= tolerance;
        foreach (var occurrence in manifest.GetProperty("occurrences").EnumerateArray()) {
            var element = document.GetElement(new ElementId(long.Parse(occurrence.GetProperty("id").GetString()!)));
            var primitives = PrimitiveDescendants(document, element).ToArray();
            var expected = primitives.SelectMany(PrimitivePoints).ToArray();
            var actual = new List<XYZ>();
            double[] transform = occurrence.GetProperty("transform").EnumerateArray().Select(value => value.GetDouble()).ToArray();
            var prototype = prototypes[occurrence.GetProperty("prototype").GetInt32()];
            byte[] bytes = new byte[prototype.GetProperty("bytes").GetInt32()];
            binary.Position = prototype.GetProperty("offset").GetInt64(); Compat.ReadExactly(binary, bytes, 0, bytes.Length);
            for (int triangle = 0; triangle < bytes.Length; triangle += 104) for (int vertex = 0; vertex < 3; vertex++) {
                int offset = triangle + vertex * 32;
                double x = BitConverter.ToSingle(bytes, offset), y = BitConverter.ToSingle(bytes, offset + 4), z = BitConverter.ToSingle(bytes, offset + 8);
                actual.Add(new XYZ(transform[0] * x + transform[3] * y + transform[6] * z + transform[9],
                    transform[1] * x + transform[4] * y + transform[7] * z + transform[10],
                    transform[2] * x + transform[5] * y + transform[8] * z + transform[11]));
            }
            double maximumError = Math.Max(actual.Max(point => expected.Min(reference => Error(point, reference))),
                expected.Max(reference => actual.Min(point => Error(point, reference))));
            diagnostics.Add(new { absolute_world_check_owner = element.Id.Value, primitive_ids = primitives.Select(primitive => primitive.Id.Value).ToArray(),
                tolerance_m = tolerance, max_world_error_m = maximumError });
            var unexpected = actual.FirstOrDefault(point => !expected.Any(reference => Near(point, reference)));
            var missing = expected.FirstOrDefault(reference => !actual.Any(point => Near(point, reference)));
            if (unexpected != null || missing != null) throw new InvalidOperationException("Absolute world geometry mismatch: owner=" + element.Id.Value +
                "; primitive_ids=" + string.Join(",", primitives.Select(primitive => primitive.Id.Value)) +
                "; source_ids=" + occurrence.GetProperty("source_element_ids").GetRawText() +
                "; unexpected=" + unexpected + "; missing=" + missing);
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
