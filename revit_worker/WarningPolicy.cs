using System;

namespace XIVgate.RevitBridge;

// Explicit, host-independent policy: exact dialog identity and narrowly scoped application allowlist.
public static class WarningPolicy
{
    public static bool DismissOptionalStartupFailure(bool enabled, bool authenticated, bool startupOnly,
        bool alreadyHandled, string dialogId, string message) =>
        enabled && authenticated && startupOnly && !alreadyHandled &&
        string.Equals(dialogId, "TaskDialog_External_Tools_External_Tool_Failure", StringComparison.Ordinal) &&
        message.IndexOf("\"Application Dynamo Player For Revit\"", StringComparison.Ordinal) >= 0;

    public static bool AcknowledgeDocumentWarning(bool enabled, bool authenticated, bool busy,
        bool openingOwnedCopy, bool capturedError, string dialogId) =>
        enabled && authenticated && busy && openingOwnedCopy && !capturedError &&
        string.Equals(dialogId, "Dialog_Revit_DocWarnDialog", StringComparison.Ordinal);

    public static bool DeleteWarning(string severity) => string.Equals(severity, "Warning", StringComparison.Ordinal);
    public static bool IsError(string severity) => severity == "Error" || severity == "DocumentCorruption";
    public static bool RequestRollback(bool accessorActive, bool committing, bool hasErrors) => accessorActive && committing && hasErrors;
}
