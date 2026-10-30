using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Runtime.CompilerServices;
using XIVgate.RevitBridge;

static class ProtocolTests
{
    static int assertions;
    static void Check(bool condition, string label) { assertions++; if (!condition) throw new Exception(label); }
    static void Throws(Action action, string label) { bool failed = false; try { action(); } catch (ArgumentException) { failed = true; } catch (IOException) { failed = true; } Check(failed, label); }
    public static int Main(string[] args)
    {
        using var dependencies = FrameworkDependencies.Install(typeof(ProtocolTests).Assembly, Console.WriteLine);
        return Run(args);
    }
    [MethodImpl(MethodImplOptions.NoInlining)]
    static int Run(string[] args)
    {
        string root = Path.GetFullPath(args[0]);
        string file = Path.Combine(root, "protocol-" + Guid.NewGuid().ToString("N") + ".json");
        try {
#if NETFRAMEWORK
            using (var scope = FrameworkDependencies.Install(typeof(ProtocolTests).Assembly)) {
                var resolve = typeof(FrameworkDependencies).GetMethod("Resolve", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance)!;
                string unsafeName = System.Reflection.AssemblyName.GetAssemblyName(Path.Combine(root, "System.Runtime.CompilerServices.Unsafe.dll")).FullName;
                Check(resolve.Invoke(scope, new object?[] { null, new ResolveEventArgs(unsafeName) }) == null, "null caller is rejected after JSON-only bootstrap completes");
                Check(resolve.Invoke(scope, new object?[] { null, new ResolveEventArgs(unsafeName, typeof(string).Assembly) }) == null, "foreign framework/plugin requester is never redirected");
                var ownDependency = resolve.Invoke(scope, new object?[] { null, new ResolveEventArgs(unsafeName, typeof(ProtocolTests).Assembly) }) as System.Reflection.Assembly;
                Check(ownDependency != null && string.Equals(Path.GetDirectoryName(ownDependency.Location), root, StringComparison.OrdinalIgnoreCase), "own dependency resolves only from exact worker folder");
                Check(resolve.Invoke(scope, new object?[] { null, new ResolveEventArgs("RevitAPI, Version=24.0.0.0, Culture=neutral, PublicKeyToken=null", typeof(ProtocolTests).Assembly) }) == null, "Autodesk assemblies are never resolved or bundled by dependency scope");
            }
#endif
            string session = Guid.NewGuid().ToString(), request = Guid.NewGuid().ToString();
            Check(OpenPolicy.NormalizeServerSnapshot(true, "RSN://example.invalid/project/model.rvt"), "server-origin local snapshot is detached before TransmissionData");
            Check(OpenPolicy.NormalizeServerSnapshot(true, "  rsn://example.invalid/model.rvt"), "server prefix is case insensitive");
            Check(!OpenPolicy.NormalizeServerSnapshot(true, @"C:\fixtures\central.rvt"), "disk central keeps existing local reference preparation");
            Check(!OpenPolicy.NormalizeServerSnapshot(false, ""), "non-workshared input keeps ordinary path");
            Check(OpenPolicy.PrepareLocalWorksharedCopy(true, false), "workshared local preparation precedes reference metadata reads");
            Check(!OpenPolicy.PrepareLocalWorksharedCopy(true, true), "explicit link-loading option is not silently overridden");
            Check(!OpenPolicy.PrepareLocalWorksharedCopy(false, false), "non-workshared files have no worksets to close");
            Check(Compat.Hex(Compat.Sha256(Encoding.UTF8.GetBytes("abc"))).ToLowerInvariant() == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", "framework SHA256 adapter matches canonical fingerprint");
            using (var stream = new MemoryStream(new byte[] { 1, 2, 3, 4 })) {
                byte[] buffer = new byte[6]; Compat.ReadExactly(stream, buffer, 1, 4);
                Check(buffer[0] == 0 && buffer[1] == 1 && buffer[4] == 4 && buffer[5] == 0, "framework binary adapter fills only requested buffer region");
                Throws(() => Compat.ReadExactly(stream, buffer, 0, 1), "truncated binary adapter input fails clearly");
            }
            using var json = JsonDocument.Parse(JsonSerializer.Serialize(new { schema = 1, request_id = request, session_id = session, token = "secret", selection = new { include_ids = new[] { "9223372036854775806", "17", "17" } } }));
            var value = json.RootElement;
            Check(Protocol.Authenticated(value, "secret", session), "matching session");
            Check(Protocol.Authenticated(value, "secret", null), "initial session binding");
            Check(!Protocol.Authenticated(value, "other", session), "reject wrong token");
            Check(!Protocol.Authenticated(value, "secret", Guid.NewGuid().ToString()), "reject stale session");
            using var array = JsonDocument.Parse("[]");
            using var badSchema = JsonDocument.Parse("{\"schema\":\"1\"}");
            Check(!Protocol.Authenticated(array.RootElement, "secret", null), "reject array payload");
            Check(!Protocol.Authenticated(badSchema.RootElement, "secret", null), "reject schema string safely");
            var ids = Protocol.Selection(value);
            Check(ids.Count == 2 && ids.Contains(9223372036854775806), "64bit authoritative IDs without duplicate");
            foreach (string invalid in new[] { "{}", "{\"selection\":null}", "{\"selection\":{\"include_ids\":[]}}", "{\"selection\":{\"include_ids\":[17]}}", "{\"selection\":{\"include_ids\":[\"not-id\"]}}", "{\"selection\":{\"include_ids\":[\"9223372036854775808\"]}}" }) {
                using var bad = JsonDocument.Parse(invalid); Throws(() => Protocol.Selection(bad.RootElement), "reject invalid/empty selection");
            }
            Check(Protocol.Below(root, file) == file, "allow owned file");
            Throws(() => Protocol.Below(root, Path.Combine(root, "..", "escape.json")), "reject traversal");
            Throws(() => Protocol.Below(root, "relative.json"), "reject relative path");
            Throws(() => Protocol.Below(root, root + "-sibling\\file.json"), "reject sibling prefix");
            File.WriteAllText(file, "{\"name\":\"Окно\"}", new UTF8Encoding(true));
            using (var bom = Protocol.Read(file)) Check(Protocol.String(bom.RootElement, "name") == "Окно", "UTF8 BOM accepted");
            Protocol.Atomic(file, new { name = "Дверь" });
            using (var atomic = Protocol.Read(file)) Check(Protocol.String(atomic.RootElement, "name") == "Дверь", "atomic replacement complete");
            Check(Directory.GetFiles(root, Path.GetFileName(file) + ".*.pending").Length == 0, "pending files cleaned");
            string dialog = "Dialog_Revit_DocWarnDialog";
            Check(WarningPolicy.AcknowledgeDocumentWarning(true, true, true, true, false, dialog), "owned opening document warning allowed");
            Check(!WarningPolicy.AcknowledgeDocumentWarning(false, true, true, true, false, dialog), "inert application cannot acknowledge");
            Check(!WarningPolicy.AcknowledgeDocumentWarning(true, false, true, true, false, dialog), "unauthenticated operation cannot acknowledge");
            Check(!WarningPolicy.AcknowledgeDocumentWarning(true, true, false, true, false, dialog), "idle process cannot acknowledge");
            Check(!WarningPolicy.AcknowledgeDocumentWarning(true, true, true, false, false, dialog), "export or foreign phase cannot acknowledge");
            Check(!WarningPolicy.AcknowledgeDocumentWarning(true, true, true, true, true, dialog), "captured errors prevent dialog acknowledgement");
            foreach (string unknown in new[] { "Dialog_Revit_Trust", "Dialog_Revit_License", "Dialog_Revit_Repair", "TaskDialog_Warning", dialog.ToLowerInvariant(), "" })
                Check(!WarningPolicy.AcknowledgeDocumentWarning(true, true, true, true, false, unknown), "unknown or nonexact dialog never accepted");
            Check(WarningPolicy.DeleteWarning("Warning"), "warning can be deleted from failure queue");
            foreach (string severity in new[] { "Error", "DocumentCorruption", "None", "warning", "unknown" })
                Check(!WarningPolicy.DeleteWarning(severity), "nonwarning severity cannot be deleted");
            Check(WarningPolicy.IsError("Error") && WarningPolicy.IsError("DocumentCorruption") && !WarningPolicy.IsError("Warning"), "error severities classified");
            Check(WarningPolicy.RequestRollback(true, true, true), "committing owned error transaction can request rollback");
            Check(!WarningPolicy.RequestRollback(false, true, true), "inactive accessor cannot rollback");
            Check(!WarningPolicy.RequestRollback(true, false, true), "already rolling back cannot request new rollback");
            Check(!WarningPolicy.RequestRollback(true, true, false), "warnings do not request rollback");
            var wake = new RequestWakeGate();
            Check(!wake.Pending, "quiet dispatcher has no work");
            wake.Signal(); long oldVersion = wake.Version; wake.Signal();
            Check(wake.Pending && wake.Version > oldVersion, "duplicate notifications retain newest generation");
            Check(wake.Complete(oldVersion, "Accepted") && wake.Pending, "accepting older generation cannot lose newer request");
            Check(!wake.Complete(wake.Version, "Pending") && !wake.Pending, "pending external event coalesces newest request");
            wake.Signal();
            Check(wake.Complete(wake.Version, "TimedOut") && wake.Pending, "synchronization timeout retries queued request");
            Check(!wake.Complete(wake.Version, "Accepted") && !wake.Pending, "successful retry clears wake without polling");
            wake.Signal();
            for (int attempt = 0; attempt < 7; attempt++) Check(wake.Complete(wake.Version, "Denied"), "bounded retry remains available");
            Check(!wake.Complete(wake.Version, "Denied") && wake.Pending, "retry exhaustion preserves durable request without busy loop");
            wake.Signal(); Check(wake.Complete(wake.Version, "TimedOut"), "new file notification resets retry budget");
            wake.Stop(); wake.Signal();
            Check(!wake.Pending && !wake.Complete(wake.Version, "Accepted"), "disposed dispatcher cannot schedule new work");
            var readRetry = new RequestReadRetryGate();
            Check(readRetry.TryRetry(), "first transient read error can retry without another file notification");
            readRetry.Reset();
            for (int attempt = 0; attempt < 8; attempt++) Check(readRetry.TryRetry(), "bounded request-read retry available");
            Check(!readRetry.TryRetry() && !readRetry.TryRetry(), "persistent read errors stop retrying");
            readRetry.Reset(); Check(readRetry.TryRetry(), "valid read or new filesystem notification resets read retry budget");
            string watchRoot = Path.Combine(root, "wake-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(watchRoot);
            string watchFile = Path.Combine(watchRoot, "request.json");
            try {
                using var first = new ManualResetEventSlim();
                using var second = new ManualResetEventSlim();
                using var retriedRead = new ManualResetEventSlim();
                int retryReads = 0;
                RequestWake? capturedWatcher = null;
                using var actualWatcher = new RequestWake(watchRoot, () => {
                    using var payload = Protocol.Read(watchFile);
                    if (Protocol.String(payload.RootElement, "value") == "first") first.Set();
                    if (Protocol.String(payload.RootElement, "value") == "second") second.Set();
                    if (Protocol.String(payload.RootElement, "value") == "retry") {
                        try {
                            if (Interlocked.Increment(ref retryReads) == 1) throw new IOException("Simulated transient sharing violation after accepted wake");
                            capturedWatcher!.ReadSucceeded();
                            retriedRead.Set();
                        } catch (IOException) { capturedWatcher!.ScheduleReadRetry(); }
                    }
                    return "Accepted";
                }, _ => { });
                capturedWatcher = actualWatcher;
                Protocol.Atomic(watchFile, new { value = "first" });
                Check(first.Wait(3000), "atomic creation triggers asynchronous wake without UI activity");
                Protocol.Atomic(watchFile, new { value = "second" });
                Check(second.Wait(3000), "atomic replacement triggers another wake without Idling");
                Protocol.Atomic(watchFile, new { value = "retry" });
                Check(retriedRead.Wait(3000) && retryReads >= 2, "accepted wake with one IOException retries to success without another file write");
            } finally {
                if (File.Exists(watchFile)) File.Delete(watchFile);
                Directory.Delete(watchRoot);
            }
            string textureRoot = Path.Combine(root, "textures-" + Guid.NewGuid().ToString("N"));
            string native = Path.Combine(textureRoot, "native"), x86 = Path.Combine(textureRoot, "x86"), modelFolder = Path.Combine(textureRoot, "source");
            string concrete = "Concrete.Cast-In-Place.Flat.Grey.1.jpg", gravel = "Sitework.Planting.Gravel.Mixed.png";
            string concrete3 = Path.Combine(native, "3", "Mats", concrete), concrete1 = Path.Combine(native, "1", "Mats", concrete), gravel1 = Path.Combine(native, "1", "Mats", gravel);
            try {
                Directory.CreateDirectory(Path.GetDirectoryName(concrete3)!);
                Directory.CreateDirectory(Path.GetDirectoryName(concrete1)!);
                Directory.CreateDirectory(modelFolder);
                File.WriteAllBytes(concrete3, new byte[] { 3, 3 }); File.WriteAllBytes(concrete1, new byte[] { 1, 1 }); File.WriteAllBytes(gravel1, new byte[] { 1, 2 });
                string oldConcrete = Path.Combine(textureRoot, "missing", "Autodesk Shared", "Materials", "Textures", "3", "Mats", concrete);
                string oldGravel = Path.Combine(textureRoot, "missing", "Autodesk Shared", "Materials", "Textures", "1", "Mats", gravel);
                Check(TextureLookup.Exact(oldConcrete, modelFolder, new[] { native, x86 }, out bool ambiguous) == concrete3 && !ambiguous, "relocate x86 Concrete by exact version 3 suffix");
                Check(TextureLookup.Exact(oldGravel.Replace('\\', '/'), modelFolder, new[] { native, x86 }, out ambiguous) == gravel1 && !ambiguous, "relocate Gravel with mixed slash style");
                Check(TextureLookup.Exact(oldConcrete.Replace("Autodesk Shared", "AUTODESK SHARED"), modelFolder, new[] { native, x86 }, out ambiguous) == concrete3, "library marker is case insensitive");
                Check(TextureLookup.Exact(Path.Combine("3", "Mats", concrete), modelFolder, new[] { native, x86 }, out ambiguous) == concrete3, "relative library suffix preserves resolution version");
                Check(TextureLookup.Exact(concrete1, modelFolder, new[] { native, x86 }, out ambiguous) == concrete1, "existing exact source takes precedence");
                string missingVersion = oldConcrete.Replace(Path.DirectorySeparatorChar + "3" + Path.DirectorySeparatorChar, Path.DirectorySeparatorChar + "2" + Path.DirectorySeparatorChar);
                Check(TextureLookup.Exact(missingVersion, modelFolder, new[] { native, x86 }, out ambiguous) == null && !ambiguous, "missing version is never silently changed to another quality");
                Check(TextureLookup.UniqueBasename(concrete, new[] { concrete1, concrete3 }, out ambiguous) == null && ambiguous, "duplicate resolution basenames remain ambiguous");
                Check(TextureLookup.UniqueBasename(gravel, new[] { concrete1, concrete3, gravel1 }, out ambiguous) == gravel1 && !ambiguous, "unique basename fallback remains available");
                string mirror = Path.Combine(x86, "3", "Mats", concrete); Directory.CreateDirectory(Path.GetDirectoryName(mirror)!);
                File.WriteAllBytes(mirror, new byte[] { 3, 3 });
                Check(TextureLookup.Exact(oldConcrete, modelFolder, new[] { native, x86 }, out ambiguous) == concrete3 && !ambiguous, "identical exact library mirrors are interchangeable");
                File.WriteAllBytes(mirror, new byte[] { 9, 9 });
                Check(TextureLookup.Exact(oldConcrete, modelFolder, new[] { native, x86 }, out ambiguous) == null && ambiguous, "conflicting exact library copies are not guessed");
            } finally {
                // Delete only this uniquely named fixture below the caller-approved output directory.
                if (!Path.GetFullPath(textureRoot).StartsWith(Compat.TrimSeparator(root) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)) throw new IOException("Unsafe fixture cleanup path");
                foreach (string fixtureFile in Directory.GetFiles(textureRoot, "*", SearchOption.AllDirectories)) File.Delete(fixtureFile);
                foreach (string fixtureDir in Directory.GetDirectories(textureRoot, "*", SearchOption.AllDirectories).OrderByDescending(p => p.Length)) Directory.Delete(fixtureDir);
                Directory.Delete(textureRoot);
            }
            Console.WriteLine("PASS: " + assertions + " protocol assertions"); return 0;
        } finally { if (File.Exists(file)) File.Delete(file); }
    }
}
