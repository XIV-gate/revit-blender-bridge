using System;
using System.IO;
using System.Threading;

namespace XIVgate.RevitBridge;

// Pure state machine. ExternalEvent's own Pending response coalesces duplicate file notifications.
public sealed class RequestWakeGate
{
    long version, delivered;
    int attempts;
    bool stopped;
    public long Version => version;
    public bool Pending => !stopped && version > delivered;
    public void Signal() { if (!stopped) { version++; attempts = 0; } }
    public bool Complete(long raisedVersion, string result)
    {
        if (stopped) return false;
        if (result == "Accepted" || result == "Pending") { delivered = Math.Max(delivered, raisedVersion); attempts = 0; return Pending; }
        return Pending && ++attempts < 8;
    }
    public void Stop() { stopped = true; }
}

public sealed class RequestReadRetryGate
{
    int attempts;
    public bool TryRetry() => ++attempts <= 8;
    public void Reset() { attempts = 0; }
}

// The only Autodesk operation injected here is ExternalEvent.Raise, designed for cross-thread signaling.
// No Document, UIApplication, transaction or geometry API is called from this watcher/timer.
internal sealed class RequestWake : IDisposable
{
    readonly object sync = new();
    readonly RequestWakeGate gate = new();
    readonly RequestReadRetryGate readRetry = new();
    readonly FileSystemWatcher watcher;
    readonly Timer timer;
    readonly Func<string> raise;
    readonly Action<string> log;
    bool disposed;
    public RequestWake(string root, Func<string> raise, Action<string> log)
    {
        this.raise = raise; this.log = log;
        timer = new Timer(OnTimer, null, Timeout.Infinite, Timeout.Infinite);
        FileSystemWatcher? created = null;
        try {
            watcher = created = new FileSystemWatcher(root, "request.json") {
                IncludeSubdirectories = false,
                NotifyFilter = NotifyFilters.FileName | NotifyFilters.LastWrite | NotifyFilters.CreationTime | NotifyFilters.Size
            };
            watcher.Changed += OnChanged;
            watcher.Created += OnChanged;
            watcher.Renamed += OnRenamed;
            watcher.Error += OnError;
            watcher.EnableRaisingEvents = true;
        } catch { created?.Dispose(); timer.Dispose(); throw; }
    }
    void OnChanged(object sender, FileSystemEventArgs e) => NotifyFileChange();
    void OnRenamed(object sender, RenamedEventArgs e) => NotifyFileChange();
    void OnError(object sender, ErrorEventArgs e) { log("Request watcher overflow/error; scheduling main-thread reread: " + e.GetException().Message); NotifyFileChange(); }
    void NotifyFileChange()
    {
        lock (sync) {
            if (disposed) return;
            readRetry.Reset();
            Signal();
        }
    }
    public void ReadSucceeded() { lock (sync) { readRetry.Reset(); } }
    public void ScheduleReadRetry()
    {
        lock (sync) {
            if (disposed) return;
            if (!readRetry.TryRetry()) { log("Transient request read retries exhausted; request retained until another file notification or normal Idling."); return; }
            gate.Signal();
            timer.Change(250, Timeout.Infinite);
        }
    }
    public void Signal()
    {
        lock (sync) {
            if (disposed) return;
            gate.Signal();
            timer.Change(25, Timeout.Infinite);
        }
    }
    void OnTimer(object? state)
    {
        lock (sync) {
            if (disposed || !gate.Pending) return;
            long version = gate.Version;
            string result;
            try { result = raise(); }
            catch (Exception ex) { result = "Exception"; log("ExternalEvent.Raise failed: " + ex.Message); }
            if (result == "Accepted") log("ExternalEvent request wake accepted.");
            if (gate.Complete(version, result)) timer.Change(250, Timeout.Infinite);
            else if (gate.Pending) log("ExternalEvent wake retries exhausted (" + result + "); request retained, normal Idling remains a fallback.");
        }
    }
    public void Dispose()
    {
        lock (sync) {
            if (disposed) return;
            disposed = true; gate.Stop();
            watcher.EnableRaisingEvents = false;
            watcher.Dispose();
            timer.Dispose();
        }
    }
}
