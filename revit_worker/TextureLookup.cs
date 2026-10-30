using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Security.Cryptography;

namespace XIVgate.RevitBridge;

// Revit material paths often retain the x86 library root after migration to native installations.
// Preserve the entire version/resolution suffix rather than guessing from a duplicate basename.
public static class TextureLookup
{
    const string Marker = "Autodesk Shared\\Materials\\Textures\\";
    public static string? Exact(string source, string sourceDirectory, IEnumerable<string> libraryRoots, out bool ambiguous)
    {
        ambiguous = false;
        source = source.Replace('/', Path.DirectorySeparatorChar).Replace('\\', Path.DirectorySeparatorChar);
        if (string.IsNullOrWhiteSpace(source)) return null;
        string direct = Compat.FullyQualified(source) ? source : Path.Combine(sourceDirectory, source);
        if (File.Exists(direct)) return Path.GetFullPath(direct);
        string normalized = source.Replace('/', '\\');
        int marker = normalized.IndexOf(Marker, StringComparison.OrdinalIgnoreCase);
        string? suffix = marker >= 0 ? normalized.Substring(marker + Marker.Length) : !Compat.FullyQualified(source) ? source : null;
        if (string.IsNullOrWhiteSpace(suffix)) return null;
        suffix = suffix!.Replace('\\', Path.DirectorySeparatorChar).Replace('/', Path.DirectorySeparatorChar);
        var found = new List<string>();
        foreach (string root in libraryRoots) {
            string prefix = Compat.TrimSeparator(Path.GetFullPath(root)) + Path.DirectorySeparatorChar;
            string candidate = Path.GetFullPath(Path.Combine(root, suffix));
            if (candidate.StartsWith(prefix, StringComparison.OrdinalIgnoreCase) && File.Exists(candidate)) found.Add(candidate);
        }
        var distinct = found.Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
        if (distinct.Length == 1) return distinct[0];
        if (distinct.Length > 1) {
            // Identical library mirrors are interchangeable; differing files at the exact same suffix are not.
            var signatures = distinct.Select(path => { using var file = File.OpenRead(path); return Compat.Hex(Compat.Sha256(file)); }).Distinct(StringComparer.Ordinal).ToArray();
            if (signatures.Length == 1) return distinct[0];
            ambiguous = true;
        }
        return null;
    }
    public static string? UniqueBasename(string source, IEnumerable<string> files, out bool ambiguous)
    {
        var matches = files.Where(p => string.Equals(Path.GetFileName(p), Path.GetFileName(source), StringComparison.OrdinalIgnoreCase))
            .Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
        ambiguous = matches.Length > 1;
        return matches.Length == 1 ? matches[0] : null;
    }
}
