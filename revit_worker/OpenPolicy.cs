using System;

namespace XIVgate.RevitBridge;

public static class OpenPolicy
{
    // Reading TransmissionData of a server-origin local snapshot can contact
    // its saved central server before OpenOptions has a chance to detach it.
    public static bool NormalizeServerSnapshot(bool workshared, string centralPath) =>
        workshared && centralPath.TrimStart().StartsWith("RSN:", StringComparison.OrdinalIgnoreCase);

    // Server references can survive SaveAs and can also occur in disk-central
    // projects. Close worksets and unload via the document API before inspecting.
    public static bool PrepareLocalWorksharedCopy(bool workshared, bool loadLinks) => workshared && !loadLinks;
}
