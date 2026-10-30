using System;
using System.IO;
using System.Reflection;
using System.Text;
using Autodesk.Revit.UI;
using XIVgate.RevitBridge;

static class LoaderTests
{
    static int assertions;
    static void Check(bool value, string label) { assertions++; if (!value) throw new Exception(label); }
    static void Throws(Action action, string label) { bool thrown = false; try { action(); } catch (IOException) { thrown = true; } catch (ArgumentException) { thrown = true; } Check(thrown, label); }
    static string Marker(string id) => "{\"schema\":1,\"package_id\":\"xivgate_revit_bridge\",\"session_id\":\"" + id + "\"}";
    static readonly string[] Variables = { "XIVGATE_RBB_JOB_ROOT", "XIVGATE_RBB_SESSION_ID", "XIVGATE_RBB_SESSION_TOKEN", "XIVGATE_RBB_WORKER_VERSION", "XIVGATE_RBB_WORKER_SHA256" };

    public static int Main(string[] args)
    {
        string root = Path.GetFullPath(args[0]);
        string loader = Path.GetFullPath(args[1]);
        string workerFixture = Path.GetFullPath(args[2]);
#if NETFRAMEWORK
        string key = "2024", year = "2024"; const int runtime = 4;
#else
        string key = "2027", year = "2027"; const int runtime = 10;
#endif
        string id = Guid.NewGuid().ToString("D"), job = Path.Combine(root, id);
        Directory.CreateDirectory(job);
        string marker = Path.Combine(job, LoaderGuard.Marker);
        File.WriteAllText(marker, Marker(id), new UTF8Encoding(true));
        Check(LoaderGuard.ValidateJob(job, id, "test-token") == job, "owned UUID marker accepts BOM");
        foreach (string bad in new[] { "{}", "[]", Marker(id).Replace("1,", "\"1\","), Marker(id).Replace("1,", "\"#1\","),
            Marker(id).Replace("1,", "1,\"schema\":1,"), Marker(id).Replace("xivgate_revit_bridge", "foreign"),
            Marker(Guid.NewGuid().ToString("D")), Marker(id) + "junk", Marker(id).Replace("schema", "sche\\u006da") }) {
            File.WriteAllText(marker, bad); Throws(() => LoaderGuard.ValidateJob(job, id, "test-token"), "invalid/foreign/duplicate marker rejected");
        }
        File.WriteAllText(marker, " { \"session_id\" : \"" + id + "\", \"package_id\" : \"xivgate_revit_bridge\", \"schema\" : 1 } ");
        Check(LoaderGuard.ValidateJob(job, id, "test-token") == job, "marker key ordering/whitespace accepted");
        Throws(() => LoaderGuard.ValidateJob(job, Guid.NewGuid().ToString(), "test-token"), "session mismatch");
        Throws(() => LoaderGuard.ValidateJob(job, id, ""), "missing token");
        Throws(() => LoaderGuard.ValidateJob("relative", id, "test-token"), "relative job path");
        string finished = Path.Combine(job, ".agr_revit_bridge_finished.json"); File.WriteAllText(finished, Marker(id));
        Throws(() => LoaderGuard.ValidateJob(job, id, "test-token"), "finished jobs cannot restart"); File.Delete(finished);
        foreach (string version in new[] { "../0.4.8", "0.4.8/../other", "v0.4.8", "0.4.8-beta", "00.4.8", "0.4", "0.4.8.1", "0.4.2147483648", "0.4.8\n" })
            Throws(() => LoaderGuard.ValidateVersion(version), "noncanonical/traversing version");
        LoaderGuard.ValidateVersion("0.4.8"); Check(true, "canonical version");
        foreach (var configuration in new[] { new[] { "2024", "2024", "4" }, new[] { "2025", "2025", "8" },
            new[] { "2025-net10", "2025", "10" }, new[] { "2026", "2026", "8" },
            new[] { "2026-net10", "2026", "10" }, new[] { "2027", "2027", "10" } }) {
            LoaderGuard.ValidateHost(configuration[0], configuration[1], int.Parse(configuration[2])); Check(true, "supported host key/runtime");
            Throws(() => LoaderGuard.ValidateHost(configuration[0], "2023", int.Parse(configuration[2])), "wrong host major");
        }
        Throws(() => LoaderGuard.ValidateHost("2025", "2025", 10), "runtime variant mismatch");
        Throws(() => LoaderGuard.ValidateHost("2027", "2027", 8), "unsupported host runtime");
        string keyRoot = Directory.GetParent(Directory.GetParent(Path.GetDirectoryName(loader)!)!.FullName)!.FullName;
        string worker = Path.Combine(keyRoot, "0.4.8", LoaderGuard.WorkerFile);
        Directory.CreateDirectory(Path.GetDirectoryName(worker)!); File.Copy(workerFixture, worker, true);
        string sha = LoaderGuard.Sha256(worker);
        Check(LoaderGuard.ValidateWorker(loader, key, "0.4.8", sha, year, runtime) == worker, "only own versioned worker path");
        Check(LoaderGuard.ValidateWorker(loader, key, "0.4.8", sha.ToUpperInvariant(), year, runtime) == worker, "SHA256 case tolerated");
        using (var pinned = LoaderGuard.OpenPinnedWorker(worker, sha)) {
            Throws(() => { using var write = new FileStream(worker, FileMode.Open, FileAccess.Write, FileShare.ReadWrite); }, "pinned load denies concurrent writes");
            Throws(() => File.Delete(worker), "pinned load denies deletion/replacement");
        }
        Throws(() => LoaderGuard.ValidateWorker(loader, key, "0.4.8", new string('0', 64), year, runtime), "modified worker hash rejected");
        Throws(() => LoaderGuard.ValidateWorker(loader, key, "0.4.8", "bad", year, runtime), "malformed digest rejected");
        Throws(() => LoaderGuard.ValidateWorker(Path.Combine(root, LoaderGuard.LoaderFile), key, "0.4.8", sha, year, runtime), "noncanonical loader location");
        Throws(() => LoaderGuard.Below(keyRoot, keyRoot + "-sibling\\file.dll"), "sibling prefix escape");
        string junction = Path.Combine(root, "junction");
        try {
            var start = new System.Diagnostics.ProcessStartInfo("cmd.exe", "/d /c mklink /J \"" + junction + "\" \"" + job + "\"") {
                UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true };
            using var process = System.Diagnostics.Process.Start(start)!; process.StandardOutput.ReadToEnd(); process.StandardError.ReadToEnd(); process.WaitForExit();
            if (process.ExitCode == 0) { Throws(() => LoaderGuard.AbsoluteWithoutLinks(Path.Combine(junction, LoaderGuard.Marker)), "junction on any ancestor rejected"); Directory.Delete(junction); }
            else Console.WriteLine("SKIP junction creation unavailable");
        } finally { if (Directory.Exists(junction)) Directory.Delete(junction); }

        // Test the real loader entry point against a small test-only API double, not Revit.
        Assembly assembly = Assembly.LoadFrom(loader);
        Check(assembly.GetName().Version!.ToString() == "1.0.0.0", "frozen assembly version");
        foreach (var reference in assembly.GetReferencedAssemblies())
            Check(reference.Name != "System.Text.Json", "loader carries no JSON dependency");
        Func<IExternalApplication> create = () => (IExternalApplication)Activator.CreateInstance(assembly.GetType(LoaderGuard.EntryClass, true)!)!;
        foreach (string name in Variables) Environment.SetEnvironmentVariable(name, null);
        var app = new UIControlledApplication();
        Check(create().OnStartup(app) == Result.Succeeded && app.HostReads == 0, "ordinary Revit startup is completely inert");
        Environment.SetEnvironmentVariable(Variables[0], job); Environment.SetEnvironmentVariable(Variables[1], id); Environment.SetEnvironmentVariable(Variables[2], "test-token");
        Environment.SetEnvironmentVariable(Variables[3], "0.4.8"); Environment.SetEnvironmentVariable(Variables[4], sha);
        var bridge = create();
        Check(bridge.OnStartup(app) == Result.Succeeded && app.WorkerStarts == 1, "public worker entry point delegated");
        Check(bridge.OnShutdown(app) == Result.Succeeded && app.WorkerStops == 1, "shutdown delegated only to owned worker");
        Environment.SetEnvironmentVariable(Variables[4], new string('0', 64));
        Check(create().OnStartup(app) == Result.Failed && app.WorkerStarts == 1, "failed hash never enters worker");
        Check(File.Exists(Path.Combine(job, "loader.log")), "diagnostics only in verified job");
        string failure = Path.Combine(job, "loader_error.txt");
        Check(File.Exists(failure) && File.ReadAllText(failure, Encoding.UTF8).Contains("SHA256"), "guard failure creates readable job-only error");
        string originalFailure = File.ReadAllText(failure);
        create().OnStartup(app);
        Check(File.ReadAllText(failure) == originalFailure, "first startup failure is never overwritten");
        File.Delete(failure);
        Environment.SetEnvironmentVariable(Variables[4], sha);
        Environment.SetEnvironmentVariable("AGR_TEST_WORKER_RESULT", "Failed");
        Check(create().OnStartup(app) == Result.Failed && app.WorkerStarts == 2 && app.WorkerStops == 2, "delegated failure shuts down partial worker");
        Check(File.Exists(failure) && File.ReadAllText(failure).Contains("Failed"), "delegated failure creates error signal");
        Environment.SetEnvironmentVariable("AGR_TEST_WORKER_RESULT", null);
        File.Delete(failure);
        File.Delete(Path.Combine(job, "loader.log")); File.WriteAllText(marker, "{}"); int reads = app.HostReads;
        Check(create().OnStartup(app) == Result.Failed && app.HostReads == reads, "invalid job never touches host API");
        Check(!File.Exists(Path.Combine(job, "loader.log")), "invalid job receives no log writes");
        Check(!File.Exists(failure), "invalid job receives no failure file");
        Console.WriteLine("AGR_LOADER_TESTS " + assertions + " assertions passed; runtime " + Environment.Version);
        return 0;
    }
}
