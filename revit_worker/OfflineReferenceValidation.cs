using System;
using System.IO;
using Autodesk.Revit.DB;

namespace XIVgate.RevitBridge;

public static class OfflineReferenceValidation
{
    public static void Validate(string copy, OfflineReferencePreparer.Preparation prepared)
    {
        if (prepared.Ids.Length == 0) return;
        using var path = new FilePath(copy);
        using var data = TransmissionData.ReadTransmissionData(path);
        if (data == null) throw new IOException("Revit не прочитал подготовленные метаданные рабочей копии.");
        if (!data.IsTransmitted) throw new IOException("Revit не подтвердил статус IsTransmitted рабочей копии.");
        foreach (long value in prepared.Ids) {
            var id = new ElementId(value);
            using var saved = data.GetLastSavedReferenceData(id);
            using var desired = data.GetDesiredReferenceData(id);
            foreach (var reference in new[] { saved, desired }) {
                using var referencePath = reference.GetPath();
                string visible = ModelPathUtils.ConvertModelPathToUserVisiblePath(referencePath);
                string expected = Path.Combine(Path.GetDirectoryName(copy)!, "UnloadedLink_" + value + ".rvt");
                if (reference.ExternalFileReferenceType != ExternalFileReferenceType.RevitLink || reference.PathType != PathType.Absolute ||
                    reference.GetLinkedFileStatus() != LinkedFileStatus.Unloaded || !string.Equals(visible, expected, StringComparison.OrdinalIgnoreCase))
                    throw new IOException("Revit не подтвердил отключение серверной связи " + value + ". Рабочая копия не открыта.");
            }
        }
    }
}
