using System;
using System.IO;
using System.Linq;
using System.Reflection;

namespace XIVgate.RevitBridge;

// Never resolves foreign plugins' dependencies or Autodesk assemblies. This scope must be active
// before entering/JITing any implementation method that uses System.Text.Json on Framework 4.8.
public sealed class FrameworkDependencies : IDisposable
{
#if NETFRAMEWORK
    readonly Assembly own;
    readonly string folder;
    readonly Action<string>? log;
    readonly System.Collections.Generic.Dictionary<string, Assembly> loaded = new(StringComparer.Ordinal);
    int warmupThread;
    readonly string[] names = { "System.Text.Json", "System.Text.Encodings.Web", "Microsoft.Bcl.AsyncInterfaces", "System.Memory", "System.Buffers", "System.Runtime.CompilerServices.Unsafe", "System.Threading.Tasks.Extensions", "System.Numerics.Vectors" };
    FrameworkDependencies(Assembly assembly, Action<string>? log)
    {
        own = assembly; folder = Path.GetDirectoryName(own.Location)!;
        this.log = log;
        foreach (string name in names) {
            string file = Path.Combine(folder, name + ".dll");
            if (File.Exists(file)) loaded[name] = Assembly.LoadFile(file);
        }
        AppDomain.CurrentDomain.AssemblyResolve += Resolve;
        // The Framework JIT sometimes reports RequestingAssembly=null for System.Memory's Unsafe
        // binding. Prime that binding in a tightly scoped, synchronous JSON-only bootstrap; no
        // Revit API or third-party callback runs inside this exception to caller verification.
        try {
            warmupThread = System.Threading.Thread.CurrentThread.ManagedThreadId;
            WarmJson();
        } catch { AppDomain.CurrentDomain.AssemblyResolve -= Resolve; throw; }
        finally { warmupThread = 0; }
    }
    [System.Runtime.CompilerServices.MethodImpl(System.Runtime.CompilerServices.MethodImplOptions.NoInlining)]
    static void WarmJson()
    {
        string json = System.Text.Json.JsonSerializer.Serialize(new System.Collections.Generic.Dictionary<long, object> { [1] = new { text = "JSON", number = 1.25, values = new[] { 1, 2 } } });
        using var parsed = System.Text.Json.JsonDocument.Parse(json);
        _ = parsed.RootElement.GetProperty("1").GetProperty("text").GetString();
    }
    Assembly? Resolve(object? sender, ResolveEventArgs args)
    {
        var requested = new AssemblyName(args.Name);
        var caller = args.RequestingAssembly;
        log?.Invoke("Resolve " + requested.Name + " for " + (caller?.FullName ?? "null"));
        if (!names.Contains(requested.Name, StringComparer.Ordinal)) return null;
        if (caller == null) { if (warmupThread == 0 || warmupThread != System.Threading.Thread.CurrentThread.ManagedThreadId) return null; }
        else if (caller != own && (!names.Contains(caller.GetName().Name, StringComparer.Ordinal) || !string.Equals(Path.GetDirectoryName(caller.Location), folder, StringComparison.OrdinalIgnoreCase))) return null;
        if (!loaded.TryGetValue(requested.Name!, out var candidate)) return null;
        // Avoid Span/Memory extension dispatch while resolving Unsafe itself (C# 14 may prefer it).
        var actualToken = candidate.GetName().GetPublicKeyToken();
        var wantedToken = requested.GetPublicKeyToken();
        if (actualToken.Length != wantedToken.Length) return null;
        for (int i = 0; i < actualToken.Length; i++) if (actualToken[i] != wantedToken[i]) return null;
        return candidate;
    }
#else
    FrameworkDependencies(Assembly assembly, Action<string>? log) { }
#endif
    public static FrameworkDependencies Install(Assembly assembly, Action<string>? log = null) => new(assembly, log);
    public void Dispose()
    {
#if NETFRAMEWORK
        AppDomain.CurrentDomain.AssemblyResolve -= Resolve;
#endif
    }
}
