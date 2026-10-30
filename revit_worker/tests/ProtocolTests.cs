using System;
using System.IO;
using System.Text;
using System.Text.Json;
using XIVgate.RevitBridge;

static class ProtocolTests
{
    static int assertions;
    static void Check(bool condition, string label) { assertions++; if (!condition) throw new Exception(label); }
    static void Throws(Action action, string label) { bool failed = false; try { action(); } catch (ArgumentException) { failed = true; } catch (IOException) { failed = true; } Check(failed, label); }
    public static int Main(string[] args)
    {
        string root = Path.GetFullPath(args[0]);
        string file = Path.Combine(root, "protocol-" + Guid.NewGuid().ToString("N") + ".json");
        try {
            string session = Guid.NewGuid().ToString(), request = Guid.NewGuid().ToString();
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
            Console.WriteLine("PASS: " + assertions + " protocol assertions"); return 0;
        } finally { if (File.Exists(file)) File.Delete(file); }
    }
}
