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
            report["production_exporter"] = true; report["fixture_kind"] = "curved_cylinder_and_flat_box";
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
    void OnIdling(object? sender, IdlingEventArgs args) {
        if(!enabled || ran || sender is not UIApplication ui)return;
        ran=true;
        try {
            if(ui.Application.Documents.Cast<Document>().Any(d=>!d.IsLinked))throw new InvalidOperationException("Separate empty process required");
            owned=ui.Application.NewProjectDocument(UnitSystem.Metric);
            ElementId curved,flat;
            using(var tx=new Transaction(owned,"Curve detail fixture")) {
                tx.Start();
                var circle=new CurveLoop();
                circle.Append(Arc.Create(new XYZ(0,0,0),1,0,Math.PI,XYZ.BasisX,XYZ.BasisY));
                circle.Append(Arc.Create(new XYZ(0,0,0),1,Math.PI,2*Math.PI,XYZ.BasisX,XYZ.BasisY));
                var cylinder=GeometryCreationUtilities.CreateExtrusionGeometry(new[]{circle},XYZ.BasisZ,10);
                var shape=DirectShape.CreateElement(owned,new ElementId(BuiltInCategory.OST_GenericModel));shape.SetShape(new GeometryObject[]{cylinder});curved=shape.Id;
                var rectangle=new CurveLoop();
                var points=new[]{new XYZ(5,0,0),new XYZ(6,0,0),new XYZ(6,1,0),new XYZ(5,1,0)};
                for(int i=0;i<4;i++)rectangle.Append(Line.CreateBound(points[i],points[(i+1)%4]));
                var box=GeometryCreationUtilities.CreateExtrusionGeometry(new[]{rectangle},XYZ.BasisZ,2);
                shape=DirectShape.CreateElement(owned,new ElementId(BuiltInCategory.OST_GenericModel));shape.SetShape(new GeometryObject[]{box});flat=shape.Id;
                tx.Commit();
            }
            string saved=Compat.Below(root,Path.Combine(root,"fixture_curves.rvt"));
            using(var save=new SaveAsOptions{OverwriteExistingFile=false,MaximumBackups=1})owned.SaveAs(saved,save);
            report["fixture_path"]=saved;
            int originalCount=new FilteredElementCollector(owned).GetElementCount();
            var byLevel=new Dictionary<int,long>();
            long? flatTriangles=null;
            foreach(string family in new[]{"coarse","medium","fine"})foreach(int level in new[]{1,5,8,15}) {
                string output=Compat.Below(root,Path.Combine(root,family+"_"+level));Directory.CreateDirectory(output);
                GeometryExporter.Run(owned,output,new HashSet<long>{curved.Value,flat.Value},new{fixture=true},Path.Combine(root,"fixture.rvt"),family,()=>false,new List<string>(),level);
                using var manifest=Protocol.Read(Path.Combine(output,"manifest.json"));var data=manifest.RootElement;
                var prototypes=data.GetProperty("prototypes").EnumerateArray().ToDictionary(p=>p.GetProperty("id").GetInt32(),p=>p.GetProperty("triangles").GetInt64());
                long Count(ElementId id) => data.GetProperty("occurrences").EnumerateArray().Where(o=>o.GetProperty("id").GetString()==id.Value.ToString()).Sum(o=>prototypes[o.GetProperty("prototype").GetInt32()]);
                long curveCount=Count(curved),flatCount=Count(flat);
                if(curveCount<=0 || flatCount<=0)throw new Exception("Missing fixture geometry");
                if(byLevel.TryGetValue(level,out long previous) && previous!=curveCount)throw new Exception("Family setting unexpectedly alters DirectShape tessellation");
                byLevel[level]=curveCount;
                flatTriangles??=flatCount;if(flatTriangles!=flatCount)throw new Exception("Flat geometry changed with curve detail");
                if(new FilteredElementCollector(owned).GetElementCount()!=originalCount)throw new Exception("Temporary view did not roll back");
                cases.Add(new{family_detail=family,curve_detail=level,curved_triangles=curveCount,flat_triangles=flatCount,rollback_unchanged=true});
                Flush();
            }
            if(byLevel[15]<=byLevel[1])throw new Exception("Curve detail did not increase tessellation");
            report["status"]="passed";report["revit_version"]=ui.Application.VersionNumber;
        } catch(Exception ex) {report["status"]="failed";report["error"]=ex.ToString();}
        finally {
            if(owned!=null){owned.Close(false);owned=null;report["owned_document_closed_without_saving"]=true;}
            report["exit_requested"]=false;
            if(!ui.Application.Documents.Cast<Document>().Any(d=>!d.IsLinked)) {
                var command=RevitCommandId.LookupPostableCommandId(PostableCommand.ExitRevit);
                if(ui.CanPostCommand(command)){ui.PostCommand(command);report["exit_requested"]=true;}
            }
            Flush();Status("completed","Curve detail regression completed");
        }
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
}

// The exporter only needs this semantic workset helper from the production implementation.
internal static class WorkerImplementation
{
    internal static string Workset(Element element) { try { return element.Document.GetWorksetTable().GetWorkset(element.WorksetId)?.Name ?? ""; } catch { return ""; } }
}
