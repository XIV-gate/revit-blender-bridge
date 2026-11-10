using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using XIVgate.RevitBridge;

static class OfflineReferenceTests
{
    public static int Run(string root)
    {
        int count = 0;
        void Check(bool value) { count++; if (!value) throw new Exception("Offline reference regression " + count); }
        Check(OfflineReferencePreparer.InventoryEntry(0, 1));
        Check(!OfflineReferencePreparer.InventoryEntry(1, 0));
        foreach (var entry in new[] { (unchecked((int)0x8003001E), 0u), (0, 0u), (1, 1u), (2, 0u) }) {
            bool rejected = false;
            try { OfflineReferencePreparer.InventoryEntry(entry.Item1, entry.Item2); } catch (COMException) { rejected = true; } catch (InvalidDataException) { rejected = true; }
            Check(rejected);
        }
        byte[] Raw(string xml) { var bytes = Encoding.Unicode.GetBytes(xml); return BitConverter.GetBytes(xml.Length).Concat(bytes).Concat(new byte[] { 10, 0 }).ToArray(); }
        string xml = "<TransmissionData version=\"5\" isTransmitted=\"false\"><ExternalFileReference><ElementId>42</ElementId><ExternalFileReferenceType>Revit Link</ExternalFileReferenceType><LastSavedPath>RSN://server/model.rvt</LastSavedPath><LastSavedAbsolutePath>RSN://server/model.rvt</LastSavedAbsolutePath><LastSavedPathType>Server Location</LastSavedPathType><LastSavedCentralServerLocation>server</LastSavedCentralServerLocation><LastSavedLoadState>Loaded</LastSavedLoadState><DesiredPath>RSN://server/model.rvt</DesiredPath><DesiredPathType>Server Location</DesiredPathType><DesiredLoadState>Loaded</DesiredLoadState></ExternalFileReference><ExternalFileReference><ElementId>43</ElementId><ExternalFileReferenceType>Keynote Table</ExternalFileReferenceType><LastSavedPath>keynotes.txt</LastSavedPath></ExternalFileReference></TransmissionData>";
        var plan = OfflineReferencePreparer.Plan(Raw(xml), root);
        Check(plan.Ids.SequenceEqual(new long[] { 42 }));
        string result = Encoding.Unicode.GetString(plan.Bytes, 4, BitConverter.ToInt32(plan.Bytes, 0) * 2);
        Check(!result.Contains("RSN:") && !result.Contains("CentralServerLocation"));
        Check(result.Contains("keynotes.txt") && result.Contains("Unloaded") && result.Contains("Absolute"));
        Check(OfflineReferencePreparer.Plan(plan.Bytes, root).Ids.Length == 0);
        Check(OfflineReferencePreparer.Plan(Raw(xml.Replace("version=\"5\"", "version=\"99\"")), root).Error.Length > 0);
        Check(OfflineReferencePreparer.Plan(new byte[] { 4, 0, 0, 0 }, root).Error.Length > 0);
        Check(OfflineReferencePreparer.Plan(Raw("<!DOCTYPE x [<!ENTITY x SYSTEM 'file:///invalid'>]><TransmissionData version=\"5\">&x;</TransmissionData>"), root).Error.Length > 0);
        Check(OfflineReferencePreparer.Plan(Raw(xml.Replace("<ElementId>42</ElementId>", "<ElementId>../bad</ElementId>")), root).Error.Length > 0);
        Check(OfflineReferencePreparer.Plan(Raw(xml.Replace("Revit Link", "CAD Link")), root).Ids.Length == 0);
        Check(OfflineReferencePreparer.Plan(Raw("<TransmissionData version=\"99\"/>"), root).Error.Length > 0);
        var declaration = OfflineReferencePreparer.Plan(Raw("<?xml version=\"1.0\"?>\r\n" + xml), root);
        Check(Encoding.Unicode.GetString(declaration.Bytes, 4, BitConverter.ToInt32(declaration.Bytes, 0) * 2).StartsWith("<?xml version=\"1.0\"?>\r\n"));
        string session = Guid.NewGuid().ToString();
        string job = Path.Combine(root, session), input = Path.Combine(job, "input");
        string source = Path.Combine(root, "fixture-" + session + ".rvt"), copy = Path.Combine(input, "copy.rvt");
        Directory.CreateDirectory(input);
        try {
            Protocol.Atomic(Path.Combine(job, ".agr_revit_bridge_job.json"), new { schema = 1, session_id = session, package_id = "xivgate_revit_bridge" });
            foreach (int padding in new[] { 0, 1700, 6000 }) {
                string padded = xml.Replace("</TransmissionData>", "<UserData>" + new string('x', padding) + "</UserData></TransmissionData>");
                Create(source, Raw("<?xml version=\"1.0\"?>\r\n" + padded));
                byte[] sourceHash; using (var file = File.OpenRead(source)) sourceHash = Compat.Sha256(file);
                File.Copy(source, copy, true);
                var prepared = OfflineReferencePreparer.Prepare(job, session, source, copy);
                Check(prepared.Ids.Length == 1 && prepared.VerifiedStreams == 1);
                OfflineReferencePreparer.Verify(copy, prepared); Check(true);
                using (var file = File.OpenRead(source)) Check(Compat.Sha256(file).SequenceEqual(sourceHash));
                Check(OfflineReferencePreparer.Prepare(job, session, source, copy).Ids.Length == 0);
                Create(copy, prepared.Bytes, true);
                bool corrupted = false;
                try { OfflineReferencePreparer.Verify(copy, prepared); } catch (IOException) { corrupted = true; }
                Check(corrupted);
            }
            bool blocked = false;
            try { OfflineReferencePreparer.ValidateOwnedCopy(job, session, copy, copy); } catch (IOException) { blocked = true; }
            Check(blocked);
            blocked = false;
            try { OfflineReferencePreparer.ValidateOwnedCopy(job, session, source, source); } catch (IOException) { blocked = true; }
            Check(blocked);
            Protocol.Atomic(Path.Combine(job, ".agr_revit_bridge_job.json"), new { schema = 1, session_id = session, package_id = "other" });
            blocked = false;
            try { OfflineReferencePreparer.Prepare(job, session, source, copy); } catch (IOException) { blocked = true; }
            Check(blocked);
        } finally { File.Delete(source); Directory.Delete(job, true); }
        return count;
    }
    static void Create(string path, byte[] metadata, bool corrupt = false)
    {
        File.Delete(path);
        Marshal.ThrowExceptionForHR(StgCreateDocfile(path, 0x1012, 0, out var storage));
        try {
            foreach (var pair in new[] { ("TransmissionData", metadata), ("GeometrySentinel", Enumerable.Range(0, 10000).Select(i => (byte)(i + (corrupt ? 1 : 0))).ToArray()) }) {
                storage.CreateStream(pair.Item1, 0x1012, 0, 0, out var stream);
                try { stream.Write(pair.Item2, pair.Item2.Length, IntPtr.Zero); stream.Commit(0); }
                finally { Marshal.ReleaseComObject(stream); }
            }
            storage.Commit(0);
        } finally { Marshal.ReleaseComObject(storage); }
    }
    [DllImport("ole32.dll", CharSet = CharSet.Unicode)]
    static extern int StgCreateDocfile(string path, uint mode, uint reserved, out IStorage storage);
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
    }
}
