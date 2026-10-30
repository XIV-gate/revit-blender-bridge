using System;
using System.IO;
using System.Diagnostics;
using System.Security.Cryptography;

namespace XIVgate.RevitBridge;

// Framework-library adapters; model/export semantics stay shared across Revit versions.
public static class Compat
{
    public static string Below(string root, string path)
    {
        if (!FullyQualified(path)) throw new IOException("Требуется абсолютный путь: " + path);
        string full = Path.GetFullPath(path);
        string prefix = TrimSeparator(Path.GetFullPath(root)) + Path.DirectorySeparatorChar;
        if (!full.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)) throw new IOException("Путь выходит за рабочий каталог: " + path);
        for (string? p = full; p != null && p.Length >= prefix.Length - 1; p = Path.GetDirectoryName(p))
            if ((File.Exists(p) || Directory.Exists(p)) && (File.GetAttributes(p) & FileAttributes.ReparsePoint) != 0)
                throw new IOException("Ссылки файловой системы в рабочем пути запрещены: " + p);
        return full;
    }
    public static bool FullyQualified(string path)
    {
#if NETFRAMEWORK
        if (string.IsNullOrEmpty(path)) return false;
        return (path.Length >= 3 && char.IsLetter(path[0]) && path[1] == ':' && (path[2] == '\\' || path[2] == '/')) ||
            (path.Length > 2 && path[0] == '\\' && path[1] == '\\');
#else
        return Path.IsPathFullyQualified(path);
#endif
    }
    public static string TrimSeparator(string path)
    {
#if NETFRAMEWORK
        string root = Path.GetPathRoot(path);
        return path.Length <= root.Length ? path : path.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
#else
        return Path.TrimEndingDirectorySeparator(path);
#endif
    }
    public static void ReplaceFile(string source, string destination)
    {
#if NETFRAMEWORK
        if (File.Exists(destination)) File.Replace(source, destination, null);
        else File.Move(source, destination);
#else
        File.Move(source, destination, true);
#endif
    }
    public static byte[] Sha256(byte[] bytes) { using var hash = SHA256.Create(); return hash.ComputeHash(bytes); }
    public static byte[] Sha256(Stream stream) { using var hash = SHA256.Create(); return hash.ComputeHash(stream); }
    public static string Hex(byte[] bytes) => BitConverter.ToString(bytes).Replace("-", "");
    public static bool Finite(double value) => !double.IsNaN(value) && !double.IsInfinity(value);
    public static int ProcessId { get { using var process = Process.GetCurrentProcess(); return process.Id; } }
    public static void ReadExactly(Stream stream, byte[] buffer, int offset, int count)
    {
        while (count > 0) {
            int read = stream.Read(buffer, offset, count);
            if (read == 0) throw new EndOfStreamException("Обрезан временный буфер геометрии.");
            offset += read; count -= read;
        }
    }
}
