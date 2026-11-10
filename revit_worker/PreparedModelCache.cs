using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.Json;

namespace XIVgate.RevitBridge;

// Immutable, content-verified local acceleration. No Autodesk dependency.
public static class PreparedModelCache
{
    const string Package = "xivgate_revit_bridge";
    const string RootMarker = ".agr_revit_bridge_prepared_root.json";
    const string ReadyMarker = ".agr_revit_bridge_prepared.json";
    const int MaximumDescriptorBytes = 8 * 1024 * 1024;
    public sealed class Entry
    {
        public string SourceSha256 { get; internal set; } = "";
        public string CreatedUtc { get; internal set; } = "";
        public string ModelPath { get; internal set; } = "";
        public string SourceFormat { get; internal set; } = "";
        public string[] Warnings { get; internal set; } = Array.Empty<string>();
        public JsonElement[] WarningRecords { get; internal set; } = Array.Empty<JsonElement>();
        public string ModelSha256 { get; internal set; } = "";
        public long ModelBytes { get; internal set; }
        public string Directory { get; internal set; } = "";
    }
    public static Entry? TryGet(string jobRoot, string sourceSha, string revitYear, string revitBuild, bool ignoreSourceChanges = false, string sourceIdentity = "")
    {
        ValidateKey(sourceSha, revitYear, revitBuild);
        string root = CacheRoot(jobRoot);
        if (!System.IO.Directory.Exists(root)) {
            if (File.Exists(root)) throw new IOException("Prepared cache path is a file.");
            return null;
        }
        ValidateRoot(root);
        foreach (string directory in System.IO.Directory.GetDirectories(root).OrderByDescending(System.IO.Directory.GetLastWriteTimeUtc)) {
            try {
                string id = Path.GetFileName(directory);
                if (!Guid.TryParseExact(id, "N", out _) || id != id.ToLowerInvariant()) continue;
                Protocol.Below(root, directory);
                string ready = Protocol.Below(root, Path.Combine(directory, ReadyMarker));
                if (!File.Exists(ready)) continue; // A crashed publisher never exposes its partial copy.
                using var document = ReadBounded(ready);
                var value = document.RootElement;
                if (!Identity(value, "prepared_model") || !Integer(value, "policy", 1) ||
                    Protocol.String(value, "entry_id") != id || !IsHash(Protocol.String(value, "source_sha256")) ||
                    (!ignoreSourceChanges && Protocol.String(value, "source_sha256") != sourceSha) ||
                    Protocol.String(value, "revit_version") != revitYear || Protocol.String(value, "revit_build") != revitBuild ||
                    Protocol.String(value, "model_file") != "model.rvt") continue;
                string hash = Protocol.String(value, "model_sha256"), format = Protocol.String(value, "source_format");
                if (!IsHash(hash) || (ignoreSourceChanges && Protocol.String(value, "source_sha256") != sourceSha && hash != sourceSha
                    && (string.IsNullOrEmpty(sourceIdentity) || Protocol.String(value, "source_identity") != sourceIdentity)) || string.IsNullOrWhiteSpace(format) ||
                    !value.TryGetProperty("model_bytes", out var bytes) || !bytes.TryGetInt64(out long length) || length <= 0 ||
                    !DateTimeOffset.TryParseExact(Protocol.String(value, "created_utc"), "O", CultureInfo.InvariantCulture, DateTimeStyles.None, out _) ||
                    !value.TryGetProperty("warnings", out var warnings) || warnings.ValueKind != JsonValueKind.Array || warnings.EnumerateArray().Any(x => x.ValueKind != JsonValueKind.String) ||
                    !value.TryGetProperty("warning_records", out var records) || records.ValueKind != JsonValueKind.Array || records.EnumerateArray().Any(x => x.ValueKind != JsonValueKind.Object)) continue;
                var contents = System.IO.Directory.GetFileSystemEntries(directory);
                if (contents.Length != 2 || contents.Any(x => Path.GetFileName(x) != "model.rvt" && Path.GetFileName(x) != ReadyMarker)) continue;
                string model = Protocol.Below(root, Path.Combine(directory, "model.rvt"));
                using (var input = new FileStream(model, FileMode.Open, FileAccess.Read, FileShare.Read))
                    if (input.Length != length || Compat.Hex(Compat.Sha256(input)).ToLowerInvariant() != hash) continue;
                return new Entry { SourceSha256 = Protocol.String(value, "source_sha256"), CreatedUtc = Protocol.String(value, "created_utc"), Directory = directory, ModelPath = model, SourceFormat = format, ModelSha256 = hash, ModelBytes = length,
                    Warnings = warnings.EnumerateArray().Select(x => x.GetString()!).ToArray(), WarningRecords = records.EnumerateArray().Select(x => x.Clone()).ToArray() };
            } catch (Exception ex) when (ex is IOException || ex is JsonException || ex is InvalidDataException || ex is UnauthorizedAccessException || ex is InvalidOperationException) {
                // Never repair or delete an untrusted/corrupt entry. Another immutable entry may be usable.
            }
        }
        return null;
    }
    public static Entry Publish(string jobRoot, string preparedPath, string sourceSha, string sourceFormat, string revitYear, string revitBuild,
        IEnumerable<string> warnings, IEnumerable<object> warningRecords, bool replaceExisting = false, string sourceIdentity = "")
    {
        ValidateKey(sourceSha, revitYear, revitBuild);
        string root = CacheRoot(jobRoot);
        string prepared = Protocol.Below(Path.Combine(Path.GetFullPath(jobRoot), "input"), preparedPath);
        if (!string.Equals(Path.GetExtension(prepared), ".rvt", StringComparison.OrdinalIgnoreCase) || string.IsNullOrWhiteSpace(sourceFormat))
            throw new InvalidDataException("Only an owned prepared RVT copy can be cached.");
        // Validate even on a hit: callers cannot publish a foreign input by supplying a known hash.
        using (var input = new FileStream(prepared, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
            if (input.Length <= 0) throw new InvalidDataException("Empty prepared model.");
        var warningArray = warnings.ToArray();
        var recordArray = warningRecords.ToArray();
        if (warningArray.Any(x => x == null)) throw new InvalidDataException("Invalid warning text.");
        using (var records = JsonDocument.Parse(JsonSerializer.Serialize(recordArray)))
            if (records.RootElement.EnumerateArray().Any(x => x.ValueKind != JsonValueKind.Object)) throw new InvalidDataException("Invalid warning record.");
        if (!System.IO.Directory.Exists(root)) System.IO.Directory.CreateDirectory(root);
        NoLinks(root);
        if (!File.Exists(Path.Combine(root, RootMarker))) {
            if (System.IO.Directory.EnumerateFileSystemEntries(root).Any()) throw new IOException("Refusing unmarked prepared cache directory.");
            Protocol.Atomic(Protocol.Below(root, Path.Combine(root, RootMarker)), new { schema = 1, package_id = Package, kind = "prepared_model_cache" });
        }
        ValidateRoot(root);
        var existing = TryGet(jobRoot, sourceSha, revitYear, revitBuild, replaceExisting, sourceIdentity);
        if (existing != null && !replaceExisting) return existing;
        string id = Guid.NewGuid().ToString("N"), directory = Protocol.Below(root, Path.Combine(root, id));
        System.IO.Directory.CreateDirectory(directory);
        string model = Protocol.Below(root, Path.Combine(directory, "model.rvt"));
        string ready = Protocol.Below(root, Path.Combine(directory, ReadyMarker));
        try {
            // SaveAs has completed on the Revit API thread, but Revit retains a
            // write-capable handle. Permit that handle and verify both snapshots.
            long size; string hash;
            using (var input = new FileStream(prepared, FileMode.Open, FileAccess.Read, FileShare.ReadWrite)) {
                size = input.Length; hash = Compat.Hex(Compat.Sha256(input)).ToLowerInvariant();
                input.Position = 0;
                using (var output = new FileStream(model, FileMode.CreateNew, FileAccess.Write, FileShare.None)) { input.CopyTo(output); output.Flush(true); }
                input.Position = 0;
                if (input.Length != size || Compat.Hex(Compat.Sha256(input)).ToLowerInvariant() != hash)
                    throw new IOException("Prepared RVT changed during cache publication.");
            }
            using (var input = new FileStream(model, FileMode.Open, FileAccess.Read, FileShare.Read))
                if (input.Length != size || Compat.Hex(Compat.Sha256(input)).ToLowerInvariant() != hash)
                    throw new IOException("Prepared RVT cache copy failed integrity verification.");
            if (size <= 0) throw new InvalidDataException("Empty copied model.");
            var descriptor = new { schema = 1, package_id = Package, kind = "prepared_model", entry_id = id, policy = 1, source_sha256 = sourceSha, source_identity = sourceIdentity,
                revit_version = revitYear, revit_build = revitBuild, source_format = sourceFormat, model_file = "model.rvt", model_sha256 = hash,
                model_bytes = size, created_utc = DateTimeOffset.UtcNow.ToString("O", CultureInfo.InvariantCulture), warnings = warningArray, warning_records = recordArray };
            if (System.Text.Encoding.UTF8.GetByteCount(JsonSerializer.Serialize(descriptor, Protocol.Json)) > MaximumDescriptorBytes) throw new InvalidDataException("Prepared descriptor exceeds limit.");
            using var clonedRecords = JsonDocument.Parse(JsonSerializer.Serialize(recordArray));
            Protocol.Atomic(ready, descriptor);
            if (existing != null) {
                // Replace only the previously validated model for this build.
                // A reader's Windows lock leaves the old entry for explicit cleanup.
                try {
                    NoLinks(existing.Directory);
                    File.Delete(existing.ModelPath);
                    File.Delete(Protocol.Below(root, Path.Combine(existing.Directory, ReadyMarker)));
                    System.IO.Directory.Delete(existing.Directory, false);
                } catch (IOException) { } catch (UnauthorizedAccessException) { }
            }
            return new Entry { SourceSha256 = sourceSha, CreatedUtc = descriptor.created_utc, Directory = directory, ModelPath = model, SourceFormat = sourceFormat, ModelSha256 = hash, ModelBytes = size,
                Warnings = warningArray, WarningRecords = clonedRecords.RootElement.EnumerateArray().Select(x => x.Clone()).ToArray() };
        } catch {
            // Only files allocated by this invocation, never an existing entry.
            // On crash the absent ready marker still prevents reuse.
            try {
                NoLinks(directory);
                if (File.Exists(ready)) File.Delete(ready);
                if (File.Exists(model)) File.Delete(model);
                System.IO.Directory.Delete(directory, false);
            } catch (IOException) { } catch (UnauthorizedAccessException) { }
            throw;
        }
    }

    static string CacheRoot(string jobRoot)
    {
        if (!Compat.FullyQualified(jobRoot)) throw new IOException("Absolute job path required.");
        string job = Compat.TrimSeparator(Path.GetFullPath(jobRoot)), session = Path.GetFileName(job);
        if (!Guid.TryParseExact(session, "D", out _)) throw new IOException("Invalid job identity.");
        NoLinks(job);
        using var marker = ReadBounded(Protocol.Below(job, Path.Combine(job, ".agr_revit_bridge_job.json")));
        if (!Integer(marker.RootElement, "schema", 1) || Protocol.String(marker.RootElement, "package_id") != Package || Protocol.String(marker.RootElement, "session_id") != session)
            throw new IOException("Invalid job marker.");
        string parent = Path.GetDirectoryName(job)!;
        return Protocol.Below(parent, Path.Combine(parent, "prepared_models"));
    }
    static void ValidateRoot(string root)
    {
        NoLinks(root);
        using var document = ReadBounded(Protocol.Below(root, Path.Combine(root, RootMarker)));
        if (!Identity(document.RootElement, "prepared_model_cache") || document.RootElement.EnumerateObject().Count() != 3)
            throw new InvalidDataException("Invalid prepared cache root marker.");
    }
    static void NoLinks(string path)
    {
        for (string? current = path; current != null; current = Path.GetDirectoryName(current))
            if ((File.Exists(current) || System.IO.Directory.Exists(current)) && (File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                throw new IOException("Filesystem links are forbidden in prepared cache paths.");
    }
    static JsonDocument ReadBounded(string path)
    {
        using var input = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read | FileShare.Delete);
        if (input.Length <= 0 || input.Length > MaximumDescriptorBytes) throw new InvalidDataException("Invalid prepared descriptor size.");
        var bytes = new byte[(int)input.Length]; Compat.ReadExactly(input, bytes, 0, bytes.Length);
        return JsonDocument.Parse(bytes);
    }
    static bool Identity(JsonElement value, string kind) => Integer(value, "schema", 1) && Protocol.String(value, "package_id") == Package && Protocol.String(value, "kind") == kind;
    static bool Integer(JsonElement value, string name, int expected) => value.ValueKind == JsonValueKind.Object && value.TryGetProperty(name, out var property) && property.ValueKind == JsonValueKind.Number && property.TryGetInt32(out int number) && number == expected;
    static bool IsHash(string value) => value.Length == 64 && value.All(c => (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'));
    static void ValidateKey(string sourceSha, string year, string build)
    {
        if (!IsHash(sourceSha) || year.Length != 4 || !year.All(c => c >= '0' && c <= '9') || string.IsNullOrWhiteSpace(build)) throw new InvalidDataException("Invalid prepared cache key.");
    }
}
