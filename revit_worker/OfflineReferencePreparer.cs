using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Security.Cryptography;
using System.Text;
using System.Xml;
using System.Xml.Linq;

namespace XIVgate.RevitBridge;

// Revit's native TransmissionData reader resolves saved RSN references before
// returning. This bounded, version-checked adapter removes ONLY those references
// from an authenticated disposable copy before invoking that reader. Other CFB
// streams are hashed before/after; any mismatch aborts opening the copy.
public static class OfflineReferencePreparer
{
    const int MaximumBytes = 8 * 1024 * 1024;
    const string StreamName = "TransmissionData";
    public sealed class Preparation
    {
        public byte[] Bytes = Array.Empty<byte>();
        public long[] Ids = Array.Empty<long>();
        public string Error = "";
        internal Dictionary<string, string> Streams = new(StringComparer.Ordinal);
        public int VerifiedStreams => Streams.Count(kv => kv.Value != "storage");
        public int VerifiedEntries => Streams.Count;
    }
    public static Preparation Plan(byte[] raw, string localDirectory)
    {
        try {
            if (raw.Length < 4 || raw.Length > MaximumBytes) throw new InvalidDataException("Unsupported TransmissionData size.");
            int chars = BitConverter.ToInt32(raw, 0);
            if (chars <= 0 || 4L + chars * 2L > raw.Length) throw new InvalidDataException("Invalid TransmissionData length.");
            int end = checked(4 + chars * 2);
            var encoding = new UnicodeEncoding(false, false, true);
            string suffix = encoding.GetString(raw, end, raw.Length - end);
            if (suffix.Length > 16 || suffix.Any(c => !char.IsWhiteSpace(c))) throw new InvalidDataException("Unknown TransmissionData suffix.");
            var settings = new XmlReaderSettings { DtdProcessing = DtdProcessing.Prohibit, XmlResolver = null, MaxCharactersInDocument = MaximumBytes / 2 };
            using var text = new StringReader(encoding.GetString(raw, 4, chars * 2));
            using var reader = XmlReader.Create(text, settings);
            var xml = XDocument.Load(reader, LoadOptions.PreserveWhitespace);
            var root = xml.Root;
            if (root == null || root.Name != "TransmissionData") throw new InvalidDataException("Unknown TransmissionData root.");
            if ((string?)root.Attribute("version") != "5") throw new InvalidDataException("Unsupported TransmissionData version; copy left unopened.");
            var refs = root.Elements("ExternalFileReference").Where(x => x.Element("ExternalFileReferenceType")?.Value == "Revit Link" &&
                x.Elements().Any(f => f.Value.TrimStart().StartsWith("RSN:", StringComparison.OrdinalIgnoreCase) ||
                ((f.Name.LocalName == "LastSavedPathType" || f.Name.LocalName == "DesiredPathType") && f.Value == "Server Location") ||
                (f.Name.LocalName.EndsWith("CentralServerLocation", StringComparison.Ordinal) && !string.IsNullOrWhiteSpace(f.Value)))).ToArray();
            if (refs.Length == 0) return new Preparation { Bytes = raw };
            if (!Compat.FullyQualified(localDirectory)) throw new IOException("Absolute local reference directory required.");
            var ids = new HashSet<long>();
            foreach (var item in refs) {
                if (!long.TryParse(item.Element("ElementId")?.Value, out long id) || id <= 0 || !ids.Add(id)) throw new InvalidDataException("Invalid/duplicate server reference ElementId.");
                foreach (string field in new[] { "LastSavedPath", "LastSavedAbsolutePath", "LastSavedPathType", "LastSavedLoadState", "DesiredPath", "DesiredPathType", "DesiredLoadState" }) {
                    if (item.Elements(field).Count() != 1) throw new InvalidDataException("Missing/duplicate server reference field: " + field);
                }
                string placeholder = Protocol.Below(localDirectory, Path.Combine(localDirectory, "UnloadedLink_" + id + ".rvt"));
                if (File.Exists(placeholder) || Directory.Exists(placeholder)) throw new IOException("Offline reference placeholder already exists.");
                item.SetElementValue("LastSavedPath", placeholder);
                item.SetElementValue("LastSavedAbsolutePath", placeholder);
                item.SetElementValue("DesiredPath", placeholder);
                item.SetElementValue("LastSavedPathType", "Absolute"); item.SetElementValue("DesiredPathType", "Absolute");
                item.SetElementValue("LastSavedLoadState", "Unloaded"); item.SetElementValue("DesiredLoadState", "Unloaded");
                item.Elements("LastSavedCentralServerLocation").Remove(); item.Elements("DesiredCentralServerLocation").Remove();
            }
            root.SetAttributeValue("isTransmitted", "true");
            string result = xml.ToString(SaveOptions.DisableFormatting);
            if (xml.Declaration != null) result = xml.Declaration + result;
            // Keep the original stream length when possible. Larger valid XML
            // is resized through the structured-storage API, never raw offsets.
            if (result.Length <= chars) result = result.PadRight(chars);
            var payload = encoding.GetBytes(result);
            if (payload.Length + 4 + suffix.Length * 2 > MaximumBytes) throw new InvalidDataException("Prepared metadata exceeds limit.");
            return new Preparation { Bytes = BitConverter.GetBytes(result.Length).Concat(payload).Concat(encoding.GetBytes(suffix)).ToArray(), Ids = ids.OrderBy(x => x).ToArray() };
        } catch (Exception ex) when (ex is IOException || ex is InvalidDataException || ex is XmlException || ex is ArgumentException || ex is OverflowException) {
            return new Preparation { Error = ex.Message };
        }
    }
    public static Preparation Prepare(string jobRoot, string session, string source, string copy)
    {
        ValidateOwnedCopy(jobRoot, session, source, copy);
        byte[]? raw = ReadMetadata(copy);
        if (raw == null) return new Preparation();
        var plan = Plan(raw, Path.GetDirectoryName(copy)!);
        if (plan.Error.Length > 0) throw new InvalidDataException("Не удалось безопасно подготовить серверные ссылки рабочей копии: " + plan.Error);
        if (plan.Ids.Length == 0) return plan;
        plan.Streams = Snapshot(copy);
        WriteMetadata(copy, plan.Bytes);
        if (!ReadMetadata(copy)!.SequenceEqual(plan.Bytes)) throw new IOException("Не совпадают записанные метаданные рабочей копии.");
        Verify(copy, plan);
        return plan;
    }
    public static void ValidateOwnedCopy(string jobRoot, string session, string source, string copy)
    {
        string fullRoot = Path.GetFullPath(jobRoot);
        if (!Guid.TryParse(session, out _) || new DirectoryInfo(fullRoot).Name != session) throw new IOException("Invalid owned job identity.");
        using var marker = Protocol.Read(Protocol.Below(fullRoot, Path.Combine(fullRoot, ".agr_revit_bridge_job.json")));
        var value = marker.RootElement;
        if (Protocol.String(value, "package_id") != "xivgate_revit_bridge" || Protocol.String(value, "session_id") != session || value.GetProperty("schema").GetInt32() != 1) throw new IOException("Invalid job marker.");
        Protocol.Below(Path.Combine(fullRoot, "input"), copy);
        if (!string.Equals(Path.GetExtension(copy), ".rvt", StringComparison.OrdinalIgnoreCase) ||
            string.Equals(Path.GetFullPath(source), Path.GetFullPath(copy), StringComparison.OrdinalIgnoreCase)) throw new IOException("Only a separate owned RVT copy may be prepared.");
    }
    public static void Verify(string copy, Preparation plan)
    {
        var after = Snapshot(copy);
        if (after.Count != plan.Streams.Count || plan.Streams.Any(kv => !after.TryGetValue(kv.Key, out string? hash) || hash != kv.Value))
            throw new IOException("Проверка целостности RVT не пройдена: изменены потоки вне TransmissionData. Рабочая копия не будет открыта.");
    }
    static byte[]? ReadMetadata(string path)
    {
        var storage = Open(path, false); IStream? stream = null;
        try {
            try { storage.OpenStream(StreamName, IntPtr.Zero, 0x10, 0, out stream); }
            catch (COMException ex) when (ex.ErrorCode == unchecked((int)0x80030002)) { return null; }
            stream.Stat(out var stat, 1);
            if (stat.cbSize < 4 || stat.cbSize > MaximumBytes) throw new InvalidDataException("Unsupported metadata size.");
            var raw = new byte[(int)stat.cbSize]; IntPtr read = Marshal.AllocHGlobal(4);
            try { stream.Read(raw, raw.Length, read); if (Marshal.ReadInt32(read) != raw.Length) throw new EndOfStreamException(); }
            finally { Marshal.FreeHGlobal(read); }
            return raw;
        } finally { if (stream != null) Marshal.ReleaseComObject(stream); Marshal.ReleaseComObject(storage); }
    }
    static void WriteMetadata(string path, byte[] raw)
    {
        var storage = Open(path, true); IStream? stream = null;
        try {
            storage.OpenStream(StreamName, IntPtr.Zero, 0x12, 0, out stream);
            stream.SetSize(raw.Length);
            IntPtr written = Marshal.AllocHGlobal(4);
            try { stream.Write(raw, raw.Length, written); if (Marshal.ReadInt32(written) != raw.Length) throw new IOException("Incomplete metadata write."); }
            finally { Marshal.FreeHGlobal(written); }
            stream.Commit(0); storage.Commit(0);
        } finally { if (stream != null) Marshal.ReleaseComObject(stream); Marshal.ReleaseComObject(storage); }
    }
    static Dictionary<string, string> Snapshot(string path)
    {
        var result = new Dictionary<string, string>(StringComparer.Ordinal);
        var storage = Open(path, false);
        try { Walk(storage, "", result, 0); }
        finally { Marshal.ReleaseComObject(storage); }
        return result;
    }
    static void Walk(IStorage storage, string prefix, Dictionary<string, string> result, int depth)
    {
        if (depth > 32 || result.Count > 100000) throw new InvalidDataException("CFB structure exceeds limits.");
        storage.EnumElements(0, IntPtr.Zero, 0, out var entries);
        try {
            while (true) {
                int status = entries.Next(1, out var stat, out uint read);
                if (!InventoryEntry(status, read)) break;
                if (result.Count > 100000) throw new InvalidDataException("CFB inventory exceeds limit.");
                if (stat.pwcsName.IndexOf('/') >= 0) throw new InvalidDataException("Invalid CFB entry name.");
                string name = prefix + stat.pwcsName;
                if (stat.type == 1) {
                    storage.OpenStorage(stat.pwcsName, IntPtr.Zero, 0x10, IntPtr.Zero, 0, out var child);
                    try { result.Add(name + "/", "storage"); Walk(child, name + "/", result, depth + 1); }
                    finally { Marshal.ReleaseComObject(child); }
                } else if (stat.type == 2 && name != StreamName) {
                    storage.OpenStream(stat.pwcsName, IntPtr.Zero, 0x10, 0, out var stream);
                    try { result.Add(name, Hash(stream, stat.cbSize)); }
                    finally { Marshal.ReleaseComObject(stream); }
                } else if (stat.type != 2) throw new InvalidDataException("Unknown CFB entry type.");
            }
        } finally { Marshal.ReleaseComObject(entries); }
    }
    internal static bool InventoryEntry(int status, uint fetched)
    {
        Marshal.ThrowExceptionForHR(status);
        if (status == 1 && fetched == 0) return false;
        if (status != 0 || fetched != 1) throw new InvalidDataException("Incomplete CFB inventory.");
        return true;
    }
    static string Hash(IStream stream, long size)
    {
        if (size < 0) throw new InvalidDataException("Invalid CFB stream size.");
        using var hash = SHA256.Create(); var buffer = new byte[1024 * 1024];
        IntPtr read = Marshal.AllocHGlobal(4);
        try {
            long total = 0;
            while (total < size) {
                stream.Read(buffer, (int)Math.Min(buffer.Length, size - total), read);
                int count = Marshal.ReadInt32(read);
                if (count <= 0 || count > buffer.Length) throw new EndOfStreamException();
                hash.TransformBlock(buffer, 0, count, buffer, 0); total += count;
            }
            hash.TransformFinalBlock(Array.Empty<byte>(), 0, 0);
            return Compat.Hex(hash.Hash!);
        } finally { Marshal.FreeHGlobal(read); }
    }
    static IStorage Open(string path, bool write)
    {
        Marshal.ThrowExceptionForHR(StgOpenStorage(path, IntPtr.Zero, write ? 0x12u : 0x20u, IntPtr.Zero, 0, out var storage));
        return storage;
    }
    [DllImport("ole32.dll", CharSet = CharSet.Unicode)]
    static extern int StgOpenStorage(string path, IntPtr priority, uint mode, IntPtr exclusions, uint reserved, out IStorage storage);
    [ComImport, Guid("0000000b-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IStorage
    {
        void CreateStream([MarshalAs(UnmanagedType.LPWStr)] string name, uint mode, uint r1, uint r2, out IStream stream);
        void OpenStream([MarshalAs(UnmanagedType.LPWStr)] string name, IntPtr r1, uint mode, uint r2, out IStream stream);
        void CreateStorage([MarshalAs(UnmanagedType.LPWStr)] string name, uint mode, uint r1, uint r2, out IStorage storage);
        void OpenStorage([MarshalAs(UnmanagedType.LPWStr)] string name, IntPtr priority, uint mode, IntPtr exclusions, uint reserved, out IStorage storage);
        void CopyTo(uint count, IntPtr interfaces, IntPtr exclusions, IStorage destination);
        void MoveElementTo([MarshalAs(UnmanagedType.LPWStr)] string name, IStorage destination, [MarshalAs(UnmanagedType.LPWStr)] string newName, uint flags);
        void Commit(uint flags);
        void Revert();
        void EnumElements(uint r1, IntPtr r2, uint r3, out IEnumStorage entries);
    }
    [ComImport, Guid("0000000d-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IEnumStorage { [PreserveSig] int Next(uint count, out System.Runtime.InteropServices.ComTypes.STATSTG value, out uint fetched); }
}
