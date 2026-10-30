using System;
using System.IO;
using System.Reflection;
using System.Runtime.CompilerServices;
using Autodesk.Revit.UI;

namespace XIVgate.RevitBridge;

// Frozen registration entry point. Ordinary user Revit sessions never load a worker.
public sealed class WorkerApp : IExternalApplication
{
    IExternalApplication? implementation;
    string? job, session, token;

    public Result OnStartup(UIControlledApplication app)
    {
        string? root = Environment.GetEnvironmentVariable("XIVGATE_RBB_JOB_ROOT");
        session = Environment.GetEnvironmentVariable("XIVGATE_RBB_SESSION_ID");
        token = Environment.GetEnvironmentVariable("XIVGATE_RBB_SESSION_TOKEN");
        if (string.IsNullOrWhiteSpace(root) || string.IsNullOrWhiteSpace(session) || string.IsNullOrWhiteSpace(token)) return Result.Succeeded;
        try {
            job = LoaderGuard.ValidateJob(root!, session!, token!);
            string version = Environment.GetEnvironmentVariable("XIVGATE_RBB_WORKER_VERSION") ?? "";
            string sha = Environment.GetEnvironmentVariable("XIVGATE_RBB_WORKER_SHA256") ?? "";
            string year = app.ControlledApplication.VersionNumber;
            string path = LoaderGuard.ValidateWorker(typeof(WorkerApp).Assembly.Location, LoaderBuild.Key, version, sha, year, Environment.Version.Major);
            using (var pin = LoaderGuard.OpenPinnedWorker(path, sha)) implementation = LoadWorker(path, year);
            Log("Worker verified; loader generation 1, key " + LoaderBuild.Key + ", worker " + version);
            var result = implementation.OnStartup(app);
            if (result != Result.Succeeded) {
                ReportFailure(new InvalidOperationException("Обработчик Revit вернул " + result + ". Подробности: worker.log или startup_error.json."));
                try { implementation.OnShutdown(app); } catch (Exception cleanup) { Log("Worker cleanup: " + cleanup); }
                implementation = null;
            }
            return result;
        } catch (Exception error) {
            ReportFailure(error);
            try { implementation?.OnShutdown(app); } catch (Exception cleanup) { Log("Worker cleanup: " + cleanup); }
            implementation = null;
            return Result.Failed;
        }
    }

    [MethodImpl(MethodImplOptions.NoInlining)]
    static IExternalApplication LoadWorker(string path, string year)
    {
        if (AssemblyName.GetAssemblyName(path).Name != "XIVgate.RevitBridge.Worker")
            throw new IOException("Сборка не является обработчиком AGR Revit.");
        var assembly = Assembly.LoadFrom(path);
        if (!string.Equals(LoaderGuard.AbsoluteWithoutLinks(assembly.Location), path, StringComparison.OrdinalIgnoreCase))
            throw new IOException("В этом процессе уже загружена другая сборка worker.");
        foreach (var reference in assembly.GetReferencedAssemblies())
            if ((reference.Name == "RevitAPI" || reference.Name == "RevitAPIUI") && reference.Version?.Major != int.Parse(year) - 2000)
                throw new IOException("Worker собран для другой версии Revit API.");
        var type = assembly.GetType(LoaderGuard.EntryClass, true)!;
        if (!type.IsPublic || type.IsAbstract || !typeof(IExternalApplication).IsAssignableFrom(type))
            throw new IOException("Не найден штатный открытый entry point worker.");
        // Instantiate the worker's public bootstrap, never its JSON-bearing implementation.
        return (IExternalApplication)Activator.CreateInstance(type)!;
    }

    public Result OnShutdown(UIControlledApplication app)
    {
        try { return implementation?.OnShutdown(app) ?? Result.Succeeded; }
        catch (Exception error) { Log("Worker shutdown: " + error); return Result.Failed; }
        finally { implementation = null; }
    }

    void Log(string message)
    {
        if (job == null || session == null || token == null) return;
        try {
            string root = LoaderGuard.ValidateJob(job, session, token);
            string path = LoaderGuard.Below(root, Path.Combine(root, "loader.log"));
            File.AppendAllText(path, DateTime.UtcNow.ToString("O") + " " + message + Environment.NewLine);
        } catch { /* Unverified paths never receive diagnostics. */ }
    }

    void ReportFailure(Exception error)
    {
        Log("Loader startup rejected: " + error);
        if (job == null || session == null || token == null) return;
        try {
            string root = LoaderGuard.ValidateJob(job, session, token);
            string path = LoaderGuard.Below(root, Path.Combine(root, "loader_error.txt"));
            using var stream = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.Read);
            using var writer = new StreamWriter(stream, new System.Text.UTF8Encoding(false));
            writer.WriteLine("Не удалось запустить обработчик Revit: " + error.Message.Replace('\r', ' ').Replace('\n', ' '));
            writer.WriteLine(error.ToString());
            writer.WriteLine(Environment.StackTrace);
        } catch (Exception reporting) { Log("Failure signal not written (existing or unavailable): " + reporting.Message); }
    }
}
