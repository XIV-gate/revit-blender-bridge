using System;
using System.IO;
using XIVgate.RevitBridge;
static class SourceReplacementTests {
    public static int Run(string root) {
        int checks=0;
        void Check(bool ok) { checks++; if(!ok) throw new Exception("Source replacement failed"); }
        void Reject(Action action) { bool failed=false; try { action(); } catch(IOException) { failed=true; } Check(failed); }
        string fixture=Path.Combine(root,"replace-"+Guid.NewGuid().ToString("N"));
        string session=Guid.NewGuid().ToString(), job=Path.Combine(fixture,session);
        Directory.CreateDirectory(Path.Combine(job,"input"));
        string source=Path.Combine(fixture,"source.rvt"), prepared=Path.Combine(job,"input","prepared.rvt");
        try {
            Protocol.Atomic(Path.Combine(job,".agr_revit_bridge_job.json"),new {schema=1,package_id="xivgate_revit_bridge",session_id=session});
            File.WriteAllText(source,"old"); File.WriteAllText(prepared,"new");
            string oldHash=Compat.Hex(Compat.Sha256(System.Text.Encoding.UTF8.GetBytes("old"))).ToLowerInvariant();
            Reject(()=>SourceReplacement.Replace(job,source,prepared,new string('a',64)));
            Check(File.ReadAllText(source)=="old");
            using(var held=new FileStream(source,FileMode.Open,FileAccess.Read,FileShare.Read)) Reject(()=>SourceReplacement.Replace(job,source,prepared,oldHash));
            Check(File.ReadAllText(source)=="old");
            string hash=SourceReplacement.Replace(job,source,prepared,oldHash).Sha256;
            Check(File.ReadAllText(source)=="new" && File.ReadAllText(prepared)=="new");
            Check(hash==Compat.Hex(Compat.Sha256(System.Text.Encoding.UTF8.GetBytes("new"))).ToLowerInvariant());
            Check(Directory.GetFiles(fixture,".agr-*").Length==0);
            File.WriteAllText(source,"concurrent user update");
            string pending=Path.Combine(fixture,".agr-pending.rvt"); File.Copy(prepared,pending);
            Reject(()=>SourceReplacement.Commit(pending,source,oldHash,hash));
            Check(File.ReadAllText(source)=="concurrent user update");
            Check(Directory.GetFiles(fixture,".agr-*").Length==0);
            File.WriteAllText(Path.Combine(job,".agr_revit_bridge_job.json"),"{}");
            Reject(()=>SourceReplacement.Replace(job,source,prepared,hash));
        } finally { Directory.Delete(fixture,true); }
        return checks;
    }
}
