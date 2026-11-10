using System;

namespace XIVgate.RevitBridge;

// Explicit, host-independent policy: exact dialog identity and narrowly scoped application allowlist.
public static class WarningPolicy
{
    // Standard task-dialog return codes. Never choose the persistent "do not warn again" link.
    public static int? OpeningCopyDialogResult(bool enabled, bool authenticated, bool busy,
        bool openingOwnedCopy, bool capturedError, int priorAcknowledgements, string dialogId)
    {
        if (!enabled || !authenticated || !busy || !openingOwnedCopy || capturedError || priorAcknowledgements >= 4)
            return null;
        if (string.Equals(dialogId, "TaskDialog_Views_Related_To_Analytical_Changed", StringComparison.Ordinal))
            return 8; // TaskDialogResult.Close
        if (string.Equals(dialogId, "TaskDialog_Missing_Third_Party_Updater", StringComparison.Ordinal) ||
            string.Equals(dialogId, "TaskDialog_Missing_Third_Party_Updaters", StringComparison.Ordinal))
            return 1001; // TaskDialogResult.CommandLink1: continue only for this copied document
        return null;
    }

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
