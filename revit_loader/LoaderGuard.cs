using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;

namespace XIVgate.RevitBridge;

// Host-independent and Framework 4.8 compatible. No JSON/dependency resolver or registry writes.
public static class LoaderGuard
{
    public const string WorkerFile = "XIVgate.RevitBridge.Worker.dll";
    public const string LoaderFile = "XIVgate.RevitBridge.Loader.dll";
    public const string EntryClass = "XIVgate.RevitBridge.WorkerApp";
    public const string Marker = ".agr_revit_bridge_job.json";
    const string FinishedMarker = ".agr_revit_bridge_finished.json";

    public static string AbsoluteWithoutLinks(string path)
    {
        if (string.IsNullOrWhiteSpace(path)) throw new IOException("Не указан абсолютный путь.");
#if NETFRAMEWORK
        bool absolute = (path.Length >= 3 && char.IsLetter(path[0]) && path[1] == ':' && (path[2] == '\\' || path[2] == '/')) ||
            (path.Length > 2 && path[0] == '\\' && path[1] == '\\');
#else
        bool absolute = Path.IsPathFullyQualified(path);
#endif
        if (!absolute || path.StartsWith(@"\\?\", StringComparison.Ordinal) || path.StartsWith(@"\\.\", StringComparison.Ordinal))
            throw new IOException("Требуется обычный абсолютный путь.");
        string full = Path.GetFullPath(path);
        for (string? item = full; item != null; item = Path.GetDirectoryName(item)) {
            if ((File.Exists(item) || Directory.Exists(item)) && (File.GetAttributes(item) & FileAttributes.ReparsePoint) != 0)
                throw new IOException("Ссылки и junction в пути загрузчика запрещены.");
        }
        return full;
    }

    public static string Below(string root, string path)
    {
        string fullRoot = AbsoluteWithoutLinks(root).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        string full = AbsoluteWithoutLinks(path);
        if (!full.StartsWith(fullRoot + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
            throw new IOException("Путь выходит за пределы разрешённого каталога.");
        return full;
    }

    public static string ValidateJob(string root, string sessionId, string token)
    {
        if (!Guid.TryParseExact(sessionId, "D", out var session) || session.ToString("D") != sessionId)
            throw new IOException("Сеанс загрузчика должен содержать канонический UUID.");
        if (string.IsNullOrWhiteSpace(token) || token.Length > 1024 || token.IndexOfAny(new[] { '\0', '\r', '\n' }) >= 0)
            throw new IOException("Не указан корректный токен сеанса.");
        string full = AbsoluteWithoutLinks(root).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        if (!Directory.Exists(full) || Path.GetFileName(full) != sessionId)
            throw new IOException("UUID рабочего каталога не совпадает с сеансом.");
        if (File.Exists(Below(full, Path.Combine(full, FinishedMarker))))
            throw new IOException("Этот сеанс уже завершён.");
        string marker = Below(full, Path.Combine(full, Marker));
        byte[] buffer = new byte[4097];
        int count = 0;
        using (var stream = new FileStream(marker, FileMode.Open, FileAccess.Read, FileShare.Read)) {
            while (count < buffer.Length) { int read = stream.Read(buffer, count, buffer.Length - count); if (read == 0) break; count += read; }
        }
        if (count > 4096) throw new IOException("Маркер задания слишком велик.");
        string text = new UTF8Encoding(false, true).GetString(buffer, 0, count).TrimStart('\ufeff');
        var fields = ReadFlatMarker(text);
        if (fields.Count != 3 || !fields.TryGetValue("schema", out var schema) || schema != "n:1" ||
            !fields.TryGetValue("package_id", out var package) || package != "s:xivgate_revit_bridge" ||
            !fields.TryGetValue("session_id", out var owner) || owner != "s:" + sessionId)
            throw new IOException("Рабочий каталог не принадлежит этому заданию AGR.");
        return full;
    }

    // Deliberately restricted grammar for our own ASCII, three-field identity marker.
    // This is not a general JSON parser: escaped strings, duplicate keys and other types fail closed.
    static Dictionary<string, string> ReadFlatMarker(string text)
    {
        int index = 0;
        Action whitespace = () => { while (index < text.Length && char.IsWhiteSpace(text[index])) index++; };
        Action<char> expect = value => { whitespace(); if (index >= text.Length || text[index++] != value) throw new IOException("Неверный маркер задания."); };
        Func<string> readString = () => {
            expect('"'); int start = index;
            while (index < text.Length && text[index] != '"') {
                if (text[index] < 32 || text[index] > 126 || text[index] == '\\') throw new IOException("Неизвестная строка маркера задания.");
                index++;
            }
            if (index >= text.Length) throw new IOException("Обрезан маркер задания.");
            string value = text.Substring(start, index - start); index++; return value;
        };
        var result = new Dictionary<string, string>(StringComparer.Ordinal);
        expect('{'); whitespace();
        if (index < text.Length && text[index] == '}') { index++; }
        else {
            while (true) {
                string key = readString(); expect(':'); whitespace();
                string value;
                if (index < text.Length && text[index] == '"') value = "s:" + readString();
                else { int start = index; while (index < text.Length && text[index] >= '0' && text[index] <= '9') index++;
                    if (start == index) throw new IOException("Неизвестный тип поля маркера."); value = "n:" + text.Substring(start, index - start); }
                if (result.ContainsKey(key)) throw new IOException("Повторяющееся поле маркера.");
                result.Add(key, value); whitespace();
                if (index < text.Length && text[index] == '}') { index++; break; }
                expect(',');
            }
        }
        whitespace(); if (index != text.Length) throw new IOException("Данные после маркера задания.");
        return result;
    }

    public static void ValidateVersion(string version)
    {
        if (version == null || version.Length > 32 || !Regex.IsMatch(version, @"\A(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\z", RegexOptions.CultureInvariant))
            throw new IOException("Версия worker должна иметь вид X.Y.Z.");
        foreach (string part in version.Split('.'))
            if (!int.TryParse(part, NumberStyles.None, CultureInfo.InvariantCulture, out _)) throw new IOException("Компонент версии worker слишком велик.");
    }

    public static void ValidateHost(string key, string hostYear, int runtimeMajor)
    {
        string expected;
        if (hostYear == "2024" && runtimeMajor == 4) expected = "2024";
        else if ((hostYear == "2025" || hostYear == "2026") && (runtimeMajor == 8 || runtimeMajor == 10))
            expected = hostYear + (runtimeMajor == 10 ? "-net10" : "");
        else if (hostYear == "2027" && runtimeMajor == 10) expected = "2027";
        else throw new IOException("Эта версия Revit или среды выполнения не поддерживается загрузчиком.");
        if (key != expected) throw new IOException("Вариант загрузчика не соответствует Revit и среде выполнения.");
    }

    public static string Sha256(string path)
    {
        using var stream = new FileStream(AbsoluteWithoutLinks(path), FileMode.Open, FileAccess.Read, FileShare.Read);
        using var hash = SHA256.Create();
        return BitConverter.ToString(hash.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
    }

    public static FileStream OpenPinnedWorker(string path, string expectedSha256)
    {
        var stream = new FileStream(AbsoluteWithoutLinks(path), FileMode.Open, FileAccess.Read, FileShare.Read);
        try {
            using var hash = SHA256.Create();
            string actual = BitConverter.ToString(hash.ComputeHash(stream)).Replace("-", "");
            if (!string.Equals(actual, expectedSha256, StringComparison.OrdinalIgnoreCase))
                throw new IOException("SHA256 worker изменился перед загрузкой.");
            stream.Position = 0;
            return stream; // Caller keeps this non-write/non-delete shared handle through LoadFrom/creation.
        } catch { stream.Dispose(); throw; }
    }

    public static string ValidateWorker(string loaderLocation, string key, string version, string expectedSha256, string hostYear, int runtimeMajor)
    {
        ValidateVersion(version); ValidateHost(key, hostYear, runtimeMajor);
        if (expectedSha256 == null || !Regex.IsMatch(expectedSha256, @"\A[0-9a-fA-F]{64}\z", RegexOptions.CultureInvariant))
            throw new IOException("Не указана контрольная сумма worker.");
        string loader = AbsoluteWithoutLinks(loaderLocation);
        var generation = new DirectoryInfo(Path.GetDirectoryName(loader)!);
        var loaderFolder = generation.Parent;
        var keyFolder = loaderFolder?.Parent;
        var revitFolder = keyFolder?.Parent;
        var runtimeFolder = revitFolder?.Parent;
        if (!File.Exists(loader) || !string.Equals(Path.GetFileName(loader), LoaderFile, StringComparison.OrdinalIgnoreCase)
            || generation.Name != "1" || loaderFolder?.Name != "loader" || keyFolder?.Name != key
            || keyFolder == null || revitFolder?.Name != "revit" || runtimeFolder?.Name != "runtime")
            throw new IOException("Загрузчик находится вне штатной структуры дополнения.");
        string worker = Below(keyFolder.FullName, Path.Combine(keyFolder.FullName, version, WorkerFile));
        if (!File.Exists(worker) || !string.Equals(Sha256(worker), expectedSha256, StringComparison.OrdinalIgnoreCase))
            throw new IOException("Файл worker отсутствует или его SHA256 не совпадает.");
        return worker;
    }
}
