using System;
using System.IO;
using System.Reflection;
using System.Linq;
using System.Runtime.CompilerServices;
using Autodesk.Revit.UI;

namespace XIVgate.RevitBridge;

// Public Revit entry point intentionally contains no System.Text.Json types. Framework dependency
// resolution must be installed before the CLR loads/JITs the implementation's JSON-bearing types.
public sealed class WorkerApp : IExternalApplication
{
    IExternalApplication? implementation;
    FrameworkDependencies? dependencies;
    public Result OnStartup(UIControlledApplication app)
    {
        string? job = Environment.GetEnvironmentVariable("XIVGATE_RBB_JOB_ROOT");
        if (string.IsNullOrWhiteSpace(job) || string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("XIVGATE_RBB_SESSION_TOKEN"))) return Result.Succeeded;
        try {
            dependencies = FrameworkDependencies.Install(typeof(WorkerApp).Assembly);
            implementation = CreateImplementation();
            var result = implementation.OnStartup(app);
            if (result != Result.Succeeded) RemoveResolver();
            return result;
        } catch (Exception ex) {
            RemoveResolver();
            try {
                if (Compat.FullyQualified(job) && Directory.Exists(job)) {
                    string path = Path.Combine(job, "bootstrap_error.log");
                    // The core path guard has no JSON runtime dependency.
                    Compat.Below(job, path);
                    File.AppendAllText(path, DateTime.UtcNow.ToString("O") + " " + ex + Environment.NewLine);
                }
            } catch { }
            return Result.Failed;
        }
    }
    [MethodImpl(MethodImplOptions.NoInlining)]
    static IExternalApplication CreateImplementation() => new WorkerImplementation();
    public Result OnShutdown(UIControlledApplication app)
    {
        try { return implementation?.OnShutdown(app) ?? Result.Succeeded; }
        finally { RemoveResolver(); }
    }
    void RemoveResolver()
    {
        dependencies?.Dispose(); dependencies = null;
    }
}
