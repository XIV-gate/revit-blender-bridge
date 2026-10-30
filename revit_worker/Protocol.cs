using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Collections.Generic;

namespace XIVgate.RevitBridge;

// No Revit dependency: file authentication and path safety are independently testable.
public static class Protocol
{
    public static readonly JsonSerializerOptions Json = new() { WriteIndented = true };
    public static JsonDocument Read(string path)
    {
        // Permit atomic replacement while this reader still holds the previous file handle.
        using var input = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read | FileShare.Delete);
        using var reader = new StreamReader(input, Encoding.UTF8, true);
        return JsonDocument.Parse(reader.ReadToEnd());
    }
    public static string String(JsonElement value, string name, string fallback = "") =>
        value.ValueKind == JsonValueKind.Object && value.TryGetProperty(name, out var p) && p.ValueKind == JsonValueKind.String ? p.GetString() ?? fallback : fallback;
    public static void Atomic(string path, object value)
    {
        string pending = path + "." + Guid.NewGuid().ToString("N") + ".pending";
        try { File.WriteAllText(pending, JsonSerializer.Serialize(value, Json), new UTF8Encoding(false)); Compat.ReplaceFile(pending, path); }
        finally { if (File.Exists(pending)) File.Delete(pending); }
    }
    public static string Below(string root, string path) => Compat.Below(root, path);
    public static bool Authenticated(JsonElement r, string token, string? session)
    {
        return r.ValueKind == JsonValueKind.Object && r.TryGetProperty("schema", out var schema) && schema.ValueKind == JsonValueKind.Number && schema.TryGetInt32(out int v) && v == 1 &&
            String(r, "token") == token && Guid.TryParse(String(r, "session_id"), out _) &&
            Guid.TryParse(String(r, "request_id"), out _) && (session == null || session == String(r, "session_id"));
    }
    public static HashSet<long> Selection(JsonElement request)
    {
        if (request.ValueKind != JsonValueKind.Object || !request.TryGetProperty("selection", out var selection) || selection.ValueKind != JsonValueKind.Object || !selection.TryGetProperty("include_ids", out var ids) || ids.ValueKind != JsonValueKind.Array)
            throw new ArgumentException("Не задан полный список selection.include_ids.");
        var result = new HashSet<long>();
        foreach (var id in ids.EnumerateArray()) {
            if (id.ValueKind != JsonValueKind.String || !long.TryParse(id.GetString(), out long value))
                throw new ArgumentException("ID элемента должен быть строкой с 64-битным целым числом.");
            result.Add(value);
        }
        if (result.Count == 0) throw new ArgumentException("Не выбрано ни одного элемента для экспорта.");
        return result;
    }
}
