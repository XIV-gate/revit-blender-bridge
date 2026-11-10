using System;
using System.IO;
namespace XIVgate.RevitBridge;

// Only called for an authenticated scan with explicit overwrite_source=true.
public static class SourceReplacement {
    public sealed class Result {
        public string Sha256 { get; internal set; } = "";
        public string RetainedBackup { get; internal set; } = "";
    }
    public static Result Replace(string jobRoot, string sourcePath, string preparedPath, string expectedSourceSha) {
        string job=Path.GetFullPath(jobRoot), source=Path.GetFullPath(sourcePath);
        if (!Compat.FullyQualified(sourcePath) || !Guid.TryParseExact(Path.GetFileName(job),"D",out _) ||
            !string.Equals(Path.GetExtension(source),".rvt",StringComparison.OrdinalIgnoreCase)) throw new IOException("Invalid source replacement paths.");
        using(var marker=Protocol.Read(Protocol.Below(job,Path.Combine(job,".agr_revit_bridge_job.json"))))
            if(Protocol.String(marker.RootElement,"package_id")!="xivgate_revit_bridge" || Protocol.String(marker.RootElement,"session_id")!=Path.GetFileName(job)) throw new IOException("Invalid source replacement job.");
        string prepared=Protocol.Below(Path.Combine(job,"input"),preparedPath);
        if(source.StartsWith(job+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase) || source==prepared) throw new IOException("Source must be outside its disposable job.");
        NoLinks(source); NoLinks(prepared);
        string pending=Path.Combine(Path.GetDirectoryName(source)!,".agr-"+Guid.NewGuid().ToString("N")+".rvt");
        try {
            string hash; long length;
            using(var input=new FileStream(prepared,FileMode.Open,FileAccess.Read,FileShare.ReadWrite)) {
                length=input.Length; hash=Compat.Hex(Compat.Sha256(input)).ToLowerInvariant(); input.Position=0;
                using(var output=new FileStream(pending,FileMode.CreateNew,FileAccess.Write,FileShare.None)) {input.CopyTo(output); output.Flush(true);}
                input.Position=0;
                if(length<=0 || input.Length!=length || Compat.Hex(Compat.Sha256(input)).ToLowerInvariant()!=hash) throw new IOException("Prepared model changed during source replacement.");
            }
            using(var verify=File.OpenRead(pending))
                if(verify.Length!=length || Compat.Hex(Compat.Sha256(verify)).ToLowerInvariant()!=hash) throw new IOException("Replacement copy failed integrity verification.");
            // Hold the old source against in-place writers through the atomic replace.
            using(var current=new FileStream(source,FileMode.Open,FileAccess.Read,FileShare.Read|FileShare.Delete)) {
                if(Compat.Hex(Compat.Sha256(current)).ToLowerInvariant()!=expectedSourceSha) throw new IOException("Исходный RVT изменился во время обработки; перезапись отменена.");
                NoLinks(source);
                return Commit(pending,source,expectedSourceSha,hash);
            }
        } finally { if(File.Exists(pending)) File.Delete(pending); }
    }
    // Inspect the file actually displaced by ReplaceFile, not just an earlier
    // pathname read: another atomic saver may have swapped that pathname.
    internal static Result Commit(string pending, string source, string expectedSourceSha, string preparedSha) {
        string backup=Path.Combine(Path.GetDirectoryName(source)!,".agr-"+Guid.NewGuid().ToString("N")+".previous.rvt");
        File.Replace(pending,source,backup);
        try {
            if (Hash(backup)!=expectedSourceSha) {
                string rescue=Path.Combine(Path.GetDirectoryName(source)!,".agr-"+Guid.NewGuid().ToString("N")+".conflict.rvt");
                if (Hash(source)==preparedSha) {
                    File.Replace(backup,source,rescue);
                    if (Hash(rescue)==preparedSha) {
                        File.Delete(rescue);
                        throw new IOException("Конкурентное сохранение RVT: замена отменена, вытеснённый оригинал восстановлен.");
                    }
                    throw new IOException("Конкурентное сохранение RVT: вытеснённый оригинал восстановлен; дополнительное изменение сохранено в " + rescue);
                }
                throw new IOException("Конкурентное сохранение RVT: исходник изменён другим процессом. Вытеснённая версия сохранена в " + backup);
            }
            try { File.Delete(backup); return new Result { Sha256=preparedSha }; }
            catch(IOException) { return new Result { Sha256=preparedSha, RetainedBackup=backup }; }
            catch(UnauthorizedAccessException) { return new Result { Sha256=preparedSha, RetainedBackup=backup }; }
        } catch(Exception ex) when(ex is IOException || ex is UnauthorizedAccessException) {
            if(File.Exists(backup)) throw new IOException("Замена RVT требует проверки: вытеснённый файл сохранён в " + backup + ". " + ex.Message,ex);
            throw;
        }
    }
    static string Hash(string file) { using(var input=new FileStream(file,FileMode.Open,FileAccess.Read,FileShare.Read|FileShare.Delete)) return Compat.Hex(Compat.Sha256(input)).ToLowerInvariant(); }
    static void NoLinks(string path) {
        for(string? current=path;current!=null;current=Path.GetDirectoryName(current))
            if((File.Exists(current)||Directory.Exists(current)) && (File.GetAttributes(current)&FileAttributes.ReparsePoint)!=0) throw new IOException("Links are forbidden during source replacement.");
    }
}
