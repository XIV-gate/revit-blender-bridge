using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.Json;
using XIVgate.RevitBridge;

static class PreparedModelCacheTests
{
    public static int Run(string root)
    {
        int count = 0;
        void Check(bool value, string label) { count++; if (!value) throw new Exception("Prepared cache: " + label); }
        void Reject(Action action, string label) {
            bool rejected = false;
            try { action(); } catch (IOException) { rejected = true; } catch (InvalidDataException) { rejected = true; }
            Check(rejected, label);
        }
        string fixture = Protocol.Below(root, Path.Combine(root, "pc-" + Guid.NewGuid().ToString("N").Substring(0, 8)));
        string session = Guid.NewGuid().ToString(), job = Path.Combine(fixture, session), input = Path.Combine(job, "input");
        Directory.CreateDirectory(input);
        string source = Path.Combine(input, "prepared.rvt"), cache = Path.Combine(fixture, "prepared_models");
        string hash = new string('a', 64), other = new string('b', 64);
        string? junction = null;
        try {
            Protocol.Atomic(Path.Combine(job, ".agr_revit_bridge_job.json"), new { schema = 1, package_id = "xivgate_revit_bridge", session_id = session });
            File.WriteAllBytes(source, new byte[] { 1, 2, 3, 4 });
            PreparedModelCache.Entry Publish() => PreparedModelCache.Publish(job, source, hash, "2019", "2024", "24.2.20", new[] { "upgrade warning" }, new object[] { new { stage = "open", description = "detail" } }, sourceIdentity: new string('c', 64));
            Check(PreparedModelCache.TryGet(job, hash, "2024", "24.2.20") == null, "absent cache misses");
            Directory.CreateDirectory(cache); File.WriteAllText(Path.Combine(cache, "foreign.txt"), "user data");
            Reject(() => Publish(), "unmarked root cannot be claimed");
            Check(File.ReadAllText(Path.Combine(cache, "foreign.txt")) == "user data", "foreign data preserved");
            File.Delete(Path.Combine(cache, "foreign.txt"));
            PreparedModelCache.Entry first;
            // Revit retains a write-capable handle after SaveAs returns.
            using (var held = new FileStream(source, FileMode.Open, FileAccess.ReadWrite, FileShare.ReadWrite)) first = Publish();
            Check(File.ReadAllBytes(source).SequenceEqual(new byte[] { 1, 2, 3, 4 }), "prepared input unchanged");
            var hit = PreparedModelCache.TryGet(job, hash, "2024", "24.2.20");
            Check(hit != null && hit.ModelPath == first.ModelPath && hit.Warnings.Single() == "upgrade warning" && hit.WarningRecords.Single().GetProperty("stage").GetString() == "open", "verified hit and cloned warning records");
            string ready = Path.Combine(first.Directory, ".agr_revit_bridge_prepared.json"), original = File.ReadAllText(ready);
            Check(Publish().Directory == first.Directory && File.ReadAllText(ready) == original && Directory.GetDirectories(cache).Length == 1, "repeat publish never rewrites immutable entry");
            Check(PreparedModelCache.TryGet(job, other, "2024", "24.2.20") == null, "source mismatch");
            Check(PreparedModelCache.TryGet(job, other, "2024", "24.2.20", true, new string('c', 64))?.ModelPath == first.ModelPath, "explicit reuse ignores changed source");
            Check(PreparedModelCache.TryGet(job, other, "2024", "24.2.20", true, new string('d',64)) == null, "a shared cache cannot substitute another source model");
            var refreshed = PreparedModelCache.Publish(job, source, hash, "2019", "2024", "24.2.20", Array.Empty<string>(), Array.Empty<object>(), true, new string('c',64));
            Check(refreshed.Directory != first.Directory, "force refresh writes a new verified copy");
            first = refreshed;
            ready = Path.Combine(first.Directory, ".agr_revit_bridge_prepared.json"); original = File.ReadAllText(ready);

            Check(PreparedModelCache.TryGet(job, hash, "2024", "24.2.21") == null, "exact build mismatch");
            Check(PreparedModelCache.TryGet(job, hash, "2025", "24.2.20") == null, "SDK year mismatch");
            File.WriteAllText(ready, original.Replace("\"policy\": 1", "\"policy\": 2"));
            Check(PreparedModelCache.TryGet(job, hash, "2024", "24.2.20") == null, "future policy mismatch");
            File.WriteAllText(ready, original);
            File.WriteAllBytes(first.ModelPath, new byte[] { 1, 2, 3, 5 });
            Check(PreparedModelCache.TryGet(job, hash, "2024", "24.2.20") == null, "same size corrupted model never reused");
            var second = Publish();
            Check(second.Directory != first.Directory && File.ReadAllBytes(first.ModelPath).Last() == 5, "corrupt entry preserved and fresh UUID published");
            File.WriteAllBytes(second.ModelPath, new byte[] { 1 });
            Check(PreparedModelCache.TryGet(job, hash, "2024", "24.2.20") == null, "truncated model rejected");
            string partial = Path.Combine(cache, Guid.NewGuid().ToString("N")); Directory.CreateDirectory(partial); File.Copy(source, Path.Combine(partial, "model.rvt"));
            Check(PreparedModelCache.TryGet(job, hash, "2024", "24.2.20") == null, "partial entry without ready marker ignored");
            string thirdMarker = Path.Combine(Publish().Directory, ".agr_revit_bridge_prepared.json");
            File.WriteAllBytes(thirdMarker, new byte[8 * 1024 * 1024 + 1]);
            Check(PreparedModelCache.TryGet(job, hash, "2024", "24.2.20") == null, "oversized sidecar rejected");
            Reject(() => PreparedModelCache.Publish(job, Path.Combine(fixture, "outside.rvt"), hash, "2019", "2024", "24.2.20", Array.Empty<string>(), Array.Empty<object>()), "foreign prepared path rejected before hit");
            Reject(() => PreparedModelCache.TryGet(job, "BAD", "2024", "build"), "invalid source fingerprint rejected");
            int beforeOversized = Directory.GetDirectories(cache).Length;
            Reject(() => PreparedModelCache.Publish(job, source, other, "2019", "2024", "24.2.20", new[] { new string('x', 8 * 1024 * 1024) }, Array.Empty<object>()), "oversized publication rejected");
            Check(Directory.GetDirectories(cache).Length == beforeOversized, "failed publication removes only its own partial entry");
            string marker = Path.Combine(job, ".agr_revit_bridge_job.json"); File.WriteAllText(marker, "{}");
            Reject(() => Publish(), "job ownership required");
            Protocol.Atomic(marker, new { schema = 1, package_id = "xivgate_revit_bridge", session_id = session });
            string rootMarker = Path.Combine(cache, ".agr_revit_bridge_prepared_root.json"), savedRootMarker = File.ReadAllText(rootMarker);
            File.WriteAllText(rootMarker, "{\"schema\":1,\"package_id\":\"foreign\",\"kind\":\"prepared_model_cache\"}");
            Reject(() => Publish(), "foreign root marker cannot be repaired implicitly");
            File.WriteAllText(rootMarker, savedRootMarker);
            // Directory junctions require no symlink privilege on Windows.
            string target = Path.Combine(fixture, "junction-target"); Directory.CreateDirectory(target);
            junction = Path.Combine(input, "linked");
            using (var process = Process.Start(new ProcessStartInfo("cmd.exe", "/c mklink /J \"" + junction + "\" \"" + target + "\"") { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true })) {
                process!.WaitForExit(); Check(process.ExitCode == 0, "junction fixture available");
            }
            File.WriteAllBytes(Path.Combine(target, "foreign.rvt"), new byte[] { 9 });
            Reject(() => PreparedModelCache.Publish(job, Path.Combine(junction, "foreign.rvt"), hash, "2019", "2024", "24.2.20", Array.Empty<string>(), Array.Empty<object>()), "junction input rejected");
            Directory.Delete(junction); junction = null;
            junction = Path.Combine(fixture, "ancestor");
            using (var process = Process.Start(new ProcessStartInfo("cmd.exe", "/c mklink /J \"" + junction + "\" \"" + fixture + "\"") { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true })) {
                process!.WaitForExit(); Check(process.ExitCode == 0, "ancestor junction fixture available");
            }
            Reject(() => PreparedModelCache.TryGet(Path.Combine(junction, session), hash, "2024", "24.2.20"), "junction ancestor rejected");
            Directory.Delete(junction); junction = null;
            Console.WriteLine("PASS: " + count + " prepared cache assertions");
            return count;
        } finally {
            if (junction != null && Directory.Exists(junction)) Directory.Delete(junction);
            Protocol.Below(root, fixture);
            if (Directory.Exists(fixture)) Directory.Delete(fixture, true);
        }
    }
}
