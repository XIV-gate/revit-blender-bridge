using System.Reflection;
#if NETFRAMEWORK
[assembly: AssemblyVersion("24.0.0.0")]
#else
[assembly: AssemblyVersion("27.0.0.0")]
#endif
// Test-only API double. Never used by the production build or distributed add-on.
namespace Autodesk.Revit.UI
{
    public enum Result { Succeeded, Failed }
    public interface IExternalApplication { Result OnStartup(UIControlledApplication app); Result OnShutdown(UIControlledApplication app); }
    public sealed class UIControlledApplication
    {
        public int HostReads, WorkerStarts, WorkerStops;
        public Controlled ControlledApplication { get { HostReads++; return new Controlled(); } }
    }
    public sealed class Controlled
    {
#if NETFRAMEWORK
        public string VersionNumber => "2024";
#else
        public string VersionNumber => "2027";
#endif
    }
}
