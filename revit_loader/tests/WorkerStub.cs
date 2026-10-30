using Autodesk.Revit.UI;
namespace XIVgate.RevitBridge
{
    public sealed class WorkerApp : IExternalApplication
    {
        public Result OnStartup(UIControlledApplication app) { app.WorkerStarts++; return System.Environment.GetEnvironmentVariable("AGR_TEST_WORKER_RESULT") == "Failed" ? Result.Failed : Result.Succeeded; }
        public Result OnShutdown(UIControlledApplication app) { app.WorkerStops++; return Result.Succeeded; }
    }
}
