using System;

namespace XIVgate.RevitBridge;

// Explicit, host-independent policy: no caption matching, localization heuristic or broad acceptance.
public static class WarningPolicy
{
    public static bool AcknowledgeDocumentWarning(bool enabled, bool authenticated, bool busy,
        bool openingOwnedCopy, bool capturedError, string dialogId) =>
        enabled && authenticated && busy && openingOwnedCopy && !capturedError &&
        string.Equals(dialogId, "Dialog_Revit_DocWarnDialog", StringComparison.Ordinal);

    public static bool DeleteWarning(string severity) => string.Equals(severity, "Warning", StringComparison.Ordinal);
    public static bool IsError(string severity) => severity == "Error" || severity == "DocumentCorruption";
    public static bool RequestRollback(bool accessorActive, bool committing, bool hasErrors) => accessorActive && committing && hasErrors;
}
